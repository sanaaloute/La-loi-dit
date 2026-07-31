"""Billing service: apply provider-neutral events to user accounts.

Idempotent by construction: applying the same event twice writes the same
state (tier/status/period end are absolute values, not deltas).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from backend.billing.base import BillingEvent

logger = logging.getLogger(__name__)


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def _resolve_user_id(user_store: Any, event: BillingEvent) -> str:
    """Event user_id, else look the account up by provider customer id."""
    if event.user_id:
        return event.user_id
    if event.customer_id:
        try:
            record = await user_store.get_by_paddle_customer_id(event.customer_id)
            if record is not None:
                return record.id
        except Exception:
            pass
    return ""


async def apply_billing_event(user_store: Any, event: BillingEvent) -> bool:
    """Apply one BillingEvent to the user's subscription state.

    Rules:
    - activated/renewed -> tier = event.tier (keep current when the event
      carries no resolvable tier), status "active";
    - canceled -> keep the tier until `current_period_end` when it lies in
      the future, otherwise downgrade to "gratuit"; status "canceled";
    - past_due -> keep the tier, status "past_due";
    - refunded -> downgrade to "gratuit", status "refunded".

    Returns True when the event was applied to a known account.
    """
    if user_store is None:
        return False
    user_id = await _resolve_user_id(user_store, event)
    if not user_id:
        logger.warning("billing event %s: no matching account", event.raw_type)
        return False
    record = await user_store.get_by_id(user_id)
    if record is None:
        logger.warning("billing event %s: unknown user %s", event.raw_type, user_id)
        return False

    tier, status = record.tier, record.subscription_status
    if event.event_type in ("activated", "renewed"):
        tier = event.tier or record.tier
        status = "active"
    elif event.event_type == "canceled":
        status = "canceled"
        period_end = _parse_iso(event.current_period_end)
        if period_end is None or period_end <= datetime.now(timezone.utc):
            tier = "gratuit"  # period over (or unknown): downgrade now
        # else: paid until period end -> keep the tier until then
    elif event.event_type == "past_due":
        status = "past_due"  # keep the tier while dunning runs
    elif event.event_type == "refunded":
        tier = "gratuit"
        status = "refunded"

    await user_store.set_billing_state(
        user_id,
        tier=tier,
        status=status,
        customer_id=event.customer_id or None,
        subscription_id=event.subscription_id or None,
        period_end=event.current_period_end or None,
        cancel_at_period_end=event.cancel_at_period_end,
    )
    logger.info(
        "billing event applied",
        extra={"event": event.raw_type, "user_id": user_id, "tier": tier, "status": status},
    )
    return True


def get_subscription_info(record: Any) -> dict[str, Any]:
    """Subscription view for the API from a UserRecord."""
    return {
        "tier": record.tier,
        "status": record.subscription_status or "none",
        "current_period_end": record.subscription_period_end or None,
        "cancel_at_period_end": bool(record.subscription_cancel_at_period_end),
    }
