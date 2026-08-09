"""Temporal awareness for retrieval and ranking (spec §10, §24).

Pure functions over :class:`EvidenceChunk` metadata used in two places:

- the retrieval coordinator hard-filters fused candidates
  (:func:`passes_temporal_filter`) so current and historical versions are
  never silently mixed;
- the evidence ranking agent blends :func:`temporal_score` into the final
  score so time-inapplicable evidence sinks instead of disappearing.

Backward compatibility: chunks ingested before the temporal fields existed
carry the defaults ``status="active"`` and no validity dates — they score 1.0
for "current" intent and always pass the filter.  Only explicitly
repealed/expired/not-yet-in-force documents are ever excluded.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from backend.core.config import Settings, get_settings
from backend.core.models import EvidenceChunk

#: Intents that trigger temporal discrimination; anything else is a no-op.
TEMPORAL_INTENTS = ("current", "historical")


def _validity_window(chunk: EvidenceChunk) -> tuple[Optional[date], Optional[date]]:
    """(start, end) of the document's in-force window, dates optional."""
    start = chunk.valid_from or chunk.effective_date or chunk.publication_date
    return start, chunk.valid_until


def _status(chunk: EvidenceChunk) -> str:
    return (chunk.status or "unknown").strip().lower()


def temporal_score(
    chunk: EvidenceChunk,
    temporal_intent: str = "any",
    scenario_date: Optional[date] = None,
    today: Optional[date] = None,
    settings: Optional[Settings] = None,
) -> float:
    """0-1 score of how well a chunk fits the plan's temporal intent.

    - "any" (or unrecognized): 1.0 — no temporal discrimination.
    - "current": 1.0 when the document is active and its validity window
      contains today; ``settings.temporal_score_unknown`` when status and
      dates are unknown; 0.0 when repealed, expired or not yet in force.
    - "historical" with a scenario date: 1.0 when in force at that date,
      ``settings.temporal_score_repealed_before_date`` when already
      repealed/expired by then, 0.0 when not yet in force,
      ``settings.temporal_score_unconfirmed`` when nothing is known.
      Without a scenario date there is nothing to discriminate on: 1.0.
    """
    intent = (temporal_intent or "any").strip().lower()
    if intent not in TEMPORAL_INTENTS:
        return 1.0
    if settings is None:
        settings = get_settings()
    today = today or date.today()
    start, end = _validity_window(chunk)
    status = _status(chunk)

    if intent == "current":
        if status in ("repealed", "future"):
            return 0.0
        if end is not None and end < today:
            return 0.0  # expired
        if start is not None and start > today:
            return 0.0  # not yet in force
        if status == "unknown" and start is None and end is None:
            return settings.temporal_score_unknown
        return 1.0

    # historical
    if scenario_date is None:
        return 1.0
    if start is not None and start > scenario_date:
        return 0.0  # not yet in force at the scenario date
    if end is not None and end < scenario_date:
        return settings.temporal_score_repealed_before_date  # already repealed/expired by then
    if status == "unknown" and start is None and end is None:
        return settings.temporal_score_unconfirmed  # cannot confirm; stay neutral
    return 1.0


def passes_temporal_filter(
    chunk: EvidenceChunk,
    temporal_intent: str = "any",
    scenario_date: Optional[date] = None,
    today: Optional[date] = None,
) -> bool:
    """Hard filter: drop only clearly-inapplicable documents.

    - "current": repealed, expired or not-yet-in-force documents.
    - "historical" with a scenario date: documents not yet in force at that
      date.  A document repealed *before* the scenario date is kept — it may
      be exactly what a historical question asks about.
    Undated/unknown-status documents always pass.
    """
    intent = (temporal_intent or "any").strip().lower()
    if intent not in TEMPORAL_INTENTS:
        return True
    today = today or date.today()
    start, end = _validity_window(chunk)
    status = _status(chunk)

    if intent == "current":
        if status in ("repealed", "future"):
            return False
        if end is not None and end < today:
            return False
        if start is not None and start > today:
            return False
        return True

    # historical: only the clearly-not-yet-in-force case is excluded.
    if scenario_date is None:
        return True
    return not (start is not None and start > scenario_date)
