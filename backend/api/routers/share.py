"""Share router: public read-only answer links.

``POST /share`` (authenticated) snapshots one answer behind an unguessable
token; ``GET /share/{token}`` is PUBLIC (no auth) — the whole point is that
the recipient has no account. Shared snapshots die with the author's account
(``UserStore.delete_user``).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.api.deps import get_ctx, require_user
from backend.security.jwt import TokenPayload

router = APIRouter(prefix="/share", tags=["share"])


class ShareIn(BaseModel):
    query: str = Field(default="", max_length=2000)
    answer: str = Field(default="", max_length=64_000)
    citations: list[dict] = Field(default_factory=list, max_length=50)
    confidence: float = 0.0


class ShareOut(BaseModel):
    token: str
    url_path: str  # /partage/<token> on the web app


class SharedAnswerOut(BaseModel):
    query: str
    answer: str
    citations: list[dict]
    confidence: float
    created_at: str


def _user_store(request: Request):
    store = getattr(get_ctx(request), "user_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="user store unavailable")
    return store


@router.post("", response_model=ShareOut, status_code=201)
async def create_share(
    payload: ShareIn,
    request: Request,
    user: TokenPayload = Depends(require_user),
) -> ShareOut:
    """Snapshot an answer for public sharing."""
    if not user.user_id:
        raise HTTPException(status_code=400, detail="registered account required")
    token = await _user_store(request).create_share(
        user.user_id,
        payload.query,
        payload.answer,
        json.dumps([c for c in payload.citations], ensure_ascii=False),
        payload.confidence,
    )
    if token is None:
        raise HTTPException(status_code=503, detail="user store unavailable")
    return ShareOut(token=token, url_path=f"/partage/{token}")


@router.get("/{token}", response_model=SharedAnswerOut)
async def get_shared_answer(token: str, request: Request) -> SharedAnswerOut:
    """PUBLIC: read a shared answer snapshot (no auth, 404 when unknown)."""
    record = await _user_store(request).get_share(token)
    if record is None:
        raise HTTPException(status_code=404, detail="unknown or expired share link")
    try:
        citations = json.loads(record["citations_json"] or "[]")
    except ValueError:
        citations = []
    return SharedAnswerOut(
        query=record["query"],
        answer=record["answer"],
        citations=citations,
        confidence=record["confidence"],
        created_at=record["created_at"],
    )
