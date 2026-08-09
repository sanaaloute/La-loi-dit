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
from backend.ingestion.chunking import legal_parent_child_chunk, looks_like_legal, parent_child_chunk, semantic_chunk
from backend.ingestion.classification import infer_authority, infer_legal_domains
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

    _DOCUMENT_TITLE_MAP: dict[str, str] = {
        "code-du-travail-burkina-faso.pdf": "Code du travail du Burkina Faso (Loi 028-2008/AN)",
        "constitution-burkina-faso.pdf": "Constitution du Burkina Faso (IVème République, 1991)",
        "constitution-burkina-faso-transition-2015.pdf": "Charte de la Transition du Burkina Faso (2015)",
        "traite-ohada.pdf": "Traité OHADA",
        "auscgie-societes-commerciales-gie-2014.pdf": "Acte uniforme OHADA relatif au droit des sociétés commerciales et du GIE (2014)",
        "audcg-droit-commercial-general-2010.pdf": "Acte uniforme OHADA relatif au droit commercial général (2010)",
        "aus-suretes-2010.pdf": "Acte uniforme OHADA relatif aux sûretés (2010)",
    }

    @classmethod
    def _display_name(cls, name: str) -> str:
        """Map a raw filename to a human-readable legal title when known."""
        lowered = name.lower().replace("\\", "/").split("/")[-1]
        return cls._DOCUMENT_TITLE_MAP.get(lowered, name)

    @staticmethod
    def _enrich_metadata(
        metadata: dict[str, Any], doc: ExtractedDocument
    ) -> dict[str, Any]:
        """Infer authority / legal domains from the document when not supplied.

        Keeps explicit caller-provided values untouched; only fills gaps so
        future retrieval and ranking can use proper provenance signals.
        """
        name = metadata.get("document_name") or doc.name
        text_sample = doc.text[:2000] if doc.text else ""
        if not metadata.get("authority"):
            metadata = {**metadata, "authority": infer_authority(name)}
        if not metadata.get("legal_domains"):
            metadata = {**metadata, "legal_domains": infer_legal_domains(name, text_sample)}
        metadata["document_name"] = IngestionPipeline._display_name(name)
        return metadata

    async def _delete_document_chunks(self, document_id: str) -> int:
        """Remove all vector and keyword chunks for a logical document.

        Safe when the vector store or BM25 corpus is missing/unreachable.
        Returns the number of vector rows deleted.
        """
        deleted = 0
        chunk_ids: list[str] = []
        store = getattr(self.ctx, "vector_store", None)
        if store is not None and hasattr(store, "get_by_document_id"):
            try:
                chunks = store.get_by_document_id(document_id)
                if inspect.isawaitable(chunks):
                    chunks = await chunks
                chunk_ids = [c.chunk_id for c in chunks]
            except Exception:
                logger.exception("Failed to query chunks for %s", document_id)

        if chunk_ids and store is not None and hasattr(store, "delete"):
            try:
                result = store.delete(chunk_ids)
                if inspect.isawaitable(result):
                    await result
                deleted = len(chunk_ids)
            except Exception:
                logger.exception("Failed to delete vector chunks for %s", document_id)

        bm25 = self.ctx.extras.get("bm25")
        if bm25 is not None and chunk_ids and hasattr(bm25, "delete_documents"):
            try:
                bm25.delete_documents(chunk_ids)
            except Exception:
                logger.exception("Failed to delete BM25 chunks for %s", document_id)
        return deleted

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

            metadata = self._enrich_metadata(metadata, cleaned)
            # _enrich_metadata may map the raw filename to a human-readable title.
            display_name = metadata.get("document_name") or name

            content_hash = _content_hash(cleaned.text)
            version, changed = self._versions.check_version(document_id, content_hash)
            if not changed:
                return DocumentIngestResult(
                    document_id=document_id,
                    document_name=display_name,
                    chunks_created=0,
                    version=version,
                    status="skipped_duplicate",
                    detail="Content unchanged since last ingest",
                )

            # Remove previous version chunks so the index never keeps stale data.
            if version > 1:
                await self._delete_document_chunks(document_id)

            chunks = self._chunk(cleaned, document_id, version, metadata)
            chunks = self._dedupe(chunks)
            if not chunks:
                raise IngestionError(f"Chunking produced no chunks for {display_name}")

            vectors = await self._embed(chunks)
            await self._upsert(chunks, vectors)
            await self._upsert_bm25(chunks)
            # Only NOW mark the content as ingested: a failure above leaves the
            # document re-ingestable instead of skipped as a duplicate.
            self._versions.commit_version(document_id, content_hash, version)

            return DocumentIngestResult(
                document_id=document_id,
                document_name=display_name,
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

    async def delete_document(self, document_id: str) -> DocumentIngestResult:
        """Remove a logical document from the vector store, keyword index and version registry."""
        deleted = await self._delete_document_chunks(document_id)
        self._versions.remove(document_id)
        return DocumentIngestResult(
            document_id=document_id,
            document_name="",
            chunks_created=-deleted,
            version=0,
            status="deleted",
            detail=f"Removed {deleted} chunks",
        )

    async def reindex_directory(
        self,
        path: Union[str, Path],
        *,
        metadata: Optional[dict[str, Any]] = None,
        gc: bool = True,
    ) -> list[DocumentIngestResult]:
        """Ingest every supported file in ``path`` and optionally remove stale documents.

        A document is considered stale when it is present in the version registry
        but no longer has a corresponding source file under ``path``.  Stale
        documents are deleted from the vector store and the registry so the index
        stays in sync with the folder.
        """
        target = Path(path)
        files: list[Path]
        if target.is_dir():
            files = sorted(
                p for p in target.rglob("*")
                if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
            )
        elif target.is_file():
            files = [target]
        else:
            raise IngestionError(f"Not found: {target}")

        base_meta = dict(metadata or {})
        results: list[DocumentIngestResult] = []
        for file_path in files:
            per_file = dict(base_meta) if len(files) == 1 else {k: v for k, v in base_meta.items() if k != "document_name"}
            results.append(await self.ingest_path(file_path, **per_file))

        if gc:
            # Compute expected document ids from the files actually found.
            expected_ids: set[str] = set()
            for f in files:
                if base_meta.get("document_id"):
                    expected_ids.add(str(base_meta["document_id"]))
                elif len(files) == 1 and base_meta.get("document_name"):
                    expected_ids.add(self._document_id(None, base_meta["document_name"]))
                else:
                    expected_ids.add(self._document_id(None, self._display_name(f.name)))
            for stale_id in self._versions.list_document_ids():
                if stale_id not in expected_ids:
                    logger.info("Removing stale document from index: %s", stale_id)
                    results.append(await self.delete_document(stale_id))

        return results

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
            strategy = "legal_parent_child" if looks_like_legal(doc.text) else "parent_child"
        if strategy == "parent_child":
            kwargs.pop("max_chunk_size", None)
            kwargs.pop("overlap", None)
            if settings is not None:
                kwargs.setdefault("parent_size", settings.chunk_parent_size)
                kwargs.setdefault("child_size", settings.chunk_child_size)
                kwargs.setdefault("child_overlap", settings.chunk_overlap)
            return parent_child_chunk(doc, document_id, **kwargs)
        if strategy == "legal_parent_child":
            kwargs.pop("max_chunk_size", None)
            kwargs.pop("overlap", None)
            if settings is not None:
                kwargs.setdefault("parent_size", settings.chunk_parent_size)
                kwargs.setdefault("child_size", settings.chunk_child_size)
                kwargs.setdefault("child_overlap", settings.chunk_overlap)
            return legal_parent_child_chunk(doc, document_id, **kwargs)
        if strategy == "semantic":
            return semantic_chunk(doc, document_id, **kwargs)
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
        # Batch large upserts so Milvus Lite / remote servers do not choke on
        # thousands of rows at once.
        batch_size = 500
        for i in range(0, len(chunks), batch_size):
            result = store.upsert(
                chunks[i : i + batch_size], vectors[i : i + batch_size]
            )
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

    if args.full_reindex:
        # Drop every known document so the next ingestion starts from scratch.
        for doc_id in list(pipeline._versions.list_document_ids()):
            print((await pipeline.delete_document(doc_id)).model_dump_json())

    if not target.exists():
        print(f"Not found: {target}")
        return 1

    results = await pipeline.reindex_directory(target, metadata=metadata, gc=args.gc)
    exit_code = 0
    for result in results:
        print(result.model_dump_json())
        if result.status == "failed":
            exit_code = 1
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest legal documents into the vector store.")
    parser.add_argument("target", help="File or directory to ingest")
    parser.add_argument("--name", dest="name", default=None, help="Override the document name (single file)")
    parser.add_argument("--url", dest="url", default=None, help="Canonical source URL for provenance")
    parser.add_argument(
        "--gc",
        dest="gc",
        action="store_true",
        default=True,
        help="Remove documents from the index that no longer exist under target (default: true)",
    )
    parser.add_argument(
        "--no-gc",
        dest="gc",
        action="store_false",
        help="Keep stale documents in the index even if their source files are gone",
    )
    parser.add_argument(
        "--full-reindex",
        dest="full_reindex",
        action="store_true",
        default=False,
        help="Delete the entire existing index before re-ingesting target",
    )
    return asyncio.run(_run_cli(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
