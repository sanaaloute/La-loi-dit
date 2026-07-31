"""Subscription billing: provider abstraction + Paddle implementation.

The platform is fully functional with billing disabled (default). Providers
plug into ``BillingProvider`` — Paddle today, CinetPay later.
"""

from backend.billing.base import BillingEvent, BillingProvider
from backend.billing.service import apply_billing_event, get_subscription_info

__all__ = ["BillingEvent", "BillingProvider", "apply_billing_event", "get_subscription_info"]
