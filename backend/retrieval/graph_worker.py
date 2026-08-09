"""Graph retrieval worker: explicit article/law lookup + neighbour expansion.

Two entry points (spec §19):

- ``run(task)`` — a regular retrieval worker for ``SearchKind.GRAPH`` tasks:
  resolves explicit mentions in the query ("article 341 du code du travail",
  "loi n° 028-2008/AN") through the relational graph store and returns the
  matching chunks from the vector store, marked ``retrieved_via="graph"``.
- ``expand(chunks)`` — follows ``references`` / ``amends`` / ``repeals`` edges
  of the top-ranked fused candidates and appends the related articles as
  low-score candidates, also marked ``retrieved_via="graph"``.

Both are fully best-effort: the store is optional, every failure degrades to
an empty list / the input unchanged, so the graph can never break retrieval.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Optional

from backend.core.context import AppContext
from backend.core.models import EvidenceChunk, SearchTask
from backend.knowledge.extraction import extract_query_mentions, normalize_article_number
from backend.knowledge.models import RelationType
from backend.knowledge.store import LegalGraphStore, graph_store_for
from backend.retrieval.workers import BaseWorker

logger = logging.getLogger(__name__)

#: Edges worth following when expanding a fused candidate set.
_EXPANSION_RELATIONS = {RelationType.REFERENCES, RelationType.AMENDS, RelationType.REPEALS}


async def _chunks_of_document(ctx: AppContext, document_id: str) -> list[EvidenceChunk]:
    store = getattr(ctx, "vector_store", None)
    if store is None or not hasattr(store, "get_by_document_id"):
        return []
    chunks = store.get_by_document_id(document_id)
    if inspect.isawaitable(chunks):
        chunks = await chunks
    return list(chunks or [])


def _mark_graph(chunk: EvidenceChunk, score: float) -> EvidenceChunk:
    chunk.retrieval_score = score
    chunk.metadata["retrieved_via"] = "graph"
    return chunk


class GraphWorker(BaseWorker):
    """SearchKind.GRAPH worker backed by the relational legal graph store."""

    def _store(self) -> Optional[LegalGraphStore]:
        try:
            return graph_store_for(self.ctx)
        except Exception:
            return None

    async def run(self, task: SearchTask) -> list[EvidenceChunk]:
        """Resolve explicit article/law mentions in the query; never raises."""
        try:
            store = self._store()
            if store is None:
                return []
            mentions = extract_query_mentions(task.query)
            if not mentions:
                return []
            out: list[EvidenceChunk] = []
            seen: set[str] = set()
            limit = max(task.top_k, self._fetch_k())
            for mention in mentions:
                if mention.law_number:
                    documents = await store.find_documents(law_number=mention.law_number)
                elif mention.document_hint:
                    documents = await store.find_documents(name_hint=mention.document_hint)
                else:
                    continue  # a bare article number without a document is too noisy
                for document in documents:
                    wanted = (
                        normalize_article_number(mention.article) if mention.article else None
                    )
                    for chunk in await _chunks_of_document(self.ctx, document.document_id):
                        if chunk.chunk_id in seen:
                            continue
                        if wanted is not None:
                            chunk_article = (
                                normalize_article_number(chunk.article) if chunk.article else None
                            )
                            if chunk_article != wanted:
                                continue
                        seen.add(chunk.chunk_id)
                        out.append(_mark_graph(chunk, score=1.0))
                        if len(out) >= limit:
                            return out
            return out
        except Exception:
            logger.warning("graph worker lookup failed; returning no graph evidence", exc_info=True)
            return []

    async def expand(self, chunks: list[EvidenceChunk]) -> list[EvidenceChunk]:
        """Append graph neighbours of the top chunks as low-score candidates.

        Follows resolved ``references``/``amends``/``repeals`` edges of the
        first ``settings.graph_expansion_sources`` chunks and pulls the target
        articles from the vector store (at most
        ``settings.graph_expansion_limit`` appended, stamped with
        ``settings.graph_expansion_score`` so they rank below directly
        retrieved evidence). Returns the input unchanged on any failure or
        when the store is unavailable.
        """
        if not chunks:
            return chunks
        try:
            store = self._store()
            if store is None:
                return chunks
            settings = self.ctx.settings
            expansion_sources = settings.graph_expansion_sources
            expansion_limit = settings.graph_expansion_limit
            expansion_score = settings.graph_expansion_score
            present = {chunk.chunk_id for chunk in chunks}
            appended: list[EvidenceChunk] = []
            for chunk in chunks[:expansion_sources]:
                if not chunk.document_id:
                    continue
                article = normalize_article_number(chunk.article) if chunk.article else None
                edges = await store.relationships_for(chunk.document_id, article)
                for edge in edges:
                    if edge.relation not in _EXPANSION_RELATIONS:
                        continue
                    if not edge.dst_document or not edge.dst_article:
                        continue  # unresolved target: nothing to fetch
                    wanted = normalize_article_number(edge.dst_article)
                    for candidate in await _chunks_of_document(self.ctx, edge.dst_document):
                        if candidate.chunk_id in present:
                            continue
                        candidate_article = (
                            normalize_article_number(candidate.article)
                            if candidate.article
                            else None
                        )
                        if candidate_article != wanted:
                            continue
                        present.add(candidate.chunk_id)
                        appended.append(_mark_graph(candidate, score=expansion_score))
                        if len(appended) >= expansion_limit:
                            return chunks + appended
            return chunks + appended
        except Exception:
            logger.warning("graph expansion failed; returning fused candidates", exc_info=True)
            return chunks
