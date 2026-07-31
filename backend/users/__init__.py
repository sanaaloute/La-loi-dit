"""User accounts, workspaces and (future) per-day usage metering.

Persistence mirrors ``backend.memory.store``: SQLAlchemy 2.0 async with a
lazily created engine, ``create_all`` on first use, Postgres -> local SQLite
fallback, and a degraded "dev-user-only" mode (no DB) that never breaks boot.
"""

from backend.users.service import UserRecord, UserStore

__all__ = ["UserRecord", "UserStore"]
