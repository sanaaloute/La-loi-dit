"""Expo Push delivery: freshness ("Nouveautés") broadcasts to every device,
plus per-user "answer ready" alerts for chat runs that completed after the
client disconnected (locked phone, app killed).

Off by default (``LEGAL_AI_PUSH_NOTIFICATIONS_ENABLED``). Uses the Expo Push
API — no SDK, one POST of the whole message batch. Dead tokens reported as
DeviceNotRegistered are pruned from the store.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


async def _post(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """One batch POST to the Expo Push API (separated for testability)."""
    import httpx

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(EXPO_PUSH_URL, json=messages)
        response.raise_for_status()
        return response.json()


async def _deliver(
    ctx: Any,
    tokens: list[str],
    title: str,
    body: str,
    data: dict[str, Any],
    channel: str | None = None,
) -> int:
    """Batch-send one notification to the given tokens; prune dead ones."""
    user_store = getattr(ctx, "user_store", None)
    messages = [
        {
            "to": token,
            "sound": "default",
            "title": title,
            "body": body[:180],
            "data": data,
            # Android notification channel (created client-side); iOS ignores it.
            **({"channelId": channel} if channel else {}),
        }
        for token in tokens
    ]
    try:
        payload = await _post(messages)
    except Exception:
        logger.warning("expo push delivery failed", exc_info=True)
        return 0

    delivered = 0
    for token, ticket in zip(tokens, payload.get("data", [])):
        if ticket.get("status") == "ok":
            delivered += 1
        elif ticket.get("details", {}).get("error") == "DeviceNotRegistered" and user_store is not None:
            await user_store.delete_push_token_unscoped(token)
    logger.info("push sent: %d/%d delivered", delivered, len(tokens))
    return delivered


async def send_push(ctx: Any, title: str, body: str, data: dict[str, Any] | None = None) -> int:
    """Push a notification to every registered device. Returns the delivered
    count; 0 when disabled, unconfigured or on any failure (never raises)."""
    settings = getattr(ctx, "settings", None)
    if settings is None or not getattr(settings, "push_notifications_enabled", False):
        return 0
    user_store = getattr(ctx, "user_store", None)
    if user_store is None:
        return 0
    tokens = await user_store.list_push_tokens()
    if not tokens:
        return 0
    return await _deliver(ctx, tokens, title, body, data or {})


async def send_push_to_user(
    ctx: Any,
    user_id: str | None,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    channel: str | None = None,
) -> int:
    """Push a notification to ONE user's devices (private events only).

    Chat answers contain the user's own legal matters, so unlike the
    broadcast ``send_push`` (public legal-news alerts) this never targets
    other accounts' devices. Never raises; returns the delivered count.
    """
    if not user_id:
        return 0
    settings = getattr(ctx, "settings", None)
    if settings is None or not getattr(settings, "push_notifications_enabled", False):
        return 0
    user_store = getattr(ctx, "user_store", None)
    if user_store is None:
        return 0
    tokens = await user_store.list_push_tokens_for_user(user_id)
    if not tokens:
        return 0
    return await _deliver(ctx, tokens, title, body, data or {}, channel)
