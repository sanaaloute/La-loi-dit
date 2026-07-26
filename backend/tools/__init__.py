"""Tool subsystem: small, offline-first, deterministic tools callable by
the agent graph through :mod:`backend.tools.registry`.

Each tool module exposes an async ``run(...)`` and a ``TOOL_SPEC`` dict
describing name, description and JSON-schema-ish parameters.
"""

from backend.tools.registry import TOOL_REGISTRY, call_tool, list_tools

__all__ = ["TOOL_REGISTRY", "call_tool", "list_tools"]
