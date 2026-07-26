"""E2B (cloud micro-VM) sandbox adapter — same interface as
:func:`backend.sandbox.python_sandbox.safe_exec`.

Disabled by default. Enable with env flag ``LEGAL_AI_E2B_ENABLED=1`` (plus
``E2B_API_KEY``) or ``settings.extras["e2b_enabled"] = True``. Until then,
and whenever the ``e2b`` SDK is not installed, calls raise
``SandboxError("not configured ...")`` and no code is executed.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from backend.core.exceptions import SandboxError
from backend.sandbox.python_sandbox import SandboxResult


def _is_enabled(settings: Optional[Any]) -> bool:
    if os.environ.get("LEGAL_AI_E2B_ENABLED", "").lower() in ("1", "true", "yes"):
        return True
    extras = getattr(settings, "extras", None) if settings is not None else None
    return bool(isinstance(extras, dict) and extras.get("e2b_enabled"))


async def safe_exec(code: str, timeout: float = 5.0, settings: Optional[Any] = None) -> SandboxResult:
    """Execute ``code`` in an E2B cloud sandbox (when configured)."""
    if not _is_enabled(settings):
        raise SandboxError(
            "e2b sandbox not configured: set LEGAL_AI_E2B_ENABLED=1 (+ E2B_API_KEY) "
            "or settings.extras['e2b_enabled']=True to enable"
        )
    try:
        import e2b  # noqa: F401  (lazy: heavy optional dependency)
    except Exception as exc:
        raise SandboxError(f"e2b sandbox not configured: e2b SDK unavailable ({exc})") from exc
    raise SandboxError("e2b sandbox not configured: no E2B_API_KEY / template provisioned")
