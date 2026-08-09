"""Polite crawler skeleton for government websites and official gazettes.

Depth-limited BFS restricted to an allowed-domain list, with a delay between
requests and optional robots.txt respect. All third-party imports (httpx,
BeautifulSoup) are lazy; without network access every fetch fails soft and
the crawl simply returns what it gathered (often ``[]``) — a true no-op.

Data source (jurisdiction-configurable)
---------------------------------------
The default allowed-domain list comes from the ``crawler_allowed_domains``
section of ``data/legal_sources.json`` (override the file via
``settings.legal_sources_path``), merged at crawl time with the
comma-separated ``settings.crawler_extra_allowed_domains``.  A missing or
corrupt file falls back to the embedded list below with a structured warning.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.parse
import urllib.robotparser
from collections import deque
from pathlib import Path
from typing import Any, Optional, Sequence, Union

from backend.core.config import get_settings
from backend.ingestion.loaders import ExtractedDocument

logger = logging.getLogger(__name__)

#: Bundled legal-sources file shipped with the repository.
DEFAULT_SOURCES_PATH = Path(__file__).resolve().parents[2] / "data" / "legal_sources.json"

#: Official domains crawled when the legal-sources file is unavailable.
_EMBEDDED_ALLOWED_DOMAINS: tuple[str, ...] = (
    "gouvernement.gov.bf",
    "assembleenationale.bf",
    "legiburkina.bf",
    "presidencedufaso.bf",
    "sig.gov.bf",
    "ohada.org",
)


def _resolve_sources_path(path: Optional[Union[str, Path]] = None) -> Path:
    """Explicit ``path`` → ``settings.legal_sources_path`` → bundled file."""
    if path:
        return Path(path)
    try:
        configured = getattr(get_settings(), "legal_sources_path", None)
    except Exception:  # settings unavailable: stay on the bundled default
        configured = None
    return Path(configured) if configured else DEFAULT_SOURCES_PATH


_DOMAINS_CACHE: dict[str, tuple[str, ...]] = {}


def load_allowed_domains(path: Optional[Union[str, Path]] = None) -> tuple[str, ...]:
    """Load the crawler allowed-domain list from the legal-sources JSON file.

    Resolution order: explicit ``path`` → ``settings.legal_sources_path`` →
    the bundled ``data/legal_sources.json``.  A missing/corrupt file falls
    back to the embedded list with a structured warning — never raises.
    """
    key = str(_resolve_sources_path(path))
    if key not in _DOMAINS_CACHE:
        try:
            data = json.loads(Path(key).read_text(encoding="utf-8"))
            raw = data.get("crawler_allowed_domains") if isinstance(data, dict) else None
            if not isinstance(raw, list):
                raise ValueError("crawler_allowed_domains section unavailable")
            _DOMAINS_CACHE[key] = tuple(str(d) for d in raw)
        except Exception as exc:
            logger.warning(
                "crawler_allowed_domains_load_failed",
                extra={"path": key, "error": str(exc), "fallback": "embedded_domains"},
            )
            _DOMAINS_CACHE[key] = _EMBEDDED_ALLOWED_DOMAINS
    return _DOMAINS_CACHE[key]


#: Default allowed domains, loaded from the bundled legal-sources file at
#: import (falling back to the embedded list). Kept importable for compatibility.
DEFAULT_ALLOWED_DOMAINS: tuple[str, ...] = load_allowed_domains()


def _default_allowed(seed_netloc: str, settings: Any) -> tuple[str, ...]:
    """Default allowlist for one crawl: seed domain + file + settings extras."""
    extra = tuple(
        d.strip()
        for d in getattr(settings, "crawler_extra_allowed_domains", "").split(",")
        if d.strip()
    )
    return tuple({seed_netloc, *load_allowed_domains(), *extra})

_SKIP_EXTENSIONS = re.compile(
    r"\.(pdf|zip|jpe?g|png|gif|svg|mp4|mp3|docx?|xlsx?|pptx?)(\?.*)?$",
    re.IGNORECASE,
)


def _same_or_subdomain(host: str, allowed: str) -> bool:
    return host == allowed or host.endswith("." + allowed)


def _extract_main_text(html: str) -> tuple[str, str, list[str]]:
    """Return ``(title, main_text, links)`` from an HTML page."""
    from bs4 import BeautifulSoup

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    links = [
        a["href"]
        for a in soup.find_all("a", href=True)
        if isinstance(a["href"], str)
    ]
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    text = main.get_text("\n")
    return title, text, links


class _RobotsCache:
    """Fetches and caches robots.txt per origin; permissive when unreachable."""

    def __init__(self, client: Any, user_agent: str):
        self._client = client
        self._user_agent = user_agent
        self._parsers: dict[str, urllib.robotparser.RobotFileParser] = {}

    async def can_fetch(self, url: str) -> bool:
        origin = urllib.parse.urlsplit(url)
        root = f"{origin.scheme}://{origin.netloc}"
        if root not in self._parsers:
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(f"{root}/robots.txt")
            try:
                response = await self._client.get(f"{root}/robots.txt")
                if response.status_code == 200:
                    parser.parse(response.text.splitlines())
                else:
                    parser.parse([])  # no robots.txt -> everything allowed
            except Exception:
                parser.parse([])  # unreachable -> fail permissive, fetch will fail anyway
            self._parsers[root] = parser
        return self._parsers[root].can_fetch(self._user_agent, url)


async def crawl(
    seed_url: str,
    max_pages: Optional[int] = None,
    allowed_domains: Optional[Sequence[str]] = None,
    *,
    max_depth: Optional[int] = None,
    delay_seconds: Optional[float] = None,
    respect_robots: bool = True,
    user_agent: Optional[str] = None,
    client: Optional[Any] = None,
) -> list[ExtractedDocument]:
    """Breadth-first crawl starting at ``seed_url``.

    Stays within ``allowed_domains`` (defaults to the seed's own domain plus
    the legal-sources file's ``crawler_allowed_domains`` — see
    :func:`load_allowed_domains` — plus ``crawler_extra_allowed_domains``),
    waits ``delay_seconds`` between fetches and honors robots.txt when
    ``respect_robots`` is true. Returns one :class:`ExtractedDocument` per
    fetched page, with the page URL and crawl depth in metadata. Offline:
    returns ``[]`` (or pages gathered so far).
    """
    settings = get_settings()
    max_pages = max_pages if max_pages is not None else settings.crawler_max_pages
    max_depth = max_depth if max_depth is not None else settings.crawler_max_depth
    delay_seconds = delay_seconds if delay_seconds is not None else settings.crawler_delay_seconds
    user_agent = user_agent if user_agent is not None else settings.crawler_user_agent

    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed; crawler is a no-op")
        return []

    seed_parts = urllib.parse.urlsplit(seed_url)
    allowed = (
        tuple(allowed_domains)
        if allowed_domains
        else _default_allowed(seed_parts.netloc, settings)
    )

    queue: deque[tuple[str, int]] = deque([(seed_url, 0)])
    visited: set[str] = {seed_url}
    documents: list[ExtractedDocument] = []

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            timeout=settings.crawler_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        )
    robots = _RobotsCache(client, user_agent) if respect_robots else None

    try:
        while queue and len(documents) < max_pages:
            url, depth = queue.popleft()

            if robots is not None and not await robots.can_fetch(url):
                logger.info("robots.txt disallows %s; skipped", url)
                continue

            try:
                response = await client.get(url)
                if response.status_code != 200:
                    continue
                content_type = response.headers.get("Content-Type", "")
                if "html" not in content_type:
                    continue
                html = response.text
            except Exception as exc:
                logger.info("Fetch failed for %s: %s (offline?)", url, exc)
                break  # no network: stop the crawl, keep what we have

            try:
                title, text, links = await asyncio.to_thread(_extract_main_text, html)
            except Exception as exc:
                logger.info("Parse failed for %s: %s", url, exc)
                continue

            documents.append(
                ExtractedDocument(
                    name=title or url,
                    text=text,
                    pages=[text],
                    metadata={"url": url, "depth": depth, "loader": "crawler"},
                )
            )

            if depth < max_depth:
                for href in links:
                    absolute = urllib.parse.urldefrag(urllib.parse.urljoin(url, href)).url
                    parts = urllib.parse.urlsplit(absolute)
                    if parts.scheme not in ("http", "https") or _SKIP_EXTENSIONS.search(parts.path):
                        continue
                    if not any(_same_or_subdomain(parts.netloc, d) for d in allowed):
                        continue
                    if absolute not in visited:
                        visited.add(absolute)
                        queue.append((absolute, depth + 1))

            if queue:
                await asyncio.sleep(max(0.0, delay_seconds))
    finally:
        if own_client:
            await client.aclose()

    return documents
