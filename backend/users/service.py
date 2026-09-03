"""UserStore: async account persistence with an offline-safe bootstrap.

Same lazy pattern as ``backend.memory.store.MemoryStore``: the engine is
created on first use, the configured database falls back to a local SQLite
file, and if no database is reachable the store degrades to "dev-user-only"
mode — every method returns None/raises nothing, the API keeps booting with
the env-var dev users, and a warning is logged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel

from backend.core.config import Settings
from backend.core.exceptions import UserAlreadyExistsError
from backend.core.models import Role
from backend.security.passwords import hash_password, verify_password
from backend.users.models import TABLES, USER_MIGRATIONS, metadata

logger = logging.getLogger(__name__)


def normalize_phone(raw: str) -> str:
    """Canonical phone form (E.164-ish): optional leading ``+`` then 6-15 digits.

    Spaces, dashes, dots and parentheses are stripped. Returns "" when the
    input is not a plausible phone number.
    """
    cleaned = re.sub(r"[\s.\-()]", "", raw.strip())
    return cleaned if re.fullmatch(r"\+?\d{6,15}", cleaned) else ""


async def probe_database(settings: Settings, timeout: float = 3.0) -> bool:
    """Cheap ``SELECT 1`` against the CONFIGURED database_url (no fallback).

    Used by /ready: readiness reports the configured infrastructure, never
    the degraded local substitutes.
    """
    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(settings.database_url)
        try:
            async with asyncio.timeout(timeout):
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
            return True
        finally:
            try:
                await engine.dispose()
            except Exception:
                pass
    except Exception:
        return False


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class UserRecord(BaseModel):
    """A persisted user account."""

    id: str
    email: str
    phone: str = ""
    name: str = ""
    role: Role = Role.USER
    tier: str = "gratuit"
    workspace_id: str = ""
    created_at: str = ""
    # --- billing (Paddle) ---
    paddle_customer_id: str = ""
    paddle_subscription_id: str = ""
    subscription_status: str = "none"
    subscription_period_end: str = ""
    subscription_cancel_at_period_end: bool = False


class UserPromptRecord(BaseModel):
    """One prompt saved for admin analytics."""

    id: int
    user_id: str
    email: str = ""
    prompt: str = ""
    source: str = ""  # search, chat, chat_stream, ws_chat
    session_id: str = ""
    created_at: str = ""
    metadata: dict[str, Any] = {}


class UserStore:
    """Account store backed by SQLAlchemy async; never breaks app boot."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._engine: Any = None
        self._session_factory: Any = None
        self._db_ready = False
        self._db_attempted = False
        self._init_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Engine / schema bootstrap (lazy, never raises)
    # ------------------------------------------------------------------

    async def _ensure_db(self) -> bool:
        """Create the engine + tables on first use. False => dev-user-only."""
        if self._db_ready or self._db_attempted:
            return self._db_ready
        async with self._init_lock:
            if self._db_ready or self._db_attempted:
                return self._db_ready
            self._db_attempted = True
            try:
                from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

                urls = [self.settings.database_url]
                # Strict infra mode: silently falling back to a local SQLite
                # file is forbidden — the store stays unavailable and the
                # outage is reported via /ready instead.
                if not self.settings.strict_infra_enabled:
                    try:
                        data_dir = self.settings.ensure_data_dir()
                        fallback = f"sqlite+aiosqlite:///{(data_dir / 'users_fallback.db').as_posix()}"
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
                        # Best-effort column migrations, one transaction per
                        # statement (a failed ALTER aborts Postgres txns).
                        from sqlalchemy import text

                        for statement in USER_MIGRATIONS:
                            try:
                                async with engine.begin() as conn:
                                    await conn.execute(text(statement))
                            except Exception:
                                pass  # duplicate column: already migrated
                        self._engine = engine
                        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)
                        self._db_ready = True
                        return True
                    except Exception:
                        if engine is not None:
                            try:
                                await engine.dispose()
                            except Exception:
                                pass
                logger.warning("user store unavailable (no reachable database); dev-user-only mode")
                return False
            except Exception:
                logger.warning("user store initialisation failed; dev-user-only mode", exc_info=True)
                return False

    @property
    def is_available(self) -> bool:
        """True once a working database has been initialised."""
        return self._db_ready

    # ------------------------------------------------------------------
    # Accounts
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_record(row: Any) -> UserRecord:
        return UserRecord(
            id=row.id,
            email=row.email or "",
            # getattr: rows from a not-yet-migrated database lack this column
            phone=getattr(row, "phone", "") or "",
            name=row.name or "",
            role=Role(row.role or Role.USER.value),
            tier=row.tier or "gratuit",
            workspace_id=row.workspace_id or "",
            created_at=row.created_at or "",
            # getattr: rows from a not-yet-migrated database lack these columns
            paddle_customer_id=getattr(row, "paddle_customer_id", "") or "",
            paddle_subscription_id=getattr(row, "paddle_subscription_id", "") or "",
            subscription_status=getattr(row, "subscription_status", "") or "none",
            subscription_period_end=getattr(row, "subscription_period_end", "") or "",
            subscription_cancel_at_period_end=bool(getattr(row, "subscription_cancel_at_period_end", 0) or 0),
        )

    async def _get_by_email(self, email: str) -> Optional[UserRecord]:
        from sqlalchemy import select

        t = TABLES["users"]
        async with self._session_factory() as session:
            row = (await session.execute(select(t).where(t.c.email == email.lower().strip()))).first()
        return self._row_to_record(row) if row else None

    async def _get_by_phone(self, phone: str) -> Optional[UserRecord]:
        from sqlalchemy import select

        t = TABLES["users"]
        async with self._session_factory() as session:
            row = (await session.execute(select(t).where(t.c.phone == phone))).first()
        return self._row_to_record(row) if row else None

    async def create_user(self, email: str, password: str, name: str = "", phone: str = "") -> UserRecord:
        """Create an account plus its personal workspace.

        Always creates a ``Role.USER`` account; admin accounts cannot be
        registered through the public API. At least one login identifier
        (email or phone) is required. Raises ``UserAlreadyExistsError`` when
        the email or phone is taken and ``RuntimeError`` when no database is
        available.
        """
        if not await self._ensure_db():
            raise RuntimeError("user store unavailable")
        email = email.lower().strip()
        phone = normalize_phone(phone) if phone else ""
        if not email and not phone:
            raise ValueError("email or phone number required")
        if email and await self._get_by_email(email) is not None:
            raise UserAlreadyExistsError(f"an account already exists for '{email}'")
        if phone and await self._get_by_phone(phone) is not None:
            raise UserAlreadyExistsError(f"an account already exists for '{phone}'")

        user_id = uuid.uuid4().hex
        workspace_id = uuid.uuid4().hex
        now = _utcnow_iso()
        async with self._session_factory() as session:
            await session.execute(
                TABLES["workspaces"].insert(),
                {
                    "id": workspace_id,
                    "name": f"Espace {name.strip() or email or phone}",
                    "owner_id": user_id,
                    "created_at": now,
                },
            )
            await session.execute(
                TABLES["users"].insert(),
                {
                    "id": user_id,
                    # NULL (not "") for phone-only accounts: the UNIQUE
                    # constraint on email must not collide across them.
                    "email": email or None,
                    "phone": phone,
                    "name": name.strip(),
                    "password_hash": hash_password(password),
                    "role": Role.USER.value,
                    "tier": "gratuit",
                    "workspace_id": workspace_id,
                    "created_at": now,
                },
            )
            await session.commit()
        return UserRecord(
            id=user_id,
            email=email,
            phone=phone,
            name=name.strip(),
            role=Role.USER,
            tier="gratuit",
            workspace_id=workspace_id,
            created_at=now,
        )

    async def authenticate(self, identifier: str, password: str) -> Optional[UserRecord]:
        """Return the user when identifier+password match, else None.

        The identifier is the account's email or phone number (the login
        form accepts both).
        """
        if not await self._ensure_db():
            return None
        from sqlalchemy import or_, select

        email = identifier.lower().strip()
        phone = normalize_phone(identifier)
        conditions = [TABLES["users"].c.email == email]
        if phone:
            conditions.append(TABLES["users"].c.phone == phone)
        t = TABLES["users"]
        async with self._session_factory() as session:
            row = (await session.execute(select(t).where(or_(*conditions)))).first()
        if row is None or not verify_password(password, row.password_hash or ""):
            return None
        return self._row_to_record(row)

    async def get_by_id(self, user_id: str) -> Optional[UserRecord]:
        """Load a user by primary key (None when missing or DB down)."""
        if not await self._ensure_db():
            return None
        from sqlalchemy import select

        t = TABLES["users"]
        async with self._session_factory() as session:
            row = (await session.execute(select(t).where(t.c.id == user_id))).first()
        return self._row_to_record(row) if row else None

    async def find_by_identifier(self, identifier: str) -> Optional[UserRecord]:
        """Look up a user by email or phone WITHOUT a password check.

        Used by the password-reset flow; callers must not reveal whether a
        match was found (enumeration).
        """
        if not await self._ensure_db():
            return None
        from sqlalchemy import or_, select

        email = identifier.lower().strip()
        phone = normalize_phone(identifier)
        conditions = [TABLES["users"].c.email == email]
        if phone:
            conditions.append(TABLES["users"].c.phone == phone)
        t = TABLES["users"]
        async with self._session_factory() as session:
            row = (await session.execute(select(t).where(or_(*conditions)))).first()
        return self._row_to_record(row) if row else None

    async def list_users(self, limit: int = 500) -> list[UserRecord]:
        """All accounts, newest first.

        Degraded dev-user-only mode returns just the dev admin record so the
        admin dashboard still shows the one usable account.
        """
        if not await self._ensure_db():
            return [
                UserRecord(id="admin", email="admin", name="admin", role=Role.ADMIN, tier="cabinet")
            ]
        from sqlalchemy import select

        t = TABLES["users"]
        async with self._session_factory() as session:
            rows = (
                await session.execute(select(t).order_by(t.c.created_at.desc()).limit(limit))
            ).all()
        return [self._row_to_record(row) for row in rows]

    async def get_workspace_name(self, workspace_id: str) -> str:
        """Workspace display name ("" when missing or DB down)."""
        if not workspace_id or not await self._ensure_db():
            return ""
        from sqlalchemy import select

        t = TABLES["workspaces"]
        async with self._session_factory() as session:
            row = (await session.execute(select(t.c.name).where(t.c.id == workspace_id))).first()
        return (row.name or "") if row else ""

    async def count_admins(self) -> int:
        """Number of admin accounts in the DB (0 when DB down)."""
        if not await self._ensure_db():
            return 0
        from sqlalchemy import func, select

        t = TABLES["users"]
        async with self._session_factory() as session:
            row = (await session.execute(select(func.count()).where(t.c.role == Role.ADMIN.value))).first()
        return int(row[0]) if row else 0

    async def has_admin(self) -> bool:
        """True if at least one admin account exists."""
        return await self.count_admins() > 0

    async def set_tier(self, user_id: str, tier: str) -> None:
        """Update a user's subscription tier (no-op when DB down)."""
        if not await self._ensure_db():
            return None
        from sqlalchemy import update

        t = TABLES["users"]
        async with self._session_factory() as session:
            await session.execute(update(t).where(t.c.id == user_id).values(tier=tier))
            await session.commit()

    async def set_role(self, user_id: str, role: str) -> bool:
        """Update a user's role; False on unknown role, missing user or DB down."""
        try:
            role_value = Role(role).value
        except ValueError:
            return False
        if not await self._ensure_db():
            return False
        from sqlalchemy import update

        t = TABLES["users"]
        async with self._session_factory() as session:
            result = await session.execute(update(t).where(t.c.id == user_id).values(role=role_value))
            await session.commit()
        return bool(result.rowcount)

    async def set_password(self, user_id: str, new_password: str) -> bool:
        """Replace a user's password hash; False on missing user or DB down."""
        if not await self._ensure_db():
            return False
        from sqlalchemy import update

        t = TABLES["users"]
        async with self._session_factory() as session:
            result = await session.execute(
                update(t).where(t.c.id == user_id).values(password_hash=hash_password(new_password))
            )
            await session.commit()
        return bool(result.rowcount)

    async def delete_user(self, user_id: str) -> bool:
        """Delete an account and its rows in this store's own tables.

        Removes the user, their personal workspace, usage rows, the
        prompt audit rows, bookmarks and public share snapshots. Per-user
        memory/chat history is the memory store's responsibility (the auth
        router purges it best-effort via ``MemoryStore``). False when the
        user does not exist or DB down.
        """
        if not await self._ensure_db():
            return False
        from sqlalchemy import delete

        async with self._session_factory() as session:
            result = await session.execute(delete(TABLES["users"]).where(TABLES["users"].c.id == user_id))
            if not result.rowcount:
                await session.rollback()
                return False
            await session.execute(delete(TABLES["workspaces"]).where(TABLES["workspaces"].c.owner_id == user_id))
            await session.execute(delete(TABLES["usage"]).where(TABLES["usage"].c.user_id == user_id))
            await session.execute(
                delete(TABLES["user_prompts"]).where(TABLES["user_prompts"].c.user_id == user_id)
            )
            await session.execute(delete(TABLES["bookmarks"]).where(TABLES["bookmarks"].c.user_id == user_id))
            await session.execute(
                delete(TABLES["shared_answers"]).where(TABLES["shared_answers"].c.user_id == user_id)
            )
            await session.execute(
                delete(TABLES["push_tokens"]).where(TABLES["push_tokens"].c.user_id == user_id)
            )
            await session.commit()
        return True

    # ------------------------------------------------------------------
    # Bookmarks & shared answers
    # ------------------------------------------------------------------

    async def add_bookmark(
        self, user_id: str, query: str, answer: str, confidence: float, session_id: str = ""
    ) -> Optional[dict[str, Any]]:
        """Save an answer snapshot; returns the record (None when DB down)."""
        if not await self._ensure_db():
            return None
        record = {
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "query": query,
            "answer": answer,
            "confidence": confidence,
            "session_id": session_id,
            "created_at": _utcnow_iso(),
        }
        async with self._session_factory() as session:
            await session.execute(TABLES["bookmarks"].insert(), record)
            await session.commit()
        return record

    async def list_bookmarks(self, user_id: str) -> list[dict[str, Any]]:
        """Newest-first bookmarks of one user ([] when DB down)."""
        if not await self._ensure_db():
            return []
        from sqlalchemy import select

        t = TABLES["bookmarks"]
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(t).where(t.c.user_id == user_id).order_by(t.c.created_at.desc())
                )
            ).all()
        return [
            {
                "id": r.id,
                "query": r.query or "",
                "answer": r.answer or "",
                "confidence": float(r.confidence or 0),
                "session_id": r.session_id or "",
                "created_at": r.created_at or "",
            }
            for r in rows
        ]

    async def delete_bookmark(self, user_id: str, bookmark_id: str) -> bool:
        """Owner-scoped delete; False when not found/foreign or DB down."""
        if not await self._ensure_db():
            return False
        from sqlalchemy import delete

        t = TABLES["bookmarks"]
        async with self._session_factory() as session:
            result = await session.execute(
                delete(t).where(t.c.id == bookmark_id).where(t.c.user_id == user_id)
            )
            await session.commit()
        return bool(result.rowcount)

    async def create_share(
        self, user_id: str, query: str, answer: str, citations_json: str, confidence: float
    ) -> Optional[str]:
        """Create a public read-only share token for one answer."""
        if not await self._ensure_db():
            return None
        import secrets as _secrets

        token = _secrets.token_urlsafe(16)
        async with self._session_factory() as session:
            await session.execute(
                TABLES["shared_answers"].insert(),
                {
                    "token": token,
                    "user_id": user_id,
                    "query": query,
                    "answer": answer,
                    "citations_json": citations_json,
                    "confidence": confidence,
                    "created_at": _utcnow_iso(),
                },
            )
            await session.commit()
        return token

    async def get_share(self, token: str) -> Optional[dict[str, Any]]:
        """Fetch a shared answer by token (None when unknown or DB down)."""
        if not await self._ensure_db():
            return None
        from sqlalchemy import select

        t = TABLES["shared_answers"]
        async with self._session_factory() as session:
            row = (await session.execute(select(t).where(t.c.token == token))).first()
        if row is None:
            return None
        return {
            "query": row.query or "",
            "answer": row.answer or "",
            "citations_json": row.citations_json or "",
            "confidence": float(row.confidence or 0),
            "created_at": row.created_at or "",
        }

    # ------------------------------------------------------------------
    # Push tokens
    # ------------------------------------------------------------------

    async def register_push_token(self, user_id: str, token: str, device_id: str = "") -> bool:
        """Upsert an Expo push token (idempotent per token; rebinds owner)."""
        if not await self._ensure_db():
            return False
        from sqlalchemy import delete, select

        t = TABLES["push_tokens"]
        async with self._session_factory() as session:
            existing = (
                await session.execute(select(t.c.user_id).where(t.c.token == token))
            ).first()
            if existing is not None and existing.user_id != user_id:
                await session.execute(delete(t).where(t.c.token == token))
            await session.execute(delete(t).where(t.c.token == token).where(t.c.user_id == user_id))
            await session.execute(
                t.insert(),
                {"token": token, "user_id": user_id, "device_id": device_id, "created_at": _utcnow_iso()},
            )
            await session.commit()
        return True

    async def delete_push_token(self, user_id: str, token: str) -> bool:
        """Owner-scoped token removal (logout on a device)."""
        if not await self._ensure_db():
            return False
        from sqlalchemy import delete

        t = TABLES["push_tokens"]
        async with self._session_factory() as session:
            result = await session.execute(
                delete(t).where(t.c.token == token).where(t.c.user_id == user_id)
            )
            await session.commit()
        return bool(result.rowcount)

    async def delete_push_token_unscoped(self, token: str) -> None:
        """Drop a dead token (Expo reports DeviceNotRegistered)."""
        if not await self._ensure_db():
            return
        from sqlalchemy import delete

        t = TABLES["push_tokens"]
        async with self._session_factory() as session:
            await session.execute(delete(t).where(t.c.token == token))
            await session.commit()

    async def list_push_tokens(self) -> list[str]:
        """All registered Expo push tokens ([] when DB down)."""
        if not await self._ensure_db():
            return []
        from sqlalchemy import select

        t = TABLES["push_tokens"]
        async with self._session_factory() as session:
            rows = (await session.execute(select(t.c.token))).all()
        return [r.token for r in rows]

    async def set_billing_state(
        self,
        user_id: str,
        *,
        tier: Optional[str] = None,
        status: Optional[str] = None,
        customer_id: Optional[str] = None,
        subscription_id: Optional[str] = None,
        period_end: Optional[str] = None,
        cancel_at_period_end: Optional[bool] = None,
    ) -> None:
        """Persist billing/subscription state (only non-None fields)."""
        if not await self._ensure_db():
            return None
        from sqlalchemy import update

        values: dict[str, Any] = {}
        if tier is not None:
            values["tier"] = tier
        if status is not None:
            values["subscription_status"] = status
        if customer_id is not None:
            values["paddle_customer_id"] = customer_id
        if subscription_id is not None:
            values["paddle_subscription_id"] = subscription_id
        if period_end is not None:
            values["subscription_period_end"] = period_end
        if cancel_at_period_end is not None:
            values["subscription_cancel_at_period_end"] = int(cancel_at_period_end)
        if not values:
            return
        t = TABLES["users"]
        async with self._session_factory() as session:
            await session.execute(update(t).where(t.c.id == user_id).values(**values))
            await session.commit()

    async def get_by_paddle_customer_id(self, customer_id: str) -> Optional[UserRecord]:
        """Resolve an account from its Paddle customer id (webhook fallback)."""
        if not customer_id or not await self._ensure_db():
            return None
        from sqlalchemy import select

        t = TABLES["users"]
        async with self._session_factory() as session:
            row = (
                await session.execute(select(t).where(t.c.paddle_customer_id == customer_id))
            ).first()
        return self._row_to_record(row) if row else None

    # ------------------------------------------------------------------
    # Token metering (per-day usage rows)
    # ------------------------------------------------------------------

    async def record_usage(self, user_id: str, tokens_in: int, tokens_out: int) -> None:
        """Upsert today's usage row: add tokens, increment requests by 1.

        No-op when the DB is down or no user is identified — metering must
        never break the answer path. Zero-token calls still count the request
        (token-less metering, e.g. audio transcription).
        """
        if not user_id:
            return
        if not await self._ensure_db():
            return
        from datetime import date

        from sqlalchemy import select, update

        today = date.today()
        t = TABLES["usage"]
        try:
            async with self._session_factory() as session:
                row = (
                    await session.execute(
                        select(t).where(t.c.user_id == user_id).where(t.c.day == today)
                    )
                ).first()
                if row is None:
                    await session.execute(
                        t.insert(),
                        {
                            "user_id": user_id,
                            "day": today,
                            "tokens_in": max(0, tokens_in),
                            "tokens_out": max(0, tokens_out),
                            "requests": 1,
                        },
                    )
                else:
                    await session.execute(
                        update(t)
                        .where(t.c.id == row.id)
                        .values(
                            tokens_in=row.tokens_in + max(0, tokens_in),
                            tokens_out=row.tokens_out + max(0, tokens_out),
                            requests=row.requests + 1,
                        )
                    )
                await session.commit()
        except Exception:
            logger.warning("record_usage failed", exc_info=True)

    async def get_usage(self, user_id: str, days: int = 30) -> list[dict[str, Any]]:
        """Per-day usage rows, most recent first ([] when DB down)."""
        if not user_id or not await self._ensure_db():
            return []
        from sqlalchemy import select

        t = TABLES["usage"]
        try:
            async with self._session_factory() as session:
                rows = (
                    await session.execute(
                        select(t).where(t.c.user_id == user_id).order_by(t.c.day.desc()).limit(days)
                    )
                ).all()
        except Exception:
            logger.warning("get_usage failed", exc_info=True)
            return []
        return [
            {
                "day": r.day.isoformat() if r.day else "",
                "tokens_in": r.tokens_in or 0,
                "tokens_out": r.tokens_out or 0,
                "requests": r.requests or 0,
            }
            for r in rows
        ]

    async def list_usage(self, days: int = 30) -> list[dict[str, Any]]:
        """Usage rows joined with user emails over the last `days` days.

        Most recent first; ``[]`` in degraded dev-user-only mode.
        """
        if not await self._ensure_db():
            return []
        from datetime import date, timedelta

        from sqlalchemy import select

        usage_t = TABLES["usage"]
        users_t = TABLES["users"]
        since = date.today() - timedelta(days=max(0, days - 1))
        stmt = (
            select(
                usage_t.c.user_id,
                users_t.c.email,
                usage_t.c.day,
                usage_t.c.tokens_in,
                usage_t.c.tokens_out,
                usage_t.c.requests,
            )
            .select_from(usage_t.join(users_t, usage_t.c.user_id == users_t.c.id))
            .where(usage_t.c.day >= since)
            .order_by(usage_t.c.day.desc())
        )
        try:
            async with self._session_factory() as session:
                rows = (await session.execute(stmt)).all()
        except Exception:
            logger.warning("list_usage failed", exc_info=True)
            return []
        return [
            {
                "user_id": r.user_id,
                "email": r.email or "",
                "day": r.day.isoformat() if r.day else "",
                "tokens_in": r.tokens_in or 0,
                "tokens_out": r.tokens_out or 0,
                "requests": r.requests or 0,
            }
            for r in rows
        ]

    async def get_today_usage(self, user_id: str) -> dict[str, int]:
        """Today's totals (zeros when DB down — never blocks the caller)."""
        rows = await self.get_usage(user_id, days=1)
        if rows:
            return rows[0]  # type: ignore[return-value]
        return {"tokens_in": 0, "tokens_out": 0, "requests": 0}

    # ------------------------------------------------------------------
    # Prompt audit trail (admin "Recherches" tab)
    # ------------------------------------------------------------------

    async def record_prompt(
        self,
        user_id: str,
        prompt: str,
        *,
        source: str,
        session_id: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Persist a user prompt best-effort; never raises."""
        if not user_id or not prompt:
            return
        if not await self._ensure_db():
            return
        email = ""
        try:
            record = await self.get_by_id(user_id)
            if record is not None:
                email = record.email or record.name or user_id
        except Exception:
            pass
        if not email:
            email = user_id
        t = TABLES["user_prompts"]
        try:
            async with self._session_factory() as session:
                await session.execute(
                    t.insert(),
                    {
                        "user_id": user_id,
                        "email": email,
                        "prompt": prompt,
                        "source": source,
                        "session_id": session_id or "",
                        "created_at": _utcnow_iso(),
                        "metadata": json.dumps(metadata or {}, ensure_ascii=False, default=str),
                    },
                )
                await session.commit()
        except Exception:
            logger.warning("record_prompt failed", exc_info=True)

    async def list_prompts(
        self,
        *,
        q: str = "",
        source: Optional[str] = None,
        user_id: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        """Paginated prompt audit trail for the admin dashboard.

        Filters are AND-combined. Text search is substring on prompt OR email.
        Dates are ISO date strings compared lexicographically against created_at.
        """
        if not await self._ensure_db():
            return {"prompts": [], "total": 0, "page": page, "page_size": page_size}
        from sqlalchemy import func, select

        t = TABLES["user_prompts"]
        stmt = select(t)
        count_stmt = select(func.count()).select_from(t)
        conditions = []

        if q:
            like_q = f"%{q}%"
            # Driver-agnostic ILIKE fallback: SQLite is case-insensitive for LIKE
            # by default for ASCII, and Postgres supports ILIKE. We use a simple
            # lower() wrapper to stay cross-dialect.
            text_condition = (
                func.lower(t.c.prompt).like(func.lower(like_q))
                | func.lower(t.c.email).like(func.lower(like_q))
            )
            conditions.append(text_condition)
        if source:
            conditions.append(t.c.source == source)
        if user_id:
            conditions.append(t.c.user_id == user_id)
        if from_date:
            conditions.append(t.c.created_at >= from_date)
        if to_date:
            # Make the upper bound inclusive of the whole day.
            conditions.append(t.c.created_at < f"{to_date}T23:59:59.999999+00:00")

        if conditions:
            where_clause = conditions[0]
            for c in conditions[1:]:
                where_clause = where_clause & c
            stmt = stmt.where(where_clause)
            count_stmt = count_stmt.where(where_clause)

        stmt = stmt.order_by(t.c.created_at.desc())
        offset = max(0, (page - 1)) * page_size
        stmt = stmt.limit(page_size).offset(offset)

        try:
            async with self._session_factory() as session:
                rows = (await session.execute(stmt)).all()
                total = (await session.execute(count_stmt)).scalar() or 0
        except Exception:
            logger.warning("list_prompts failed", exc_info=True)
            return {"prompts": [], "total": 0, "page": page, "page_size": page_size}

        def _parse_meta(raw: Any) -> dict[str, Any]:
            try:
                return json.loads(raw) if raw else {}
            except Exception:
                return {}

        return {
            "prompts": [
                UserPromptRecord(
                    id=r.id,
                    user_id=r.user_id or "",
                    email=r.email or "",
                    prompt=r.prompt or "",
                    source=r.source or "",
                    session_id=r.session_id or "",
                    created_at=r.created_at or "",
                    metadata=_parse_meta(r.metadata),
                )
                for r in rows
            ],
            "total": int(total),
            "page": page,
            "page_size": page_size,
        }

    # ------------------------------------------------------------------
    # App settings (key-value, e.g. admin tier budget overrides)
    # ------------------------------------------------------------------

    async def get_setting(self, key: str) -> Optional[str]:
        """Read an app setting (None when missing or DB down)."""
        if not key or not await self._ensure_db():
            return None
        from sqlalchemy import select

        t = TABLES["app_settings"]
        async with self._session_factory() as session:
            row = (await session.execute(select(t.c.value).where(t.c.key == key))).first()
        return row.value if row else None

    async def set_setting(self, key: str, value: str) -> None:
        """Upsert an app setting (no-op when DB down)."""
        if not key or not await self._ensure_db():
            return None
        from sqlalchemy import select, update

        t = TABLES["app_settings"]
        async with self._session_factory() as session:
            row = (await session.execute(select(t).where(t.c.key == key))).first()
            if row is None:
                await session.execute(t.insert(), {"key": key, "value": value})
            else:
                await session.execute(update(t).where(t.c.key == key).values(value=value))
            await session.commit()
