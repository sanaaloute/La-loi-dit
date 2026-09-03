"""Push router: device token registration for freshness notifications.

The mobile app registers its Expo push token at login (and re-registers on
app start); tokens are deleted on logout and pruned when Expo reports a dead
device. Delivery lives in ``backend/core/push.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend.api.deps import get_ctx, require_user
from backend.security.jwt import TokenPayload

router = APIRouter(prefix="/push", tags=["push"])


class PushTokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=128)
    device_id: str = Field(default="", max_length=128)


class PushTokenDelete(BaseModel):
    token: str = Field(min_length=10, max_length=128)


def _user_store(request: Request):
    store = getattr(get_ctx(request), "user_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="user store unavailable")
    return store


@router.post("/token")
async def register_push_token(
    payload: PushTokenIn,
    request: Request,
    user: TokenPayload = Depends(require_user),
) -> dict[str, str]:
    """Register/refresh this device's Expo push token for the current user."""
    if not user.user_id:
        raise HTTPException(status_code=400, detail="registered account required")
    if not payload.token.startswith(("ExponentPushToken[", "ExpoPushToken[")):
        raise HTTPException(status_code=422, detail="not an Expo push token")
    ok = await _user_store(request).register_push_token(
        user.user_id, payload.token, payload.device_id
    )
    if not ok:
        raise HTTPException(status_code=503, detail="user store unavailable")
    return {"detail": "registered"}


@router.delete("/token", status_code=204)
async def delete_push_token(
    payload: PushTokenDelete,
    request: Request,
    user: TokenPayload = Depends(require_user),
) -> Response:
    """Unregister a token (logout on this device). 404 when foreign/unknown."""
    if not user.user_id:
        raise HTTPException(status_code=400, detail="registered account required")
    deleted = await _user_store(request).delete_push_token(user.user_id, payload.token)
    if not deleted:
        raise HTTPException(status_code=404, detail="unknown push token")
    return Response(status_code=204)
