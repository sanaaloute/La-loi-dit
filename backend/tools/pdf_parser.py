"""PDF text extraction tool. Uses ``pypdf`` via lazy import so the tool
registry stays importable even when pypdf is missing; failures return a
graceful error payload instead of raising.
"""

from __future__ import annotations

from typing import Optional

from backend.core.config import get_settings


def _default_max_pages() -> int:
    return get_settings().pdf_parser_max_pages


TOOL_SPEC = {
    "name": "pdf_parser",
    "description": "Extract plain text from a PDF file (path on the local filesystem).",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the PDF file."},
            "max_pages": {"type": "integer", "description": "Maximum number of pages to read.", "default": _default_max_pages()},
        },
        "required": ["path"],
    },
}


async def run(path: str, max_pages: Optional[int] = None) -> dict:
    """TOOL entrypoint: extract text from a PDF file."""
    if max_pages is None:
        max_pages = _default_max_pages()
    try:
        from pypdf import PdfReader  # lazy: heavy optional dependency
    except Exception as exc:
        return {"success": False, "error": f"pypdf is not installed: {exc}"}
    try:
        reader = PdfReader(path)
        pages = []
        for i, page in enumerate(reader.pages):
            if i >= max_pages:
                break
            pages.append(page.extract_text() or "")
        return {
            "success": True,
            "path": path,
            "pages_read": len(pages),
            "total_pages": len(reader.pages),
            "text": "\n\n".join(pages),
        }
    except Exception as exc:
        return {"success": False, "error": f"failed to parse PDF: {exc}", "path": path}
