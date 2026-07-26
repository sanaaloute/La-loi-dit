"""Web/source search subsystem: official source registry and fetch orchestrator."""

from backend.search.orchestrator import search_sources
from backend.search.sources import DEFAULT_REGISTRY, OfficialSource, authority_for_url

__all__ = ["DEFAULT_REGISTRY", "OfficialSource", "authority_for_url", "search_sources"]
