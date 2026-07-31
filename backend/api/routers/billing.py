"""Billing router: checkout, subscription status and Paddle webhooks.

Billing is optional: with ``paddle_enabled=false`` (default) the platform
works exactly as before and only ``/billing/config`` and
``/billing/subscription`` stay meaningful. The webhook endpoint is public by
design — authenticity comes from the HMAC signature over the raw body, not
from a bearer token (the only middleware in front of it is the rate limiter,
which does not block it).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.api.deps import get_ctx, require_user
from backend.billing.base import BillingProvider
from backend.billing.paddle import BillingUnavailableError, PaddleProvider
from backend.billing.service import apply_billing_event, get_subscription_info
from backend.security.jwt import TokenPayload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])

_PAID_TIERS = ("pro", "cabinet")


class CheckoutRequest(BaseModel):
    tier: str


def _provider() -> BillingProvider:
    """The configured billing provider (Paddle; CinetPay plugs in here)."""
    return PaddleProvider()


@router.get("/config")
async def billing_config(request: Request) -> dict[str, Any]:
    """Public billing advertisement for the frontend pricing page."""
    settings = get_ctx(request).settings
    return {
        "enabled": settings.billing_enabled,
        "provider": "paddle" if settings.paddle_enabled else None,
    }


@router.post("/checkout")
async def create_checkout(
    payload: CheckoutRequest,
    request: Request,
    user: TokenPayload = Depends(require_user),
) -> dict[str, Any]:
    """Create a hosted checkout session for a paid tier."""
    settings = get_ctx(request).settings
    if not settings.billing_enabled:
        raise HTTPException(
            status_code=503,
            detail="Le module de paiement n'est pas configuré pour le moment.",
        )
    tier = payload.tier.strip().lower()
    if tier not in _PAID_TIERS:
        raise HTTPException(
            status_code=400,
            detail="Offre invalide : choisissez 'pro' ou 'cabinet'.",
        )

    user_store = getattr(get_ctx(request), "user_store", None)
    record = None
    if user_store is not None and user.user_id:
        try:
            record = await user_store.get_by_id(user.user_id)
        except Exception:
            record = None
    if record is None:
        raise HTTPException(status_code=400, detail="Compte utilisateur introuvable.")

    try:
        checkout_url = await _provider().create_checkout(
            user_id=record.id, email=record.email, tier=tier, settings=settings
        )
    except BillingUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail="Le paiement est momentanément indisponible, veuillez réessayer.",
        ) from exc
    return {"checkout_url": checkout_url}


@router.get("/subscription")
async def subscription_status(
    request: Request,
    user: TokenPayload = Depends(require_user),
) -> dict[str, Any]:
    """Current subscription state (status "none" when billing is disabled)."""
    user_store = getattr(get_ctx(request), "user_store", None)
    record = None
    if user_store is not None and user.user_id:
        try:
            record = await user_store.get_by_id(user.user_id)
        except Exception:
            record = None
    if record is None:
        # Dev-store principal: synthesize from the token claims.
        return {
            "tier": user.tier,
            "status": "none",
            "current_period_end": None,
            "cancel_at_period_end": False,
        }
    return get_subscription_info(record)


@router.post("/webhook")
async def paddle_webhook(request: Request) -> dict[str, Any]:
    """Receive Paddle webhooks (HMAC-verified, unauthenticated by design).

    Always 200 on well-formed deliveries — Paddle retries non-2xx, so only
    signature/shape problems and the disabled switch get error codes.
    """
    settings = get_ctx(request).settings
    if not settings.billing_enabled:
        raise HTTPException(status_code=503, detail="Billing webhook not configured.")

    body = await request.body()  # raw body required for the HMAC
    signature = request.headers.get("paddle-signature", "")
    provider = _provider()
    if not provider.verify_webhook(body, signature, settings):
        raise HTTPException(status_code=401, detail="Signature invalide.")

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Payload JSON invalide.")

    try:
        event = provider.normalize_webhook(payload, settings)
    except Exception:  # unknown shape: log and ack, never 500
        logger.warning("billing webhook: unparseable payload", exc_info=True)
        return {"received": True, "ignored": "unparseable"}
    if event is None:
        return {"received": True, "ignored": payload.get("event_type", "unknown")}

    try:
        applied = await apply_billing_event(get_ctx(request).user_store, event)
    except Exception:
        logger.exception("billing webhook: failed to apply %s", event.raw_type)
        return {"received": True, "ignored": "apply-failed"}
    return {"received": True, "applied": applied}
