"""BM25 keyword retriever over an in-memory EvidenceChunk corpus.

Wraps ``rank_bm25.BM25Okapi`` when the package is installed (lazy import at
first use); otherwise a compact TF-IDF fallback in this file keeps keyword
search fully functional offline. Tokenization is French-friendly: lowercase,
accent-stripped, split on non-alphanumerics.
"""

from __future__ import annotations

import asyncio
import inspect
import math
import re
import unicodedata
from pathlib import Path
from typing import Any, Optional

from backend.core.models import EvidenceChunk
from backend.retrieval.scoring import bm25_to_unit
from backend.vectorstore.memory_store import matches_filters

_SPLIT_RE = re.compile(r"[^0-9a-z]+")


def tokenize(text: str) -> list[str]:
    """French-sane tokenizer: lowercase, strip accents, split non-alnum."""
    normalized = unicodedata.normalize("NFKD", text.lower())
    ascii_text = "".join(c for c in normalized if not unicodedata.combining(c))
    return [token for token in _SPLIT_RE.split(ascii_text) if token]


class _TfidfFallback:
    """Minimal TF-IDF scorer used when rank_bm25 is unavailable."""

    def __init__(self, corpus: list[list[str]]):
        self._corpus = corpus
        n_docs = max(1, len(corpus))
        df: dict[str, int] = {}
        for doc in corpus:
            for token in set(doc):
                df[token] = df.get(token, 0) + 1
        self._idf = {
            token: math.log((n_docs - count + 0.5) / (count + 0.5) + 1.0)
            for token, count in df.items()
        }

    def get_scores(self, query: list[str]) -> list[float]:
        scores = []
        for doc in self._corpus:
            tf: dict[str, int] = {}
            for token in doc:
                tf[token] = tf.get(token, 0) + 1
            score = sum(
                (tf.get(token, 0) / max(1, len(doc))) * self._idf.get(token, 0.0)
                for token in query
            )
            scores.append(score)
        return scores


class BM25Retriever:
    """Keyword retriever over EvidenceChunk content (BM25 or TF-IDF)."""

    def __init__(self) -> None:
        self._chunks: list[EvidenceChunk] = []
        self._scorer: Any = None
        self._backend = "tfidf"

    def _rebuild(self) -> None:
        if not self._chunks:
            self._scorer = None
            self._backend = "empty"
            return
        # retrieval_text carries the contextual prefix when set (article
        # children); raw content is the fallback for every other chunk.
        corpus = [tokenize(chunk.retrieval_text or chunk.content) for chunk in self._chunks]
        try:
            from rank_bm25 import BM25Okapi

            self._scorer = BM25Okapi(corpus)
            self._backend = "bm25"
        except ImportError:
            self._scorer = _TfidfFallback(corpus)
            self._backend = "tfidf"

    def add_documents(self, chunks: list[EvidenceChunk]) -> None:
        """Add chunks to the corpus and rebuild the index.

        Heading chunks (``role="heading"``) are skipped: bare headings like
        "LIVRE IV : ..." stay in the vector store for structure browsing but
        must never be direct keyword hits.  Their terms still reach the index
        through the children's contextual ``retrieval_text`` prefix.
        """
        self._chunks.extend(
            chunk for chunk in chunks if (chunk.metadata or {}).get("role") != "heading"
        )
        self._rebuild()

    def delete_documents(self, chunk_ids: list[str]) -> int:
        """Remove chunks by id and rebuild the index. Returns removed count."""
        removed = 0
        kept: list[EvidenceChunk] = []
        for chunk in self._chunks:
            if chunk.chunk_id in chunk_ids:
                removed += 1
            else:
                kept.append(chunk)
        self._chunks = kept
        self._rebuild()
        return removed

    def clear(self) -> None:
        """Empty the keyword corpus."""
        self._chunks = []
        self._scorer = None

    @property
    def size(self) -> int:
        return len(self._chunks)

    def search(
        self,
        query: str,
        top_k: int,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[EvidenceChunk]:
        """Score the corpus; return top_k chunks with scores in [0, 1]."""
        if not self._chunks or self._scorer is None:
            return []
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        raw_scores = self._scorer.get_scores(query_tokens)
        scored = [
            (float(score), chunk)
            for score, chunk in zip(raw_scores, self._chunks)
            if score > 0 and matches_filters(chunk, filters)
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        results: list[EvidenceChunk] = []
        for score, chunk in scored[:top_k]:
            chunk.retrieval_score = bm25_to_unit(score)
            results.append(chunk)
        return results


def corpus_generation(data_dir: Any) -> str:
    """Identify the persisted corpus version used to coordinate index refreshes."""
    try:
        stat = (Path(data_dir) / "versions.json").stat()
    except OSError:
        return "missing"
    return f"{stat.st_mtime_ns}:{stat.st_size}"


async def hydrate_bm25(ctx: Any, *, force: bool = False) -> BM25Retriever:
    """Build BM25 from the shared vector store and atomically swap it into the context."""
    generation = corpus_generation(ctx.settings.data_dir)
    current = ctx.extras.get("bm25")
    if (
        not force
        and isinstance(current, BM25Retriever)
        and ctx.extras.get("bm25_generation") == generation
    ):
        return current

    lock = ctx.extras.setdefault("bm25_refresh_lock", asyncio.Lock())
    async with lock:
        current = ctx.extras.get("bm25")
        if (
            not force
            and isinstance(current, BM25Retriever)
            and ctx.extras.get("bm25_generation") == generation
        ):
            return current
        from backend.ingestion.versioning import VersionStore

        chunks: list[EvidenceChunk] = []
        getter = getattr(ctx.vector_store, "get_by_document_id", None)
        if getter is not None:
            for document_id in VersionStore(ctx.settings.data_dir).list_document_ids():
                result = getter(document_id)
                if inspect.isawaitable(result):
                    result = await result
                chunks.extend(result or [])

        refreshed = BM25Retriever()
        refreshed.add_documents(chunks)
        ctx.extras["bm25"] = refreshed
        ctx.extras["bm25_generation"] = generation
        ctx.extras["bm25_chunk_count"] = refreshed.size
        return refreshed
