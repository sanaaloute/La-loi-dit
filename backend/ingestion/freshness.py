"""Knowledge freshness monitoring for official legal sources.

Polls RSS feeds (feedparser, lazy import) and plain web pages (HTTP
``ETag`` / ``Last-Modified`` via httpx HEAD, lazy import), persists the
seen-state under ``settings.data_dir`` and emits a :class:`ChangeEvent` for
every source that changed. An injectable ``on_change`` callback can trigger
incremental re-indexing.

Fully offline-safe: without network access every check fails soft and
:meth:`FreshnessMonitor.check_sources` simply returns ``[]``.

Data source (jurisdiction-configurable): the monitored sources come from the
``freshness_registry`` section of ``data/legal_sources.json`` (override via
``settings.legal_sources_path``); a missing/corrupt file falls back to the
embedded registry with a structured warning.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Sequence, Union

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

OnChangeCallback = Callable[["ChangeEvent"], Union[None, Awaitable[None]]]


class SourceSpec(BaseModel):
    """One monitored source: an RSS feed or a plain web page."""

    name: str
    url: str
    kind: str = "web"  # "rss" | "web"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChangeEvent(BaseModel):
    """Emitted when a monitored source published something new."""

    source_name: str
    url: str
    kind: str
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    detail: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


#: Official Burkina Faso / OHADA sources monitored when the legal-sources
#: file is unavailable (embedded fallback).
_EMBEDDED_REGISTRY: list[SourceSpec] = [
    SourceSpec(
        name="OHADA — Actualités",
        url="https://www.ohada.org/feed/",
        kind="rss",
        metadata={"authority": "treaty_ohada", "government_body": "OHADA"},
    ),
    SourceSpec(
        name="Législation du Burkina Faso (LégiBurkina)",
        url="https://www.legiburkina.bf/",
        kind="web",
        metadata={"authority": "law", "government_body": "Gouvernement du Burkina Faso"},
    ),
    SourceSpec(
        name="Assemblée Nationale du Burkina Faso",
        url="https://www.assembleenationale.bf/",
        kind="web",
        metadata={"authority": "law", "government_body": "Assemblée Nationale"},
    ),
    SourceSpec(
        name="Portail du Gouvernement du Burkina Faso",
        url="https://www.gouvernement.gov.bf/",
        kind="web",
        metadata={"authority": "official_news", "government_body": "Gouvernement du Burkina Faso"},
    ),
]


#: Bundled legal-sources file shipped with the repository.
DEFAULT_SOURCES_PATH = Path(__file__).resolve().parents[2] / "data" / "legal_sources.json"


def _resolve_sources_path(path: Optional[Union[str, Path]] = None) -> Path:
    """Explicit ``path`` → ``settings.legal_sources_path`` → bundled file."""
    if path:
        return Path(path)
    try:
        from backend.core.config import get_settings

        configured = getattr(get_settings(), "legal_sources_path", None)
    except Exception:  # settings unavailable: stay on the bundled default
        configured = None
    return Path(configured) if configured else DEFAULT_SOURCES_PATH


_REGISTRY_CACHE: dict[str, list[SourceSpec]] = {}


def load_registry(path: Optional[Union[str, Path]] = None) -> list[SourceSpec]:
    """Load the freshness-monitored sources from the legal-sources JSON file.

    Resolution order: explicit ``path`` → ``settings.legal_sources_path`` →
    the bundled ``data/legal_sources.json`` (``freshness_registry`` section).
    A missing/corrupt file falls back to the embedded registry with a
    structured warning — never raises.
    """
    key = str(_resolve_sources_path(path))
    if key not in _REGISTRY_CACHE:
        try:
            data = json.loads(Path(key).read_text(encoding="utf-8"))
            raw = data.get("freshness_registry") if isinstance(data, dict) else None
            if not isinstance(raw, list):
                raise ValueError("freshness_registry section unavailable")
            _REGISTRY_CACHE[key] = [SourceSpec(**entry) for entry in raw]
        except Exception as exc:
            logger.warning(
                "freshness_registry_load_failed",
                extra={"path": key, "error": str(exc), "fallback": "embedded_registry"},
            )
            _REGISTRY_CACHE[key] = list(_EMBEDDED_REGISTRY)
    return list(_REGISTRY_CACHE[key])


#: Default registry, loaded from the bundled legal-sources file at import
#: (falling back to the embedded registry). Kept importable for compatibility.
DEFAULT_REGISTRY: list[SourceSpec] = load_registry()


class FreshnessMonitor:
    """Polls sources, diffs against persisted seen-state, fires ``on_change``."""

    def __init__(self, ctx: Any, on_change: Optional[OnChangeCallback] = None):
        self.ctx = ctx
        self.on_change = on_change
        data_dir = getattr(getattr(ctx, "settings", None), "data_dir", Path("./data"))
        self._state_path = Path(data_dir) / "freshness_state.json"

    # --------------------------------------------------------------- state

    def _load_state(self) -> dict:
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self, state: dict) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._state_path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self._state_path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # --------------------------------------------------------------- checks

    async def check_sources(
        self, registry: Optional[Sequence[Union[SourceSpec, dict]]] = None
    ) -> list[ChangeEvent]:
        """Check every source in ``registry`` (default: the legal-sources
        file's ``freshness_registry`` section, see :func:`load_registry`).

        Returns one :class:`ChangeEvent` per changed source. Network or parse
        failures are logged and skipped — never raised.
        """
        sources = [s if isinstance(s, SourceSpec) else SourceSpec(**s) for s in (registry or load_registry())]
        state = self._load_state()
        events: list[ChangeEvent] = []

        for source in sources:
            try:
                fingerprint, detail = await self._check_one(source)
            except Exception as exc:
                logger.info("Freshness check failed for %s (%s): %s", source.name, source.url, exc)
                continue
            if fingerprint is None:
                continue
            if state.get(source.url, {}).get("fingerprint") == fingerprint:
                continue  # unchanged

            event = ChangeEvent(
                source_name=source.name,
                url=source.url,
                kind=source.kind,
                detail=detail,
                metadata=dict(source.metadata),
            )
            events.append(event)
            state[source.url] = {
                "fingerprint": fingerprint,
                "checked_at": event.detected_at.isoformat(),
            }

        if events:
            self._save_state(state)
            for event in events:
                await self._notify(event)
        return events

    async def _check_one(self, source: SourceSpec) -> tuple[Optional[str], str]:
        """Return ``(fingerprint, detail)`` or ``(None, "")`` when unverifiable."""
        if source.kind == "rss":
            return await self._check_rss(source.url)
        return await self._check_web(source.url)

    def _timeout(self) -> float:
        ctx = getattr(self, "ctx", None)
        settings = getattr(ctx, "settings", None)
        return getattr(settings, "ingestion_freshness_timeout_seconds", 20.0)

    async def _check_rss(self, url: str) -> tuple[Optional[str], str]:
        """Fingerprint a feed by its newest entry id/link/published date."""
        try:
            import feedparser
            import httpx
        except ImportError as exc:
            logger.info("feedparser/httpx unavailable, skipping RSS check: %s", exc)
            return None, ""
        async with httpx.AsyncClient(timeout=self._timeout(), follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
        feed = await asyncio.to_thread(feedparser.parse, response.content)
        entries = feed.get("entries") or []
        if not entries:
            etag = response.headers.get("ETag")
            return (f"etag:{etag}", "feed empty, ETag fallback") if etag else (None, "")
        newest = entries[0]
        marker = newest.get("id") or newest.get("link") or newest.get("published") or ""
        if not marker:
            return None, ""
        title = newest.get("title", "")
        return f"entry:{marker}", f"latest: {title}".strip()

    async def _check_web(self, url: str) -> tuple[Optional[str], str]:
        """Fingerprint a page by ETag, falling back to Last-Modified."""
        try:
            import httpx
        except ImportError as exc:
            logger.info("httpx unavailable, skipping web check: %s", exc)
            return None, ""
        async with httpx.AsyncClient(timeout=self._timeout(), follow_redirects=True) as client:
            response = await client.head(url)
            response.raise_for_status()
        etag = response.headers.get("ETag")
        if etag:
            return f"etag:{etag}", "ETag changed"
        last_modified = response.headers.get("Last-Modified")
        if last_modified:
            return f"last-modified:{last_modified}", "Last-Modified changed"
        return None, ""  # server gives no validators; cannot detect changes cheaply

    async def _notify(self, event: ChangeEvent) -> None:
        """Trigger the incremental re-index callback, if one was injected."""
        if self.on_change is None:
            return
        try:
            result = self.on_change(event)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception("on_change callback failed for %s", event.url)


# ---------------------------------------------------------------------------
# Event store (shared by the polling loop and the /freshness/events endpoint)
# ---------------------------------------------------------------------------

EVENTS_FILENAME = "freshness_events.jsonl"
EVENTS_CAP = 500


def append_event(data_dir: Union[str, Path], event: ChangeEvent) -> None:
    """Append one change event to the JSONL store, trimming to EVENTS_CAP.

    Multiple uvicorn workers detect the same change simultaneously (the seen
    state file only dedupes across polls, not across racing workers): an
    identical (url, detail) event already in the store is not appended again.
    """
    path = Path(data_dir) / EVENTS_FILENAME
    try:
        existing = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        existing = []
    for line in existing:
        try:
            prior = ChangeEvent.model_validate_json(line)
        except ValueError:
            continue
        if prior.url == event.url and prior.detail == event.detail:
            return
    existing.append(event.model_dump_json())
    if len(existing) > EVENTS_CAP:
        existing = existing[-EVENTS_CAP:]
    path.write_text("\n".join(existing) + "\n", encoding="utf-8")


def read_events(data_dir: Union[str, Path], limit: int = 50) -> list[ChangeEvent]:
    """Newest-first change events from the JSONL store ([] when absent)."""
    path = Path(data_dir) / EVENTS_FILENAME
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events: list[ChangeEvent] = []
    for line in reversed(lines):
        try:
            events.append(ChangeEvent.model_validate_json(line))
        except ValueError:
            continue
        if len(events) >= limit:
            break
    return events
