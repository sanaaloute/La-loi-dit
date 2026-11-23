"""Chunking strategies producing :class:`EvidenceChunk` objects.

- :func:`legal_parent_child_chunk` — parents follow article boundaries (a
  whole article, kept intact with its headings and tables); children are
  split on alinéa boundaries and linked via ``parent_chunk_id`` for dense
  retrieval.  Article children carry a contextual ``retrieval_text`` prefix
  used for embedding/indexing.
- :func:`parent_child_chunk` — size-based parents/children for unstructured
  text (fallback when no legal structure is detected).
- :func:`semantic_chunk` — splits on article boundaries and stamps
  ``article``/``section`` on each chunk; oversized articles fall back to
  size-based splitting.

Heading levels are tracked separately: every chunk also carries a
``hierarchy`` map (``{"livre": "I", "titre": "II", ...}``) with the ordered
heading path in force, while ``section`` keeps the deepest heading string
for backward compatibility.

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

# Article numbers: "1", "1.2", "123-4", ordinal forms "1er"/"1ère" and the
# spelled-out "premier"/"première" used for the first article of French codes.
# "ler"/"ter" are systematic OCR misreads of "1er" (tesseract l/t confusion on
# the leading 1, scanned gazettes); they are not French words, so accepting
# them after a line-anchored "Article" is safe. "unique" is the drafting
# convention for one-article instruments ("Article unique : …").
_ARTICLE_NUM_RE = r"([0-9]+(?:[.\-][0-9A-Za-z]+)*|premier|premi[èe]re|ler|ter|unique)(?:er|[èe]re)?"

_ARTICLE_HEADING_RE = re.compile(
    r"^[ \t]*(?:article|art\.)\s*(?:n[°o]?\s*)?" + _ARTICLE_NUM_RE + r"\b",
    re.IGNORECASE | re.MULTILINE,
)
_SECTION_HEADING_RE = re.compile(
    r"^[ \t]*(section|chapitre|titre|partie|livre|annexe)\s+([IVXLCDM]+|[0-9]{1,3})\b",
    re.IGNORECASE | re.MULTILINE,
)
# Boundary groups: 1=article word, 2=article number, 3=level word,
# 4=level number, 5=annexe word, 6=annexe number (optional).
# Heading numbers cap at 3 digits: finance-law budget lines carry 4-8-digit
# account codes ("Chapitre 1218241 : Bureau du Médiateur") that are table
# rows, not structure.
_BOUNDARY_RE = re.compile(
    r"^[ \t]*(?:(article|art\.)\s*(?:n[°o]?\s*)?" + _ARTICLE_NUM_RE + r"\b"
    r"|(section|chapitre|titre|partie|livre)\s+([IVXLCDM]+|[0-9]{1,3})\b"
    r"|(annexe)\b(?:[ \t]+([IVXLCDM]+|[0-9]{1,3})\b)?)",
    re.IGNORECASE | re.MULTILINE,
)

# Hierarchy levels from highest to lowest; ``annexe`` is handled separately
# (an annexe replaces the whole heading path instead of nesting under it).
_HIERARCHY_LEVELS = ("livre", "partie", "titre", "chapitre", "section")

_FR_ORDINAL_ONE = {"premier", "première", "premiere", "ler", "ter", "unique"}

# Hard cap on a legal parent chunk's size: chunks serialize into Milvus's
# 65535-char ``chunk_json`` varchar (text + metadata), so parents must stay
# well below it. Legal parents otherwise keep whole articles intact.
_LEGAL_PARENT_MAX = 30_000


def _normalize_article_number(raw: str) -> str:
    """Canonical article key: "1er"/"premier"/"unique"/OCR-"ler" collapse to "1"."""
    lowered = raw.strip().lower()
    if lowered in _FR_ORDINAL_ONE:
        return "1"
    return raw.strip()


def _update_hierarchy(hierarchy: dict[str, str], level: str, number: str) -> dict[str, str]:
    """Return a new hierarchy with ``level`` set and all deeper levels dropped."""
    if level == "annexe":
        return {"annexe": number} if number else {}
    updated: dict[str, str] = {}
    for existing in _HIERARCHY_LEVELS:
        if existing == level:
            updated[level] = number
            break
        if existing in hierarchy:
            updated[existing] = hierarchy[existing]
    return updated


def _deepest_heading(hierarchy: dict[str, str]) -> Optional[str]:
    """Compat ``section`` string: the deepest heading currently in force."""
    if "annexe" in hierarchy:
        return f"Annexe {hierarchy['annexe']}".rstrip()
    for level in reversed(_HIERARCHY_LEVELS):
        if level in hierarchy:
            return f"{level.capitalize()} {hierarchy[level]}"
    return None


def _hierarchy_path(hierarchy: dict[str, str]) -> str:
    """Ordered "Livre I > Titre II > Chapitre 1" rendering of a hierarchy map."""
    if "annexe" in hierarchy:
        return f"Annexe {hierarchy['annexe']}".rstrip()
    parts = [
        f"{level.capitalize()} {hierarchy[level]}"
        for level in _HIERARCHY_LEVELS
        if hierarchy.get(level)
    ]
    return " > ".join(parts)


def _context_prefix(
    document_name: str,
    law_number: Optional[str],
    hierarchy: dict[str, str],
    article: str,
) -> str:
    """One-line retrieval prefix locating a child chunk inside its document."""
    source = document_name
    if law_number:
        source += f" ({law_number})"
    prefix = f"« {source}"
    path = _hierarchy_path(hierarchy)
    if path:
        prefix += f" — {path}"
    return f"{prefix} — Article {article}. »"


# A document opening its own article numbering ("Article 1er :", "Article
# unique :", OCR variants) at a line start is structured legal text even when
# it has a single article — ratification laws genuinely do.
_ARTICLE_ONE_RE = re.compile(
    r"^[ \t]*(?:article|art\.)\s*(?:n[°o]?\s*)?"
    r"(1(?:er|[èe]re)?|premier|premi[èe]re|ler|ter|unique)\b",
    re.IGNORECASE | re.MULTILINE,
)

# Amendment marker stranded at a segment's tail ("Au lieu de :", "Lire :").
_AMENDMENT_MARKER_RE = re.compile(r"\s*(?:au lieu de|lire)\s*:\s*$", re.IGNORECASE)


def looks_like_legal(text: str) -> bool:
    """Heuristic: structured legal text detection.

    Certain when at least two article/section headings exist; a single line-
    anchored "Article 1" (any ordinal form) suffices — non-legal documents
    (reports, minutes) cite articles inline, never at a line start, and never
    open their own article numbering.
    """
    if _ARTICLE_ONE_RE.search(text):
        return True
    return len(_BOUNDARY_RE.findall(text)) >= 2


def _boundary_segments(
    text: str,
) -> list[tuple[int, str, Optional[str], Optional[str], dict[str, str]]]:
    """Split ``text`` at article headings.

    Returns ``(start, body, article, section, hierarchy)`` tuples. Only
    articles open a segment: section-level headings (Titre, Chapitre,
    Section...) update the running hierarchy but stay inline in the body, so
    an article keeps everything up to the next article — in finance laws the
    tables between "Article 1" and "Article 2" ARE article 1's content.
    ``section`` is the deepest heading in force at the article's position
    (compat behavior); ``hierarchy`` is the ordered level map
    (``{"titre": "II", "chapitre": "1", ...}``) stamped on every chunk. Text
    before the first article heading is kept as a preamble segment; a
    document with no article heading at all yields one preamble segment.
    """
    matches = list(_BOUNDARY_RE.finditer(text))
    hierarchy: dict[str, str] = {}
    arts: list[tuple[re.Match, str, Optional[str], dict[str, str]]] = []
    for match in matches:
        if match.group(1) is not None:  # article / art.
            article = _normalize_article_number(match.group(2))
            arts.append((match, article, _deepest_heading(hierarchy), dict(hierarchy)))
        else:  # section / chapitre / titre / partie / livre / annexe
            level = (match.group(3) or match.group(5)).lower()
            number = match.group(4) or match.group(6) or ""
            hierarchy = _update_hierarchy(hierarchy, level, number)

    segments: list[tuple[int, str, Optional[str], Optional[str], dict[str, str]]] = []
    if not arts:
        body = text.strip()
        return [(0, body, None, None, {})] if body else []
    if arts[0][0].start() > 0:
        preamble = text[: arts[0][0].start()].strip()
        if preamble:
            segments.append((0, preamble, None, None, {}))
    for i, (match, article, section, hie) in enumerate(arts):
        end = arts[i + 1][0].start() if i + 1 < len(arts) else len(text)
        body = text[match.start() : end].strip()
        if body:
            segments.append((match.start(), body, article, section, hie))

    # Amending laws quote each modified article as "Au lieu de : <old> Lire :
    # <new>". The marker precedes the article heading, so plain boundary
    # splitting strands it at the previous segment's tail; move it onto the
    # article block it introduces.
    for i in range(1, len(segments)):
        start, body, article, section, hie = segments[i]
        if article is None:
            continue
        prev_start, prev_body, prev_art, prev_sec, prev_hie = segments[i - 1]
        marker = _AMENDMENT_MARKER_RE.search(prev_body)
        if not marker:
            continue
        segments[i - 1] = (prev_start, prev_body[: marker.start()].rstrip(), prev_art, prev_sec, prev_hie)
        segments[i] = (start, f"{marker.group(0).strip()}\n\n{body}", article, section, hie)

    # Budget nomenclature in finance laws repeats one line ("Article 62 :
    # Achats de biens et services") per ministry — dozens of times across the
    # document, mostly mid-line where no segment opens. Real instruments
    # never restate a number beyond an amendment's old/new pair (2 colon-form
    # headings), so count the heading form "Article N :" in the full text:
    # past 3 occurrences the lines are table text, not article headings, and
    # their segments fold into the previous one (searchable, out of the index).
    counts: dict[str, int] = {}
    for _, _, art, _, _ in segments:
        if art is not None and art not in counts:
            counts[art] = len(re.findall(rf"(?i)article\s*{re.escape(art)}\s*:", text))
    noisy = {a for a, n in counts.items() if n > 3}
    if noisy:
        merged: list[tuple[int, str, Optional[str], Optional[str], dict[str, str]]] = []
        for start, body, art, sec, hie in segments:
            if art is not None and art in noisy and merged:
                p_start, p_body, p_art, p_sec, p_hie = merged[-1]
                merged[-1] = (p_start, f"{p_body}\n\n{body}", p_art, p_sec, p_hie)
            else:
                merged.append((start, body, art, sec, hie))
        segments = merged

    return segments


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


def _split_alineas(text: str, size: int, overlap: int) -> list[tuple[int, str]]:
    """Split text on alinéa (line) boundaries, packing whole lines up to ``size``.

    French legal texts separate alinéas with single newlines, so every line is
    a boundary candidate: lines are never broken mid-way, and an article whose
    lines fit within ``size`` stays a single chunk.  Only an individual line
    longer than ``size`` is hard-sliced, with ``overlap`` characters of
    context carry-over.

    Returns ``(start_offset, chunk_text)`` pairs.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [(0, text)]

    lines = [ln for ln in re.split(r"\n+", text) if ln.strip()]
    chunks: list[tuple[int, str]] = []
    current: list[str] = []
    current_start = 0
    cursor = 0  # approximate offset tracking (split removes separators)

    def flush() -> None:
        nonlocal current, current_start
        if current:
            chunks.append((current_start, "\n".join(current).strip()))
            current = []

    for line in lines:
        idx = text.find(line, cursor)
        start = idx if idx >= 0 else cursor
        cursor = start + len(line)
        if len(line) > size:  # overlong alinéa: slice it
            flush()
            step = max(1, size - overlap)
            for i in range(0, len(line), step):
                piece = line[i : i + size].strip()
                if piece:
                    chunks.append((start + i, piece))
            continue
        if sum(len(p) for p in current) + len(line) + 1 > size and current:
            flush()
            current_start = start
        elif not current:
            current_start = start
        current.append(line.strip())
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
    # ``seq`` is the reading order within the document; the full-text view
    # sorts on it (page alone cannot order several parents sharing a page).
    for seq, chunk in enumerate(chunks):
        chunk.metadata["seq"] = seq
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
    law_number: Optional[str] = None,
    version: int = 1,
    child_size: Optional[int] = None,
    child_overlap: Optional[int] = None,
) -> list[EvidenceChunk]:
    """Parent-child chunking where parents follow legal article boundaries.

    Parents are created at article headings (Article N) and always keep the
    whole article intact, including any Titre/Chapitre/Section headings and
    tables that appear before the next article (finance-law budget tables are
    their article's content); section headings only stamp the ``section`` /
    ``hierarchy`` metadata. Children are split on alinéa boundaries (never
    mid-line), so a short article yields a single child and ``child_size``
    only caps oversized articles.  This gives the RAG layer meaningful
    context (the whole article) while keeping the retrieval units small and
    precise.

    The segment before the first article (long title, visas) is the preamble;
    it stays retrievable like articles.  Children carry ``retrieval_text`` =
    one-line context prefix + raw ``content``; it is the text embedded and
    BM25-indexed, while ``content`` stays the raw display/citation text.
    """
    cfg = _settings()
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
            child_size=child_size,
            child_overlap=child_overlap,
        )

    segments = _boundary_segments(text)
    # The segment before the first article heading is the preamble (long
    # title, visas, ceremonial clause): it carries the instrument's best
    # keyword text, so it stays retrievable (role parent/child).
    has_preamble = bool(segments) and segments[0][2] is None

    chunks: list[EvidenceChunk] = []
    for seg_index, (start, body, article, section, hierarchy) in enumerate(segments):
        # Legal parents follow article boundaries. Keep the whole segment as
        # one parent so the LLM always sees the complete article context —
        # but cap it: a serialized chunk must fit Milvus's 65535-char
        # varchar, and a pathological segment (a session report's whole
        # preamble, an annexed budget table) can be several times that.
        preamble = seg_index == 0 and has_preamble
        parent_pieces = (
            _split_alineas(body, _LEGAL_PARENT_MAX, child_overlap)
            if len(body) > _LEGAL_PARENT_MAX
            else [(0, body)]
        )
        for piece_offset, piece_text in parent_pieces:
            parent = EvidenceChunk(
                content=piece_text,
                article=article,
                section=section,
                hierarchy=dict(hierarchy),
                page=_page_for_offset(offsets, start + piece_offset),
                **{**prov, "metadata": {**prov["metadata"], "role": "parent"}},
            )
            chunks.append(parent)
            if preamble:
                source = prov["document_name"] or ""
                if law_number:
                    source += f" ({law_number})"
                prefix = f"« {source}. »" if source else None
            else:
                prefix = _context_prefix(prov["document_name"], law_number, hierarchy, article)
            for child_offset, child_text in _split_alineas(piece_text, child_size, child_overlap):
                chunks.append(
                    EvidenceChunk(
                        content=child_text,
                        retrieval_text=None if prefix is None else f"{prefix}\n{child_text}",
                        article=article,
                        section=section,
                        hierarchy=dict(hierarchy),
                        parent_chunk_id=parent.chunk_id,
                        page=_page_for_offset(offsets, start + piece_offset + child_offset),
                        **{**prov, "metadata": {**prov["metadata"], "role": "child"}},
                    )
                )
    # ``seq`` is the reading order within the document; the full-text view
    # sorts on it (page alone cannot order several parents sharing a page).
    for seq, chunk in enumerate(chunks):
        chunk.metadata["seq"] = seq
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
    """Split on article boundaries (``Article N``, ``Art. N``).

    Each chunk gets ``article`` and/or ``section`` set from the heading that
    opened its segment (articles inherit the section in force; section
    headings stay inline in the body). Segments larger than
    ``max_chunk_size`` fall back to size-based splitting while keeping their
    article/section metadata. Text before the first article heading is kept
    as a preamble chunk.
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

    segments = _boundary_segments(text)

    chunks: list[EvidenceChunk] = []
    for start, body, article, section, hierarchy in segments:
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
                    hierarchy=dict(hierarchy),
                    page=_page_for_offset(offsets, piece_start),
                    **prov,
                )
            )
    return chunks
