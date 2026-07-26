"""MemoryStore: tiered conversational memory (MemGPT-style).

Tiers:
  * buffer   — raw recent chat messages per session (table ``messages``)
  * semantic — long-term memories with embeddings (table ``memories``),
               including ``summary`` records produced by
               :mod:`backend.memory.summarizer`
  * preferences — per-user settings blob (table ``preferences``)

Persistence uses SQLAlchemy 2.0 async with a lazily created engine. If the
configured database is unreachable (e.g. Postgres down), the store falls
back to a local SQLite file under ``settings.data_dir``; if that also
fails, everything degrades to process-local in-memory dicts. No public
method ever raises — the answer path must survive any storage outage.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Optional

from backend.core.models import ChatMessage, MemoryRecord

_RECALL_CACHE_PREFIX = "mem:recall:"
_PREFS_CACHE_PREFIX = "mem:prefs:"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class MemoryStore:
    """Async memory store implementing ``MemoryStoreProtocol`` (plus
    ``set_preferences`` and maintenance helpers used by pruning/summarizer).
    """

    def __init__(self, settings, cache, embedder):
        self.settings = settings
        self.cache = cache
        self.embedder = embedder
        # Lazily initialised SQLAlchemy state (heavy imports stay lazy).
        self._engine: Any = None
        self._session_factory: Any = None
        self._tables: Any = None
        self._db_ready = False
        self._db_attempted = False
        self._init_lock = asyncio.Lock()
        # In-memory fallback / mirror (also the last-resort store).
        self._mem_messages: dict[str, list[ChatMessage]] = {}
        self._mem_memories: dict[str, list[MemoryRecord]] = {}
        self._mem_prefs: dict[str, dict[str, Any]] = {}
        # Observability: hot-cache vs memory hits, plus DB failure count.
        self.stats: dict[str, int] = {"cache_hits": 0, "memory_hits": 0, "db_failures": 0}

    # ------------------------------------------------------------------
    # Engine / schema bootstrap (lazy, never raises)
    # ------------------------------------------------------------------

    def _build_schema(self):
        from sqlalchemy import Column, Float, Integer, MetaData, String, Table, Text

        metadata = MetaData()
        messages = Table(
            "messages",
            metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("session_id", String(128), index=True),
            Column("user_id", String(128), index=True),
            Column("role", String(32)),
            Column("content", Text),
            Column("created_at", String(64)),
        )
        memories = Table(
            "memories",
            metadata,
            Column("id", String(64), primary_key=True),
            Column("user_id", String(128), index=True),
            Column("session_id", String(128), default=""),
            Column("kind", String(32), default="semantic"),
            Column("content", Text),
            Column("embedding", Text),  # JSON-encoded list[float]
            Column("importance", Float, default=0.5),
            Column("created_at", String(64)),
            Column("last_accessed", String(64)),
        )
        preferences = Table(
            "preferences",
            metadata,
            Column("user_id", String(128), primary_key=True),
            Column("data", Text),  # JSON-encoded dict
        )
        return metadata, {"messages": messages, "memories": memories, "preferences": preferences}

    async def _ensure_db(self) -> bool:
        """Create the engine + tables on first use. Returns True when a
        working database is available, False otherwise (in-memory mode)."""
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
                urls = [self.settings.database_url]
                try:
                    data_dir = self.settings.ensure_data_dir()
                    fallback = f"sqlite+aiosqlite:///{(data_dir / 'memory_fallback.db').as_posix()}"
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
                # SQLAlchemy/aiosqlite not installed or broken: in-memory only.
                self.stats["db_failures"] += 1
                return False

    # ------------------------------------------------------------------
    # Buffer (short-term conversation window)
    # ------------------------------------------------------------------

    async def load_buffer(self, session_id: str, limit: int = 20) -> list[ChatMessage]:
        """Return the most recent ``limit`` messages of a session, oldest first."""
        try:
            if await self._ensure_db():
                from sqlalchemy import select

                t = self._tables["messages"]
                async with self._session_factory() as session:
                    rows = (
                        await session.execute(
                            select(t).where(t.c.session_id == session_id).order_by(t.c.id.desc()).limit(limit)
                        )
                    ).all()
                return [
                    ChatMessage(
                        role=r.role,
                        content=r.content,
                        created_at=datetime.fromisoformat(r.created_at),
                    )
                    for r in reversed(rows)
                ]
        except Exception:
            self.stats["db_failures"] += 1
        return list(self._mem_messages.get(session_id, []))[-limit:]

    async def append_turn(self, session_id: str, user_id: str, messages: list[ChatMessage]) -> None:
        """Append a conversation turn to the buffer (in-memory mirror + DB)."""
        mirror = self._mem_messages.setdefault(session_id, [])
        mirror.extend(messages)
        try:
            if await self._ensure_db():
                t = self._tables["messages"]
                async with self._session_factory() as session:
                    await session.execute(
                        t.insert(),
                        [
                            {
                                "session_id": session_id,
                                "user_id": user_id,
                                "role": m.role,
                                "content": m.content,
                                "created_at": _iso(m.created_at),
                            }
                            for m in messages
                        ],
                    )
                    await session.commit()
        except Exception:
            self.stats["db_failures"] += 1

    # ------------------------------------------------------------------
    # Semantic long-term memory
    # ------------------------------------------------------------------

    async def _load_records(self, user_id: Optional[str] = None) -> Optional[list[MemoryRecord]]:
        """Load memory rows from the DB. Returns None when DB unavailable."""
        if not await self._ensure_db():
            return None
        from sqlalchemy import select

        t = self._tables["memories"]
        async with self._session_factory() as session:
            stmt = select(t)
            if user_id is not None:
                stmt = stmt.where(t.c.user_id == user_id)
            rows = (await session.execute(stmt)).all()
        records = []
        for r in rows:
            try:
                embedding = json.loads(r.embedding) if r.embedding else None
            except Exception:
                embedding = None
            records.append(
                MemoryRecord(
                    id=r.id,
                    user_id=r.user_id,
                    session_id=r.session_id or "",
                    kind=r.kind or "semantic",
                    content=r.content or "",
                    embedding=embedding,
                    importance=r.importance if r.importance is not None else 0.5,
                    created_at=datetime.fromisoformat(r.created_at),
                    last_accessed=datetime.fromisoformat(r.last_accessed),
                )
            )
        return records

    async def recall(self, user_id: str, query: str, limit: int = 5) -> list[MemoryRecord]:
        """Embed ``query`` and cosine-rank the user's stored memories.

        Hot results are served from the cache; ``stats`` counts cache hits
        versus actual memory (re)computations.
        """
        cache_key = _RECALL_CACHE_PREFIX + hashlib.sha256(f"{user_id}|{query}".encode()).hexdigest()[:32]
        try:
            cached = await self.cache.get(cache_key)
        except Exception:
            cached = None
        if cached:
            self.stats["cache_hits"] += 1
            try:
                return [MemoryRecord(**d) for d in cached]
            except Exception:
                pass  # corrupt cache entry: recompute below

        try:
            records = await self._load_records(user_id)
        except Exception:
            self.stats["db_failures"] += 1
            records = None
        if records is None:
            records = list(self._mem_memories.get(user_id, []))

        query_vec: Optional[list[float]] = None
        if query:
            try:
                query_vec = (await self.embedder.embed([query]))[0]
            except Exception:
                query_vec = None

        scored = [
            (_cosine(query_vec, r.embedding) if query_vec and r.embedding else 0.0, r.importance, r)
            for r in records
        ]
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        out = [r for _, _, r in scored[:limit]]
        now = _utcnow()
        for r in out:
            r.last_accessed = now
        self.stats["memory_hits"] += len(out)
        try:
            await self.cache.set(cache_key, [r.model_dump(mode="json") for r in out])
        except Exception:
            pass
        return out

    async def remember(self, record: MemoryRecord) -> None:
        """Persist a memory record (embedding it first if needed)."""
        if record.embedding is None and record.content:
            try:
                record.embedding = (await self.embedder.embed([record.content]))[0]
            except Exception:
                record.embedding = None
        # In-memory mirror (upsert by id).
        bucket = self._mem_memories.setdefault(record.user_id, [])
        bucket[:] = [r for r in bucket if r.id != record.id]
        bucket.append(record)
        try:
            if await self._ensure_db():
                from sqlalchemy import delete

                t = self._tables["memories"]
                async with self._session_factory() as session:
                    await session.execute(delete(t).where(t.c.id == record.id))
                    await session.execute(
                        t.insert(),
                        {
                            "id": record.id,
                            "user_id": record.user_id,
                            "session_id": record.session_id,
                            "kind": record.kind,
                            "content": record.content,
                            "embedding": json.dumps(record.embedding) if record.embedding else None,
                            "importance": record.importance,
                            "created_at": _iso(record.created_at),
                            "last_accessed": _iso(record.last_accessed),
                        },
                    )
                    await session.commit()
                await self._invalidate_recall_cache()
        except Exception:
            self.stats["db_failures"] += 1

    async def _invalidate_recall_cache(self) -> None:
        try:
            await self.cache.clear_prefix(_RECALL_CACHE_PREFIX)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Preferences
    # ------------------------------------------------------------------

    async def get_preferences(self, user_id: str) -> dict[str, Any]:
        """Return the user's preference blob ({} when none stored)."""
        cache_key = _PREFS_CACHE_PREFIX + user_id
        try:
            cached = await self.cache.get(cache_key)
            if isinstance(cached, dict):
                return cached
        except Exception:
            pass
        try:
            if await self._ensure_db():
                from sqlalchemy import select

                t = self._tables["preferences"]
                async with self._session_factory() as session:
                    row = (await session.execute(select(t).where(t.c.user_id == user_id))).first()
                data = json.loads(row.data) if row and row.data else {}
                try:
                    await self.cache.set(cache_key, data)
                except Exception:
                    pass
                return data
        except Exception:
            self.stats["db_failures"] += 1
        return dict(self._mem_prefs.get(user_id, {}))

    async def set_preferences(self, user_id: str, prefs: dict[str, Any]) -> None:
        """Store (replace) the user's preference blob."""
        self._mem_prefs[user_id] = dict(prefs)
        try:
            if await self._ensure_db():
                from sqlalchemy import delete

                t = self._tables["preferences"]
                async with self._session_factory() as session:
                    await session.execute(delete(t).where(t.c.user_id == user_id))
                    await session.execute(t.insert(), {"user_id": user_id, "data": json.dumps(prefs, default=str)})
                    await session.commit()
        except Exception:
            self.stats["db_failures"] += 1
        try:
            await self.cache.set(_PREFS_CACHE_PREFIX + user_id, dict(prefs))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Maintenance helpers (used by pruning.py / summarizer.py)
    # ------------------------------------------------------------------

    async def list_memories(self, user_id: Optional[str] = None) -> list[MemoryRecord]:
        """List stored memory records, optionally restricted to one user."""
        try:
            records = await self._load_records(user_id)
        except Exception:
            self.stats["db_failures"] += 1
            records = None
        if records is not None:
            return records
        if user_id is not None:
            return list(self._mem_memories.get(user_id, []))
        return [r for bucket in self._mem_memories.values() for r in bucket]

    async def delete_memories(self, record_ids: list[str]) -> int:
        """Delete records by id. Returns how many were removed."""
        if not record_ids:
            return 0
        ids = set(record_ids)
        removed = 0
        for uid, bucket in self._mem_memories.items():
            before = len(bucket)
            bucket[:] = [r for r in bucket if r.id not in ids]
            removed += before - len(bucket)
        try:
            if await self._ensure_db():
                from sqlalchemy import delete, func, select

                t = self._tables["memories"]
                async with self._session_factory() as session:
                    count = (
                        await session.execute(select(func.count()).select_from(t).where(t.c.id.in_(record_ids)))
                    ).scalar() or 0
                    await session.execute(delete(t).where(t.c.id.in_(record_ids)))
                    await session.commit()
                removed = max(removed, int(count))
                await self._invalidate_recall_cache()
        except Exception:
            self.stats["db_failures"] += 1
        return removed
