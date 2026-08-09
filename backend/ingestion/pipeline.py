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
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence, Union

from backend.core.embeddings import HashEmbeddings
from backend.core.exceptions import IngestionError
from backend.core.models import AuthorityLevel, DocumentIngestResult, DocumentType, EvidenceChunk
from backend.ingestion.chunking import legal_parent_child_chunk, looks_like_legal, parent_child_chunk, semantic_chunk
from backend.ingestion.classification import (
    extract_law_number,
    infer_authority,
    infer_document_type,
    infer_legal_domains,
)
from backend.ingestion.loaders import SUPPORTED_EXTENSIONS, ExtractedDocument, load_any
from backend.ingestion.text_cleaning import clean_document
from backend.ingestion.versioning import VersionStore

logger = logging.getLogger(__name__)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


#: Filename (under ``settings.data_dir``) of the persisted ingestion results.
RESULTS_FILENAME = "ingestion_results.json"


def load_ingestion_results(data_dir: Union[str, Path]) -> dict[str, dict[str, Any]]:
    """Latest ingestion record per document id (``{}`` when absent/corrupt)."""
    try:
        data = json.loads((Path(data_dir) / RESULTS_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record_ingestion_result(
    data_dir: Union[str, Path],
    result: DocumentIngestResult,
    *,
    path: Optional[Union[str, Path]] = None,
) -> None:
    """Persist the outcome of one ingest, keeping the latest record per document.

    Merges into ``ingestion_results.json`` and rewrites it atomically (temp
    file + ``os.replace``), mirroring :class:`VersionStore` so a crash
    mid-write cannot corrupt the store.
    """
    record: dict[str, Any] = {
        "document_id": result.document_id,
        "document_name": result.document_name,
        "status": result.status,
        "version": result.version,
        "chunks_created": result.chunks_created,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if path is not None:
        record["path"] = str(path)
    if result.status == "failed":
        record["error"] = result.detail

    state = load_ingestion_results(data_dir)
    state[result.document_id] = record

    target = Path(data_dir) / RESULTS_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


#: Fallback article key for chunks without an ``article`` metadata value.
_NO_ARTICLE_KEY = "__no_article__"


def _article_hashes(chunks: Sequence[EvidenceChunk]) -> dict[str, str]:
    """Hash each article's combined chunk text (spec §26 change detection).

    Chunks sharing an ``article`` key contribute their content in order;
    article-less chunks fall back to a ``section:``-qualified key, then to
    ``_NO_ARTICLE_KEY``.
    """
    buckets: dict[str, list[str]] = {}
    for chunk in chunks:
        key = chunk.article or (f"section:{chunk.section}" if chunk.section else _NO_ARTICLE_KEY)
        buckets.setdefault(key, []).append(chunk.content)
    return {key: _content_hash("\n\n".join(contents)) for key, contents in buckets.items()}


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


#: Bundled legal-sources file shipped with the repository.
_DEFAULT_SOURCES_PATH = Path(__file__).resolve().parents[2] / "data" / "legal_sources.json"

_TITLE_CACHE: dict[str, dict[str, str]] = {}


def _read_titles_file(resolved: Path) -> dict[str, str]:
    """Read a standalone ``{filename: display title}`` JSON file (fallback-safe)."""
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("document titles file must be a JSON object")
        return {str(k): str(v) for k, v in data.items()}
    except Exception as exc:
        logger.warning(
            "document_titles_load_failed",
            extra={"path": str(resolved), "error": str(exc), "fallback": "embedded_title_map"},
        )
        return dict(IngestionPipeline._DOCUMENT_TITLE_MAP)


def load_document_titles(path: Optional[Union[str, Path]] = None) -> dict[str, str]:
    """Resolve the document display-title map (jurisdiction-configurable).

    Resolution order: explicit ``path`` (standalone ``{filename: title}``
    JSON) → ``settings.document_titles_path`` (same shape) → the
    ``document_titles`` section of the legal-sources file
    (``settings.legal_sources_path`` or the bundled
    ``data/legal_sources.json``).  A missing/corrupt source falls back to the
    embedded ``IngestionPipeline._DOCUMENT_TITLE_MAP`` with a structured
    warning — never raises.
    """
    try:
        from backend.core.config import get_settings

        settings = get_settings()
    except Exception:  # settings unavailable: stay on the bundled default
        settings = None

    standalone = path or getattr(settings, "document_titles_path", None)
    if standalone:
        key = str(standalone)
        if key not in _TITLE_CACHE:
            _TITLE_CACHE[key] = _read_titles_file(Path(key))
        return _TITLE_CACHE[key]

    sources_path = getattr(settings, "legal_sources_path", None) or _DEFAULT_SOURCES_PATH
    key = f"{sources_path}#document_titles"
    if key not in _TITLE_CACHE:
        try:
            data = json.loads(Path(sources_path).read_text(encoding="utf-8"))
            raw = data.get("document_titles") if isinstance(data, dict) else None
            if not isinstance(raw, dict):
                raise ValueError("document_titles section unavailable")
            _TITLE_CACHE[key] = {str(k): str(v) for k, v in raw.items()}
        except Exception as exc:
            logger.warning(
                "document_titles_load_failed",
                extra={"path": str(sources_path), "error": str(exc), "fallback": "embedded_title_map"},
            )
            _TITLE_CACHE[key] = dict(IngestionPipeline._DOCUMENT_TITLE_MAP)
    return _TITLE_CACHE[key]


class IngestionPipeline:
    """Orchestrates document ingestion against an :class:`AppContext`.

    Integrates defensively: a missing vector store, embedder or BM25 corpus
    (``ctx.extras["bm25"]``) degrades the run instead of crashing it.
    """

    def __init__(self, ctx: Any):
        self.ctx = ctx
        data_dir = getattr(getattr(ctx, "settings", None), "data_dir", Path("./data"))
        self._data_dir = data_dir
        self._versions = VersionStore(data_dir)

    def _record_result(
        self, result: DocumentIngestResult, *, path: Optional[Union[str, Path]] = None
    ) -> None:
        """Persist the ingest outcome (spec §49); best-effort, never raises."""
        try:
            record_ingestion_result(self._data_dir, result, path=path)
        except Exception:
            logger.warning(
                "failed to persist ingestion result for %s", result.document_id, exc_info=True
            )

    # ------------------------------------------------------------------ API

    async def ingest_path(self, path: Union[str, Path], **metadata: Any) -> DocumentIngestResult:
        """Load a file from disk (extension-dispatched) and ingest it."""
        p = Path(path)
        try:
            doc = await load_any(p)
        except IngestionError as exc:
            result = DocumentIngestResult(
                document_id=self._document_id(metadata.get("document_id"), p.name),
                document_name=metadata.get("document_name") or p.name,
                chunks_created=0,
                version=0,
                status="failed",
                detail=str(exc),
            )
            self._record_result(result, path=p)
            return result
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

    #: Embedded fallback display titles; the effective map is resolved by
    #: :func:`load_document_titles` (legal-sources file / settings override).
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
        return load_document_titles().get(lowered, name)

    @staticmethod
    def _enrich_metadata(
        metadata: dict[str, Any], doc: ExtractedDocument
    ) -> dict[str, Any]:
        """Infer authority / domains / type / law number when not supplied.

        Keeps explicit caller-provided values untouched; only fills gaps so
        future retrieval and ranking can use proper provenance signals.  The
        display titles resolved by :func:`load_document_titles` carry the
        official law numbers (e.g. "(Loi 028-2008/AN)"), so ``law_number``
        is extracted from the display name first, then from the raw name.

        Temporal defaults (kept deliberately simple): ``valid_from`` defaults
        to ``effective_date``; ``status`` becomes "future" only when the
        effective date lies ahead, otherwise the model default ("active")
        stands — we do not claim repeal/expiry without explicit data.
        """
        name = metadata.get("document_name") or doc.name
        text_sample = doc.text[:2000] if doc.text else ""
        if not metadata.get("authority"):
            metadata = {**metadata, "authority": infer_authority(name)}
        if not metadata.get("legal_domains"):
            metadata = {**metadata, "legal_domains": infer_legal_domains(name, text_sample)}
        metadata["document_name"] = IngestionPipeline._display_name(name)
        display_name = metadata["document_name"]
        if not metadata.get("document_type"):
            metadata["document_type"] = infer_document_type(display_name, text_sample)
        if not metadata.get("law_number"):
            metadata["law_number"] = extract_law_number(display_name) or extract_law_number(name)
        if not metadata.get("issuing_authority") and metadata.get("government_body"):
            metadata["issuing_authority"] = metadata["government_body"]
        effective = _coerce_date(metadata.get("effective_date"))
        if not metadata.get("valid_from") and effective:
            metadata["valid_from"] = effective
        if not metadata.get("status") and effective and effective > date.today():
            metadata["status"] = "future"
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
                result = DocumentIngestResult(
                    document_id=document_id,
                    document_name=display_name,
                    chunks_created=0,
                    version=version,
                    status="skipped_duplicate",
                    detail="Content unchanged since last ingest",
                )
                self._record_result(result, path=source_meta.get("path"))
                return result

            # Remove previous version chunks so the index never keeps stale data.
            if version > 1:
                await self._delete_document_chunks(document_id)

            chunks = self._chunk(cleaned, document_id, version, metadata)
            chunks = self._dedupe(chunks)
            if not chunks:
                raise IngestionError(f"Chunking produced no chunks for {display_name}")
            self._stamp_document_metadata(chunks, metadata)

            article_hashes = _article_hashes(chunks)
            article_diff = self._versions.diff_articles(document_id, article_hashes)
            logger.info(
                "article_diff document_id=%s version=%d added=%d modified=%d deleted=%d",
                document_id,
                version,
                len(article_diff.added_articles),
                len(article_diff.modified_articles),
                len(article_diff.deleted_articles),
            )

            vectors = await self._embed(chunks)
            await self._upsert(chunks, vectors)
            await self._upsert_bm25(chunks)
            # Only NOW mark the content as ingested: a failure above leaves the
            # document re-ingestable instead of skipped as a duplicate.
            self._versions.commit_version(
                document_id, content_hash, version, article_hashes=article_hashes
            )
            # Legal knowledge graph persistence (spec §19/§34): additive and
            # best-effort — a graph failure must never fail ingestion.
            await self._persist_legal_graph(
                document_id, display_name, metadata, chunks, content_hash, version
            )

            detail_payload: dict[str, Any] = {"article_diff": article_diff.to_dict()}
            if source_meta:
                detail_payload["source"] = source_meta
            result = DocumentIngestResult(
                document_id=document_id,
                document_name=display_name,
                chunks_created=len(chunks),
                version=version,
                status="indexed",
                detail=json.dumps(detail_payload, default=str),
            )
            self._record_result(result, path=source_meta.get("path"))
            return result
        except Exception as exc:
            logger.exception("Ingestion failed for %s", name)
            result = DocumentIngestResult(
                document_id=document_id,
                document_name=name,
                chunks_created=0,
                version=0,
                status="failed",
                detail=str(exc),
            )
            self._record_result(result, path=source_meta.get("path"))
            return result

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

    @staticmethod
    def _stamp_document_metadata(chunks: list[EvidenceChunk], metadata: dict[str, Any]) -> None:
        """Propagate document-level enrichment onto every chunk (spec §6).

        Only fills fields the enrichment actually resolved; chunk defaults
        (``status="active"``, ``jurisdiction="Burkina Faso"``) stand otherwise.
        """
        document_type = metadata.get("document_type")
        if document_type is not None and not isinstance(document_type, DocumentType):
            try:
                document_type = DocumentType(str(document_type))
            except ValueError:
                document_type = None
        fields: dict[str, Any] = {
            "document_type": document_type,
            "law_number": metadata.get("law_number"),
            "issuing_authority": metadata.get("issuing_authority"),
            "jurisdiction": metadata.get("jurisdiction"),
            "status": metadata.get("status"),
            "valid_from": _coerce_date(metadata.get("valid_from")),
            "valid_until": _coerce_date(metadata.get("valid_until")),
        }
        for chunk in chunks:
            for field, value in fields.items():
                if value is not None:
                    setattr(chunk, field, value)

    def _embedding_model_name(self) -> Optional[str]:
        """Name of the embedder in use, stamped on chunks at upsert time."""
        embedder = getattr(self.ctx, "embedder", None)
        if embedder is None:
            return None
        if isinstance(embedder, HashEmbeddings):
            return "hash-embeddings"  # deterministic offline embedder
        settings = getattr(self.ctx, "settings", None)
        return getattr(settings, "embedding_model", None)

    async def _embed(self, chunks: list[EvidenceChunk]) -> list[list[float]]:
        embedder = getattr(self.ctx, "embedder", None)
        if embedder is None or not hasattr(embedder, "embed"):
            raise IngestionError("ctx.embedder is missing or does not provide embed()")
        vectors = await embedder.embed([c.content for c in chunks])
        if len(vectors) != len(chunks):
            raise IngestionError(
                f"Embedder returned {len(vectors)} vectors for {len(chunks)} chunks"
            )
        model_name = self._embedding_model_name()
        if model_name:
            for chunk in chunks:
                chunk.embedding_model = model_name
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

    async def _persist_legal_graph(
        self,
        document_id: str,
        display_name: str,
        metadata: dict[str, Any],
        chunks: list[EvidenceChunk],
        content_hash: str,
        version: int,
    ) -> None:
        """Upsert the document, its articles and extracted relationships into
        the relational legal knowledge graph (spec §19/§34).

        Fully best-effort: any failure is logged as a warning and swallowed so
        graph persistence can never fail ingestion. No-op when
        ``legal_graph_enabled`` is off.
        """
        try:
            from backend.knowledge.extraction import extract_from_chunks
            from backend.knowledge.models import LegalArticleRecord, LegalDocumentRecord
            from backend.knowledge.store import graph_store_for

            store = graph_store_for(self.ctx)
            if store is None:
                return

            def _iso(value: Any) -> Optional[str]:
                coerced = _coerce_date(value)
                return coerced.isoformat() if coerced else None

            document_type = metadata.get("document_type")
            authority = metadata.get("authority")
            await store.upsert_document(
                LegalDocumentRecord(
                    document_id=document_id,
                    name=display_name,
                    document_type=str(getattr(document_type, "value", document_type) or "") or None,
                    law_number=metadata.get("law_number"),
                    jurisdiction=metadata.get("jurisdiction") or "",
                    status=metadata.get("status") or "",
                    issuing_authority=metadata.get("issuing_authority"),
                    authority=str(getattr(authority, "value", authority) or "") or None,
                    publication_date=_iso(metadata.get("publication_date")),
                    effective_date=_iso(metadata.get("effective_date")),
                    source_url=metadata.get("url"),
                    version=version,
                    content_hash=content_hash,
                )
            )

            # One article row per distinct article key (first chunk wins).
            seen_articles: set[str] = set()
            articles: list[LegalArticleRecord] = []
            for chunk in chunks:
                if not chunk.article or chunk.article in seen_articles:
                    continue
                seen_articles.add(chunk.article)
                articles.append(
                    LegalArticleRecord(
                        document_id=document_id,
                        article=chunk.article,
                        section=chunk.section,
                        hierarchy=dict(chunk.hierarchy),
                        page=chunk.page,
                        text_preview=chunk.content[:300],
                        status=chunk.status or "",
                        valid_from=chunk.valid_from.isoformat() if chunk.valid_from else None,
                        valid_until=chunk.valid_until.isoformat() if chunk.valid_until else None,
                    )
                )
            await store.upsert_articles(document_id, articles)

            await store.add_relationships(extract_from_chunks(chunks))
        except Exception:
            logger.warning(
                "legal graph persistence failed for document_id=%s; ingestion is unaffected",
                document_id,
                exc_info=True,
            )


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
