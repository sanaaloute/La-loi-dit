"""Temporal workflows for durable conversations and document ingestion.

`temporalio` is an optional dependency: this module must import cleanly
without it. When it is missing, the workflow classes become no-op fallbacks
whose `run` methods raise a clear error, so the rest of the application
(API, tests, evaluation) never breaks.

Conversation durability model: each user turn is executed as an activity
(`run_chat_turn_activity`) and the serialized ChatResponse is appended to an
in-workflow checkpoint list. Temporal persists every completed activity in
the event history and replays it after a worker restart, so the
checkpoints — and therefore the conversation — resume exactly where they
were interrupted. The long-term conversation itself is additionally
persisted by the memory subsystem inside the activity.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any, Optional

try:  # optional dependency
    from temporalio import workflow
    from temporalio.common import RetryPolicy

    _TEMPORAL_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only when temporalio absent
    workflow = None  # type: ignore[assignment]
    RetryPolicy = None  # type: ignore[assignment]
    _TEMPORAL_AVAILABLE = False

# Bound the event history of a single workflow execution; the durable
# conversation itself lives in the memory store, so ending the execution
# loses nothing. Configurable via LEGAL_AI_TEMPORAL_MAX_TURNS_PER_EXECUTION.
MAX_TURNS_PER_EXECUTION = int(os.environ.get("LEGAL_AI_TEMPORAL_MAX_TURNS_PER_EXECUTION", "50"))

if _TEMPORAL_AVAILABLE:
    with workflow.unsafe.imports_passed_through():
        from backend.temporal.activities import (
            ingest_path_activity,
            run_chat_turn_activity,
        )
else:  # fallback names so the workflow bodies still resolve
    run_chat_turn_activity = None  # type: ignore[assignment]
    ingest_path_activity = None  # type: ignore[assignment]

# No-op decorators when temporalio is missing, so the class bodies below
# stay syntactically valid and importable.
_workflow_defn = workflow.defn if _TEMPORAL_AVAILABLE else (lambda cls: cls)
_workflow_run = workflow.run if _TEMPORAL_AVAILABLE else (lambda fn: fn)
_workflow_signal = workflow.signal if _TEMPORAL_AVAILABLE else (lambda fn: fn)
_workflow_query = workflow.query if _TEMPORAL_AVAILABLE else (lambda fn: fn)


def _require_temporalio() -> None:
    if not _TEMPORAL_AVAILABLE:
        raise RuntimeError(
            "temporalio is not installed; Temporal workflows cannot execute. "
            "Install the 'temporalio' package and start a Temporal server."
        )


@_workflow_defn
class ConversationWorkflow:
    """Durable multi-turn chat workflow with replay-safe checkpoints.

    Signals:
        send_message(query, language=None, scenario_date=None) — queue a turn.
        close() — drain queued turns and terminate the workflow.

    Queries:
        checkpoints() — serialized ChatResponse per completed turn, in order.
    """

    def __init__(self) -> None:
        self._checkpoints: list[dict[str, Any]] = []
        self._pending_turns: list[dict[str, Any]] = []
        self._closed = False

    @_workflow_run
    async def run(
        self,
        session_id: str,
        user_id: str = "anonymous",
        first_query: Optional[str] = None,
        language: Optional[str] = None,
        scenario_date: Optional[str] = None,
    ) -> dict[str, Any]:
        _require_temporalio()
        if first_query:
            self._pending_turns.append(
                {"query": first_query, "language": language, "scenario_date": scenario_date}
            )
        while True:
            await workflow.wait_condition(lambda: bool(self._pending_turns) or self._closed)
            if self._closed and not self._pending_turns:
                break
            turn = self._pending_turns.pop(0)
            response = await workflow.execute_activity(
                run_chat_turn_activity,
                {"session_id": session_id, "user_id": user_id, **turn},
                start_to_close_timeout=timedelta(
                    seconds=float(os.environ.get("LEGAL_AI_TEMPORAL_ACTIVITY_TIMEOUT_SECONDS", "180"))
                ),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            # Checkpoint: replayed from event history after any restart.
            self._checkpoints.append(response)
            if len(self._checkpoints) >= MAX_TURNS_PER_EXECUTION:
                self._closed = True
        return {"session_id": session_id, "turns": len(self._checkpoints)}

    @_workflow_signal
    def send_message(
        self,
        query: str,
        language: Optional[str] = None,
        scenario_date: Optional[str] = None,
    ) -> None:
        """Queue a new user turn for this conversation."""
        self._pending_turns.append(
            {"query": query, "language": language, "scenario_date": scenario_date}
        )

    @_workflow_signal
    def close(self) -> None:
        """Terminate the workflow once queued turns are drained."""
        self._closed = True

    @_workflow_query
    def checkpoints(self) -> list[dict[str, Any]]:
        """Return the serialized ChatResponse checkpoints, in turn order."""
        return list(self._checkpoints)


@_workflow_defn
class IngestionWorkflow:
    """Durable document ingestion job: ingest a path (file or directory)."""

    def __init__(self) -> None:
        self._results: list[dict[str, Any]] = []

    @_workflow_run
    async def run(self, path: str) -> list[dict[str, Any]]:
        _require_temporalio()
        minutes = int(os.environ.get("LEGAL_AI_TEMPORAL_INGESTION_TIMEOUT_MINUTES", "30"))
        self._results = await workflow.execute_activity(
            ingest_path_activity,
            path,
            start_to_close_timeout=timedelta(minutes=minutes),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        return self._results

    @_workflow_query
    def results(self) -> list[dict[str, Any]]:
        """Return the serialized DocumentIngestResult entries."""
        return list(self._results)
