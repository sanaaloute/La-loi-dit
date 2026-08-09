"""Lightweight relational legal knowledge graph (spec §19, §27, §34).

- :mod:`backend.knowledge.models` — typed records (documents, articles, edges)
- :mod:`backend.knowledge.store` — SQLAlchemy persistence (SQLite/Postgres)
- :mod:`backend.knowledge.extraction` — deterministic French-first regex extraction
"""

from backend.knowledge.extraction import (
    extract_from_chunks,
    extract_query_mentions,
    extract_relationships,
    normalize_article_number,
)
from backend.knowledge.models import (
    ExtractedRelationship,
    LegalArticleRecord,
    LegalDocumentRecord,
    LegalRelationshipRecord,
    QueryArticleMention,
    RelationType,
)
from backend.knowledge.store import LegalGraphStore, graph_store_for

__all__ = [
    "ExtractedRelationship",
    "LegalArticleRecord",
    "LegalDocumentRecord",
    "LegalGraphStore",
    "LegalRelationshipRecord",
    "QueryArticleMention",
    "RelationType",
    "extract_from_chunks",
    "extract_query_mentions",
    "extract_relationships",
    "graph_store_for",
    "normalize_article_number",
]
