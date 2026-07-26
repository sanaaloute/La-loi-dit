"""Pyodide (WebAssembly) sandbox adapter — same interface as
:func:`backend.sandbox.python_sandbox.safe_exec`.

Disabled by default. Enable with env flag ``LEGAL_AI_PYODIDE_ENABLED=1``
or ``settings.extras["pyodide_enabled"] = True``. Until then, and whenever
the Pyodide runtime is not actually available, calls raise
``SandboxError("not configured ...")`` and no code is executed.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from backend.core.exceptions import SandboxError
from backend.sandbox.python_sandbox import SandboxResult


def _is_enabled(settings: Optional[Any]) -> bool:
    if os.environ.get("LEGAL_AI_PYODIDE_ENABLED", "").lower() in ("1", "true", "yes"):
        return True
    extras = getattr(settings, "extras", None) if settings is not None else None
    return bool(isinstance(extras, dict) and extras.get("pyodide_enabled"))


async def safe_exec(code: str, timeout: float = 5.0, settings: Optional[Any] = None) -> SandboxResult:
    """Execute ``code`` inside a Pyodide runtime (when configured)."""
    if not _is_enabled(settings):
        raise SandboxError(
            "pyodide sandbox not configured: set LEGAL_AI_PYODIDE_ENABLED=1 "
            "or settings.extras['pyodide_enabled']=True to enable"
        )
    raise SandboxError(
        "pyodide sandbox not configured: no Pyodide runtime is installed in this environment"
    )
