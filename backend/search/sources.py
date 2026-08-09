"""Registry of official Burkina Faso / OHADA legal sources.

Each entry records where a source lives, which authority level its documents
carry and which search kind it serves. ``authority_for_url`` classifies any
URL against the official domains (``load_official_domains`` — module default
:data:`backend.core.constants.OFFICIAL_DOMAINS`, overridable via
``settings.authority_config_path``) so official sources always outrank blogs.

Data source (jurisdiction-configurable)
---------------------------------------
The registry's primary source is the ``search_registry`` section of
``data/legal_sources.json`` (override the file via
``settings.legal_sources_path``).  A missing/corrupt file falls back to the
embedded registry below with a structured warning.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, Union
from urllib.parse import urlparse

from pydantic import BaseModel

from backend.core.constants import load_official_domains
from backend.core.models import AuthorityLevel, SearchKind

logger = logging.getLogger(__name__)

#: Bundled legal-sources file shipped with the repository.
DEFAULT_SOURCES_PATH = Path(__file__).resolve().parents[2] / "data" / "legal_sources.json"


class OfficialSource(BaseModel):
    """One registered official source."""

    name: str
    base_url: str
    authority: AuthorityLevel
    kind: SearchKind
    government_body: str
    # Optional endpoints; sources without one are registry-only (no fetching).
    search_path: Optional[str] = None  # query endpoint; "{query}" is substituted
    rss_path: Optional[str] = None  # RSS/Atom feed path

    def search_url(self, query: str) -> Optional[str]:
        """Build the query URL for this source, when it supports search."""
        if not self.search_path:
            return None
        from urllib.parse import quote

        return f"{self.base_url}{self.search_path}".replace("{query}", quote(query))

    def feed_url(self) -> Optional[str]:
        """Build the RSS/Atom feed URL for this source, when available."""
        if not self.rss_path:
            return None
        return f"{self.base_url}{self.rss_path}"


_EMBEDDED_REGISTRY: list[OfficialSource] = [
    OfficialSource(
        name="Portail du Gouvernement du Burkina Faso",
        base_url="https://www.gouv.bf",
        authority=AuthorityLevel.OFFICIAL_NEWS,
        kind=SearchKind.GOVERNMENT,
        government_body="Gouvernement du Burkina Faso",
        search_path="/recherche?search_api_fulltext={query}",
        rss_path="/rss.xml",
    ),
    OfficialSource(
        name="Journal Officiel du Burkina Faso",
        base_url="https://www.jo.gouv.bf",
        authority=AuthorityLevel.OFFICIAL_GAZETTE,
        kind=SearchKind.REGULATION,
        government_body="Secrétariat général du Gouvernement",
        search_path="/recherche?search_api_fulltext={query}",
    ),
    OfficialSource(
        name="Assemblée nationale du Burkina Faso",
        base_url="https://www.assembleenationale.bf",
        authority=AuthorityLevel.LAW,
        kind=SearchKind.REGULATION,
        government_body="Assemblée nationale",
        rss_path="/rss.xml",
    ),
    OfficialSource(
        name="Conseil constitutionnel du Burkina Faso",
        base_url="https://www.conseil-constitutionnel.bf",
        authority=AuthorityLevel.CONSTITUTION,
        kind=SearchKind.CASE_LAW,
        government_body="Conseil constitutionnel",
    ),
    OfficialSource(
        name="Cour suprême / Cour de cassation du Burkina Faso",
        base_url="https://www.coursupreme.bf",
        authority=AuthorityLevel.CASE_LAW,
        kind=SearchKind.CASE_LAW,
        government_body="Cour suprême",
    ),
    OfficialSource(
        name="OHADA — Organisation pour l'Harmonisation en Afrique du Droit des Affaires",
        base_url="https://www.ohada.org",
        authority=AuthorityLevel.TREATY_OHADA,
        kind=SearchKind.REGULATION,
        government_body="OHADA",
        search_path="/recherche/?search_api_fulltext={query}",
    ),
    OfficialSource(
        name="Ministère de l'Économie, des Finances et de la Prospective",
        base_url="https://www.finances.gov.bf",
        authority=AuthorityLevel.OFFICIAL_NEWS,
        kind=SearchKind.GOVERNMENT,
        government_body="Ministère des Finances",
        rss_path="/rss.xml",
    ),
    OfficialSource(
        name="Ministère de la Justice",
        base_url="https://www.justice.gov.bf",
        authority=AuthorityLevel.MINISTERIAL_CIRCULAR,
        kind=SearchKind.GOVERNMENT,
        government_body="Ministère de la Justice",
    ),
    OfficialSource(
        name="Ministère du Travail, de l'Emploi et de la Protection sociale",
        base_url="https://www.travail.gov.bf",
        authority=AuthorityLevel.MINISTERIAL_CIRCULAR,
        kind=SearchKind.GOVERNMENT,
        government_body="Ministère du Travail",
    ),
]


def resolve_sources_path(path: Optional[Union[str, Path]] = None) -> Path:
    """Explicit ``path`` → ``settings.legal_sources_path`` → bundled file."""
    if path:
        return Path(path)
    try:
        from backend.core.config import get_settings

        configured = getattr(get_settings(), "legal_sources_path", None)
    except Exception:  # settings unavailable: stay on the bundled default
        configured = None
    return Path(configured) if configured else DEFAULT_SOURCES_PATH


def read_sources_section(
    section: str, path: Optional[Union[str, Path]] = None
) -> Optional[object]:
    """Read one section of the legal-sources JSON file (``None`` on failure)."""
    resolved = resolve_sources_path(path)
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("legal sources file must be a JSON object")
        return data.get(section)
    except Exception as exc:
        logger.warning(
            "legal_sources_load_failed",
            extra={"path": str(resolved), "section": section, "error": str(exc)},
        )
        return None


_REGISTRY_CACHE: dict[str, list[OfficialSource]] = {}


def load_registry(path: Optional[Union[str, Path]] = None) -> list[OfficialSource]:
    """Load the official-source registry from the legal-sources JSON file.

    Resolution order: explicit ``path`` → ``settings.legal_sources_path`` →
    the bundled ``data/legal_sources.json``.  A missing/corrupt file falls
    back to the embedded registry with a structured warning — never raises.
    """
    key = str(resolve_sources_path(path))
    if key not in _REGISTRY_CACHE:
        raw = read_sources_section("search_registry", key)
        try:
            if raw is None:
                raise ValueError("search_registry section unavailable")
            _REGISTRY_CACHE[key] = [OfficialSource(**entry) for entry in raw]  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning(
                "search_registry_load_failed",
                extra={"path": key, "error": str(exc), "fallback": "embedded_registry"},
            )
            _REGISTRY_CACHE[key] = list(_EMBEDDED_REGISTRY)
    return list(_REGISTRY_CACHE[key])


#: Default registry, loaded from the bundled legal-sources file at import
#: (falling back to the embedded registry). Kept importable for compatibility.
DEFAULT_REGISTRY: list[OfficialSource] = load_registry()


# Domain-specific authority overrides; anything else in OFFICIAL_DOMAINS
# defaults to OFFICIAL_NEWS (still above any non-official source).
_DOMAIN_AUTHORITY: dict[str, AuthorityLevel] = {
    "jo.gouv.bf": AuthorityLevel.OFFICIAL_GAZETTE,
    "conseil-constitutionnel.bf": AuthorityLevel.CONSTITUTION,
    "coursupreme.bf": AuthorityLevel.CASE_LAW,
    "assembleenationale.bf": AuthorityLevel.LAW,
    "ohada.org": AuthorityLevel.TREATY_OHADA,
    "ohada.com": AuthorityLevel.TREATY_OHADA,
}

_BLOG_PLATFORMS = ("wordpress.", "blogspot.", "medium.com", "over-blog.", "skyblog.")


def _registered_domain(host: str) -> str:
    """Reduce a host to its registrable domain (last two labels)."""
    parts = host.lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()


def authority_for_url(url: Optional[str]) -> AuthorityLevel:
    """Classify a URL's authority from its domain.

    Official Burkina Faso / OHADA domains always rank above non-official
    sources; obvious blog platforms are explicitly marked as BLOG so they
    can never outrank an official source.
    """
    if not url:
        return AuthorityLevel.UNKNOWN
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
    except ValueError:
        return AuthorityLevel.UNKNOWN
    if not host:
        return AuthorityLevel.UNKNOWN

    for domain, authority in _DOMAIN_AUTHORITY.items():
        if host == domain or host.endswith(f".{domain}"):
            return authority
    official_domains = load_official_domains()
    domain = _registered_domain(host)
    if domain in official_domains or any(
        host == d or host.endswith(f".{d}") for d in official_domains
    ):
        return AuthorityLevel.OFFICIAL_NEWS
    if any(marker in host for marker in _BLOG_PLATFORMS):
        return AuthorityLevel.BLOG
    return AuthorityLevel.UNKNOWN


def sources_for_kind(
    kind: SearchKind, registry: Optional[list[OfficialSource]] = None
) -> list[OfficialSource]:
    """Filter the registry for a search kind.

    WEB/WEBSITE tasks search every registered source; other kinds match
    sources registered for exactly that kind.  Without an explicit
    ``registry``, the registry is loaded from the legal-sources file (see
    :func:`load_registry`).
    """
    registry = registry if registry is not None else load_registry()
    if kind in (SearchKind.WEB, SearchKind.WEBSITE):
        return list(registry)
    return [source for source in registry if source.kind == kind]
