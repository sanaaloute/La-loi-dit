"""Global registry of agent tools."""

from __future__ import annotations

from typing import Any

from backend.agents.tools.base import ToolImpl, ToolSpec, get_tool_spec

_TOOL_REGISTRY: dict[str, ToolImpl] = {}
_TOOL_SPECS: dict[str, ToolSpec] = {}


def register_tool(fn: ToolImpl) -> ToolImpl:
    """Register a @tool-decorated function in the global registry."""
    spec = get_tool_spec(fn)
    _TOOL_REGISTRY[spec.name] = fn
    _TOOL_SPECS[spec.name] = spec
    return fn


def get_tool(name: str) -> ToolImpl:
    """Return a registered tool implementation or raise ValueError."""
    impl = _TOOL_REGISTRY.get(name)
    if impl is None:
        raise ValueError(
            f"unknown tool: {name!r} (available: {sorted(_TOOL_REGISTRY)})"
        )
    return impl


def list_tools() -> list[ToolSpec]:
    """Return the specs of all registered tools."""
    return list(_TOOL_SPECS.values())


# Convenience alias for tests / external code.
TOOL_REGISTRY: dict[str, ToolImpl] = _TOOL_REGISTRY


def tool_spec(name: str) -> ToolSpec:
    """Return the spec of a registered tool."""
    return _TOOL_SPECS[name]
