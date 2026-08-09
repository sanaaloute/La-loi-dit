"""Web search orchestrator: parallel fetches against registered official sources.

Fires one HTTP request per (task, source) with full per-source exception
isolation — a failing or offline source yields zero chunks, never an error.
httpx is imported lazily inside the fetch path, so importing this module
performs no network access and has no hard dependency at import time.
"""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import Any, Optional

from backend.core.config import get_settings
from backend.core.constants import AUTHORITY_WEIGHTS
from backend.core.models import EvidenceChunk, SearchTask
from backend.search.sources import DEFAULT_REGISTRY, OfficialSource, sources_for_kind

logger = logging.getLogger(__name__)


def _first(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _parse_json_results(payload: Any) -> list[dict[str, str]]:
    """Extract a list of {title, url, content} dicts from a JSON payload."""
    items: Any = None
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("results", "items", "data", "hits", "nodes"):
            if isinstance(payload.get(key), list):
                items = payload[key]
                break
    if not isinstance(items, list):
        return []
    results = []
    for item in items:
        if isinstance(item, dict):
            results.append(
                {
                    "title": _first(item, "title", "name", "label"),
                    "url": _first(item, "url", "link", "href", "path"),
                    "content": _first(
                        item, "content", "summary", "description", "body", "excerpt"
                    ),
                }
            )
    return results


def _parse_rss_results(text: str) -> list[dict[str, str]]:
    """Extract {title, url, content} dicts from an RSS/Atom feed."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    results = []
    for item in root.iter():
        tag = item.tag.rsplit("}", 1)[-1].lower()
        if tag not in ("item", "entry"):
            continue
        fields: dict[str, str] = {"title": "", "url": "", "content": ""}
        for child in item:
            ctag = child.tag.rsplit("}", 1)[-1].lower()
            text_value = (child.text or "").strip()
            if ctag == "title" and text_value:
                fields["title"] = text_value
            elif ctag == "link":
                href = child.attrib.get("href", "").strip()
                if href or text_value:
                    fields["url"] = href or text_value
            elif ctag in ("description", "summary", "content", "encoded") and text_value:
                fields["content"] = text_value
        if fields["title"] or fields["url"]:
            results.append(fields)
    return results


def _to_chunk(
    raw: dict[str, str],
    source: OfficialSource,
    task: SearchTask,
    max_content_chars: int,
    settings: Any,
) -> EvidenceChunk:
    """Convert one parsed result into an EvidenceChunk with full metadata."""
    title = raw["title"] or source.name
    content = (raw["content"] or raw["title"])[:max_content_chars]
    return EvidenceChunk(
        document_name=title,
        content=content,
        url=raw["url"] or None,
        government_body=source.government_body,
        source_kind=task.kind,
        authority=source.authority,
        confidence=AUTHORITY_WEIGHTS.get(source.authority, settings.search_authority_fallback),
        retrieval_score=settings.search_web_hit_score,  # refined by fusion/rerank
        metadata={"source": source.name, "retrieved_via": "web"},
    )


async def _fetch_source(
    client: Any,
    source: OfficialSource,
    task: SearchTask,
    max_results_per_source: int,
    max_content_chars: int,
    settings: Any,
) -> list[EvidenceChunk]:
    """Fetch and parse one source for one task; [] on any failure."""
    try:
        url = source.search_url(task.query)
        if url is not None:
            response = await client.get(url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "json" in content_type:
                raws = _parse_json_results(response.json())
            else:
                raws = _parse_rss_results(response.text) or _parse_json_results(
                    response.json()
                )
        else:
            feed = source.feed_url()
            if feed is None:
                return []
            response = await client.get(feed)
            response.raise_for_status()
            raws = _parse_rss_results(response.text)
        return [
            _to_chunk(raw, source, task, max_content_chars, settings)
            for raw in raws[:max_results_per_source]
        ]
    except Exception as exc:
        logger.debug("source fetch failed (%s): %s", source.name, exc)
        return []


async def search_sources(
    tasks: list[SearchTask],
    registry: Optional[list[OfficialSource]] = None,
    timeout: Optional[float] = None,
) -> list[EvidenceChunk]:
    """Search official sources for every task, in parallel.

    When ``registry`` is omitted, the default registry is filtered per task
    kind (WEB/WEBSITE tasks use every source); an explicitly passed registry
    is used as-is (workers pre-filter it by kind). All failures — missing
    httpx, offline network, timeouts, malformed payloads — degrade to an
    empty list.
    """
    settings = get_settings()
    timeout = timeout if timeout is not None else settings.search_timeout_seconds
    max_results = settings.search_max_results_per_source
    max_content_chars = settings.search_max_content_chars
    user_agent = settings.search_user_agent

    if not tasks:
        return []
    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed; web search disabled")
        return []

    jobs: list[tuple[OfficialSource, SearchTask]] = []
    for task in tasks:
        # An explicitly passed registry is already kind-filtered by the
        # caller (workers); otherwise filter the default registry per kind.
        candidates = (
            list(registry)
            if registry is not None
            else sources_for_kind(task.kind, DEFAULT_REGISTRY)
        )
        for source in candidates:
            if source.search_path or source.rss_path:
                jobs.append((source, task))
    if not jobs:
        return []

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        ) as client:
            results = await asyncio.gather(
                *(
                    _fetch_source(client, source, task, max_results, max_content_chars, settings)
                    for source, task in jobs
                ),
                return_exceptions=True,
            )
    except Exception as exc:
        logger.debug("web search orchestrator failed: %s", exc)
        return []

    chunks: list[EvidenceChunk] = []
    seen_urls: set[str] = set()
    for result in results:
        if isinstance(result, Exception):
            continue
        for chunk in result:
            key = chunk.url or f"{chunk.document_name}:{chunk.content[:80]}"
            if key in seen_urls:
                continue
            seen_urls.add(key)
            chunks.append(chunk)
    return chunks
