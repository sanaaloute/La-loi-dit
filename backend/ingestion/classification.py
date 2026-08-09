"""Heuristic classification of legal documents from their name and content.

These helpers fill in ``authority``, ``legal_domains``, ``document_type``
and ``law_number`` metadata when the caller did not provide them explicitly.
They are intentionally conservative: when classification is uncertain, they
leave values empty/unknown rather than guess wrongly.
"""

from __future__ import annotations

import re
from typing import Optional

from backend.core.constants import LEGAL_DOMAINS
from backend.core.models import AuthorityLevel, DocumentType

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

_DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "constitution": ("constitution", "transition"),
    "criminal_law": ("pénal", "penal", "infraction", "crime", "délit", "criminal"),
    "civil_law": ("civil", "contrat", "responsabilité civile", "droit commercial général"),
    "family_code": ("famille", "mariage", "divorce", "filiation"),
    "labor_code": ("travail", "licenciement", "salaire", "employeur"),
    "tax_law": ("impôt", "impots", "fiscal", "taxe", "tva"),
    "commercial_law": ("commercial", "société", "commerce", "ohada", "sarl", "sa ", "sociétés commerciales"),
    "ohada_law": ("ohada", "acte uniforme"),
    "administrative_law": ("administratif", "administration", "fonction publique"),
    "land_law": ("foncier", "terrain", "propriété"),
    "procurement_law": ("marchés publics", "appel d'offres"),
    "environmental_law": ("environnement", "pollution"),
    "immigration": ("immigration", "visa", "titre de séjour", "étranger"),
    "public_service": ("fonction publique", "fonctionnaire"),
    "elections": ("élection", "élections", "ceni", "vote"),
    "health_regulations": ("santé", "hôpital"),
    "education_regulations": ("éducation", "école", "université"),
    "government_procedures": ("procédure", "guichet"),
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
    """Return legal-domain labels matched in the document name or a sample."""
    haystack = _normalize(name)
    if text_sample:
        haystack += " " + _normalize(text_sample[:2000])
    domains: list[str] = []
    for domain in LEGAL_DOMAINS:
        if domain in _DOMAIN_KEYWORDS and any(kw in haystack for kw in _DOMAIN_KEYWORDS[domain]):
            domains.append(domain)
    return domains


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
