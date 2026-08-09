"""Pyodide (WebAssembly) sandbox adapter — same interface as
:func:`backend.sandbox.python_sandbox.safe_exec`.

Disabled by default. Enable with the ``pyodide_enabled`` setting (env var
``LEGAL_AI_PYODIDE_ENABLED=1``) or ``settings.extras["pyodide_enabled"] =
True``. Until then, and whenever the Pyodide runtime is not actually
available, calls raise ``SandboxError("not configured ...")`` and no code
is executed.
"""

from __future__ import annotations

from typing import Any, Optional

from backend.core.exceptions import SandboxError
from backend.sandbox.python_sandbox import SandboxResult


def _is_enabled(settings: Optional[Any]) -> bool:
    # ``pyodide_enabled`` is a pydantic setting, so LEGAL_AI_PYODIDE_ENABLED
    # is already parsed (1/true/yes) by the time it reaches us.
    if settings is not None and getattr(settings, "pyodide_enabled", False):
        return True
    extras = getattr(settings, "extras", None) if settings is not None else None
    return bool(isinstance(extras, dict) and extras.get("pyodide_enabled"))


async def safe_exec(code: str, timeout: Optional[float] = None, settings: Optional[Any] = None) -> SandboxResult:
    """Execute ``code`` inside a Pyodide runtime (when configured).

    ``timeout`` defaults to ``settings.sandbox_timeout_seconds``.
    """
    if timeout is None:
        timeout = getattr(settings, "sandbox_timeout_seconds", 5.0) if settings is not None else 5.0
    if not _is_enabled(settings):
        raise SandboxError(
            "pyodide sandbox not configured: set LEGAL_AI_PYODIDE_ENABLED=1 "
            "or settings.extras['pyodide_enabled']=True to enable"
        )
    raise SandboxError(
        "pyodide sandbox not configured: no Pyodide runtime is installed in this environment"
    )
