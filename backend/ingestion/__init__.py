"""Document ingestion subsystem.

Loaders, cleaning, chunking, versioning, pipeline orchestration, source
freshness monitoring and a polite crawler. Async-first and offline-first:
every third-party dependency is imported lazily inside functions so the
package always imports, even with missing optional dependencies.
"""

from backend.ingestion.loaders import (
    ExtractedDocument,
    load_any,
    load_csv,
    load_docx,
    load_html,
    load_markdown,
    load_pdf,
    load_txt,
)
from backend.ingestion.text_cleaning import clean_document, clean_text
from backend.ingestion.chunking import parent_child_chunk, semantic_chunk
from backend.ingestion.versioning import ArticleDiff, VersionStore
from backend.ingestion.freshness import FreshnessMonitor, ChangeEvent, DEFAULT_REGISTRY
from backend.ingestion.crawler import crawl

# NOTE: IngestionPipeline is intentionally NOT re-exported here — importing
# backend.ingestion.pipeline at package import time breaks `python -m
# backend.ingestion.pipeline` (runpy double-import warning). Import it from
# backend.ingestion.pipeline directly, like every consumer does.

__all__ = [
    "ExtractedDocument",
    "load_any",
    "load_csv",
    "load_docx",
    "load_html",
    "load_markdown",
    "load_pdf",
    "load_txt",
    "clean_document",
    "clean_text",
    "parent_child_chunk",
    "semantic_chunk",
    "VersionStore",
    "ArticleDiff",
    "FreshnessMonitor",
    "ChangeEvent",
    "DEFAULT_REGISTRY",
    "crawl",
]
