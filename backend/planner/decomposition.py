"""Deterministic question decomposition fallback (spec §15).

A small curated French legal issue taxonomy keyed by domain/topic keywords.
Used by the heuristic planner when the LLM is unavailable: a broad question
(droits, procédure, obligations...) matching a known topic is decomposed into
its underlying legal issues so retrieval searches per issue instead of only
repeating the question's own words.

Data source (jurisdiction-configurable)
---------------------------------------
The primary taxonomy source is the JSON file ``data/decomposition.json``
(``{"topics": {name: {domains, keywords, issues}, ...}}``).
``settings.decomposition_path`` points at an alternative file.  A missing or
corrupt file falls back to the embedded ``_TOPIC_TAXONOMY`` below with a
structured warning — decomposition never crashes.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional, TypedDict, Union

from backend.core.models import QuestionType

logger = logging.getLogger(__name__)

#: Bundled taxonomy shipped with the repository.
DEFAULT_TAXONOMY_PATH = Path(__file__).resolve().parents[2] / "data" / "decomposition.json"


class _Topic(TypedDict):
    domains: tuple[str, ...]
    keywords: tuple[str, ...]
    issues: tuple[str, ...]


# Question types broad enough to warrant an issue-based decomposition.
# Specific lookups (article, amount, definition, current/historical law...)
# are answered by direct searches and are never decomposed here.
_DECOMPOSABLE_TYPES = {
    QuestionType.RIGHTS,
    QuestionType.OBLIGATIONS,
    QuestionType.PROCEDURE,
    QuestionType.LEGAL_RULE,
    QuestionType.CASE_ANALYSIS,
    QuestionType.GENERAL,
}

_TOPIC_TAXONOMY: dict[str, _Topic] = {
    "licenciement": {
        "domains": ("labor_code",),
        "keywords": ("licenciement", "licencié", "licencie", r"\bdismissal\b", r"\bdismissed\b", r"\bfired\b"),
        "issues": (
            "motifs légitimes du licenciement",
            "préavis et indemnité compensatrice de préavis",
            "indemnité de licenciement",
            "licenciement abusif et dommages-intérêts",
            "droits acquis du salarié (congés payés, certificat de travail)",
            "voies de recours du salarié licencié",
            "juridiction compétente en cas de licenciement",
        ),
    },
    "contrat_de_travail": {
        "domains": ("labor_code",),
        "keywords": ("contrat de travail", "employment contract", "work contract"),
        "issues": (
            "formation et preuve du contrat de travail",
            "période d'essai",
            "modification du contrat de travail",
            "rupture du contrat de travail",
            "obligations des parties au contrat de travail",
        ),
    },
    "divorce": {
        "domains": ("family_code",),
        "keywords": ("divorce",),
        "issues": (
            "formes et causes de divorce",
            "procédure de divorce",
            "conséquences patrimoniales du divorce",
            "pension alimentaire",
            "garde des enfants et autorité parentale",
        ),
    },
    "bail_loyer": {
        "domains": ("civil_law", "land_law"),
        "keywords": (r"\bbail", "loyer", "locataire", "location", r"\blease\b", r"\brent\b", r"\btenant\b", r"\blandlord\b"),
        "issues": (
            "formation et contenu du bail",
            "fixation et révision du loyer",
            "charges et réparations locatives",
            "congé et résiliation du bail",
            "expulsion du locataire",
        ),
    },
    "succession": {
        "domains": ("civil_law", "family_code"),
        "keywords": ("succession", "héritage", "héritier", "testament", r"\binheritance\b", r"\bheir\b", r"\bwill\b"),
        "issues": (
            "ouverture de la succession et héritiers",
            "réserve héréditaire et quotité disponible",
            "testament et legs",
            "acceptation et renonciation à la succession",
            "partage successoral",
        ),
    },
    "societe_ohada": {
        "domains": ("commercial_law", "ohada_law"),
        "keywords": ("société", "sarl", " ohada", r"\bcompany\b", r"\bcorporation\b"),
        "issues": (
            "formes de sociétés commerciales OHADA",
            "constitution et immatriculation de la société",
            "capital social et apports",
            "dirigeants sociaux et responsabilité",
            "dissolution et liquidation de la société",
        ),
    },
}


def _resolve_taxonomy_path() -> Path:
    """``settings.decomposition_path`` when set, else the bundled JSON file."""
    try:
        from backend.core.config import get_settings

        configured = getattr(get_settings(), "decomposition_path", None)
    except Exception:  # settings unavailable: stay on the bundled default
        configured = None
    return Path(configured) if configured else DEFAULT_TAXONOMY_PATH


def load_taxonomy(path: Optional[Union[str, Path]] = None) -> dict[str, _Topic]:
    """Load the issue taxonomy from a JSON file.

    Resolution order: explicit ``path`` → ``settings.decomposition_path`` →
    the bundled ``data/decomposition.json``.  A missing/corrupt file falls
    back to the embedded ``_TOPIC_TAXONOMY`` with a structured warning.
    """
    resolved = Path(path) if path else _resolve_taxonomy_path()
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
        raw_topics = data.get("topics") if isinstance(data, dict) else data
        if not isinstance(raw_topics, dict):
            raise ValueError("decomposition file must hold a mapping of topics")
        taxonomy: dict[str, _Topic] = {}
        for name, raw in raw_topics.items():
            taxonomy[str(name)] = _Topic(
                domains=tuple(str(d) for d in raw.get("domains") or ()),
                keywords=tuple(str(k) for k in raw["keywords"]),
                issues=tuple(str(i) for i in raw["issues"]),
            )
        return taxonomy
    except Exception as exc:
        logger.warning(
            "decomposition_load_failed",
            extra={"path": str(resolved), "error": str(exc), "fallback": "embedded_taxonomy"},
        )
        return dict(_TOPIC_TAXONOMY)


_TAXONOMY_CACHE: dict[str, dict[str, _Topic]] = {}


def _active_taxonomy() -> dict[str, _Topic]:
    """The effective taxonomy for decomposition (cached per resolved path)."""
    key = str(_resolve_taxonomy_path())
    if key not in _TAXONOMY_CACHE:
        _TAXONOMY_CACHE[key] = load_taxonomy(key)
    return _TAXONOMY_CACHE[key]


def deterministic_decompose(
    query: str,
    question_type: QuestionType = QuestionType.GENERAL,
    legal_domains: list[str] | None = None,
) -> list[str]:
    """Return curated sub-issue search strings for a broad question, or [].

    Only decomposable (broad) question types are expanded; when
    ``legal_domains`` is given, matched topics whose domains do not intersect
    it are skipped.  Returns [] when nothing matches — the caller then keeps
    its existing behavior.
    """
    if question_type not in _DECOMPOSABLE_TYPES:
        return []
    q = query.lower()
    domains = set(legal_domains or [])
    issues: list[str] = []
    for topic in _active_taxonomy().values():
        if not any(re.search(k, q) for k in topic["keywords"]):
            continue
        if domains and not domains.intersection(topic["domains"]):
            continue
        issues.extend(topic["issues"])
    # Deduplicate while preserving order.
    return list(dict.fromkeys(issues))
