"""Chunking strategies producing :class:`EvidenceChunk` objects.

- :func:`parent_child_chunk` — large parent chunks for LLM context, small
  child chunks (linked via ``parent_chunk_id``) for dense retrieval.
- :func:`semantic_chunk` — splits on legal structure boundaries
  (``Article N``, ``Art. N``, ``Section``, ``Chapitre``, ``Titre``...) and
  stamps ``article``/``section`` on each chunk; oversized articles fall
  back to size-based splitting.

Both stamp full provenance metadata (document name, article, section, page,
publication date, government body, URL, version) on every chunk.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional, Sequence

from backend.core.models import AuthorityLevel, EvidenceChunk
from backend.ingestion.loaders import ExtractedDocument

# --- legal boundary detection -------------------------------------------------

_ARTICLE_HEADING_RE = re.compile(
    r"^[ \t]*(?:article|art\.)\s*(?:n[°o]?\s*)?([0-9]+(?:[.\-][0-9A-Za-z]+)*)\b",
    re.IGNORECASE | re.MULTILINE,
)
_SECTION_HEADING_RE = re.compile(
    r"^[ \t]*(section|chapitre|titre|partie|livre)\s+([IVXLCDM]+|[0-9]+)\b",
    re.IGNORECASE | re.MULTILINE,
)
_BOUNDARY_RE = re.compile(
    r"^[ \t]*(?:(article|art\.)\s*(?:n[°o]?\s*)?([0-9]+(?:[.\-][0-9A-Za-z]+)*)"
    r"|(section|chapitre|titre|partie|livre)\s+([IVXLCDM]+|[0-9]+))\b",
    re.IGNORECASE | re.MULTILINE,
)


def looks_like_legal(text: str) -> bool:
    """Heuristic: at least two article/section headings => structured legal text."""
    return len(_BOUNDARY_RE.findall(text)) >= 2


# --- provenance ---------------------------------------------------------------

from backend.core.config import Settings, get_settings


def _settings() -> Settings:
    return get_settings()


def _provenance(
    document_id: str,
    version: int,
    document_name: Optional[str],
    authority: AuthorityLevel,
    publication_date: Optional[date],
    effective_date: Optional[date],
    government_body: Optional[str],
    url: Optional[str],
    legal_domains: Optional[Sequence[str]],
) -> dict[str, Any]:
    """Fields shared by every chunk of a document."""
    return {
        "document_id": document_id,
        "document_name": document_name or "",
        "authority": authority,
        "publication_date": publication_date,
        "effective_date": effective_date,
        "government_body": government_body,
        "url": url,
        "version": version,
        "metadata": {"legal_domains": list(legal_domains or [])},
    }


def _page_offsets(pages: list[str]) -> list[int]:
    """Cumulative character offsets of each page start in the joined text."""
    offsets, pos = [], 0
    for page in pages:
        offsets.append(pos)
        pos += len(page) + 2  # pages are joined with "\n\n"
    return offsets


def _page_for_offset(offsets: list[int], offset: int) -> Optional[int]:
    """1-based page number containing a character offset, if pages are known."""
    if not offsets:
        return None
    page = 1
    for i, start in enumerate(offsets):
        if start > offset:
            break
        page = i + 1
    return page


def _split_sized(text: str, size: int, overlap: int) -> list[tuple[int, str]]:
    """Split text into <= ``size`` chunks, preferring paragraph boundaries.

    Returns ``(start_offset, chunk_text)`` pairs. Overlong paragraphs are
    hard-sliced with ``overlap`` characters of context carry-over.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [(0, text)]

    paragraphs = [p for p in re.split(r"\n{2,}", text) if p.strip()]
    chunks: list[tuple[int, str]] = []
    current: list[str] = []
    current_start = 0
    cursor = 0  # approximate offset tracking (split removes separators)

    def flush() -> None:
        nonlocal current, current_start
        if current:
            chunks.append((current_start, "\n\n".join(current).strip()))
            current = []

    for para in paragraphs:
        idx = text.find(para, cursor)
        start = idx if idx >= 0 else cursor
        cursor = start + len(para)
        if len(para) > size:  # overlong paragraph: slice it
            flush()
            step = max(1, size - overlap)
            for i in range(0, len(para), step):
                piece = para[i : i + size].strip()
                if piece:
                    chunks.append((start + i, piece))
            continue
        if sum(len(p) for p in current) + len(para) + 2 > size and current:
            flush()
            current_start = start
        elif not current:
            current_start = start
        current.append(para.strip())
    flush()
    return chunks


# --- strategies ---------------------------------------------------------------


def parent_child_chunk(
    doc: ExtractedDocument,
    document_id: str,
    *,
    document_name: Optional[str] = None,
    authority: AuthorityLevel = AuthorityLevel.UNKNOWN,
    publication_date: Optional[date] = None,
    effective_date: Optional[date] = None,
    government_body: Optional[str] = None,
    url: Optional[str] = None,
    legal_domains: Optional[Sequence[str]] = None,
    version: int = 1,
    parent_size: Optional[int] = None,
    child_size: Optional[int] = None,
    child_overlap: Optional[int] = None,
) -> list[EvidenceChunk]:
    """Parent chunks for context + child chunks for dense retrieval.

    Children carry ``parent_chunk_id`` so retrieval can fetch small units and
    the answer layer can expand to the surrounding parent context. The list
    returned contains parents and children; each chunk's ``metadata["role"]``
    is ``"parent"`` or ``"child"``.
    """
    cfg = _settings()
    parent_size = parent_size if parent_size is not None else cfg.chunk_parent_size
    child_size = child_size if child_size is not None else cfg.chunk_child_size
    child_overlap = child_overlap if child_overlap is not None else cfg.chunk_overlap
    prov = _provenance(
        document_id, version, document_name or doc.name, authority,
        publication_date, effective_date, government_body, url, legal_domains,
    )
    text = doc.text or "\n\n".join(doc.pages)
    offsets = _page_offsets(doc.pages) if len(doc.pages) > 1 else []

    chunks: list[EvidenceChunk] = []
    for parent_offset, parent_text in _split_sized(text, parent_size, overlap=100):
        parent = EvidenceChunk(
            content=parent_text,
            page=_page_for_offset(offsets, parent_offset),
            **{**prov, "metadata": {**prov["metadata"], "role": "parent"}},
        )
        chunks.append(parent)
        for child_offset, child_text in _split_sized(parent_text, child_size, child_overlap):
            chunks.append(
                EvidenceChunk(
                    content=child_text,
                    parent_chunk_id=parent.chunk_id,
                    page=_page_for_offset(offsets, parent_offset + child_offset),
                    **{**prov, "metadata": {**prov["metadata"], "role": "child"}},
                )
            )
    return chunks


def legal_parent_child_chunk(
    doc: ExtractedDocument,
    document_id: str,
    *,
    document_name: Optional[str] = None,
    authority: AuthorityLevel = AuthorityLevel.UNKNOWN,
    publication_date: Optional[date] = None,
    effective_date: Optional[date] = None,
    government_body: Optional[str] = None,
    url: Optional[str] = None,
    legal_domains: Optional[Sequence[str]] = None,
    version: int = 1,
    parent_size: Optional[int] = None,
    child_size: Optional[int] = None,
    child_overlap: Optional[int] = None,
) -> list[EvidenceChunk]:
    """Parent-child chunking where parents follow legal article/section boundaries.

    Parents are created at legal headings (Article N, Section, Chapitre, Titre,
    Partie, Livre).  Each parent is then split into child chunks for dense
    retrieval.  This gives the RAG layer meaningful context (whole article or
    section) while keeping the retrieval units small and precise.
    """
    cfg = _settings()
    parent_size = parent_size if parent_size is not None else cfg.chunk_parent_size
    child_size = child_size if child_size is not None else cfg.chunk_child_size
    child_overlap = child_overlap if child_overlap is not None else cfg.chunk_overlap
    prov = _provenance(
        document_id, version, document_name or doc.name, authority,
        publication_date, effective_date, government_body, url, legal_domains,
    )
    text = doc.text or "\n\n".join(doc.pages)
    offsets = _page_offsets(doc.pages) if len(doc.pages) > 1 else []

    matches = list(_BOUNDARY_RE.finditer(text))
    if not matches:
        # No legal structure detected: fall back to plain parent-child chunking.
        return parent_child_chunk(
            doc, document_id,
            document_name=document_name or doc.name,
            authority=authority,
            publication_date=publication_date,
            effective_date=effective_date,
            government_body=government_body,
            url=url,
            legal_domains=legal_domains,
            version=version,
            parent_size=parent_size,
            child_size=child_size,
            child_overlap=child_overlap,
        )

    segments: list[tuple[int, str, Optional[str], Optional[str]]] = []
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            segments.append((0, preamble, None, None))

    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[match.start() : end].strip()
        if not body:
            continue
        if match.group(1) is not None:  # article / art.
            article = match.group(2)
            segments.append((match.start(), body, article, None))
        else:  # section / chapitre / titre / partie / livre
            section = f"{match.group(3).capitalize()} {match.group(4)}"
            segments.append((match.start(), body, None, section))

    chunks: list[EvidenceChunk] = []
    for start, body, article, section in segments:
        # Legal parents follow article/section boundaries. Keep the whole segment
        # as one parent so the LLM always sees the complete article/section context,
        # even when it exceeds the configured parent_size.
        piece_text = body
        piece_offset = 0
        parent = EvidenceChunk(
            content=piece_text,
            article=article,
            section=section,
            page=_page_for_offset(offsets, start + piece_offset),
            **{**prov, "metadata": {**prov["metadata"], "role": "parent"}},
        )
        chunks.append(parent)
        for child_offset, child_text in _split_sized(piece_text, child_size, child_overlap):
            chunks.append(
                EvidenceChunk(
                    content=child_text,
                    article=article,
                    section=section,
                    parent_chunk_id=parent.chunk_id,
                    page=_page_for_offset(offsets, start + piece_offset + child_offset),
                    **{**prov, "metadata": {**prov["metadata"], "role": "child"}},
                )
            )
    return chunks


def semantic_chunk(
    doc: ExtractedDocument,
    document_id: str,
    *,
    document_name: Optional[str] = None,
    authority: AuthorityLevel = AuthorityLevel.UNKNOWN,
    publication_date: Optional[date] = None,
    effective_date: Optional[date] = None,
    government_body: Optional[str] = None,
    url: Optional[str] = None,
    legal_domains: Optional[Sequence[str]] = None,
    version: int = 1,
    max_chunk_size: Optional[int] = None,
    overlap: Optional[int] = None,
) -> list[EvidenceChunk]:
    """Split on legal boundaries (Article/Art./Section/Chapitre/Titre...).

    Each chunk gets ``article`` and/or ``section`` set from the heading that
    opened its segment (articles inherit the section in force). Segments
    larger than ``max_chunk_size`` fall back to size-based splitting while
    keeping their article/section metadata. Text before the first heading is
    kept as a preamble chunk.
    """
    cfg = _settings()
    max_chunk_size = max_chunk_size if max_chunk_size is not None else cfg.chunk_max_size
    overlap = overlap if overlap is not None else cfg.chunk_overlap
    prov = _provenance(
        document_id, version, document_name or doc.name, authority,
        publication_date, effective_date, government_body, url, legal_domains,
    )
    text = doc.text or "\n\n".join(doc.pages)
    offsets = _page_offsets(doc.pages) if len(doc.pages) > 1 else []

    matches = list(_BOUNDARY_RE.finditer(text))
    if not matches:  # no legal structure found: plain size-based chunks
        return [
            EvidenceChunk(
                content=piece,
                page=_page_for_offset(offsets, start),
                **prov,
            )
            for start, piece in _split_sized(text, max_chunk_size, overlap)
        ]

    segments: list[tuple[int, str, Optional[str], Optional[str]]] = []
    current_section: Optional[str] = None

    if matches[0].start() > 0:  # preamble before the first heading
        preamble = text[: matches[0].start()].strip()
        if preamble:
            segments.append((0, preamble, None, None))

    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[match.start() : end].strip()
        if not body:
            continue
        if match.group(1) is not None:  # article / art.
            article = match.group(2)
            segments.append((match.start(), body, article, current_section))
        else:  # section / chapitre / titre / partie / livre
            current_section = f"{match.group(3).capitalize()} {match.group(4)}"
            segments.append((match.start(), body, None, current_section))

    chunks: list[EvidenceChunk] = []
    for start, body, article, section in segments:
        if len(body) <= max_chunk_size:
            pieces = [(start, body)]
        else:  # oversized article: size-based fallback, metadata preserved
            pieces = [(start + off, piece) for off, piece in _split_sized(body, max_chunk_size, overlap)]
        for piece_start, piece in pieces:
            chunks.append(
                EvidenceChunk(
                    content=piece,
                    article=article,
                    section=section,
                    page=_page_for_offset(offsets, piece_start),
                    **prov,
                )
            )
    return chunks
