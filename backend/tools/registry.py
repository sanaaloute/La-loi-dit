"""Tool registry: single entrypoint the agent graph uses to discover and
invoke tools. Every tool module exposes ``TOOL_SPEC`` and an async
``run(**kwargs)``; all tool modules are stdlib-only at import time so the
registry always loads, even offline without optional dependencies.
"""

from __future__ import annotations

from typing import Any, Callable

from backend.tools import (
    calculator,
    csv_analysis,
    currency,
    date_tool,
    doc_compare,
    legal_search,
    ocr,
    pdf_parser,
    table,
)

_MODULES = [
    calculator,
    date_tool,
    currency,
    pdf_parser,
    ocr,
    legal_search,
    table,
    csv_analysis,
    doc_compare,
]

TOOL_REGISTRY: dict[str, Callable] = {m.TOOL_SPEC["name"]: m.run for m in _MODULES}
_TOOL_SPECS: dict[str, dict] = {m.TOOL_SPEC["name"]: m.TOOL_SPEC for m in _MODULES}


def list_tools() -> list[dict]:
    """Return the TOOL_SPEC of every registered tool."""
    return list(_TOOL_SPECS.values())


async def call_tool(name: str, **kwargs) -> Any:
    """Invoke a registered tool by name. Raises ValueError for unknown tools."""
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        raise ValueError(f"unknown tool: {name!r} (available: {sorted(TOOL_REGISTRY)})")
    return await fn(**kwargs)
