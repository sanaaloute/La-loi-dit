"""Document comparison tool: unified diff of two texts (e.g. two versions
of a law or contract) using stdlib ``difflib``.
"""

from __future__ import annotations

import difflib

TOOL_SPEC = {
    "name": "doc_compare",
    "description": "Compare two texts and return a unified diff (e.g. two versions of a legal document).",
    "parameters": {
        "type": "object",
        "properties": {
            "text_a": {"type": "string", "description": "Original text."},
            "text_b": {"type": "string", "description": "New text."},
            "label_a": {"type": "string", "default": "version A"},
            "label_b": {"type": "string", "default": "version B"},
        },
        "required": ["text_a", "text_b"],
    },
}


async def run(text_a: str, text_b: str, label_a: str = "version A", label_b: str = "version B") -> dict:
    """TOOL entrypoint: unified diff between two texts."""
    try:
        diff_lines = list(
            difflib.unified_diff(
                text_a.splitlines(),
                text_b.splitlines(),
                fromfile=label_a,
                tofile=label_b,
                lineterm="",
            )
        )
        changed = sum(1 for l in diff_lines if l.startswith(("+", "-")) and not l.startswith(("+++", "---")))
        return {
            "success": True,
            "identical": not diff_lines,
            "changed_lines": changed,
            "diff": "\n".join(diff_lines),
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
