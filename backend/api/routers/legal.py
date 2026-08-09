"""Legal router: versioned alias surface for the core legal query flow.

`POST /api/v1/legal/query` is a thin alias of `POST /api/v1/chat` — same
request/response contract, same auth, same implementation (it delegates to
the chat endpoint function, so caching/metering/tracing behave identically).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from backend.api.routers.chat import chat as _chat
from backend.core.models import ChatRequest, ChatResponse, Role
from backend.security.jwt import TokenPayload
from backend.security.rbac import require_role

router = APIRouter(prefix="/legal", tags=["legal"])


@router.post("/query", response_model=ChatResponse)
async def legal_query(
    payload: ChatRequest,
    request: Request,
    user: TokenPayload = Depends(require_role(Role.VIEWER)),
) -> ChatResponse:
    """Run the full agentic workflow for one legal query (alias of /chat)."""
    return await _chat(payload, request, user)
