"""Heuristic classification of legal documents from their name and content.

These helpers fill in ``authority``, ``legal_domains``, ``document_type``
and ``law_number`` metadata when the caller did not provide them explicitly.
They are intentionally conservative: when classification is uncertain, they
leave values empty/unknown rather than guess wrongly.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional, Union

from backend.core.models import AuthorityLevel, DocumentType

logger = logging.getLogger(__name__)

# Authority rules: each rule is (groups, authority). A group matches when any
# of its keywords is present; the rule matches when every group matches.
# Order matters: more specific rules must come first.
_AUTHORITY_RULES: list[tuple[list[tuple[str, ...]], AuthorityLevel]] = [
    ([("constitution",)], AuthorityLevel.CONSTITUTION),
    # OHADA treaty and uniform acts — standalone abbreviations or explicit OHADA refs.
    ([("traité ohada", "traite ohada", "ohada")], AuthorityLevel.TREATY_OHADA),
    ([("acte uniforme", "acte_uniforme", "aus", "audcg", "auscgie")], AuthorityLevel.TREATY_OHADA),
    ([("code", "loi")], AuthorityLevel.LAW),
    ([("décret", "decret")], AuthorityLevel.DECREE),
    ([("arrêté", "arrete")], AuthorityLevel.ORDER),
    ([("circulaire",)], AuthorityLevel.MINISTERIAL_CIRCULAR),
    ([("journal officiel", "journal_officiel")], AuthorityLevel.OFFICIAL_GAZETTE),
    ([("jurisprudence", "arrêt", "arret", "cour", "tribunal")], AuthorityLevel.CASE_LAW),
    ([("communiqué", "communique", "presse")], AuthorityLevel.OFFICIAL_PRESS_RELEASE),
]

# Embedded fallback taxonomy, used only when the JSON file is missing/corrupt.
# Same per-entry shape as data/legal_domains.json: a French display "label"
# (admin UI) plus "keywords" — matched against an accent-folded haystack (see
# :func:`_normalize`), so they are accent-folded once at load time; stems like
# "licenci"/"salari"/"preavis" cover inflected forms
# (licencié/licenciement, salarié/salarial).
_DOMAIN_KEYWORDS: dict[str, dict[str, Any]] = {
    "constitution": {"label": "Constitution", "keywords": ("constitution", "transition")},
    "criminal_law": {
        "label": "Droit pénal",
        "keywords": ("pénal", "penal", "infraction", "crime", "délit", "criminal"),
    },
    "civil_law": {
        "label": "Droit civil",
        "keywords": ("civil", "contrat", "responsabilité civile", "droit commercial général"),
    },
    "family_code": {"label": "Droit de la famille", "keywords": ("famille", "mariage", "divorce", "filiation")},
    "labor_code": {
        "label": "Droit du travail",
        "keywords": ("travail", "licenci", "salaire", "salari", "employeur", "preavis"),
    },
    "tax_law": {"label": "Droit fiscal", "keywords": ("impôt", "impots", "fiscal", "taxe", "tva")},
    "commercial_law": {
        "label": "Droit commercial",
        "keywords": ("commercial", "société", "commerce", "ohada", "sarl", "sa ", "sociétés commerciales"),
    },
    "ohada_law": {"label": "Droit OHADA", "keywords": ("ohada", "acte uniforme")},
    "administrative_law": {
        "label": "Droit administratif",
        "keywords": ("administratif", "administration", "fonction publique"),
    },
    "land_law": {"label": "Droit foncier", "keywords": ("foncier", "terrain", "propriété")},
    "procurement_law": {"label": "Marchés publics", "keywords": ("marchés publics", "appel d'offres")},
    "environmental_law": {"label": "Droit de l'environnement", "keywords": ("environnement", "pollution")},
    "immigration": {
        "label": "Immigration",
        "keywords": ("immigration", "visa", "titre de séjour", "étranger"),
    },
    "public_service": {"label": "Fonction publique", "keywords": ("fonction publique", "fonctionnaire")},
    "elections": {"label": "Élections", "keywords": ("élection", "élections", "ceni", "vote")},
    "health_regulations": {"label": "Réglementation sanitaire", "keywords": ("santé", "hôpital")},
    "education_regulations": {
        "label": "Droit de l'éducation",
        "keywords": ("éducation", "école", "université"),
    },
    "government_procedures": {"label": "Démarches administratives", "keywords": ("procédure", "guichet")},
}


def _normalize(text: str) -> str:
    """Lower-case, strip accents lightly, keep ASCII-ish for matching."""
    text = text.lower()
    for a, b in (
        ("é", "e"),
        ("è", "e"),
        ("ê", "e"),
        ("à", "a"),
        ("â", "a"),
        ("ô", "o"),
        ("ù", "u"),
        ("ç", "c"),
        ("î", "i"),
        ("ï", "i"),
        ("-", " "),
        ("_", " "),
    ):
        text = text.replace(a, b)
    return text


def domain_slug(name: str) -> str:
    """Folder/display name -> domain slug ("Code de la route" -> "code_de_la_route")."""
    return re.sub(r"[^a-z0-9]+", "_", _normalize(name)).strip("_")


#: Bundled domain-taxonomy file shipped with the repository.
_DEFAULT_DOMAINS_PATH = Path(__file__).resolve().parents[2] / "data" / "legal_domains.json"

_DOMAINS_CACHE: dict[str, tuple[dict[str, dict[str, Any]], Optional[float]]] = {}


def resolve_domains_path(path: Optional[Union[str, Path]] = None) -> Path:
    """Taxonomy file resolution: explicit ``path`` → ``settings.legal_domains_path``
    → the bundled ``data/legal_domains.json``."""
    resolved = path
    if resolved is None:
        try:
            from backend.core.config import get_settings

            resolved = getattr(get_settings(), "legal_domains_path", None)
        except Exception:  # settings unavailable: stay on the bundled default
            resolved = None
    return Path(resolved or _DEFAULT_DOMAINS_PATH)


def _humanize_domain_slug(slug: str) -> str:
    """Fallback label for an entry without one ("civil_law" -> "Civil law")."""
    return slug.replace("_", " ").capitalize() or slug


def _embedded_domain_entries() -> dict[str, dict[str, Any]]:
    """The embedded fallback taxonomy in parsed-entries shape."""
    return {
        domain: {"label": entry["label"], "keywords": [_normalize(kw) for kw in entry["keywords"]]}
        for domain, entry in _DOMAIN_KEYWORDS.items()
    }


def _read_domain_entries(resolved: Path) -> dict[str, dict[str, Any]]:
    """Parse the taxonomy file into slug -> {"label": str, "keywords": [...]}.

    Accepts both the current shape (``{"label": ..., "keywords": [...]}``) and
    the legacy bare keyword list (``"slug": [...]`` — label left empty, the
    humanized slug is used at display time). Raises on a missing/corrupt
    file; callers fall back to the embedded taxonomy.

    Keywords are accent-folded with :func:`_normalize` here (once, at load
    time) so accented admin-added keywords ("préavis", "société") match the
    accent-folded haystack exactly like the bundled stems.
    """
    data = json.loads(Path(resolved).read_text(encoding="utf-8"))
    raw = data.get("domains") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        raise ValueError("domain keywords file must hold a 'domains' object")
    entries: dict[str, dict[str, Any]] = {}
    for domain, value in raw.items():
        if isinstance(value, dict):
            keywords = value.get("keywords") or []
            label = str(value.get("label") or "").strip()
        else:  # legacy shape: bare keyword list
            keywords = value or []
            label = ""
        entries[str(domain)] = {"label": label, "keywords": [_normalize(str(kw)) for kw in keywords]}
    return entries


def _domain_entries(resolved: Path, kind: str) -> dict[str, dict[str, Any]]:
    """Taxonomy entries for ``resolved``, cached per path — never raises.

    The cache entry carries the file's mtime; when the mtime changes (admin
    edit written by another process/worker), the file is re-read. A missing
    file counts as "no mtime change": the last known entries keep being
    served.
    """
    key = f"{resolved}#{kind}"
    try:
        mtime: Optional[float] = os.stat(resolved).st_mtime
    except OSError:
        mtime = None
    cached = _DOMAINS_CACHE.get(key)
    if cached is not None and (mtime is None or mtime == cached[1]):
        return cached[0]
    try:
        entries = _read_domain_entries(resolved)
    except Exception as exc:
        logger.warning(
            f"{kind}_load_failed",
            extra={"path": str(resolved), "error": str(exc), "fallback": "embedded_domain_keywords"},
        )
        entries = _embedded_domain_entries()
    _DOMAINS_CACHE[key] = (entries, mtime)
    return entries


def invalidate_domain_cache() -> None:
    """Drop every cached taxonomy (called after the admin API rewrites the file)."""
    _DOMAINS_CACHE.clear()


def load_domain_keywords(path: Optional[Union[str, Path]] = None) -> dict[str, list[str]]:
    """Resolve the domain-keyword taxonomy (jurisdiction-configurable).

    The file shape is ``{"version": 1, "domains": {"<domain_slug>": {"label":
    "Droit civil", "keywords": ["unaccented stem", ...]}}}``; the legacy shape
    ``{"<domain_slug>": ["stem", ...]}`` is still accepted.  Keywords are
    accent-folded at load time (see :func:`_normalize`) because they are
    matched against an accent-folded haystack — accented keywords work too.
    A missing/corrupt file falls back to the embedded
    :data:`_DOMAIN_KEYWORDS` with a structured warning — never raises.
    Results are cached per resolved path and re-read when the file's mtime
    changes.
    """
    entries = _domain_entries(resolve_domains_path(path), "domain_keywords")
    return {domain: entry["keywords"] for domain, entry in entries.items()}


def load_domain_labels(path: Optional[Union[str, Path]] = None) -> dict[str, str]:
    """Resolve the domain display labels (slug -> French label).

    Same resolution/caching as :func:`load_domain_keywords`. Entries without
    an explicit label (legacy files) fall back to a humanized slug
    ("civil_law" -> "Civil law").
    """
    entries = _domain_entries(resolve_domains_path(path), "domain_labels")
    return {
        domain: entry["label"] or _humanize_domain_slug(domain)
        for domain, entry in entries.items()
    }


def _matches_rule(haystack: str, groups: list[tuple[str, ...]]) -> bool:
    """True when every keyword group has at least one keyword in haystack."""
    for group in groups:
        if not any(kw in haystack for kw in group):
            return False
    return True


def infer_authority(name: str) -> AuthorityLevel:
    """Best-guess authority level from a document filename/title."""
    lowered = _normalize(name)
    for groups, authority in _AUTHORITY_RULES:
        if _matches_rule(lowered, groups):
            return authority
    return AuthorityLevel.UNKNOWN


def infer_legal_domains(name: str, text_sample: str = "") -> list[str]:
    """Return legal-domain labels matched in the document name or a sample.

    Iterates the taxonomy resolved by :func:`load_domain_keywords` (not a
    hardcoded list) so domains added to ``data/legal_domains.json`` are
    recognized with zero code changes.
    """
    haystack = _normalize(name)
    if text_sample:
        haystack += " " + _normalize(text_sample[:2000])
    return [
        domain
        for domain, keywords in load_domain_keywords().items()
        if any(kw in haystack for kw in keywords)
    ]


# Document-type rules: (keywords, type). Order matters — more specific
# instruments first. In particular "arrêté"/"décision" (DECISION) must beat
# "arrêt" (CASE_LAW): once normalized, "arrete" contains "arret".
_DOCUMENT_TYPE_RULES: list[tuple[tuple[str, ...], DocumentType]] = [
    (("traité", "traite", "acte uniforme", "acte_uniforme"), DocumentType.TREATY),
    (("code",), DocumentType.CODE),
    (("ordonnance",), DocumentType.ORDINANCE),
    (("décret", "decret"), DocumentType.DECREE),
    (("arrêté", "arrete", "décision", "decision"), DocumentType.DECISION),
    (("jurisprudence", "arrêt", "arret"), DocumentType.CASE_LAW),
    (("loi",), DocumentType.LAW),
]

_LAW_NUMBER_RE = re.compile(
    # keyword, up to 3 qualifier words ("arrêté conjoint", "loi de finances"),
    # optional "n°", then the number itself (must start with a digit).
    r"(?:loi|d[ée]cret|ordonnance|arr[êe]t[ée]?|d[ée]cision)"
    r"(?:\s+[a-zàâäéèêëîïôöùûüç]+){0,3}?\s*(?:n[°o]?\s*)?(\d[\w\-/]*)",
    re.IGNORECASE,
)


def infer_document_type(name: str, text_sample: str = "") -> Optional[DocumentType]:
    """Best-guess instrument type from the document name (then content sample).

    Conservative like :func:`infer_authority`: returns None when nothing
    matches rather than guessing ``OTHER``.
    """
    haystack = _normalize(name)
    if not any(kw in haystack for rule in _DOCUMENT_TYPE_RULES for kw in rule[0]) and text_sample:
        haystack += " " + _normalize(text_sample[:2000])
    for keywords, doc_type in _DOCUMENT_TYPE_RULES:
        if any(kw in haystack for kw in keywords):
            return doc_type
    return None


def extract_law_number(name: str) -> Optional[str]:
    """Extract a structured law number (e.g. "028-2008/AN") from a title.

    Matches ``loi``/``décret``/``ordonnance``/``arrêté``/``décision`` followed
    by optional qualifier words, an optional ``n°`` and a digit-led number;
    returns None when absent ("Loi de finances" without a year, titles
    without any numbered instrument).
    """
    match = _LAW_NUMBER_RE.search(name)
    if not match:
        return None
    number = match.group(1).strip().rstrip(".,;:)")
    return number.upper() or None
