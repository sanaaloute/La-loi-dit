"""Temporal client factory — returns None when Temporal is off or unreachable."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from backend.core.config import Settings


async def get_temporal_client(settings: Settings) -> Optional[Any]:
    """Connect to the Temporal server, or return None when disabled/unreachable."""
    if not settings.temporal_enabled:
        return None
    try:
        from temporalio.client import Client
    except Exception:
        return None
    try:
        return await asyncio.wait_for(
            Client.connect(settings.temporal_address, namespace=settings.temporal_namespace),
            timeout=settings.temporal_connect_timeout_seconds,
        )
    except Exception:
        return None
