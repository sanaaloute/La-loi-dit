"""Legal search tool: thin wrapper around the platform retriever.

The retriever is any async callable ``fn(query: str, top_k: int) -> list``
returning evidence-like objects or dicts. Register it once at startup with
:func:`set_retriever` (or pass it per call); without one the tool reports
a graceful "not configured" error.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

TOOL_SPEC = {
    "name": "legal_search",
    "description": "Search Burkina Faso legal sources (laws, decrees, OHADA, case law) via the platform retriever.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query (French recommended)."},
            "top_k": {"type": "integer", "description": "Maximum number of results.", "default": 8},
        },
        "required": ["query"],
    },
}

_retriever: Optional[Callable] = None


def set_retriever(fn: Callable) -> None:
    """Register the platform retriever callable used by this tool."""
    global _retriever
    _retriever = fn


def _serialize(item: Any) -> dict:
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json")
    if isinstance(item, dict):
        return item
    return {"content": str(item)}


async def run(query: str, top_k: int = 8, retriever: Optional[Callable] = None) -> dict:
    """TOOL entrypoint: run a legal search through the retriever."""
    fn = retriever or _retriever
    if fn is None:
        return {"success": False, "error": "legal_search not configured: no retriever registered"}
    try:
        result = fn(query, top_k=top_k)
        items = await result if inspect.isawaitable(result) else result
        results = [_serialize(it) for it in (items or [])]
        return {"success": True, "query": query, "count": len(results), "results": results}
    except Exception as exc:
        return {"success": False, "error": f"legal search failed: {exc}", "query": query}
