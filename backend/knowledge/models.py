"""Typed records for the relational legal knowledge graph (spec §19, §34).

Kept separate from :mod:`backend.core.models` so the shared contract stays
untouched; everything here is pydantic v2 like the rest of the codebase.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RelationType(str, Enum):
    """Edge vocabulary of the legal knowledge graph (spec §19)."""

    CONTAINS = "contains"  # LAW contains ARTICLE
    AMENDS = "amends"  # instrument modifies another instrument/article
    REPEALS = "repeals"  # instrument abrogates another instrument/article
    REFERENCES = "references"  # explicit cross-reference ("conformément à l'article 12")
    REFERENCED_BY = "referenced_by"  # inverse edge (materialized or manual)
    ISSUED_BY = "issued_by"  # instrument issued by an AUTHORITY
    APPLIES_TO = "applies_to"  # instrument applies to a legal DOMAIN


class ExtractedRelationship(BaseModel):
    """One candidate edge, produced by regex extraction or added manually.

    ``src_document`` / ``dst_document`` hold logical document ids when the
    endpoint is resolved; unresolved targets keep ``dst_document=None`` and
    carry the raw mention (law number, code name, domain, authority) in
    ``dst_free_text``.
    """

    src_document: str = ""
    src_article: Optional[str] = None
    relation: RelationType
    dst_document: Optional[str] = None
    dst_article: Optional[str] = None
    dst_free_text: Optional[str] = None
    extracted_by: Literal["regex", "manual"] = "regex"
    confidence: float = 1.0

    def dedup_key(self) -> tuple:
        return (
            self.src_document,
            self.src_article,
            self.relation.value,
            self.dst_document,
            self.dst_article,
            self.dst_free_text,
        )


class LegalDocumentRecord(BaseModel):
    """Row of the ``documents`` table."""

    document_id: str
    name: str = ""
    document_type: Optional[str] = None
    law_number: Optional[str] = None
    jurisdiction: str = ""
    status: str = ""
    issuing_authority: Optional[str] = None
    authority: Optional[str] = None
    publication_date: Optional[str] = None  # ISO date
    effective_date: Optional[str] = None  # ISO date
    source_url: Optional[str] = None
    version: int = 1
    content_hash: str = ""
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class LegalArticleRecord(BaseModel):
    """Row of the ``legal_articles`` table (one per document+article)."""

    document_id: str
    article: str
    section: Optional[str] = None
    hierarchy: dict[str, str] = Field(default_factory=dict)
    page: Optional[int] = None
    text_preview: str = ""
    status: str = ""
    valid_from: Optional[str] = None  # ISO date
    valid_until: Optional[str] = None  # ISO date


class LegalRelationshipRecord(ExtractedRelationship):
    """Row of the ``legal_relationships`` table (persisted edge)."""

    id: Optional[int] = None
    created_at: datetime = Field(default_factory=_utcnow)


class QueryArticleMention(BaseModel):
    """Explicit article/law mention extracted from a user query."""

    article: Optional[str] = None
    document_hint: Optional[str] = None  # e.g. "code du travail"
    law_number: Optional[str] = None  # e.g. "028-2008/AN"
