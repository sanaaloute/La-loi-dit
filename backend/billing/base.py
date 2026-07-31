"""Billing provider abstraction — the CinetPay extension point.

One file per provider (see ``paddle.py``). A provider knows how to create a
checkout session and how to normalize its webhooks into the provider-neutral
``BillingEvent`` below; everything downstream (tier updates, persistence)
is provider-agnostic (see ``service.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from backend.core.config import Settings


@dataclass
class BillingEvent:
    """Provider-neutral subscription event."""

    event_type: str  # activated | renewed | canceled | past_due | refunded
    user_id: str = ""  # internal user id (may be empty -> resolve by customer_id)
    tier: str = ""
    customer_id: str = ""
    subscription_id: str = ""
    current_period_end: str = ""  # ISO date-time, "" when unknown
    cancel_at_period_end: bool = False
    raw_type: str = ""  # original provider event name
    raw: dict[str, Any] = field(default_factory=dict)


class BillingProvider(Protocol):
    """Contract every payment provider implements."""

    name: str

    async def create_checkout(self, *, user_id: str, email: str, tier: str, settings: Settings) -> str:
        """Create a checkout session for `tier`; return the hosted checkout URL."""
        ...

    def verify_webhook(self, body: bytes, signature_header: str, settings: Settings) -> bool:
        """Verify the webhook signature over the RAW request body."""
        ...

    def normalize_webhook(self, payload: dict[str, Any], settings: Settings) -> Optional[BillingEvent]:
        """Map a decoded webhook payload to a BillingEvent (None = ignore)."""
        ...
