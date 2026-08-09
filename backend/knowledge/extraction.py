"""Deterministic, French-first relationship extraction (spec §19).

Precision over recall: an edge is only emitted when an explicit article
number or law number is present in the matched text. Unresolved targets
("l'article 5 du Code pénal" when the Code pénal is not in the corpus) keep
``dst_document=None`` and carry the raw mention in ``dst_free_text`` so they
can be resolved later. Bare references ("conformément à l'article 12") and
qualifiers pointing at the instrument itself ("du présent code") resolve to
the current document. Passive amendment forms ("est abrogé") are skipped:
without an explicit agent the edge direction would be a guess.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

from backend.core.models import EvidenceChunk
from backend.knowledge.models import (
    ExtractedRelationship,
    QueryArticleMention,
    RelationType,
)

# "article 12", "art. 5", "articles L. 234-1", "article R213-4", "article 1er"
_ARTICLE_NUM = r"([A-Z]?\.?\s*\d[\d\-]*|1er|premier|première|premiere)"
_ARTICLE_RE = re.compile(r"\barticles?\b\.?\s*(?:n[°o]\s*)?" + _ARTICLE_NUM, re.IGNORECASE)

# Qualifier after an article mention: "du Code pénal", "de la loi n° 028-2008/AN
# du 13 mai 2008", "du présent code". Capped to keep free text tidy.
_QUALIFIER_RE = re.compile(
    r"\barticles?\b\.?\s*(?:n[°o]\s*)?" + _ARTICLE_NUM
    + r"\s+(?:de\s+la|du|de|des)\s+([^.,;:!?\)\n]{2,80})",
    re.IGNORECASE,
)

# Law numbers: "loi n° 028-2008/AN", "loi n°010-99/AN", "loi 028-2008/AN"
_LAW_NUM = r"(\d[\dA-Z\-/]*(?:/AN)?)"
_LAW_RE = re.compile(r"\bloi\s*(?:n[°o]\s*)?" + _LAW_NUM, re.IGNORECASE)

# Active amendment verbs targeting an article or a whole law.
_AMEND_VERBS = r"(?:modifie|modifiant|remplace|remplaçant|remplacant|complète|complete|complétant|completant)"
_REPEAL_VERBS = r"(?:abroge|abrogeant)"
_AMEND_ARTICLE_RE = re.compile(
    r"\b" + _AMEND_VERBS + r"\s+(?:l['’]\s*)?articles?\s*(?:n[°o]\s*)?" + _ARTICLE_NUM,
    re.IGNORECASE,
)
_REPEAL_ARTICLE_RE = re.compile(
    r"\b" + _REPEAL_VERBS + r"\s+(?:l['’]\s*)?articles?\s*(?:n[°o]\s*)?" + _ARTICLE_NUM,
    re.IGNORECASE,
)
_AMEND_LAW_RE = re.compile(r"\b" + _AMEND_VERBS + r"\s+la\s+loi\s*(?:n[°o]\s*)?" + _LAW_NUM, re.IGNORECASE)
_REPEAL_LAW_RE = re.compile(r"\b" + _REPEAL_VERBS + r"\s+la\s+loi\s*(?:n[°o]\s*)?" + _LAW_NUM, re.IGNORECASE)

# Qualifiers that refer to the instrument being read, not another document.
_SELF_QUALIFIER_RE = re.compile(r"présent|present|présente|presente", re.IGNORECASE)

# Trailing date tails trimmed from free-text qualifiers ("loi n° X du 13 mai 2008").
_DATE_TAIL_RE = re.compile(r"\s+du\s+\d{1,2}\s+\w+\.?\s+\d{4}.*$", re.IGNORECASE)

_FR_ORDINAL_ONE = {"1er", "premier", "première", "premiere"}


def normalize_article_number(raw: str) -> str:
    """Canonical article key matching the chunker's normalization."""
    cleaned = re.sub(r"\s+", "", (raw or "").strip().rstrip("."))
    if cleaned.lower() in _FR_ORDINAL_ONE:
        return "1"
    return cleaned


def _clean_free_text(raw: str) -> str:
    text = _DATE_TAIL_RE.sub("", raw.strip())
    return text.strip(" \t-–—")


def _law_free_text(law_number: str) -> str:
    return f"loi n° {law_number.strip()}"


def _target_from_qualifier(
    qualifier: Optional[str], current_document_id: str
) -> tuple[Optional[str], Optional[str]]:
    """Resolve a qualifier to (dst_document, dst_free_text).

    Same-instrument qualifiers resolve to the current document; everything
    else stays unresolved with the raw mention as free text.
    """
    if not qualifier:
        return current_document_id, None
    if _SELF_QUALIFIER_RE.search(qualifier):
        return current_document_id, None
    law = _LAW_RE.search(qualifier)
    if law:
        return None, _law_free_text(law.group(1))
    return None, _clean_free_text(qualifier)


def extract_relationships(chunk: EvidenceChunk) -> list[ExtractedRelationship]:
    """Extract graph edges from one chunk (content + structured metadata)."""
    document_id = chunk.document_id
    if not document_id:
        return []
    out: list[ExtractedRelationship] = []
    own_article = normalize_article_number(chunk.article) if chunk.article else None
    text = chunk.content or ""

    # --- containment: DOCUMENT contains ARTICLE (from chunk metadata) ---
    if own_article:
        out.append(
            ExtractedRelationship(
                src_document=document_id,
                relation=RelationType.CONTAINS,
                dst_document=document_id,
                dst_article=own_article,
            )
        )

    # --- provenance: issued_by AUTHORITY / applies_to DOMAIN ---
    if chunk.issuing_authority:
        out.append(
            ExtractedRelationship(
                src_document=document_id,
                relation=RelationType.ISSUED_BY,
                dst_free_text=chunk.issuing_authority.strip(),
            )
        )
    for domain in chunk.metadata.get("legal_domains") or []:
        domain_text = str(domain).strip()
        if domain_text:
            out.append(
                ExtractedRelationship(
                    src_document=document_id,
                    relation=RelationType.APPLIES_TO,
                    dst_free_text=domain_text,
                )
            )

    if not text:
        return out

    # --- amendment / repeal language (active forms only) ---
    # Spans already claimed by amendment phrases must not also count as plain
    # cross-references ("modifie l'article 34 de la loi n° X" is an amends
    # edge, not a references edge).
    amend_spans: list[tuple[int, int]] = []
    for pattern, relation in (
        (_AMEND_ARTICLE_RE, RelationType.AMENDS),
        (_REPEAL_ARTICLE_RE, RelationType.REPEALS),
    ):
        for match in pattern.finditer(text):
            amend_spans.append(match.span())
            article = normalize_article_number(match.group(1))
            if not article:
                continue
            # Look just after the mention for a law qualifier ("de la loi n° X").
            tail = text[match.end() : match.end() + 120]
            law = _LAW_RE.search(tail.split("\n", 1)[0])
            out.append(
                ExtractedRelationship(
                    src_document=document_id,
                    src_article=own_article,
                    relation=relation,
                    dst_article=article,
                    dst_free_text=_law_free_text(law.group(1)) if law else None,
                    confidence=0.9,
                )
            )
    for pattern, relation in (
        (_AMEND_LAW_RE, RelationType.AMENDS),
        (_REPEAL_LAW_RE, RelationType.REPEALS),
    ):
        for match in pattern.finditer(text):
            out.append(
                ExtractedRelationship(
                    src_document=document_id,
                    src_article=own_article,
                    relation=relation,
                    dst_free_text=_law_free_text(match.group(1)),
                    confidence=0.9,
                )
            )

    # --- cross-references with an explicit qualifier ---
    def _in_amend_span(start: int) -> bool:
        return any(a_start <= start < a_end for a_start, a_end in amend_spans)

    qualified_spans: list[tuple[int, int]] = []
    for match in _QUALIFIER_RE.finditer(text):
        article = normalize_article_number(match.group(1))
        qualifier = match.group(2)
        qualified_spans.append(match.span())
        if not article or _in_amend_span(match.start()):
            continue
        dst_document, dst_free_text = _target_from_qualifier(qualifier, document_id)
        if dst_document == document_id and own_article and article == own_article:
            continue  # self-reference: no signal
        out.append(
            ExtractedRelationship(
                src_document=document_id,
                src_article=own_article,
                relation=RelationType.REFERENCES,
                dst_document=dst_document,
                dst_article=article,
                dst_free_text=dst_free_text,
            )
        )

    # --- bare references ("conformément à l'article 12") resolve to this doc ---
    for match in _ARTICLE_RE.finditer(text):
        if any(start <= match.start() < end for start, end in qualified_spans):
            continue  # already handled with its qualifier
        if _in_amend_span(match.start()):
            continue  # amendment target, not a plain cross-reference
        article = normalize_article_number(match.group(1))
        if not article:
            continue
        if own_article and article == own_article:
            continue  # an article restating its own number is not an edge
        out.append(
            ExtractedRelationship(
                src_document=document_id,
                src_article=own_article,
                relation=RelationType.REFERENCES,
                dst_document=document_id,
                dst_article=article,
            )
        )

    return out


def extract_from_chunks(chunks: Sequence[EvidenceChunk]) -> list[ExtractedRelationship]:
    """Extract and dedupe edges over a batch of chunks (one document)."""
    seen: set[tuple] = set()
    out: list[ExtractedRelationship] = []
    for chunk in chunks:
        for rel in extract_relationships(chunk):
            key = rel.dedup_key()
            if key in seen:
                continue
            seen.add(key)
            out.append(rel)
    return out


# ----------------------------------------------------------------------
# Query-side extraction (used by the graph retrieval worker)
# ----------------------------------------------------------------------

_QUERY_ARTICLE_RE = re.compile(
    r"\barticles?\b\.?\s*(?:n[°o]\s*)?" + _ARTICLE_NUM
    + r"(?:\s+(?:de\s+la|du|de)\s+([^.,;:!?\)\n]{2,60}))?",
    re.IGNORECASE,
)


def extract_query_mentions(query: str) -> list[QueryArticleMention]:
    """Explicit article/law mentions in a user query.

    "l'article 341 du code du travail" -> article="341", hint="code du travail";
    "la loi n° 028-2008/AN" -> law_number="028-2008/AN".
    """
    mentions: list[QueryArticleMention] = []
    for match in _QUERY_ARTICLE_RE.finditer(query or ""):
        article = normalize_article_number(match.group(1))
        if not article:
            continue
        hint = match.group(2)
        if hint and _SELF_QUALIFIER_RE.search(hint):
            hint = None
        mentions.append(
            QueryArticleMention(
                article=article,
                document_hint=_clean_free_text(hint) if hint else None,
            )
        )
    for match in _LAW_RE.finditer(query or ""):
        mentions.append(QueryArticleMention(law_number=match.group(1)))
    return mentions
