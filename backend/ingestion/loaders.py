"""Document loaders: PDF, DOCX, HTML, plain text and Markdown.

All third-party imports (pypdf, python-docx, BeautifulSoup, pytesseract,
Pillow, httpx) happen lazily inside the loader functions so this module
imports cleanly even when optional dependencies are missing. Loaders raise
:class:`backend.core.exceptions.IngestionError` on unreadable files.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Optional, Union

from pydantic import BaseModel, Field

from backend.core.exceptions import IngestionError

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

#: Extensions dispatched by :func:`load_any`.
SUPPORTED_EXTENSIONS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".html": "html",
    ".htm": "html",
    ".txt": "txt",
    ".md": "markdown",
    ".markdown": "markdown",
}


class ExtractedDocument(BaseModel):
    """Raw text extracted from a source document, before cleaning/chunking."""

    name: str
    text: str = ""
    pages: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _read_text_file(path: Path) -> str:
    """Read a text file, tolerating common encodings (UTF-8 first)."""
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _ocr_available() -> bool:
    """True when pytesseract and Pillow are both importable."""
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return True


def load_pdf(path: Union[str, Path]) -> ExtractedDocument:
    """Extract per-page text from a PDF via pypdf.

    Pages that yield no text are recorded in ``metadata["ocr_needed_pages"]``.
    When pytesseract and Pillow are importable, OCR over embedded page images
    is attempted best-effort (never fatal) and the outcome noted in metadata.
    """
    p = Path(path)
    if not p.is_file():
        raise IngestionError(f"PDF not found: {p}")
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise IngestionError("pypdf is required to load PDF files") from exc

    try:
        reader = PdfReader(str(p))
        pages: list[str] = []
        for page in reader.pages:
            try:
                pages.append((page.extract_text() or "").strip())
            except Exception:
                pages.append("")  # a broken page must not kill the document
    except IngestionError:
        raise
    except Exception as exc:
        raise IngestionError(f"Failed to read PDF {p}: {exc}") from exc

    metadata: dict[str, Any] = {"loader": "pdf", "page_count": len(pages), "path": str(p)}
    ocr_needed = [i + 1 for i, text in enumerate(pages) if not text]
    if ocr_needed:
        metadata["ocr_needed_pages"] = ocr_needed
        metadata["ocr_available"] = _ocr_available()
        if metadata["ocr_available"]:
            _best_effort_ocr(reader, pages, ocr_needed, metadata)

    return ExtractedDocument(name=p.name, text="\n\n".join(pages), pages=pages, metadata=metadata)


def _best_effort_ocr(reader: Any, pages: list[str], ocr_needed: list[int], metadata: dict[str, Any]) -> None:
    """OCR images embedded in textless pages. Never raises."""
    try:
        import pytesseract
    except ImportError:
        return
    metadata["ocr_attempted"] = True
    ocr_ok: list[int] = []
    for page_no in ocr_needed:
        try:
            fragments = []
            for image_file in reader.pages[page_no - 1].images:
                image = image_file.image  # PIL Image (Pillow present, checked earlier)
                try:
                    fragments.append(pytesseract.image_to_string(image, lang="fra"))
                except Exception:
                    fragments.append(pytesseract.image_to_string(image))
            text = "\n".join(f for f in fragments if f and f.strip()).strip()
            if text:
                pages[page_no - 1] = text
                ocr_ok.append(page_no)
        except Exception:
            continue
    if ocr_ok:
        metadata["ocr_recovered_pages"] = ocr_ok


def load_docx(path: Union[str, Path]) -> ExtractedDocument:
    """Extract paragraphs and table cells from a DOCX via python-docx."""
    p = Path(path)
    if not p.is_file():
        raise IngestionError(f"DOCX not found: {p}")
    try:
        import docx
    except ImportError as exc:
        raise IngestionError("python-docx is required to load DOCX files") from exc

    try:
        document = docx.Document(str(p))
        parts = [para.text for para in document.paragraphs if para.text.strip()]
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                line = " | ".join(c for c in cells if c)
                if line:
                    parts.append(line)
    except Exception as exc:
        raise IngestionError(f"Failed to read DOCX {p}: {exc}") from exc

    text = "\n".join(parts)
    return ExtractedDocument(
        name=p.name,
        text=text,
        pages=[text],  # DOCX has no stable pagination
        metadata={"loader": "docx", "path": str(p)},
    )


def load_html(path_or_url: Union[str, Path], content: Optional[str] = None) -> ExtractedDocument:
    """Extract main text from HTML via BeautifulSoup.

    ``content`` may be supplied directly (e.g. by the crawler). Otherwise a
    local file is read, or an ``http(s)://`` URL is fetched with httpx
    (lazy import); network failures raise :class:`IngestionError`.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise IngestionError("beautifulsoup4 is required to load HTML") from exc

    source = str(path_or_url)
    metadata: dict[str, Any] = {"loader": "html"}
    if content is None:
        if _URL_RE.match(source):
            metadata["url"] = source
            try:
                import httpx
            except ImportError as exc:
                raise IngestionError("httpx is required to fetch HTML URLs") from exc
            try:
                from backend.core.config import get_settings

                timeout = get_settings().ingestion_html_timeout_seconds
                response = httpx.get(source, timeout=timeout, follow_redirects=True)
                response.raise_for_status()
                content = response.text
            except Exception as exc:
                raise IngestionError(f"Failed to fetch {source}: {exc}") from exc
            name = source.rstrip("/").rsplit("/", 1)[-1] or source
        else:
            p = Path(source)
            if not p.is_file():
                raise IngestionError(f"HTML file not found: {p}")
            content = _read_text_file(p)
            name = p.name
            metadata["path"] = str(p)
    else:
        name = Path(source).name if not _URL_RE.match(source) else source
        if _URL_RE.match(source):
            metadata["url"] = source
        else:
            metadata["path"] = source

    try:
        try:
            soup = BeautifulSoup(content, "lxml")
        except Exception:
            soup = BeautifulSoup(content, "html.parser")
        for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
            tag.decompose()
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        if title:
            metadata["title"] = title
        text = soup.get_text("\n")
    except Exception as exc:
        raise IngestionError(f"Failed to parse HTML from {source}: {exc}") from exc

    return ExtractedDocument(name=name, text=text, pages=[text], metadata=metadata)


def load_txt(path: Union[str, Path]) -> ExtractedDocument:
    """Load a plain-text file."""
    p = Path(path)
    if not p.is_file():
        raise IngestionError(f"Text file not found: {p}")
    try:
        text = _read_text_file(p)
    except Exception as exc:
        raise IngestionError(f"Failed to read {p}: {exc}") from exc
    return ExtractedDocument(name=p.name, text=text, pages=[text], metadata={"loader": "txt", "path": str(p)})


def load_markdown(path: Union[str, Path]) -> ExtractedDocument:
    """Load a Markdown file, stripping the most common markup markers."""
    p = Path(path)
    if not p.is_file():
        raise IngestionError(f"Markdown file not found: {p}")
    try:
        raw = _read_text_file(p)
    except Exception as exc:
        raise IngestionError(f"Failed to read {p}: {exc}") from exc
    text = _strip_markdown(raw)
    return ExtractedDocument(
        name=p.name,
        text=text,
        pages=[text],
        metadata={"loader": "markdown", "path": str(p)},
    )


def _strip_markdown(raw: str) -> str:
    """Lightweight Markdown-to-text (headings, links, emphasis, code fences)."""
    text = re.sub(r"```.*?```", lambda m: m.group(0).strip("`"), raw, flags=re.DOTALL)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)  # images -> alt text
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # links -> anchor text
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)  # heading markers
    text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text)
    text = re.sub(r"(\*|_)(.*?)\1", r"\2", text)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)  # bullets
    return text


async def load_any(path: Union[str, Path]) -> ExtractedDocument:
    """Dispatch to the right loader based on file extension.

    Raises :class:`IngestionError` for unsupported extensions or unreadable
    files. Runs the synchronous loaders in a worker thread.
    """
    p = Path(path)
    kind = SUPPORTED_EXTENSIONS.get(p.suffix.lower())
    if kind is None:
        raise IngestionError(
            f"Unsupported file extension '{p.suffix}' for {p}. "
            f"Supported: {sorted(SUPPORTED_EXTENSIONS)}"
        )
    loaders = {
        "pdf": load_pdf,
        "docx": load_docx,
        "html": load_html,
        "txt": load_txt,
        "markdown": load_markdown,
    }
    return await asyncio.to_thread(loaders[kind], p)
