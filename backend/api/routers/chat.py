"""Chat router: REST, SSE streaming and WebSocket endpoints.

Streaming strategy: true token-level streaming is not exposed by the graph,
so we stream real per-node graph updates (`stream_query`, stream_mode
"updates") as they happen and reconstruct the final `ChatResponse` from the
accumulated node updates — the graph runs exactly once. The final SSE/WS
event carries the full ChatResponse. (Memory persistence on streamed
requests happens only via the REST endpoint's `run_query`; streamed runs
skip it, matching `stream_query`'s contract.)

All chat entry points create a Langfuse trace per request with descriptive
names, user/session ids, feature tags, and explicit input/output. The trace
id is returned to the client so explicit feedback can be scored on the
correct trace.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncIterator, Literal, Optional

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from backend.api.deps import get_ctx, get_graph
from backend.core.config import Settings
from backend.core.models import ChatRequest, ChatResponse, FinalAnswer, Role
from backend.core.state import GraphState
from backend.observability import metrics
from backend.observability.langfuse_client import (
    create_feedback_score,
    traced_chat_run,
    update_trace_output,
)
from backend.security.jwt import TokenPayload
from backend.security.rbac import require_role

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

_LIST_KEYS = {"trace", "errors"}  # state keys that accumulate across node updates


class FeedbackPayload(BaseModel):
    trace_id: str
    score: Literal["thumbs-up", "thumbs-down"] | int = "thumbs-up"
    comment: Optional[str] = None


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
    state = _make_state(payload, user.sub)
    settings: Settings = ctx.settings

    metrics.chat_requests_total.inc()
    with metrics.time_histogram(metrics.chat_latency_seconds):
        async with traced_chat_run(
            payload.query,
            user_id=payload.user_id or user.sub,
            session_id=payload.session_id,
            feature="chat",
            settings=settings,
        ) as (trace_id, trace, handler):
            response = await run_query(graph, ctx, state, config=_graph_config(handler))
            response.trace_id = trace_id
            update_trace_output(trace, _trace_output_from_response(response), settings=settings)
            return response


async def _stream_events(
    graph: Any,
    state: GraphState,
    payload: ChatRequest,
    user_sub: str,
    settings: Settings,
) -> AsyncIterator[str]:
    """Yield SSE frames: one per node update, then the final ChatResponse.

    The Langfuse trace is managed inside the generator so it spans the full
    streaming lifecycle.
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
        config = _graph_config(handler)
        try:
            async for event in stream_query(graph, state, config=config):
                update = event.get("update")
                if isinstance(update, dict):
                    _merge_update(merged, update)
                yield f"data: {json.dumps({'type': 'update', **event}, ensure_ascii=False, default=str)}\n\n"
            response = _final_response(merged, state, started, trace_id=trace_id)
            metrics.chat_latency_seconds.observe(response.latency_ms / 1000.0)
            update_trace_output(trace, _trace_output_from_response(response), settings=settings)
            yield f"data: {json.dumps({'type': 'final', 'response': response.model_dump(mode='json')}, ensure_ascii=False)}\n\n"
        except Exception as exc:  # surface failures to the client instead of hanging
            logger.exception("chat stream failed")
            metrics.errors_total.labels(kind="chat_stream").inc()
            yield f"data: {json.dumps({'type': 'error', 'detail': str(exc)}, ensure_ascii=False)}\n\n"


@router.get("/chat/stream")
async def chat_stream(
    request: Request,
    query: str = Query(..., min_length=1),
    session_id: Optional[str] = Query(None),
    language: Optional[str] = Query(None),
    user: TokenPayload = Depends(require_role(Role.VIEWER)),
) -> StreamingResponse:
    """SSE: per-node updates followed by a final event with the ChatResponse."""
    payload = ChatRequest(query=query, session_id=session_id, language=language)
    state = _make_state(payload, user.sub)
    settings = get_ctx(request).settings
    return StreamingResponse(
        _stream_events(get_graph(request), state, payload, user.sub, settings),
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
        token = ws.query_params.get("token")
        if token:
            try:
                from backend.security.jwt import decode_access_token

                user_sub = decode_access_token(token, ctx.settings).sub
            except Exception:
                if ctx.settings.env != "development":
                    await ws.close(code=4401)
                    return

        data = await ws.receive_json()
        try:
            payload = ChatRequest(**data)
        except ValidationError as exc:
            await ws.send_json({"type": "error", "detail": exc.errors()})
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
                    await ws.send_json({"type": "update", **event})
                response = _final_response(merged, state, started, trace_id=trace_id)
                update_trace_output(trace, _trace_output_from_response(response), settings=settings)
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
