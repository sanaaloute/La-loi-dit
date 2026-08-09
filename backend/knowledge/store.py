"""LegalGraphStore: relational legal knowledge graph (spec §19, §34).

Deliberately relational (SQLite dev / Postgres prod via SQLAlchemy Core) —
no graph database. Tables:

  * ``documents``           — one row per ingested instrument
  * ``legal_articles``      — one row per (document, article), with hierarchy
  * ``legal_relationships`` — typed edges (contains / amends / repeals /
                              references / referenced_by / issued_by /
                              applies_to)

Follows the :mod:`backend.memory.store` pattern: lazily created async engine,
fallback to a local SQLite file under ``settings.data_dir`` when the
configured database is unreachable, and no public method ever raises — graph
persistence must never break ingestion or retrieval; outages degrade to
no-op writes and empty reads (counted in ``stats["db_failures"]``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import unicodedata
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from backend.knowledge.models import (
    ExtractedRelationship,
    LegalArticleRecord,
    LegalDocumentRecord,
    LegalRelationshipRecord,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _normalize(text: str) -> str:
    """Lowercase, strip accents and collapse whitespace for name matching."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(ascii_only.lower().split())


class LegalGraphStore:
    """Async relational store for the legal knowledge graph."""

    def __init__(self, settings: Any, url: Optional[str] = None):
        self.settings = settings
        self._url_override = url
        # Lazily initialised SQLAlchemy state (heavy imports stay lazy).
        self._engine: Any = None
        self._session_factory: Any = None
        self._tables: Any = None
        self._db_ready = False
        self._db_attempted = False
        self._init_lock = asyncio.Lock()
        self.stats: dict[str, int] = {"db_failures": 0}

    # ------------------------------------------------------------------
    # Engine / schema bootstrap (lazy, never raises)
    # ------------------------------------------------------------------

    def _build_schema(self):
        from sqlalchemy import (
            Column,
            Float,
            Integer,
            MetaData,
            String,
            Table,
            Text,
            UniqueConstraint,
        )

        metadata = MetaData()
        documents = Table(
            "documents",
            metadata,
            Column("document_id", String(64), primary_key=True),
            Column("name", Text),
            Column("document_type", String(32)),
            Column("law_number", String(64), index=True),
            Column("jurisdiction", String(64)),
            Column("status", String(32)),
            Column("issuing_authority", String(255)),
            Column("authority", String(64)),
            Column("publication_date", String(10)),  # ISO date
            Column("effective_date", String(10)),  # ISO date
            Column("source_url", Text),
            Column("version", Integer, default=1),
            Column("content_hash", String(64)),
            Column("created_at", String(64)),
            Column("updated_at", String(64)),
        )
        legal_articles = Table(
            "legal_articles",
            metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("document_id", String(64), index=True),
            Column("article", String(64)),
            Column("section", String(255)),
            Column("hierarchy", Text),  # JSON-encoded dict[str, str]
            Column("page", Integer),
            Column("text_preview", Text),
            Column("status", String(32)),
            Column("valid_from", String(10)),  # ISO date
            Column("valid_until", String(10)),  # ISO date
            UniqueConstraint("document_id", "article", name="uq_legal_article"),
        )
        legal_relationships = Table(
            "legal_relationships",
            metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("src_document", String(64), index=True),
            Column("src_article", String(64)),
            Column("relation", String(32), index=True),
            Column("dst_document", String(64), index=True),
            Column("dst_article", String(64)),
            Column("dst_free_text", Text),  # unresolved target mention
            Column("extracted_by", String(16), default="regex"),
            Column("confidence", Float, default=1.0),
            Column("created_at", String(64)),
        )
        return metadata, {
            "documents": documents,
            "legal_articles": legal_articles,
            "legal_relationships": legal_relationships,
        }

    async def _ensure_db(self) -> bool:
        """Create the engine + tables on first use (False when unavailable)."""
        if self._db_ready:
            return True
        if self._db_attempted:
            return False
        async with self._init_lock:
            if self._db_ready:
                return True
            if self._db_attempted:
                return False
            self._db_attempted = True
            try:
                from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

                metadata, tables = self._build_schema()
                urls = [self._url_override or self.settings.database_url]
                try:
                    data_dir = self.settings.ensure_data_dir()
                    fallback = f"sqlite+aiosqlite:///{(data_dir / 'legal_graph_fallback.db').as_posix()}"
                    if fallback not in urls:
                        urls.append(fallback)
                except Exception:
                    pass
                for url in urls:
                    engine = None
                    try:
                        engine = create_async_engine(url)
                        async with engine.begin() as conn:
                            await conn.run_sync(metadata.create_all)
                        self._engine = engine
                        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)
                        self._tables = tables
                        self._db_ready = True
                        return True
                    except Exception:
                        if engine is not None:
                            try:
                                await engine.dispose()
                            except Exception:
                                pass
                self.stats["db_failures"] += 1
                return False
            except Exception:
                # SQLAlchemy/aiosqlite not installed or broken: degrade to no-op.
                self.stats["db_failures"] += 1
                return False

    async def close(self) -> None:
        if self._engine is not None:
            try:
                await self._engine.dispose()
            except Exception:
                pass
            self._engine = None
            self._session_factory = None
            self._db_ready = False
            self._db_attempted = False

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------

    async def upsert_document(self, record: LegalDocumentRecord) -> None:
        """Insert or replace a document row (delete-then-insert, portable)."""
        try:
            if not await self._ensure_db():
                return
            from sqlalchemy import delete, select

            t = self._tables["documents"]
            async with self._session_factory() as session:
                existing = (
                    await session.execute(
                        select(t.c.created_at).where(t.c.document_id == record.document_id)
                    )
                ).first()
                created_at = existing.created_at if existing else _iso(record.created_at)
                await session.execute(delete(t).where(t.c.document_id == record.document_id))
                await session.execute(
                    t.insert(),
                    {
                        "document_id": record.document_id,
                        "name": record.name,
                        "document_type": record.document_type,
                        "law_number": record.law_number,
                        "jurisdiction": record.jurisdiction,
                        "status": record.status,
                        "issuing_authority": record.issuing_authority,
                        "authority": record.authority,
                        "publication_date": record.publication_date,
                        "effective_date": record.effective_date,
                        "source_url": record.source_url,
                        "version": record.version,
                        "content_hash": record.content_hash,
                        "created_at": created_at,
                        "updated_at": _iso(_utcnow()),
                    },
                )
                await session.commit()
        except Exception:
            self.stats["db_failures"] += 1
            logger.warning("legal graph: upsert_document failed", exc_info=True)

    async def get_document(self, document_id: str) -> Optional[LegalDocumentRecord]:
        try:
            if not await self._ensure_db():
                return None
            from sqlalchemy import select

            t = self._tables["documents"]
            async with self._session_factory() as session:
                row = (
                    await session.execute(select(t).where(t.c.document_id == document_id))
                ).first()
            return self._row_to_document(row) if row else None
        except Exception:
            self.stats["db_failures"] += 1
            logger.warning("legal graph: get_document failed", exc_info=True)
            return None

    async def find_documents(
        self, name_hint: Optional[str] = None, law_number: Optional[str] = None
    ) -> list[LegalDocumentRecord]:
        """Resolve a free-text hint ("code du travail") or law number to documents.

        Name matching is normalized (case/accents) substring containment in
        either direction; law numbers match exactly. The corpus is small, so
        filtering happens in Python after a full scan.
        """
        try:
            if not await self._ensure_db():
                return []
            from sqlalchemy import select

            t = self._tables["documents"]
            async with self._session_factory() as session:
                rows = (await session.execute(select(t))).all()
            records = [self._row_to_document(r) for r in rows]
            if law_number:
                wanted = _normalize(law_number).replace(" ", "")
                return [
                    r for r in records if r.law_number and _normalize(r.law_number).replace(" ", "") == wanted
                ]
            if name_hint:
                hint = _normalize(name_hint)
                return [r for r in records if r.name and (hint in _normalize(r.name) or _normalize(r.name) in hint)]
            return records
        except Exception:
            self.stats["db_failures"] += 1
            logger.warning("legal graph: find_documents failed", exc_info=True)
            return []

    @staticmethod
    def _row_to_document(row: Any) -> LegalDocumentRecord:
        return LegalDocumentRecord(
            document_id=row.document_id,
            name=row.name or "",
            document_type=row.document_type,
            law_number=row.law_number,
            jurisdiction=row.jurisdiction or "",
            status=row.status or "",
            issuing_authority=row.issuing_authority,
            authority=row.authority,
            publication_date=row.publication_date,
            effective_date=row.effective_date,
            source_url=row.source_url,
            version=row.version or 1,
            content_hash=row.content_hash or "",
            created_at=datetime.fromisoformat(row.created_at),
            updated_at=datetime.fromisoformat(row.updated_at),
        )

    # ------------------------------------------------------------------
    # Articles
    # ------------------------------------------------------------------

    async def upsert_articles(self, document_id: str, articles: Sequence[LegalArticleRecord]) -> None:
        """Replace the article set of a document (a re-ingest re-stamps it)."""
        try:
            if not await self._ensure_db():
                return
            from sqlalchemy import delete

            t = self._tables["legal_articles"]
            async with self._session_factory() as session:
                await session.execute(delete(t).where(t.c.document_id == document_id))
                if articles:
                    await session.execute(
                        t.insert(),
                        [
                            {
                                "document_id": document_id,
                                "article": a.article,
                                "section": a.section,
                                "hierarchy": json.dumps(a.hierarchy, ensure_ascii=False),
                                "page": a.page,
                                "text_preview": a.text_preview,
                                "status": a.status,
                                "valid_from": a.valid_from,
                                "valid_until": a.valid_until,
                            }
                            for a in articles
                        ],
                    )
                await session.commit()
        except Exception:
            self.stats["db_failures"] += 1
            logger.warning("legal graph: upsert_articles failed", exc_info=True)

    async def articles_of(self, document_id: str, article: Optional[str] = None) -> list[LegalArticleRecord]:
        try:
            if not await self._ensure_db():
                return []
            from sqlalchemy import select

            t = self._tables["legal_articles"]
            stmt = select(t).where(t.c.document_id == document_id).order_by(t.c.id)
            if article is not None:
                stmt = stmt.where(t.c.article == article)
            async with self._session_factory() as session:
                rows = (await session.execute(stmt)).all()
            return [
                LegalArticleRecord(
                    document_id=r.document_id,
                    article=r.article,
                    section=r.section,
                    hierarchy=json.loads(r.hierarchy) if r.hierarchy else {},
                    page=r.page,
                    text_preview=r.text_preview or "",
                    status=r.status or "",
                    valid_from=r.valid_from,
                    valid_until=r.valid_until,
                )
                for r in rows
            ]
        except Exception:
            self.stats["db_failures"] += 1
            logger.warning("legal graph: articles_of failed", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    async def add_relationships(self, relationships: Sequence[ExtractedRelationship]) -> int:
        """Insert edges, skipping exact duplicates already stored. Returns count added."""
        if not relationships:
            return 0
        try:
            if not await self._ensure_db():
                return 0
            from sqlalchemy import select

            t = self._tables["legal_relationships"]
            src_ids = {r.src_document for r in relationships if r.src_document}
            async with self._session_factory() as session:
                existing_keys: set[tuple] = set()
                for src_id in src_ids:
                    rows = (
                        await session.execute(
                            select(
                                t.c.src_document,
                                t.c.src_article,
                                t.c.relation,
                                t.c.dst_document,
                                t.c.dst_article,
                                t.c.dst_free_text,
                            ).where(t.c.src_document == src_id)
                        )
                    ).all()
                    existing_keys.update(tuple(row) for row in rows)
                now = _iso(_utcnow())
                payload = []
                seen: set[tuple] = set()
                for rel in relationships:
                    key = (
                        rel.src_document,
                        rel.src_article,
                        rel.relation.value,
                        rel.dst_document,
                        rel.dst_article,
                        rel.dst_free_text,
                    )
                    if key in existing_keys or key in seen:
                        continue
                    seen.add(key)
                    payload.append(
                        {
                            "src_document": rel.src_document,
                            "src_article": rel.src_article,
                            "relation": rel.relation.value,
                            "dst_document": rel.dst_document,
                            "dst_article": rel.dst_article,
                            "dst_free_text": rel.dst_free_text,
                            "extracted_by": rel.extracted_by,
                            "confidence": rel.confidence,
                            "created_at": now,
                        }
                    )
                if payload:
                    await session.execute(t.insert(), payload)
                await session.commit()
            return len(payload)
        except Exception:
            self.stats["db_failures"] += 1
            logger.warning("legal graph: add_relationships failed", exc_info=True)
            return 0

    async def relationships_for(
        self, document_id: str, article: Optional[str] = None
    ) -> list[LegalRelationshipRecord]:
        """Edges touching a document (optionally one article), either direction.

        Returning dst-side edges is what answers "referenced by whom?" /
        "qu'est-ce qui a changé dans cette disposition ?" (spec §27) without
        materializing inverse ``referenced_by`` rows.
        """
        try:
            if not await self._ensure_db():
                return []
            from sqlalchemy import or_, select

            t = self._tables["legal_relationships"]
            if article is None:
                cond = or_(t.c.src_document == document_id, t.c.dst_document == document_id)
            else:
                cond = or_(
                    (t.c.src_document == document_id) & (t.c.src_article == article),
                    (t.c.dst_document == document_id) & (t.c.dst_article == article),
                )
            async with self._session_factory() as session:
                rows = (await session.execute(select(t).where(cond).order_by(t.c.id))).all()
            return [
                LegalRelationshipRecord(
                    id=r.id,
                    src_document=r.src_document or "",
                    src_article=r.src_article,
                    relation=r.relation,
                    dst_document=r.dst_document,
                    dst_article=r.dst_article,
                    dst_free_text=r.dst_free_text,
                    extracted_by=r.extracted_by or "regex",
                    confidence=r.confidence if r.confidence is not None else 1.0,
                    created_at=datetime.fromisoformat(r.created_at),
                )
                for r in rows
            ]
        except Exception:
            self.stats["db_failures"] += 1
            logger.warning("legal graph: relationships_for failed", exc_info=True)
            return []

    async def clear_document(self, document_id: str) -> None:
        """Drop a document's rows and its outgoing edges (used on re-ingest)."""
        try:
            if not await self._ensure_db():
                return
            from sqlalchemy import delete

            async with self._session_factory() as session:
                await session.execute(
                    delete(self._tables["legal_relationships"]).where(
                        self._tables["legal_relationships"].c.src_document == document_id
                    )
                )
                await session.execute(
                    delete(self._tables["legal_articles"]).where(
                        self._tables["legal_articles"].c.document_id == document_id
                    )
                )
                await session.execute(
                    delete(self._tables["documents"]).where(
                        self._tables["documents"].c.document_id == document_id
                    )
                )
                await session.commit()
        except Exception:
            self.stats["db_failures"] += 1
            logger.warning("legal graph: clear_document failed", exc_info=True)


# ----------------------------------------------------------------------
# Context-bound accessor (shared by the ingestion hook and the graph worker)
# ----------------------------------------------------------------------


def graph_store_for(ctx: Any) -> Optional[LegalGraphStore]:
    """Return the process-shared graph store, memoized in ``ctx.extras``.

    Returns None when the feature flag is off. Creating the store is cheap —
    the engine and schema bootstrap happen lazily on first query.
    """
    settings = getattr(ctx, "settings", None)
    if settings is not None and not getattr(settings, "legal_graph_enabled", True):
        return None
    extras = getattr(ctx, "extras", None)
    if extras is None:
        return None
    store = extras.get("legal_graph")
    if store is None:
        store = LegalGraphStore(settings)
        extras["legal_graph"] = store
    return store
