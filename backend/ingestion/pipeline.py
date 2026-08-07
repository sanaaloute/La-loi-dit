"""Ingestion pipeline: extract -> clean -> chunk -> embed -> dedupe -> version -> upsert.

CLI:
    python -m backend.ingestion.pipeline <file-or-dir> [--name X] [--url Y]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Optional, Sequence, Union

from backend.core.exceptions import IngestionError
from backend.core.models import AuthorityLevel, DocumentIngestResult, EvidenceChunk
from backend.ingestion.chunking import looks_like_legal, parent_child_chunk, semantic_chunk
from backend.ingestion.loaders import SUPPORTED_EXTENSIONS, ExtractedDocument, load_any
from backend.ingestion.text_cleaning import clean_document
from backend.ingestion.versioning import VersionStore

logger = logging.getLogger(__name__)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _coerce_authority(value: Any) -> AuthorityLevel:
    if isinstance(value, AuthorityLevel):
        return value
    try:
        return AuthorityLevel(str(value))
    except ValueError:
        return AuthorityLevel.UNKNOWN


def _coerce_date(value: Any) -> Optional[date]:
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


class IngestionPipeline:
    """Orchestrates document ingestion against an :class:`AppContext`.

    Integrates defensively: a missing vector store, embedder or BM25 corpus
    (``ctx.extras["bm25"]``) degrades the run instead of crashing it.
    """

    def __init__(self, ctx: Any):
        self.ctx = ctx
        data_dir = getattr(getattr(ctx, "settings", None), "data_dir", Path("./data"))
        self._versions = VersionStore(data_dir)

    # ------------------------------------------------------------------ API

    async def ingest_path(self, path: Union[str, Path], **metadata: Any) -> DocumentIngestResult:
        """Load a file from disk (extension-dispatched) and ingest it."""
        p = Path(path)
        try:
            doc = await load_any(p)
        except IngestionError as exc:
            return DocumentIngestResult(
                document_id=self._document_id(metadata.get("document_id"), p.name),
                document_name=metadata.get("document_name") or p.name,
                chunks_created=0,
                version=0,
                status="failed",
                detail=str(exc),
            )
        metadata.setdefault("document_name", doc.name)
        doc_meta = dict(doc.metadata)
        doc_meta.update(metadata.pop("extra_metadata", {}) or {})
        return await self._ingest_document(doc, metadata, source_meta=doc_meta)

    async def ingest_text(self, name: str, text: str, **metadata: Any) -> DocumentIngestResult:
        """Ingest raw text under a logical document name."""
        doc = ExtractedDocument(name=name, text=text, pages=[text], metadata={"loader": "text"})
        metadata.setdefault("document_name", name)
        return await self._ingest_document(doc, metadata, source_meta={})

    # ------------------------------------------------------------- internals

    @staticmethod
    def _document_id(explicit: Optional[str], name: str) -> str:
        if explicit:
            return str(explicit)
        return hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]

    async def _ingest_document(
        self,
        doc: ExtractedDocument,
        metadata: dict[str, Any],
        source_meta: dict[str, Any],
    ) -> DocumentIngestResult:
        name = metadata.get("document_name") or doc.name
        document_id = self._document_id(metadata.get("document_id"), name)
        try:
            cleaned = clean_document(doc)
            if not cleaned.text.strip():
                raise IngestionError(f"No extractable text in {name}")

            content_hash = _content_hash(cleaned.text)
            version, changed = self._versions.check_version(document_id, content_hash)
            if not changed:
                return DocumentIngestResult(
                    document_id=document_id,
                    document_name=name,
                    chunks_created=0,
                    version=version,
                    status="skipped_duplicate",
                    detail="Content unchanged since last ingest",
                )

            chunks = self._chunk(cleaned, document_id, version, metadata)
            chunks = self._dedupe(chunks)
            if not chunks:
                raise IngestionError(f"Chunking produced no chunks for {name}")

            vectors = await self._embed(chunks)
            await self._upsert(chunks, vectors)
            await self._upsert_bm25(chunks)
            # Only NOW mark the content as ingested: a failure above leaves the
            # document re-ingestable instead of skipped as a duplicate.
            self._versions.commit_version(document_id, content_hash, version)

            return DocumentIngestResult(
                document_id=document_id,
                document_name=name,
                chunks_created=len(chunks),
                version=version,
                status="indexed",
                detail=json.dumps({"source": source_meta}, default=str) if source_meta else "",
            )
        except Exception as exc:
            logger.exception("Ingestion failed for %s", name)
            return DocumentIngestResult(
                document_id=document_id,
                document_name=name,
                chunks_created=0,
                version=0,
                status="failed",
                detail=str(exc),
            )

    def _chunk(
        self,
        doc: ExtractedDocument,
        document_id: str,
        version: int,
        metadata: dict[str, Any],
    ) -> list[EvidenceChunk]:
        settings = getattr(self.ctx, "settings", None)
        kwargs: dict[str, Any] = {
            "document_name": metadata.get("document_name") or doc.name,
            "authority": _coerce_authority(metadata.get("authority")),
            "publication_date": _coerce_date(metadata.get("publication_date")),
            "effective_date": _coerce_date(metadata.get("effective_date")),
            "government_body": metadata.get("government_body"),
            "url": metadata.get("url"),
            "legal_domains": metadata.get("legal_domains") or [],
            "version": version,
        }
        if settings is not None:
            kwargs["max_chunk_size"] = settings.chunk_max_size
            kwargs["overlap"] = settings.chunk_overlap
        strategy = metadata.get("chunk_strategy", "auto")
        if strategy == "auto":
            strategy = "semantic" if looks_like_legal(doc.text) else "parent_child"
        if strategy == "semantic":
            return semantic_chunk(doc, document_id, **kwargs)
        if strategy == "parent_child":
            kwargs.pop("max_chunk_size", None)
            kwargs.pop("overlap", None)
            if settings is not None:
                kwargs["parent_size"] = settings.chunk_parent_size
                kwargs["child_size"] = settings.chunk_child_size
                kwargs["child_overlap"] = settings.chunk_overlap
            return parent_child_chunk(doc, document_id, **kwargs)
        raise IngestionError(f"Unknown chunk strategy: {strategy}")

    @staticmethod
    def _dedupe(chunks: Sequence[EvidenceChunk]) -> list[EvidenceChunk]:
        """Drop chunks with identical content within this batch."""
        seen: set[str] = set()
        unique: list[EvidenceChunk] = []
        for chunk in chunks:
            digest = _content_hash(chunk.content)
            if digest in seen:
                continue
            seen.add(digest)
            unique.append(chunk)
        return unique

    async def _embed(self, chunks: list[EvidenceChunk]) -> list[list[float]]:
        embedder = getattr(self.ctx, "embedder", None)
        if embedder is None or not hasattr(embedder, "embed"):
            raise IngestionError("ctx.embedder is missing or does not provide embed()")
        vectors = await embedder.embed([c.content for c in chunks])
        if len(vectors) != len(chunks):
            raise IngestionError(
                f"Embedder returned {len(vectors)} vectors for {len(chunks)} chunks"
            )
        return vectors

    async def _upsert(self, chunks: list[EvidenceChunk], vectors: list[list[float]]) -> None:
        store = getattr(self.ctx, "vector_store", None)
        if store is None or not hasattr(store, "upsert"):
            logger.warning("ctx.vector_store missing or without upsert(); skipping vector upsert")
            return
        result = store.upsert(chunks, vectors)
        if inspect.isawaitable(result):
            await result

    async def _upsert_bm25(self, chunks: list[EvidenceChunk]) -> None:
        """Feed the BM25 corpus at ctx.extras['bm25'] when present (defensive)."""
        extras = getattr(self.ctx, "extras", None)
        if extras is None:
            return
        bm25 = extras.get("bm25") if hasattr(extras, "get") else getattr(extras, "bm25", None)
        if bm25 is None or not hasattr(bm25, "add_documents"):
            return
        try:
            result = bm25.add_documents(list(chunks))
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception("BM25 add_documents failed; vector index is unaffected")


# ---------------------------------------------------------------------- CLI


async def _run_cli(args: argparse.Namespace) -> int:
    from backend.core.context import build_context  # lazy: wires other subsystems

    ctx = await build_context()
    pipeline = IngestionPipeline(ctx)
    target = Path(args.target)

    metadata: dict[str, Any] = {}
    if args.name:
        metadata["document_name"] = args.name
    if args.url:
        metadata["url"] = args.url

    files: list[Path]
    if target.is_dir():
        files = sorted(
            p for p in target.rglob("*")
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        )
        if not files:
            print(f"No supported files found under {target}")
            return 1
    elif target.is_file():
        files = [target]
    else:
        print(f"Not found: {target}")
        return 1

    exit_code = 0
    for file_path in files:
        per_file = dict(metadata) if len(files) == 1 else {k: v for k, v in metadata.items() if k != "document_name"}
        result = await pipeline.ingest_path(file_path, **per_file)
        print(result.model_dump_json())
        if result.status == "failed":
            exit_code = 1
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest legal documents into the vector store.")
    parser.add_argument("target", help="File or directory to ingest")
    parser.add_argument("--name", dest="name", default=None, help="Override the document name (single file)")
    parser.add_argument("--url", dest="url", default=None, help="Canonical source URL for provenance")
    return asyncio.run(_run_cli(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
