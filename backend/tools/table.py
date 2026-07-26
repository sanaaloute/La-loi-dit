"""Markdown table builder: turns headers + rows into a GitHub-flavored
markdown table (used to present comparisons, fee schedules, deadlines…).
"""

from __future__ import annotations

TOOL_SPEC = {
    "name": "table",
    "description": "Build a markdown table from headers and rows.",
    "parameters": {
        "type": "object",
        "properties": {
            "headers": {"type": "array", "items": {"type": "string"}},
            "rows": {"type": "array", "items": {"type": "array"}},
        },
        "required": ["headers", "rows"],
    },
}


def build_markdown_table(headers: list, rows: list[list]) -> str:
    """Render headers + rows as a markdown table."""
    cols = [str(h) for h in headers]

    def cell(value) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ").strip()

    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for row in rows:
        padded = [cell(v) for v in list(row)[: len(cols)]]
        padded += [""] * (len(cols) - len(padded))
        lines.append("| " + " | ".join(padded) + " |")
    return "\n".join(lines)


async def run(headers: list, rows: list[list]) -> dict:
    """TOOL entrypoint: build a markdown table."""
    try:
        table = build_markdown_table(headers, rows)
        return {"success": True, "markdown": table, "rows": len(rows)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
