"""Memory hygiene: prune stale, low-value records so the long-term store
stays bounded and recall stays fast.

A record's keep-score combines importance, age and recency of access:
old records that were never accessed again and carry low importance are
dropped first. High-importance or recently used memories are kept.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from backend.core.config import get_settings
from backend.core.models import MemoryRecord


def _settings():
    return get_settings()


def _age_days(dt: datetime, now: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 86400.0)


def _keep_score(record: MemoryRecord, now: datetime) -> float:
    """Higher is better. Range roughly [0, 1]."""
    cfg = _settings()
    age_full = cfg.memory_age_full_penalty_days
    access_full = cfg.memory_access_full_penalty_days
    age_penalty = min(_age_days(record.created_at, now) / age_full, 1.0)
    access_penalty = min(_age_days(record.last_accessed, now) / access_full, 1.0)
    return 0.6 * record.importance + 0.25 * (1.0 - age_penalty) + 0.15 * (1.0 - access_penalty)


def _is_stale(record: MemoryRecord, now: datetime, min_importance: float) -> bool:
    """A record is stale when it is old, unimportant and not accessed recently."""
    cfg = _settings()
    return (
        record.importance < min_importance
        and _age_days(record.created_at, now) > cfg.memory_age_full_penalty_days
        and _age_days(record.last_accessed, now) > cfg.memory_access_full_penalty_days
    )


async def prune_memories(
    store,
    max_records: Optional[int] = None,
    min_importance: Optional[float] = None,
    now: Optional[datetime] = None,
) -> int:
    """Drop stale low-importance records and enforce the ``max_records`` cap.

    Returns the number of records deleted. Never raises.
    """
    try:
        records = await store.list_memories()
    except Exception:
        return 0
    if not records:
        return 0

    cfg = _settings()
    max_records = max_records if max_records is not None else cfg.memory_max_records
    min_importance = min_importance if min_importance is not None else cfg.memory_min_importance
    now = now or datetime.now(timezone.utc)
    doomed = [r.id for r in records if _is_stale(r, now, min_importance)]

    remaining = [r for r in records if r.id not in set(doomed)]
    if len(remaining) > max_records:
        remaining.sort(key=lambda r: _keep_score(r, now))
        overflow = len(remaining) - max_records
        doomed.extend(r.id for r in remaining[:overflow])

    if not doomed:
        return 0
    try:
        return await store.delete_memories(doomed)
    except Exception:
        return 0
