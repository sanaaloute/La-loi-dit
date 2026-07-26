"""Date tool: today's date, date arithmetic and parsing of French date
formats (numeric ``31/12/2025`` style plus French month names such as
``1er janvier 2025`` / ``15 août 2025``). Fully offline (stdlib only).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional

TOOL_SPEC = {
    "name": "date_tool",
    "description": "Get today's date, add/subtract days, or parse French date strings.",
    "parameters": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["today", "add", "parse"],
                "description": "today: current date; add: date + days; parse: parse a FR date string.",
            },
            "date": {"type": "string", "description": "Base date (for add) or text to parse (for parse)."},
            "days": {"type": "integer", "description": "Days to add (negative to subtract)."},
        },
        "required": ["operation"],
    },
}

_FR_MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
}

_NUMERIC_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%y")


def parse_fr_date(text: str) -> date:
    """Parse a French date string into a ``date``. Raises ValueError."""
    cleaned = text.strip().lower()
    cleaned = re.sub(r"\b1er\b", "1", cleaned)
    for fmt in _NUMERIC_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            pass
    m = re.match(r"^(\d{1,2})\s+([a-zàâäéèêëîïôöùûüç]+)\s+(\d{4})$", cleaned)
    if m and m.group(2) in _FR_MONTHS:
        return date(int(m.group(3)), _FR_MONTHS[m.group(2)], int(m.group(1)))
    raise ValueError(f"unparseable date: {text!r}")


async def run(operation: str, date: Optional[str] = None, days: int = 0) -> dict:
    """TOOL entrypoint for date operations."""
    try:
        if operation == "today":
            return {"success": True, "date": date_today().isoformat(), "weekday": date_today().strftime("%A")}
        if operation == "parse":
            if not date:
                raise ValueError("'date' argument is required for parse")
            parsed = parse_fr_date(date)
            return {"success": True, "date": parsed.isoformat(), "weekday": parsed.strftime("%A")}
        if operation == "add":
            base = parse_fr_date(date) if date else date_today()
            result = base + timedelta(days=int(days))
            return {
                "success": True,
                "base": base.isoformat(),
                "days": int(days),
                "date": result.isoformat(),
                "weekday": result.strftime("%A"),
            }
        return {"success": False, "error": f"unknown operation: {operation}"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def date_today() -> date:
    return datetime.now().date()
