"""Chat router: REST, SSE streaming and WebSocket endpoints.

Streaming strategy: true token-level streaming is not exposed by the graph,
so we stream real per-node graph updates (`stream_query`, stream_mode
"updates") as they happen and reconstruct the final `ChatResponse` from the
accumulated node updates — the graph runs exactly once. The verified answer
text then streams as `delta` frames (typewriter playback of the final,
post-verification answer), and a closing `final` event carries the full
authoritative ChatResponse. Memory persistence happens via the REST
endpoint's `run_query` and, for SSE, after the stream finishes successfully
(`_stream_events` appends the same user/assistant turns); the WebSocket path
stays unpersisted, matching `stream_query`'s original contract.)

All chat entry points create a Langfuse trace per request with descriptive
names, user/session ids, feature tags, and explicit input/output. The trace
id is returned to the client so explicit feedback can be scored on the
correct trace.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, AsyncIterator, Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from langchain_core.callbacks import AsyncCallbackHandler
from pydantic import BaseModel, ValidationError

from backend.api.deps import get_ctx, get_graph, require_user
from backend.core import stt
from backend.core.answer_cache import AnswerCache, is_cacheable
from backend.core.config import Settings
from backend.core.exceptions import STTError
from backend.core.model_router import check_budget, resolve_llm, resolve_model_entry
from backend.core.llm import LLMClient
from backend.core.models import ChatMessage, ChatRequest, ChatResponse, FinalAnswer, Role, parse_answer_json
from backend.core.state import GraphState
from backend.observability import metrics
from backend.observability.langfuse_client import (
    create_feedback_score,
    traced_chat_run,
    update_trace_output,
)
from backend.security.jwt import TokenPayload
from backend.security.rbac import has_role, require_role

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

_LIST_KEYS = {"trace", "errors"}  # state keys that accumulate across node updates


def _strip_trace_frame(event: dict[str, Any], include_trace: bool) -> dict[str, Any]:
    """Drop internal trace entries from a stream event for non-admin callers.

    Spec §48: the internal chain-of-thought is exposed to administrators
    only; other roles get trace-free stream frames and an empty final trace.
    """
    if include_trace:
        return event
    update = event.get("update")
    if isinstance(update, dict) and "trace" in update:
        event = {**event, "update": {k: v for k, v in update.items() if k != "trace"}}
    return event


class FeedbackPayload(BaseModel):
    trace_id: str
    score: Literal["thumbs-up", "thumbs-down"] | int = "thumbs-up"
    comment: Optional[str] = None


class CancelPayload(BaseModel):
    session_id: str


# In-flight graph executions keyed by session_id, so /chat/cancel can stop
# them. Entries are removed as soon as the run ends (success, error, cancel).
_RUNNING: dict[str, "asyncio.Task[Any]"] = {}

#: Persisted as the assistant turn when a run cannot complete after the client
#: disconnected (timeout/error), so history-polling recovery resolves with an
#: honest message instead of waiting for an answer that will never exist.
RUN_FAILURE_NOTE = (
    "Le traitement de votre question a dépassé le délai maximal ou a échoué "
    "côté serveur. Veuillez relancer la question — si le problème persiste, "
    "simplifiez-la ou réessayez plus tard."
)


def _register_run(session_id: str, task: "asyncio.Task[Any]") -> None:
    if session_id:
        _RUNNING[session_id] = task


def _unregister_run(session_id: str, task: "asyncio.Task[Any]") -> None:
    if _RUNNING.get(session_id) is task:
        _RUNNING.pop(session_id, None)


async def _drain_run(session_id: str, task: "asyncio.Task[Any]") -> None:
    """Await a POST /chat run detached after a client disconnect.

    Keeps the run registered (cancellable via /chat/cancel, visible to the
    run-status endpoint) until it actually ends; run_query persists the turn
    itself on success, so the client can recover the answer from the history.
    """
    try:
        await task
    except asyncio.CancelledError:
        pass  # cancelled via /chat/cancel while draining
    except Exception:
        logger.warning("chat run failed after client disconnect", exc_info=True)
    finally:
        _unregister_run(session_id, task)


@router.post("/chat/cancel")
async def chat_cancel(
    payload: CancelPayload,
    user: TokenPayload = Depends(require_role(Role.VIEWER)),
) -> dict[str, bool]:
    """Stop an in-flight chat run for the given session (UI stop button).

    Cancelling the task raises CancelledError inside the LangGraph execution,
    so the workflow really stops in the backend — no orphan LLM calls.
    """
    task = _RUNNING.get(payload.session_id)
    if task is None or task.done():
        return {"cancelled": False}
    task.cancel()
    return {"cancelled": True}


def _state_user_id(payload: ChatRequest, user: TokenPayload) -> str:
    """Identity a turn is recorded under.

    Authenticated callers: the JWT identity — DB `user_id` claim, falling
    back to the username — so history is keyed by a stable id the client
    cannot spoof (`payload.user_id` is ignored). Anonymous chats keep the
    ephemeral client-supplied id; they are simply not listable.
    """
    if user.sub != "anonymous":
        return user.user_id or user.sub
    return payload.user_id or "anonymous"


def _make_state(payload: ChatRequest, user_sub: str) -> GraphState:
    from backend.workflows.graph import initial_state

    return initial_state(
        payload.query,
        session_id=payload.session_id,
        user_id=payload.user_id or user_sub,
        language=payload.language,
        scenario_date=payload.scenario_date.isoformat() if payload.scenario_date else None,
    )


def _merge_update(merged: dict[str, Any], update: dict[str, Any]) -> None:
    """Merge one serialized node update into the accumulated final state."""
    for key, value in update.items():
        if key in _LIST_KEYS and isinstance(value, list):
            merged.setdefault(key, [])
            merged[key] = merged[key] + value
        else:
            merged[key] = value


def _final_response(
    merged: dict[str, Any],
    state: GraphState,
    started: float,
    *,
    trace_id: str = "",
) -> ChatResponse:
    """Reconstruct the ChatResponse from accumulated stream updates."""
    raw_answer = merged.get("final_answer")
    answer = FinalAnswer(**raw_answer) if isinstance(raw_answer, dict) else FinalAnswer(answer="")
    return ChatResponse(
        session_id=state.get("session_id", ""),
        answer=answer,
        trace=merged.get("trace", []),
        latency_ms=round((time.perf_counter() - started) * 1000, 1),
        trace_id=trace_id,
    )


def _graph_config(handler: Any | None) -> dict[str, Any] | None:
    """Build the LangGraph callback config when a Langfuse handler is available."""
    return {"callbacks": [handler]} if handler is not None else None


def _trace_output_from_response(response: ChatResponse) -> dict[str, Any]:
    """Sanitised trace output: answer text plus metadata, never full PII."""
    return {
        "session_id": response.session_id,
        "answer": response.answer.answer[:2000],
        "confidence": response.answer.confidence,
        "refused": response.answer.refused,
        "requires_human_review": response.answer.requires_human_review,
    }


def _cached_response(cached: dict[str, Any], state: GraphState) -> ChatResponse:
    """Rebuild a ChatResponse from a cache entry with fresh request metadata."""
    response = ChatResponse(**cached)
    response.session_id = state.get("session_id", "")
    response.latency_ms = 0.0
    response.trace_id = ""
    response.answer.metadata["cache_hit"] = True
    return response


async def _meter(
    ctx: Any,
    user: TokenPayload,
    llm: Any,
    before: dict[str, int],
    *,
    query: str = "",
    answer: str = "",
) -> None:
    """Record this request's token usage for authenticated DB users.

    Primary source: the delta of the per-request client's cumulative
    usage_totals (it can be shared across requests in mock offline mode).
    Fallback: the offline mock pipeline short-circuits the LLM entirely, so
    when no call was metered we estimate from the query and the answer with
    the same chars/4 heuristic — offline usage stays realistically metered.
    """
    if not getattr(user, "user_id", None) or ctx.user_store is None or llm is None:
        return
    tokens_in = llm.usage_totals["tokens_in"] - before.get("tokens_in", 0)
    tokens_out = llm.usage_totals["tokens_out"] - before.get("tokens_out", 0)
    if tokens_in <= 0 and tokens_out <= 0:
        tokens_in = LLMClient._estimate_tokens(query)
        tokens_out = LLMClient._estimate_tokens(answer)
    try:
        await ctx.user_store.record_usage(user.user_id, tokens_in, tokens_out)
    except Exception:
        pass  # metering must never break the answer path


def _enforce_word_limit(query: str, settings: Settings) -> None:
    """Reject over-long questions (word count), matching the UI input limit."""
    max_words = getattr(settings, "input_max_words", 0)
    if max_words and len(query.split()) > max_words:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Votre question dépasse la limite de {max_words} mots. "
                "Raccourcissez-la puis réessayez."
            ),
        )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    request: Request,
    user: TokenPayload = Depends(require_role(Role.VIEWER)),
) -> ChatResponse:
    """Run the full agentic workflow for one user query."""
    from backend.workflows.graph import run_query

    ctx = get_ctx(request)
    graph = get_graph(request)
    settings: Settings = ctx.settings
    _enforce_word_limit(payload.query, settings)
    state = _make_state(payload, _state_user_id(payload, user))
    await check_budget(ctx.user_store, user, settings)
    entry = resolve_model_entry(ctx, user, payload.model, query=payload.query)
    model_id = entry.id if entry is not None else ""
    llm = resolve_llm(ctx, user, payload.model, query=payload.query)
    state["llm"] = llm

    # Answer cache: an explicit session_id means mid-conversation context,
    # which bypasses the cache (same query, different meaning).
    answer_cache = AnswerCache(ctx.cache, ctx.embedder, settings)
    use_cache = not payload.session_id
    if use_cache:
        cached = await answer_cache.get(payload.query, model_id)
        if cached is not None:
            metrics.chat_requests_total.inc()
            return _cached_response(cached, state)

    usage_before = dict(llm.usage_totals)
    metrics.chat_requests_total.inc()
    with metrics.time_histogram(metrics.chat_latency_seconds):
        async with traced_chat_run(
            payload.query,
            user_id=payload.user_id or user.sub,
            session_id=payload.session_id,
            feature="chat",
            settings=settings,
        ) as (trace_id, trace, handler):
            run_task = asyncio.create_task(run_query(graph, ctx, state, config=_graph_config(handler)))
            _register_run(state.get("session_id", ""), run_task)
            keep_registered = False
            try:
                # shield: a client disconnect cancels this request coroutine
                # but must not kill the graph run (see CancelledError below).
                response = await asyncio.wait_for(
                    asyncio.shield(run_task), timeout=settings.chat_run_timeout_seconds
                )
            except asyncio.TimeoutError:
                run_task.cancel()  # the shield keeps it alive; enforce the cap
                if ctx.memory is not None:
                    try:
                        await ctx.memory.append_turn(
                            state.get("session_id", ""),
                            state.get("user_id", "anonymous"),
                            [
                                ChatMessage(role="user", content=payload.query),
                                ChatMessage(role="assistant", content=RUN_FAILURE_NOTE),
                            ],
                        )
                    except Exception:
                        pass  # memory persistence must never break the answer path
                raise HTTPException(status_code=504, detail="le traitement a dépassé le délai maximal")
            except asyncio.CancelledError:
                if run_task.cancelled():
                    # /chat/cancel (UI stop button) stopped the graph run.
                    raise HTTPException(status_code=409, detail="request cancelled by user")
                # Client disconnected mid-run: the shield kept the graph run
                # alive — detach a watcher so it finishes and persists the
                # turn; the client recovers the answer from the history.
                logger.info(
                    "POST /chat client disconnected; finishing the run silently",
                    extra={"session_id": state.get("session_id", "")},
                )
                keep_registered = True
                asyncio.ensure_future(_drain_run(state.get("session_id", ""), run_task))
                raise
            finally:
                if not keep_registered:
                    _unregister_run(state.get("session_id", ""), run_task)
            response.trace_id = trace_id
            if use_cache and is_cacheable(response.model_dump(mode="json"), settings):
                # Never persist the internal trace in the shared answer cache:
                # it is admin-only (spec §48) and stale for a cached replay.
                cache_payload = response.model_dump(mode="json")
                cache_payload["trace"] = []
                await answer_cache.set(payload.query, model_id, cache_payload)
            update_trace_output(trace, _trace_output_from_response(response), settings=settings)
    await _meter(ctx, user, llm, usage_before, query=payload.query, answer=response.answer.answer)
    if not has_role(user.role, Role.ADMIN):
        # Internal chain-of-thought is exposed to administrators only (spec §48).
        response.trace = []
    return response


async def _pump_events(
    graph: Any,
    state: GraphState,
    config: Optional[dict[str, Any]],
    queue: "asyncio.Queue[tuple[str, Any]]",
) -> None:
    """Forward graph stream events into a queue; cancelled via /chat/cancel.

    Cancelling this task raises CancelledError inside the LangGraph stream,
    which stops the workflow for real, then reports "cancelled" to the
    consumer so the SSE stream can close cleanly without persisting anything.
    """
    from backend.workflows.graph import stream_query

    try:
        async for event in stream_query(graph, state, config=config):
            await queue.put(("update", event))
        await queue.put(("done", None))
    except asyncio.CancelledError:
        await queue.put(("cancelled", None))
    except Exception as exc:  # surfaced to the consumer as an error frame
        await queue.put(("error", exc))


class _NodeStartHandler(AsyncCallbackHandler):
    """LangChain async callback: pushes a queue event when a graph node STARTS.

    LangGraph "updates" stream mode only fires AFTER each node completes, so
    without this the UI cannot show which node is currently running (or stuck).
    """

    def __init__(self, queue: "asyncio.Queue[tuple[str, Any]]"):
        super().__init__()
        self._queue = queue
        self._last_node: Optional[str] = None

    async def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        metadata: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        node = (metadata or {}).get("langgraph_node")
        if node and node != self._last_node:
            self._last_node = node
            self._queue.put_nowait(("node_start", node))


# Final-answer streaming: the VERIFIED answer text (post claim/citation
# verification and output guardrail) is emitted as `delta` frames just ahead
# of the authoritative `final` frame, so the UI types the answer out
# progressively. Chunking is adaptive: about ANSWER_DELTA_TARGET_FRAMES frames
# whatever the answer length, keeping playback around ~2 s.
ANSWER_DELTA_MIN_CHARS = 12
ANSWER_DELTA_TARGET_FRAMES = 80
ANSWER_DELTA_DELAY_SECONDS = 0.025


async def _answer_deltas(text: str) -> AsyncIterator[str]:
    """Yield `text` in word-boundary chunks for SSE/WS typewriter playback."""
    if not text:
        return
    size = max(ANSWER_DELTA_MIN_CHARS, (len(text) + ANSWER_DELTA_TARGET_FRAMES - 1) // ANSWER_DELTA_TARGET_FRAMES)
    pos = 0
    while pos < len(text):
        end = min(pos + size, len(text))
        if end < len(text):
            # Cut right after the last space inside the window so a word is
            # never split (hard cut only for pathologically long words).
            space = text.rfind(" ", pos + 1, end + 1)
            if space > pos:
                end = space + 1
        yield text[pos:end]
        pos = end
        await asyncio.sleep(ANSWER_DELTA_DELAY_SECONDS)


async def _stream_events(
    graph: Any,
    state: GraphState,
    payload: ChatRequest,
    user_sub: str,
    settings: Settings,
    memory: Any = None,
    user_store: Any = None,
    meter_user_id: Optional[str] = None,
    usage_before: Optional[dict[str, int]] = None,
    answer_cache: Optional[AnswerCache] = None,
    model_id: str = "",
    include_trace: bool = False,
) -> AsyncIterator[str]:
    """Yield SSE frames: one per node update, then the final ChatResponse.

    The Langfuse trace is managed inside the generator so it spans the full
    streaming lifecycle. Once the stream finishes successfully (a final
    answer was produced), the turn is persisted exactly like `run_query`
    does for the REST endpoint, token usage is metered, and the answer is
    cached when eligible; failed/aborted streams persist nothing.
    """
    from backend.workflows.graph import stream_query

    async with traced_chat_run(
        payload.query,
        user_id=payload.user_id or user_sub,
        session_id=payload.session_id,
        feature="chat-stream",
        settings=settings,
    ) as (trace_id, trace, handler):
        started = time.perf_counter()
        merged: dict[str, Any] = {}
        metrics.chat_requests_total.inc()
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        callbacks: list[Any] = [_NodeStartHandler(queue)]
        if handler is not None:
            callbacks.insert(0, handler)
        config = {"callbacks": callbacks}
        pump = asyncio.create_task(_pump_events(graph, state, config, queue))
        session_id = state.get("session_id", "")
        _register_run(session_id, pump)
        logger.info("chat stream started", extra={"session_id": session_id, "query": payload.query[:80]})
        cancelled = False
        # Set when the client connection dropped mid-run: the generator then
        # finishes the run silently (see the drain loop below) instead of
        # aborting it, so the answer still lands in the session history.
        disconnected = False
        try:
            deadline = started + settings.chat_run_timeout_seconds
            try:
                while True:
                    try:
                        kind, value = await asyncio.wait_for(
                            queue.get(), timeout=settings.chat_heartbeat_seconds
                        )
                    except asyncio.TimeoutError:
                        # Heartbeat: keeps proxies (nginx, next dev) and browsers
                        # from killing the idle SSE connection during long nodes.
                        if time.perf_counter() > deadline:
                            pump.cancel()
                            logger.warning("chat stream timed out", extra={"session_id": session_id})
                            yield f"data: {json.dumps({'type': 'error', 'detail': 'Le traitement a dépassé le délai maximal. Réessayez ou simplifiez la question.'}, ensure_ascii=False)}\n\n"
                            return
                        yield ": hb\n\n"
                        continue
                    if kind == "update":
                        update = value.get("update")
                        if isinstance(update, dict):
                            _merge_update(merged, update)
                        frame = _strip_trace_frame(value, include_trace)
                        yield f"data: {json.dumps({'type': 'update', **frame}, ensure_ascii=False, default=str)}\n\n"
                    elif kind == "node_start":
                        yield f"data: {json.dumps({'type': 'node_start', 'node': value}, ensure_ascii=False)}\n\n"
                    elif kind == "done":
                        break
                    elif kind == "cancelled":
                        cancelled = True
                        logger.info("chat stream cancelled by user", extra={"session_id": session_id})
                        yield f"data: {json.dumps({'type': 'cancelled'}, ensure_ascii=False)}\n\n"
                        break
                    else:  # "error"
                        raise value
            except (asyncio.CancelledError, GeneratorExit):
                # Client disconnected mid-run (mobile screen lock, network
                # drop, proxy cut): the server cancels this generator. Do NOT
                # abort the graph run with it — drain it to completion below
                # so the turn is still persisted; the client recovers the
                # answer by polling the session history.
                disconnected = True
            if disconnected and not cancelled:
                logger.info(
                    "chat stream client disconnected; finishing the run silently",
                    extra={"session_id": session_id},
                )
                # Set when the drained run can no longer complete (timeout or
                # error): the client is polling the history for an answer that
                # will never exist — persist an honest failure marker instead,
                # so recovery resolves immediately instead of polling in vain.
                drain_failed = False
                while True:
                    if pump.done() and queue.empty():
                        break
                    remaining = deadline - time.perf_counter()
                    if remaining <= 0:
                        pump.cancel()
                        drain_failed = True
                        break
                    try:
                        kind, value = await asyncio.wait_for(queue.get(), timeout=remaining)
                    except asyncio.TimeoutError:
                        pump.cancel()
                        drain_failed = True
                        logger.warning("chat stream timed out", extra={"session_id": session_id})
                        break
                    if kind == "update":
                        update = value.get("update")
                        if isinstance(update, dict):
                            _merge_update(merged, update)
                    elif kind == "done":
                        break
                    elif kind == "cancelled":
                        cancelled = True
                        break
                    elif kind != "node_start":  # "error"
                        drain_failed = True
                        logger.warning(
                            "chat stream run failed after client disconnect: %s",
                            value,
                            extra={"session_id": session_id},
                        )
                        break
                if drain_failed and not cancelled:
                    if memory is not None:
                        try:
                            await memory.append_turn(
                                state.get("session_id", ""),
                                state.get("user_id", "anonymous"),
                                [
                                    ChatMessage(role="user", content=payload.query),
                                    ChatMessage(role="assistant", content=RUN_FAILURE_NOTE),
                                ],
                            )
                        except Exception:
                            pass  # memory persistence must never break the stream
                    return
            if cancelled:
                return
            response = _final_response(merged, state, started, trace_id=trace_id)
            if not include_trace:
                response.trace = []
            metrics.chat_latency_seconds.observe(response.latency_ms / 1000.0)
            update_trace_output(trace, _trace_output_from_response(response), settings=settings)
            if memory is not None and merged.get("final_answer") is not None:
                try:
                    await memory.append_turn(
                        state.get("session_id", ""),
                        state.get("user_id", "anonymous"),
                        [
                            ChatMessage(role="user", content=payload.query),
                            # Full FinalAnswer JSON, same convention as run_query.
                            ChatMessage(role="assistant", content=response.answer.model_dump_json()),
                        ],
                    )
                    # Same long-term-memory hook as run_query (see graph.py).
                    from backend.memory.summarizer import maybe_summarize

                    await maybe_summarize(
                        memory,
                        state.get("session_id", ""),
                        llm=state.get("llm"),
                        user_id=state.get("user_id", "anonymous"),
                    )
                except Exception:
                    pass  # memory persistence must never break the stream
                if user_store is not None and meter_user_id and usage_before is not None:
                    llm = state.get("llm")
                    if llm is not None:
                        tokens_in = llm.usage_totals["tokens_in"] - usage_before.get("tokens_in", 0)
                        tokens_out = llm.usage_totals["tokens_out"] - usage_before.get("tokens_out", 0)
                        if tokens_in <= 0 and tokens_out <= 0:
                            # Offline/mock: no LLM call happened; estimate instead.
                            tokens_in = LLMClient._estimate_tokens(payload.query)
                            tokens_out = LLMClient._estimate_tokens(response.answer.answer)
                        try:
                            await user_store.record_usage(meter_user_id, tokens_in, tokens_out)
                        except Exception:
                            pass
                if answer_cache is not None and is_cacheable(response.model_dump(mode="json"), settings):
                    # Cache without the internal trace (admin-only, spec §48).
                    cache_payload = response.model_dump(mode="json")
                    cache_payload["trace"] = []
                    await answer_cache.set(payload.query, model_id, cache_payload)
            if disconnected:
                # The run completed and was persisted after the client left;
                # nothing more can be sent on the dead stream.
                logger.info(
                    "chat stream done after client disconnect",
                    extra={"session_id": session_id, "latency_ms": response.latency_ms},
                )
                return
            # Verified-answer playback: `delta` frames type the text out in
            # the UI; the `final` frame stays the authoritative payload.
            async for _delta in _answer_deltas(response.answer.answer):
                yield f"data: {json.dumps({'type': 'delta', 'text': _delta}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'final', 'response': response.model_dump(mode='json')}, ensure_ascii=False)}\n\n"
            logger.info(
                "chat stream done",
                extra={"session_id": session_id, "latency_ms": response.latency_ms},
            )
        except Exception as exc:  # surface failures to the client instead of hanging
            logger.exception("chat stream failed", extra={"session_id": session_id})
            metrics.errors_total.labels(kind="chat_stream").inc()
            if not disconnected:
                yield f"data: {json.dumps({'type': 'error', 'detail': str(exc)}, ensure_ascii=False)}\n\n"
        finally:
            _unregister_run(session_id, pump)
            if not pump.done():
                pump.cancel()


async def _cached_stream_events(response: ChatResponse, include_trace: bool) -> AsyncIterator[str]:
    """Synthetic SSE sequence for an answer-cache hit (update + deltas + final)."""
    update: dict[str, Any] = {"node": "answer_cache", "update": {}}
    if include_trace:
        update["update"] = {"trace": ["answer_cache: hit"]}
    yield f"data: {json.dumps({'type': 'update', **update}, ensure_ascii=False)}\n\n"
    async for _delta in _answer_deltas(response.answer.answer):
        yield f"data: {json.dumps({'type': 'delta', 'text': _delta}, ensure_ascii=False)}\n\n"
    yield f"data: {json.dumps({'type': 'final', 'response': response.model_dump(mode='json')}, ensure_ascii=False)}\n\n"


@router.get("/chat/stream")
async def chat_stream(
    request: Request,
    query: str = Query(..., min_length=1),
    session_id: Optional[str] = Query(None),
    language: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    user: TokenPayload = Depends(require_role(Role.VIEWER)),
) -> StreamingResponse:
    """SSE: per-node updates followed by a final event with the ChatResponse."""
    payload = ChatRequest(query=query, session_id=session_id, language=language, model=model)
    state = _make_state(payload, _state_user_id(payload, user))
    ctx = get_ctx(request)
    settings = ctx.settings
    _enforce_word_limit(query, settings)
    await check_budget(ctx.user_store, user, settings)
    entry = resolve_model_entry(ctx, user, model, query=query)
    model_id = entry.id if entry is not None else ""
    llm = resolve_llm(ctx, user, model, query=query)
    state["llm"] = llm

    # Same cache rule as POST /chat: explicit session_id => mid-conversation,
    # bypass. A hit is replayed as a synthetic update+final event sequence.
    answer_cache = AnswerCache(ctx.cache, ctx.embedder, settings)
    use_cache = not session_id
    include_trace = has_role(user.role, Role.ADMIN)
    if use_cache:
        cached = await answer_cache.get(query, model_id)
        if cached is not None:
            return StreamingResponse(
                _cached_stream_events(_cached_response(cached, state), include_trace),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

    usage_before = dict(llm.usage_totals)
    return StreamingResponse(
        _stream_events(
            get_graph(request),
            state,
            payload,
            user.sub,
            settings,
            memory=ctx.memory,
            user_store=ctx.user_store,
            meter_user_id=user.user_id,
            usage_before=usage_before,
            answer_cache=answer_cache if use_cache else None,
            model_id=model_id,
            include_trace=include_trace,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.websocket("/ws/chat")
async def ws_chat(ws: WebSocket) -> None:
    """WebSocket: receive one ChatRequest JSON, stream node updates, then the
    final ChatResponse, then close. Auth: optional `token` query param."""
    await ws.accept()
    try:
        ctx = get_ctx(ws)  # type: ignore[arg-type]
        settings: Settings = ctx.settings
        user_sub = "anonymous"
        user_role = Role.USER  # anonymous/dev-fallback callers never see the trace
        token = ws.query_params.get("token")
        if token:
            try:
                from backend.security.jwt import decode_access_token

                token_payload = decode_access_token(token, ctx.settings)
                user_sub = token_payload.sub
                user_role = token_payload.role
            except Exception:
                if ctx.settings.env != "development":
                    await ws.close(code=4401)
                    return
        include_trace = has_role(user_role, Role.ADMIN)

        data = await ws.receive_json()
        try:
            payload = ChatRequest(**data)
        except ValidationError as exc:
            await ws.send_json({"type": "error", "detail": exc.errors()})
            await ws.close(code=4400)
            return
        try:
            _enforce_word_limit(payload.query, settings)
        except HTTPException as exc:
            await ws.send_json({"type": "error", "detail": exc.detail})
            await ws.close(code=4400)
            return

        state = _make_state(payload, user_sub)

        from backend.workflows.graph import stream_query

        async with traced_chat_run(
            payload.query,
            user_id=payload.user_id or user_sub,
            session_id=payload.session_id,
            feature="chat-ws",
            settings=settings,
        ) as (trace_id, trace, handler):
            started = time.perf_counter()
            merged: dict[str, Any] = {}
            config = _graph_config(handler)

            metrics.chat_requests_total.inc()
            try:
                async for event in stream_query(get_graph(ws), state, config=config):  # type: ignore[arg-type]
                    update = event.get("update")
                    if isinstance(update, dict):
                        _merge_update(merged, update)
                    await ws.send_json({"type": "update", **_strip_trace_frame(event, include_trace)})
                response = _final_response(merged, state, started, trace_id=trace_id)
                if not include_trace:
                    response.trace = []
                update_trace_output(trace, _trace_output_from_response(response), settings=settings)
                # Same verified-answer playback as the SSE path.
                async for _delta in _answer_deltas(response.answer.answer):
                    await ws.send_json({"type": "delta", "text": _delta})
                await ws.send_json(
                    {"type": "final", "response": response.model_dump(mode="json")}
                )
                await ws.close()
            except WebSocketDisconnect:
                pass
            except Exception as exc:
                logger.exception("ws chat failed")
                metrics.errors_total.labels(kind="ws_chat").inc()
                try:
                    await ws.send_json({"type": "error", "detail": str(exc)})
                    await ws.close(code=1011)
                except Exception:
                    pass
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("ws chat failed")
        try:
            await ws.send_json({"type": "error", "detail": str(exc)})
            await ws.close(code=1011)
        except Exception:
            pass


@router.post("/chat/feedback", status_code=202)
async def chat_feedback(
    payload: FeedbackPayload,
    request: Request,
    user: TokenPayload = Depends(require_role(Role.VIEWER)),
) -> dict[str, str]:
    """Record explicit user feedback (thumbs up/down) as a Langfuse score.

    Requires the ``trace_id`` returned by any of the chat endpoints. The score
    name is ``user-thumbs`` so it can be aggregated consistently across the app.
    """
    settings = get_ctx(request).settings
    value = 1 if payload.score == "thumbs-up" else 0
    if isinstance(payload.score, int):
        value = max(0, min(1, payload.score))
    create_feedback_score(
        payload.trace_id,
        "user-thumbs",
        value,
        comment=payload.comment,
        data_type="BOOLEAN",
        settings=settings,
    )
    return {"status": "recorded"}


# ---------------------------------------------------------------------------
# Audio transcription (voice input for the chat composer)
# ---------------------------------------------------------------------------

#: Accepted uploads: extension OR content-type must match (browsers are
#: inconsistent about what MediaRecorder reports).
_AUDIO_EXTENSIONS = {".webm", ".ogg", ".mp3", ".wav", ".m4a", ".mp4"}
_AUDIO_CONTENT_TYPES = {"video/mp4", "application/ogg"}  # plus any audio/*


@router.post("/chat/transcribe")
async def chat_transcribe(
    request: Request,
    file: UploadFile = File(...),
    user: TokenPayload = Depends(require_role(Role.VIEWER)),
) -> dict[str, str]:
    """Transcribe a voice message to text; the chat flow itself is unchanged.

    The browser records with MediaRecorder and posts the blob here; the
    returned text is inserted into the composer so the user reviews it and
    sends it through the normal chat endpoints.
    """
    ctx = get_ctx(request)
    settings: Settings = ctx.settings
    # Same quota gate as the chat endpoints: over-budget users get a 429
    # instead of free transcriptions.
    await check_budget(ctx.user_store, user, settings)
    ext = Path(file.filename or "").suffix.lower()
    content_type = (file.content_type or "").lower()
    if ext not in _AUDIO_EXTENSIONS and not (
        content_type.startswith("audio/") or content_type in _AUDIO_CONTENT_TYPES
    ):
        raise HTTPException(
            status_code=400,
            detail="Format audio non pris en charge. Formats acceptés : webm, ogg, mp3, wav, m4a, mp4.",
        )
    audio = await file.read()
    if len(audio) > settings.stt_max_audio_bytes:
        raise HTTPException(
            status_code=413,
            detail="Le fichier audio dépasse la taille maximale autorisée.",
        )
    if not audio:
        raise HTTPException(status_code=400, detail="Le fichier audio est vide.")
    if not stt.stt_available():
        raise HTTPException(
            status_code=503,
            detail="La transcription audio n'est pas disponible sur ce serveur.",
        )
    try:
        text = await stt.transcribe_audio(audio, file.filename or f"audio{ext or '.webm'}")
    except STTError as exc:
        raise HTTPException(
            status_code=502,
            detail="La transcription a échoué. Réessayez ou saisissez votre question.",
        ) from exc
    # Meter one request, no tokens (same best-effort pattern as _meter).
    if getattr(user, "user_id", None) and ctx.user_store is not None:
        try:
            await ctx.user_store.record_usage(user.user_id, 0, 0)
        except Exception:
            pass
    return {"text": text}


# ---------------------------------------------------------------------------
# Chat history (per-user, Bearer required)
# ---------------------------------------------------------------------------


@router.get("/chat/sessions")
async def list_chat_sessions(
    request: Request,
    user: TokenPayload = Depends(require_user),
) -> dict[str, Any]:
    """List the caller's chat sessions, most recently updated first.

    Scoping is by user_id — with today's 1:1 personal workspaces that *is*
    workspace isolation. Anonymous callers get a 401.
    """
    ctx = get_ctx(request)
    if ctx.memory is None:
        return {"sessions": []}
    sessions = await ctx.memory.list_sessions(user.user_id or user.sub)
    return {"sessions": sessions}


@router.get("/chat/sessions/{session_id}")
async def get_chat_session(
    session_id: str,
    request: Request,
    user: TokenPayload = Depends(require_user),
) -> dict[str, Any]:
    """Return one session's messages, oldest first.

    Assistant turns carry `answer`: the parsed FinalAnswer dict when the
    stored content is its JSON serialization, else null. Unknown sessions
    and sessions owned by another user both return 404 (no existence leak).
    """
    ctx = get_ctx(request)
    messages = (
        []
        if ctx.memory is None
        else await ctx.memory.get_session_messages(user.user_id or user.sub, session_id)
    )
    if not messages:
        raise HTTPException(status_code=404, detail="Session introuvable.")
    return {
        "session_id": session_id,
        "messages": [
            {
                # Simple per-session index (0, 1, 2, …) so clients can match a
                # prompt to its final answer without comparing text.
                "index": index,
                "role": m.role,
                "content": m.content,
                "answer": parse_answer_json(m.content) if m.role == "assistant" else None,
                "created_at": m.created_at.isoformat(),
            }
            for index, m in enumerate(messages)
        ],
    }


@router.get("/chat/sessions/{session_id}/run")
async def get_chat_run_status(
    session_id: str,
    user: TokenPayload = Depends(require_user),
) -> dict[str, bool]:
    """Whether a chat run is currently in flight for this session.

    Lets a client whose stream dropped tell "the server is still computing my
    answer" (keep polling the history) from "nothing is running" (the turn
    failed or never started — retry instead of waiting). Session ids are
    unguessable uuids; the boolean leaks nothing else.
    """
    task = _RUNNING.get(session_id)
    return {"running": task is not None and not task.done()}


@router.delete("/chat/sessions/{session_id}", status_code=204, response_class=Response)
async def delete_chat_session(
    session_id: str,
    request: Request,
    user: TokenPayload = Depends(require_user),
) -> Response:
    """Delete one of the caller's chat sessions (all its messages).

    Owner-scoped like the read endpoints: deleting another user's session
    yields 404 (no existence leak). 204 with an empty body on success.
    """
    ctx = get_ctx(request)
    deleted = (
        0
        if ctx.memory is None
        else await ctx.memory.delete_session(user.user_id or user.sub, session_id)
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Session introuvable.")
    return Response(status_code=204)
