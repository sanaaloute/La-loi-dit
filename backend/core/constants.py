"""Static domain data: authority weights, official domains, legal domains.

Tunable policy values (retry budgets, thresholds, top-k, timeouts) are NOT
constants — they live in ``backend.core.config.Settings`` (env prefix
``LEGAL_AI_``); never hardcode them here. The retry strategy is
intentionally strict to avoid infinite loops: max retry = 1 everywhere
(planning, retrieval, reflection) by default.

Overrides (jurisdiction-configurable)
-------------------------------------
The module constants below stay the built-in defaults and remain importable.
``settings.authority_config_path`` may point at a JSON file with optional
keys ``authority_weights`` (merged onto :data:`AUTHORITY_WEIGHTS`),
``official_domains`` and ``legal_domains`` (replacing the defaults when
present); the ``load_*`` functions below apply that merge.  A missing or
corrupt file is ignored with a structured warning.

Routed through the loaders: ``backend.search.sources.authority_for_url``.
Still default-only (use sites outside the configurability scope, taking no
settings/ctx): ``backend.search.orchestrator``,
``backend.agents.evidence_ranking``, ``backend.agents.response_generator``,
``backend.agents.conflict_resolver`` and ``backend.ingestion.classification``
keep using the module constants directly.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional, Union

from backend.core.models import AuthorityLevel

logger = logging.getLogger(__name__)

# --- Authority-weighted evidence ranking ---
# Constitution > OHADA treaty > (amended) law > decree > order > ministerial
# circular > official gazette/case law > official press release > official news
# > uploaded document > trusted legal site > news > blog.
AUTHORITY_WEIGHTS: dict[AuthorityLevel, float] = {
    AuthorityLevel.CONSTITUTION: 1.00,
    AuthorityLevel.TREATY_OHADA: 0.95,
    AuthorityLevel.LAW: 0.90,
    AuthorityLevel.AMENDED_LAW: 0.92,
    AuthorityLevel.DECREE: 0.80,
    AuthorityLevel.ORDER: 0.72,
    AuthorityLevel.MINISTERIAL_CIRCULAR: 0.65,
    AuthorityLevel.OFFICIAL_GAZETTE: 0.78,
    AuthorityLevel.CASE_LAW: 0.70,
    AuthorityLevel.OFFICIAL_PRESS_RELEASE: 0.55,
    AuthorityLevel.OFFICIAL_NEWS: 0.45,
    AuthorityLevel.UPLOADED_DOCUMENT: 0.50,
    AuthorityLevel.TRUSTED_LEGAL_SITE: 0.40,
    AuthorityLevel.NEWS: 0.25,
    AuthorityLevel.BLOG: 0.10,
    AuthorityLevel.UNKNOWN: 0.15,
}

# Official Burkina Faso / OHADA domains never outranked by blogs.
OFFICIAL_DOMAINS = (
    "gouv.bf",
    "assembleenationale.bf",
    "conseil-constitutionnel.bf",
    "coursupreme.bf",
    "jo.gouv.bf",
    "ohada.org",
    "ohada.com",
    "finances.gov.bf",
    "travail.gov.bf",
    "justice.gov.bf",
)

# Domains the system covers; the planner maps keywords to these.
LEGAL_DOMAINS = (
    "constitution",
    "criminal_law",
    "civil_law",
    "family_code",
    "labor_code",
    "tax_law",
    "commercial_law",
    "ohada_law",
    "administrative_law",
    "land_law",
    "procurement_law",
    "environmental_law",
    "immigration",
    "public_service",
    "elections",
    "health_regulations",
    "education_regulations",
    "government_procedures",
)

DEFAULT_RESPONSE_LANGUAGE = "fr"  # Burkina Faso's official language


# ---------------------------------------------------------------------------
# Authority-config overrides (settings.authority_config_path)
# ---------------------------------------------------------------------------

_AUTHORITY_CONFIG_CACHE: dict[str, dict[str, Any]] = {}


def _resolve_authority_config_path(path: Optional[Union[str, Path]]) -> Optional[str]:
    if path:
        return str(path)
    try:
        from backend.core.config import get_settings

        return getattr(get_settings(), "authority_config_path", None) or None
    except Exception:  # settings unavailable: no override
        return None


def load_authority_config(path: Optional[Union[str, Path]] = None) -> dict[str, Any]:
    """Load the authority-config JSON override (``{}`` when unset/corrupt).

    Expected shape: ``{"authority_weights": {...}?, "official_domains": [...]?,
    "legal_domains": [...]?}`` — every key optional; absent keys keep the
    module defaults.  Never raises; a structured warning is logged instead.
    """
    resolved = _resolve_authority_config_path(path)
    if not resolved:
        return {}
    if resolved not in _AUTHORITY_CONFIG_CACHE:
        try:
            data = json.loads(Path(resolved).read_text(encoding="utf-8"))
            config = data if isinstance(data, dict) else {}
            if not isinstance(data, dict):
                raise ValueError("authority config must be a JSON object")
        except Exception as exc:
            logger.warning(
                "authority_config_load_failed",
                extra={"path": resolved, "error": str(exc), "fallback": "module_defaults"},
            )
            config = {}
        _AUTHORITY_CONFIG_CACHE[resolved] = config
    return _AUTHORITY_CONFIG_CACHE[resolved]


def load_authority_weights(
    path: Optional[Union[str, Path]] = None
) -> dict[AuthorityLevel, float]:
    """:data:`AUTHORITY_WEIGHTS` merged with any ``authority_weights`` override.

    Override keys accept both enum values (``"law"``) and names (``"LAW"``);
    unknown levels are skipped with a warning.
    """
    weights = dict(AUTHORITY_WEIGHTS)
    overrides = load_authority_config(path).get("authority_weights") or {}
    for key, value in overrides.items():
        level: Optional[AuthorityLevel]
        try:
            level = key if isinstance(key, AuthorityLevel) else AuthorityLevel(str(key))
        except ValueError:
            try:
                level = AuthorityLevel[str(key)]
            except KeyError:
                logger.warning(
                    "authority_weight_unknown_level",
                    extra={"level": str(key), "fallback": "skipped"},
                )
                continue
        try:
            weights[level] = float(value)
        except (TypeError, ValueError):
            logger.warning(
                "authority_weight_invalid_value",
                extra={"level": str(key), "value": str(value), "fallback": "skipped"},
            )
    return weights


def load_official_domains(path: Optional[Union[str, Path]] = None) -> tuple[str, ...]:
    """:data:`OFFICIAL_DOMAINS`, replaced by any ``official_domains`` override."""
    domains = load_authority_config(path).get("official_domains")
    if not domains:
        return OFFICIAL_DOMAINS
    return tuple(str(d) for d in domains)


def load_legal_domains(path: Optional[Union[str, Path]] = None) -> tuple[str, ...]:
    """:data:`LEGAL_DOMAINS`, replaced by any ``legal_domains`` override."""
    domains = load_authority_config(path).get("legal_domains")
    if not domains:
        return LEGAL_DOMAINS
    return tuple(str(d) for d in domains)
