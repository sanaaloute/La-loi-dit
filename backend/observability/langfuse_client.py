"""Langfuse client and tracing helpers.

`get_langfuse(settings)` returns a configured client, or None when
credentials are missing or the package is unavailable. `trace_run` is a
legacy context manager yielding a trace object and degrading to a no-op without
a client.

New code should use `register_litellm_callbacks()` once at startup and
`traced_chat_run()` around each chat request. All langfuse imports are lazy so
missing credentials or packages never break the application.
"""

from __future__ import annotations

import contextvars
import logging
import uuid
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Iterator, Optional

import litellm

from backend.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

_cached_client: Any = None
_client_resolved = False

# Context variables used to link LiteLLM generations to the current Langfuse
# trace without threading explicit trace ids through every call site.
_trace_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "langfuse_trace_id", default=None
)
_generation_name_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "langfuse_generation_name", default=None
)


def get_langfuse(settings: Optional[Settings] = None) -> Optional[Any]:
    """Return a Langfuse client, or None when disabled/unavailable."""
    settings = settings or get_settings()
    if not settings.langfuse_enabled:
        return None
    try:
        from langfuse import Langfuse
    except Exception:  # pragma: no cover - dependency optional
        logger.warning("langfuse keys configured but langfuse package unavailable")
        return None
    try:
        return Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    except Exception as exc:  # pragma: no cover - network/service issues
        logger.warning("langfuse client init failed: %s", exc)
        return None


def _default_client() -> Optional[Any]:
    """Process-wide cached client built from the global settings."""
    global _cached_client, _client_resolved
    if not _client_resolved:
        _cached_client = get_langfuse(get_settings())
        _client_resolved = True
    return _cached_client


def register_litellm_callbacks(settings: Optional[Settings] = None) -> None:
    """Register Langfuse as a LiteLLM success/failure callback.

    This captures every LLM call automatically (model name, token usage,
    input/output) without manual instrumentation. Safe to call multiple times.

    LiteLLM's native Langfuse callback reads from the standard ``LANGFUSE_*``
    environment variables, whereas this app prefixes its settings with
    ``LEGAL_AI_``. When Langfuse is enabled we expose the configured values via
    ``os.environ.setdefault`` so the LiteLLM callback uses the same credentials
    as the rest of the app without overriding any explicitly set ``LANGFUSE_*``
    variables.
    """
    import os

    settings = settings or get_settings()
    if not settings.langfuse_enabled:
        return

    # Make the prefixed settings discoverable by LiteLLM's callback integration.
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
    os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
    os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_host)

    try:
        if "langfuse" not in litellm.success_callback:
            litellm.success_callback.append("langfuse")
        if "langfuse" not in litellm.failure_callback:
            litellm.failure_callback.append("langfuse")
    except Exception as exc:  # pragma: no cover - tracing must never break the app
        logger.debug("failed to register litellm langfuse callbacks: %s", exc)


def get_current_trace_id() -> Optional[str]:
    """Return the trace id for the currently active chat run, if any."""
    return _trace_id_var.get()


def set_current_observation_context(*, trace_id: Optional[str], generation_name: Optional[str] = None) -> None:
    """Set the trace/generation context for the current async task."""
    _trace_id_var.set(trace_id)
    _generation_name_var.set(generation_name)


def clear_current_observation_context() -> None:
    """Clear trace/generation context for the current async task."""
    _trace_id_var.set(None)
    _generation_name_var.set(None)


def litellm_trace_metadata(generation_name: Optional[str] = None) -> dict[str, Any]:
    """Build LiteLLM metadata that links a generation to the active trace.

    Both `trace_id` and `existing_trace_id` are included for compatibility with
    the Langfuse LiteLLM callback across versions.
    """
    trace_id = _trace_id_var.get()
    name = generation_name or _generation_name_var.get()
    meta: dict[str, Any] = {}
    if name:
        meta["generation_name"] = name
    if trace_id:
        meta["trace_id"] = trace_id
        meta["existing_trace_id"] = trace_id
    return meta


@contextmanager
def trace_run(
    name: str,
    metadata: Optional[dict[str, Any]] = None,
    client: Optional[Any] = None,
) -> Iterator[Optional[Any]]:
    """Yield a Langfuse trace for `name`; no-op (yields None) without a client.

    Uses the process-wide client from `get_langfuse(get_settings())` unless
    an explicit `client` is passed. Tracing must never break the app.
    """
    client = client if client is not None else _default_client()
    if client is None:
        yield None
        return
    trace = None
    try:
        trace = client.trace(name=name, metadata=metadata or {})
        yield trace
    except Exception as exc:  # pragma: no cover - tracing must never break the app
        logger.debug("langfuse trace failed: %s", exc)
        yield None
    finally:
        try:
            if trace is not None:
                client.flush()
        except Exception:
            pass


@asynccontextmanager
async def traced_chat_run(
    query: str,
    *,
    user_id: str,
    session_id: Optional[str],
    feature: str = "chat",
    metadata: Optional[dict[str, Any]] = None,
    settings: Optional[Settings] = None,
) -> AsyncIterator[tuple[str, Any, Optional[Any]]]:
    """Create a Langfuse trace for a chat request and yield trace metadata.

    Yields a 3-tuple of ``(trace_id, trace, langgraph_handler)``. If Langfuse is
    disabled or unavailable the tuple is ``("", None, None)`` and the graph
    runs normally without tracing.

    The returned ``trace`` is a Langfuse ``StatefulTraceClient``; use
    ``trace.update(output=...)`` to attach the final answer. The
    ``langgraph_handler`` should be passed to ``graph.ainvoke``/``astream`` via
    ``config={"callbacks": [langgraph_handler]}`` so that every graph node is
    nested under this trace.

    The trace is configured with best-practice attributes: descriptive name,
    user/session ids, feature tag, and explicit input (the user query only).
    """
    settings = settings or get_settings()
    client = get_langfuse(settings)
    if client is None:
        yield "", None, None
        return

    trace_id = uuid.uuid4().hex
    trace = None
    handler = None
    try:
        trace = client.trace(
            id=trace_id,
            name=f"legal-{feature}",
            input={"query": query},
            user_id=user_id,
            session_id=session_id,
            tags=[feature],
            metadata={
                "app": settings.app_name,
                "version": settings.app_version,
                "env": settings.env,
                **(metadata or {}),
            },
        )
        handler = trace.getNewHandler()
        _trace_id_var.set(trace_id)
    except Exception as exc:  # pragma: no cover - tracing must never break the app
        logger.debug("langfuse trace setup failed: %s", exc)
        trace_id = ""
    try:
        yield trace_id, trace, handler
    finally:
        _trace_id_var.set(None)
        try:
            client.flush()
        except Exception:
            pass


def update_trace_output(
    trace: Any,
    output: Any,
    *,
    trace_id: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> None:
    """Attach ``output`` to an existing Langfuse trace.

    Accepts either the trace object returned by ``traced_chat_run`` or a raw
    trace id. Tracing failures are swallowed so the response path is never
    broken by observability.
    """
    if trace is not None:
        try:
            trace.update(output=output)
            return
        except Exception as exc:  # pragma: no cover
            logger.debug("failed to update trace via trace object: %s", exc)

    if trace_id:
        client = get_langfuse(settings)
        if client is not None:
            try:
                client.trace(id=trace_id, output=output)
            except Exception as exc:  # pragma: no cover
                logger.debug("failed to update trace output: %s", exc)


def create_feedback_score(
    trace_id: str,
    name: str,
    value: Any,
    *,
    comment: Optional[str] = None,
    data_type: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> None:
    """Create a score on ``trace_id``.

    Used for explicit user feedback (thumbs, stars) and implicit signals.
    ``data_type`` should be ``"BOOLEAN"`` for thumbs up/down and ``"NUMERIC"``
    for star ratings. See the user-feedback skill reference for score naming
    conventions.
    """
    if not trace_id:
        return
    client = get_langfuse(settings)
    if client is None:
        return
    try:
        kwargs: dict[str, Any] = {
            "trace_id": trace_id,
            "name": name,
            "value": value,
        }
        if comment is not None:
            kwargs["comment"] = comment
        if data_type is not None:
            kwargs["data_type"] = data_type
        client.score(**kwargs)
    except Exception as exc:  # pragma: no cover - feedback must never break the app
        logger.debug("failed to create langfuse score: %s", exc)
