"""Bookmarks router: saved answer snapshots (the user's personal library).

Bookmarks survive chat-history deletion by design — they are the user's
curated reference shelf.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend.api.deps import get_ctx, require_user
from backend.security.jwt import TokenPayload

router = APIRouter(prefix="/bookmarks", tags=["bookmarks"])


class BookmarkIn(BaseModel):
    query: str = Field(default="", max_length=2000)
    answer: str = Field(default="", max_length=64_000)
    confidence: float = 0.0
    session_id: str = Field(default="", max_length=128)


class BookmarkOut(BaseModel):
    id: str
    query: str
    answer: str
    confidence: float
    session_id: str
    created_at: str


def _user_store(request: Request):
    store = getattr(get_ctx(request), "user_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="user store unavailable")
    return store


@router.post("", response_model=BookmarkOut, status_code=201)
async def add_bookmark(
    payload: BookmarkIn,
    request: Request,
    user: TokenPayload = Depends(require_user),
) -> BookmarkOut:
    """Save an answer snapshot for the current user."""
    if not user.user_id:
        raise HTTPException(status_code=400, detail="registered account required")
    record = await _user_store(request).add_bookmark(
        user.user_id, payload.query, payload.answer, payload.confidence, payload.session_id
    )
    if record is None:
        raise HTTPException(status_code=503, detail="user store unavailable")
    return BookmarkOut(**record)


@router.get("", response_model=list[BookmarkOut])
async def list_bookmarks(
    request: Request,
    user: TokenPayload = Depends(require_user),
) -> list[BookmarkOut]:
    """Newest-first bookmarks of the current user."""
    if not user.user_id:
        return []
    rows = await _user_store(request).list_bookmarks(user.user_id)
    return [BookmarkOut(**r) for r in rows]


@router.delete("/{bookmark_id}", status_code=204)
async def delete_bookmark(
    bookmark_id: str,
    request: Request,
    user: TokenPayload = Depends(require_user),
) -> Response:
    """Remove one bookmark (owner-scoped; 404 when foreign or unknown)."""
    if not user.user_id:
        raise HTTPException(status_code=400, detail="registered account required")
    deleted = await _user_store(request).delete_bookmark(user.user_id, bookmark_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="unknown bookmark")
    return Response(status_code=204)
