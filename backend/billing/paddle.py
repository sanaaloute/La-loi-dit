"""Paddle Billing provider.

API notes (Paddle Billing, not Classic):
- Base URLs: https://sandbox-api.paddle.com / https://api.paddle.com.
- Checkout: POST /transactions with items[price_id], custom_data and an
  optional checkout.url override; the hosted checkout URL comes back at
  data.checkout.url.
- Webhooks: the Paddle-Signature header is "ts=...,h1=..."; the signed
  payload is "{ts}:{raw_body}" with HMAC-SHA256(webhook secret).

Every payload assumption is isolated in a small helper so a dashboard-side
schema difference is a one-line fix.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any, Optional

import httpx

from backend.billing.base import BillingEvent
from backend.core.config import Settings
from backend.core.exceptions import LegalAIError

logger = logging.getLogger(__name__)

_API_BASES = {
    "sandbox": "https://sandbox-api.paddle.com",
    "production": "https://api.paddle.com",
}

_WEBHOOK_TS_TOLERANCE_SECONDS = 300  # reject stale signatures (> 5 min)


class BillingUnavailableError(LegalAIError):
    """The billing provider could not create a checkout."""


class PaddleProvider:
    """Paddle Billing implementation of the BillingProvider protocol."""

    name = "paddle"

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _api_base(settings: Settings) -> str:
        return _API_BASES.get(settings.paddle_env, _API_BASES["sandbox"])

    @staticmethod
    def _price_for_tier(tier: str, settings: Settings) -> str:
        return {
            "pro": settings.paddle_price_pro,
            "cabinet": settings.paddle_price_cabinet,
        }.get(tier, "")

    @staticmethod
    def _tier_for_price(price_id: str, settings: Settings) -> str:
        if price_id and price_id == settings.paddle_price_pro:
            return "pro"
        if price_id and price_id == settings.paddle_price_cabinet:
            return "cabinet"
        return ""

    # ------------------------------------------------------------------
    # Checkout
    # ------------------------------------------------------------------

    async def create_checkout(self, *, user_id: str, email: str, tier: str, settings: Settings) -> str:
        """Create a Paddle transaction; return its hosted checkout URL."""
        price_id = self._price_for_tier(tier, settings)
        if not price_id:
            raise BillingUnavailableError(f"no Paddle price configured for tier '{tier}'")
        payload = {
            "items": [{"price_id": price_id, "quantity": 1}],
            "custom_data": {"user_id": user_id},
            "customer": {"email": email},
            "checkout": {"url": settings.paddle_checkout_success_url},
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{self._api_base(settings)}/transactions",
                    json=payload,
                    headers={"Authorization": f"Bearer {settings.paddle_api_key}"},
                )
                response.raise_for_status()
                data = response.json().get("data") or {}
        except BillingUnavailableError:
            raise
        except Exception as exc:
            raise BillingUnavailableError(f"Paddle checkout creation failed: {exc}") from exc
        checkout_url = (data.get("checkout") or {}).get("url") or ""
        if not checkout_url:
            raise BillingUnavailableError("Paddle response carried no checkout URL")
        return checkout_url

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    def verify_webhook(self, body: bytes, signature_header: str, settings: Settings) -> bool:
        """Verify Paddle-Signature: ts + HMAC-SHA256("{ts}:{body}", secret)."""
        if not settings.paddle_webhook_secret or not signature_header:
            return False
        parts = {}
        for chunk in signature_header.split(","):
            key, _, value = chunk.partition("=")
            parts[key.strip()] = value.strip()
        ts_raw, received_hmac = parts.get("ts", ""), parts.get("h1", "")
        if not ts_raw or not received_hmac:
            return False
        try:
            ts = int(ts_raw)
        except ValueError:
            return False
        if abs(time.time() - ts) > _WEBHOOK_TS_TOLERANCE_SECONDS:
            return False  # stale: replay protection
        signed = ts_raw.encode() + b":" + body
        expected = hmac.new(settings.paddle_webhook_secret.encode(), signed, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, received_hmac)

    # -- payload shape helpers (isolated assumptions) --

    @staticmethod
    def _extract_price_id(data: dict[str, Any]) -> str:
        """First item's price id — items may carry `price_id` or `price.id`."""
        items = data.get("items") or []
        if items and isinstance(items[0], dict):
            first = items[0]
            return first.get("price_id") or (first.get("price") or {}).get("id") or ""
        return ""

    @staticmethod
    def _extract_period_end(data: dict[str, Any]) -> str:
        period = data.get("current_billing_period") or {}
        return period.get("ends_at") or ""

    @staticmethod
    def _extract_cancel_at_period_end(data: dict[str, Any]) -> bool:
        scheduled = data.get("scheduled_change") or {}
        return scheduled.get("action") == "cancel"

    def normalize_webhook(self, payload: dict[str, Any], settings: Settings) -> Optional[BillingEvent]:
        raw_type = str(payload.get("event_type") or "")
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            return None

        event_type = {
            "transaction.completed": "activated",
            "subscription.activated": "activated",
            "subscription.updated": "renewed",
            "subscription.canceled": "canceled",
            "subscription.past_due": "past_due",
            "transaction.refunded": "refunded",
        }.get(raw_type)
        if event_type is None:
            return None  # unknown event: ignored upstream with a 200

        custom_data = data.get("custom_data") or {}
        is_subscription = raw_type.startswith("subscription.")
        return BillingEvent(
            event_type=event_type,
            user_id=str(custom_data.get("user_id") or ""),
            tier=self._tier_for_price(self._extract_price_id(data), settings),
            customer_id=str(data.get("customer_id") or ""),
            subscription_id=str(data.get("id") or "") if is_subscription else str(data.get("subscription_id") or ""),
            current_period_end=self._extract_period_end(data),
            cancel_at_period_end=self._extract_cancel_at_period_end(data),
            raw_type=raw_type,
            raw=data,
        )
