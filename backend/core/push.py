"""Expo Push delivery for freshness ("Nouveautés") events.

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

    messages = [
        {
            "to": token,
            "sound": "default",
            "title": title,
            "body": body[:180],
            "data": data or {},
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
        elif ticket.get("details", {}).get("error") == "DeviceNotRegistered":
            await user_store.delete_push_token_unscoped(token)
    logger.info("push sent: %d/%d delivered", delivered, len(tokens))
    return delivered
