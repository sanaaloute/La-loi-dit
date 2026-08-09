"""Agent tools: typed, traceable, sandbox-free functions."""

from __future__ import annotations

from backend.agents.tools.base import (
    ToolCall,
    ToolImpl,
    ToolResult,
    ToolSpec,
    as_function_schema,
    execute_tool_call,
    execute_tool_calls,
    get_tool_spec,
    tool,
)
from backend.agents.tools.registry import (
    TOOL_REGISTRY,
    get_tool,
    list_tools,
    register_tool,
)

# Import tool modules so their @tool-decorated functions register themselves.
from backend.agents.tools import guardrails, generation, planning, retrieval, verification

__all__ = [
    "ToolCall",
    "ToolImpl",
    "ToolResult",
    "ToolSpec",
    "as_function_schema",
    "execute_tool_call",
    "execute_tool_calls",
    "get_tool_spec",
    "tool",
    "TOOL_REGISTRY",
    "get_tool",
    "list_tools",
    "register_tool",
]
