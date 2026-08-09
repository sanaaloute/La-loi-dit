"""E2B (cloud micro-VM) sandbox adapter — same interface as
:func:`backend.sandbox.python_sandbox.safe_exec`.

Disabled by default. Enable with the ``e2b_enabled`` setting (env var
``LEGAL_AI_E2B_ENABLED=1``, plus ``E2B_API_KEY``) or
``settings.extras["e2b_enabled"] = True``. Until then, and whenever the
``e2b`` SDK is not installed, calls raise ``SandboxError("not configured
...")`` and no code is executed.
"""

from __future__ import annotations

from typing import Any, Optional

from backend.core.exceptions import SandboxError
from backend.sandbox.python_sandbox import SandboxResult


def _is_enabled(settings: Optional[Any]) -> bool:
    # ``e2b_enabled`` is a pydantic setting, so LEGAL_AI_E2B_ENABLED is
    # already parsed (1/true/yes) by the time it reaches us.
    if settings is not None and getattr(settings, "e2b_enabled", False):
        return True
    extras = getattr(settings, "extras", None) if settings is not None else None
    return bool(isinstance(extras, dict) and extras.get("e2b_enabled"))


async def safe_exec(code: str, timeout: Optional[float] = None, settings: Optional[Any] = None) -> SandboxResult:
    """Execute ``code`` in an E2B cloud sandbox (when configured).

    ``timeout`` defaults to ``settings.sandbox_timeout_seconds``.
    """
    if timeout is None:
        timeout = getattr(settings, "sandbox_timeout_seconds", 5.0) if settings is not None else 5.0
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
