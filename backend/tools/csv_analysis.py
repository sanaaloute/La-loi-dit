"""CSV analysis tool: summary statistics over CSV text using only the
stdlib (``csv`` + ``statistics``). Numeric columns get count/mean/median/
min/max/stdev; other columns get row count and distinct-value counts.
"""

from __future__ import annotations

import csv
import io
import statistics
from typing import Optional

TOOL_SPEC = {
    "name": "csv_analysis",
    "description": "Compute summary statistics (count, mean, min, max, stdev) for columns of CSV text.",
    "parameters": {
        "type": "object",
        "properties": {
            "csv_text": {"type": "string", "description": "Raw CSV content (with header row)."},
            "delimiter": {"type": "string", "description": "Column delimiter.", "default": ","},
        },
        "required": ["csv_text"],
    },
}


def _to_float(value: str) -> Optional[float]:
    try:
        return float(value.replace(" ", "").replace(",", "."))
    except (ValueError, AttributeError):
        return None


async def run(csv_text: str, delimiter: str = ",") -> dict:
    """TOOL entrypoint: summarize CSV text column by column."""
    try:
        reader = csv.DictReader(io.StringIO(csv_text), delimiter=delimiter)
        rows = list(reader)
        if not rows or reader.fieldnames is None:
            return {"success": False, "error": "no rows or header found in CSV text"}

        columns: dict[str, dict] = {}
        for field in reader.fieldnames:
            raw = [(r.get(field) or "").strip() for r in rows]
            numbers = [v for v in (_to_float(x) for x in raw if x) if v is not None]
            if numbers and len(numbers) >= max(1, len([x for x in raw if x]) // 2):
                columns[field] = {
                    "type": "numeric",
                    "count": len(numbers),
                    "mean": round(statistics.fmean(numbers), 4),
                    "median": round(statistics.median(numbers), 4),
                    "min": min(numbers),
                    "max": max(numbers),
                    "stdev": round(statistics.stdev(numbers), 4) if len(numbers) > 1 else 0.0,
                }
            else:
                columns[field] = {
                    "type": "text",
                    "count": len([x for x in raw if x]),
                    "distinct": len(set(raw)),
                    "samples": sorted(set(raw))[:5],
                }
        return {"success": True, "rows": len(rows), "columns": columns}
    except Exception as exc:
        return {"success": False, "error": f"CSV analysis failed: {exc}"}
