"""Legal terminology lexicon (spec §29) and query expansion support (§14).

A typed, data-driven lexicon of Burkina Faso / French legal terminology.
Each entry records a canonical term plus its relations: synonyms, related
terms, broader terms and narrower terms.  Relations are deliberately NOT
treated as universal interchangeability — e.g. "licenciement" is *related*
to "rupture du contrat de travail" but the two are not identical legal
concepts.  Query expansion therefore:

- matches a query against canonical terms and synonyms only,
- expands with synonyms + related terms (recall-oriented, meaning-preserving),
- never expands with broader/narrower terms by default, because moving up or
  down the hierarchy *changes* the legal meaning (broader loses precision,
  narrower adds qualifications the user never stated),
- only ADDS retrieval queries; the user's original terms are never rewritten
  or replaced.

Data source (jurisdiction-configurable)
---------------------------------------
The primary lexicon source is the JSON file ``data/terminology.json``
(``{"terms": [{canonical, synonyms, related_terms, broader_terms,
narrower_terms}, ...]}``).  ``settings.terminology_path`` points at an
alternative file (e.g. another jurisdiction's lexicon).  When the resolved
file is missing or corrupt, the embedded ``LEXICON`` below is used instead
and a structured warning is logged — lookup/expansion never crash.  The
embedded copy is kept deliberately as the offline-safe fallback (and for
backward compatibility, since ``LEXICON`` is importable).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)

#: Bundled lexicon shipped with the repository.
DEFAULT_LEXICON_PATH = Path(__file__).resolve().parents[2] / "data" / "terminology.json"


@dataclass(frozen=True)
class TermEntry:
    """One canonical legal term and its typed relations."""

    canonical: str
    synonyms: list[str] = field(default_factory=list)
    related_terms: list[str] = field(default_factory=list)
    broader_terms: list[str] = field(default_factory=list)
    narrower_terms: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Lexicon data — French primary, English equivalents where natural.
# ---------------------------------------------------------------------------

LEXICON: list[TermEntry] = [
    # ---- Labour law (droit du travail) ----
    TermEntry(
        canonical="licenciement",
        synonyms=["licencié", "licencier", "renvoi", "dismissal"],
        related_terms=[
            "rupture du contrat de travail",
            "résiliation du contrat de travail",
            "indemnité de licenciement",
            "préavis",
        ],
        broader_terms=["cessation du contrat de travail"],
        narrower_terms=["licenciement abusif", "licenciement économique", "licenciement pour faute"],
    ),
    TermEntry(
        canonical="préavis",
        synonyms=["délai de prévenance", "notice period"],
        related_terms=["indemnité compensatrice de préavis", "licenciement", "démission"],
        broader_terms=["rupture du contrat de travail"],
        narrower_terms=["préavis de licenciement", "préavis de démission"],
    ),
    TermEntry(
        canonical="indemnité de licenciement",
        synonyms=["severance pay"],
        related_terms=["indemnité compensatrice de préavis", "dommages et intérêts", "licenciement"],
        broader_terms=["indemnités de rupture"],
        narrower_terms=["indemnité de licenciement économique"],
    ),
    TermEntry(
        canonical="licenciement abusif",
        synonyms=["licenciement sans cause réelle et sérieuse", "unfair dismissal"],
        related_terms=["dommages et intérêts", "réintégration", "licenciement"],
        broader_terms=["licenciement"],
        narrower_terms=["licenciement nul"],
    ),
    TermEntry(
        canonical="licenciement économique",
        synonyms=["licenciement pour motif économique", "redundancy"],
        related_terms=["reclassement", "indemnité de licenciement"],
        broader_terms=["licenciement"],
    ),
    TermEntry(
        canonical="contrat de travail",
        synonyms=["employment contract", "work contract"],
        related_terms=["salarié", "employeur", "rémunération"],
        broader_terms=["contrat"],
        narrower_terms=[
            "contrat à durée indéterminée",
            "CDI",
            "contrat à durée déterminée",
            "CDD",
            "contrat d'apprentissage",
        ],
    ),
    TermEntry(
        canonical="faute lourde",
        synonyms=["gross misconduct"],
        related_terms=["faute grave", "licenciement disciplinaire", "faute simple"],
        broader_terms=["faute professionnelle"],
    ),
    TermEntry(
        canonical="faute simple",
        synonyms=["faute légère", "minor misconduct"],
        related_terms=["faute grave", "faute lourde", "sanction disciplinaire"],
        broader_terms=["faute professionnelle"],
    ),
    TermEntry(
        canonical="démission",
        synonyms=["démissionner", "resignation"],
        related_terms=["rupture du contrat de travail", "préavis"],
        broader_terms=["cessation du contrat de travail"],
    ),
    TermEntry(
        canonical="salaire",
        synonyms=["rémunération", "paie", "wage", "salary"],
        related_terms=["salaire minimum", "primes", "bulletin de paie"],
        broader_terms=["rémunération"],
        narrower_terms=["salaire minimum interprofessionnel garanti", "SMIG"],
    ),
    TermEntry(
        canonical="tribunal du travail",
        synonyms=["labour court", "labor court"],
        related_terms=["juridiction compétente", "inspecteur du travail", "contentieux du travail"],
        broader_terms=["juridiction"],
    ),
    TermEntry(
        canonical="inspecteur du travail",
        synonyms=["labour inspector", "labor inspector", "inspection du travail"],
        related_terms=["conciliation", "tribunal du travail", "ministère du travail"],
        broader_terms=["administration du travail"],
    ),
    TermEntry(
        canonical="congés payés",
        synonyms=["congé annuel", "paid leave", "annual leave"],
        related_terms=["certificat de travail", "droits acquis du salarié"],
        broader_terms=["droits du salarié"],
    ),
    TermEntry(
        canonical="certificat de travail",
        synonyms=["work certificate"],
        related_terms=["solde de tout compte", "congés payés", "fin du contrat de travail"],
        broader_terms=["droits acquis du salarié"],
    ),
    # ---- Civil law / procedure ----
    TermEntry(
        canonical="bail",
        synonyms=["bail locatif", "lease"],
        related_terms=["location", "loyer", "locataire", "bailleur"],
        broader_terms=["contrat de location"],
        narrower_terms=["bail commercial", "bail d'habitation", "bail rural"],
    ),
    TermEntry(
        canonical="loyer",
        synonyms=["rent", "rental"],
        related_terms=["bail", "charges locatives", "révision du loyer", "impayés de loyer"],
        broader_terms=["obligations locatives"],
    ),
    TermEntry(
        canonical="expulsion locative",
        synonyms=["expulsion du locataire", "eviction"],
        related_terms=["bail", "commandement de payer", "loyer"],
        broader_terms=["procédure d'exécution"],
    ),
    TermEntry(
        canonical="responsabilité civile",
        synonyms=["civil liability"],
        related_terms=["dommages et intérêts", "faute", "préjudice"],
        broader_terms=["droit des obligations"],
        narrower_terms=["responsabilité contractuelle", "responsabilité délictuelle"],
    ),
    TermEntry(
        canonical="dommages et intérêts",
        synonyms=["dommages-intérêts", "indemnisation", "damages"],
        related_terms=["préjudice", "responsabilité civile", "réparation"],
        broader_terms=["réparation du préjudice"],
    ),
    TermEntry(
        canonical="prescription",
        synonyms=["prescription extinctive", "limitation period"],
        related_terms=["délai de recours", "forclusion"],
        broader_terms=["droit des obligations"],
        narrower_terms=["prescription civile", "prescription pénale"],
    ),
    TermEntry(
        canonical="mise en demeure",
        synonyms=["sommation", "formal notice"],
        related_terms=["injonction de payer", "recouvrement", "délai de paiement"],
        broader_terms=["procédure civile"],
    ),
    TermEntry(
        canonical="contrat",
        synonyms=["convention", "contract", "agreement"],
        related_terms=["obligations contractuelles", "clause contractuelle"],
        broader_terms=["acte juridique"],
        narrower_terms=["contrat de travail", "contrat de vente", "contrat de bail"],
    ),
    TermEntry(
        canonical="vente",
        synonyms=["sale"],
        related_terms=["prix de vente", "transfert de propriété", "acheteur", "vendeur"],
        broader_terms=["contrat"],
        narrower_terms=["vente commerciale", "vente immobilière"],
    ),
    TermEntry(
        canonical="juridiction compétente",
        synonyms=["competent court", "tribunal compétent"],
        related_terms=["tribunal du travail", "compétence territoriale", "compétence d'attribution"],
        broader_terms=["procédure judiciaire"],
    ),
    # ---- Family law ----
    TermEntry(
        canonical="divorce",
        synonyms=["dissolution du mariage"],
        related_terms=["séparation de corps", "pension alimentaire", "garde des enfants"],
        broader_terms=["dissolution du lien conjugal"],
        narrower_terms=["divorce contentieux", "divorce par consentement mutuel"],
    ),
    TermEntry(
        canonical="garde des enfants",
        synonyms=["garde d'enfants", "custody", "child custody"],
        related_terms=["autorité parentale", "droit de visite", "pension alimentaire", "divorce"],
        broader_terms=["autorité parentale"],
    ),
    TermEntry(
        canonical="pension alimentaire",
        synonyms=["alimony", "child support"],
        related_terms=["divorce", "garde des enfants", "obligation alimentaire"],
        broader_terms=["obligation alimentaire"],
    ),
    TermEntry(
        canonical="mariage",
        synonyms=["marriage"],
        related_terms=["régime matrimonial", "célébration du mariage", "acte de mariage"],
        narrower_terms=["mariage civil", "mariage coutumier", "mariage religieux"],
    ),
    TermEntry(
        canonical="succession",
        synonyms=["héritage", "inheritance"],
        related_terms=["héritier", "testament", "partage successoral", "réserve héréditaire"],
        broader_terms=["droit des successions"],
        narrower_terms=["succession ab intestat", "succession testamentaire"],
    ),
    TermEntry(
        canonical="testament",
        synonyms=["will", "last will"],
        related_terms=["legs", "héritier", "notaire", "succession"],
        broader_terms=["droit des successions"],
        narrower_terms=["testament olographe", "testament authentique"],
    ),
    TermEntry(
        canonical="filiation",
        synonyms=["parentage"],
        related_terms=["paternité", "reconnaissance d'enfant", "autorité parentale"],
        narrower_terms=["filiation légitime", "filiation naturelle"],
    ),
    TermEntry(
        canonical="autorité parentale",
        synonyms=["parental authority"],
        related_terms=["garde des enfants", "droit de visite", "filiation"],
    ),
    # ---- Criminal law ----
    TermEntry(
        canonical="infraction",
        synonyms=["offense", "criminal offence"],
        related_terms=["peine", "poursuites pénales", "code pénal"],
        broader_terms=["droit pénal"],
        narrower_terms=["crime", "délit", "contravention"],
    ),
    TermEntry(
        canonical="vol",
        synonyms=["theft", "larcin"],
        related_terms=["recel", "cambriolage", "atteinte aux biens"],
        broader_terms=["infraction contre les biens"],
        narrower_terms=["vol simple", "vol qualifié"],
    ),
    TermEntry(
        canonical="coups et blessures",
        synonyms=["assault", "violences volontaires"],
        related_terms=["agression", "légitime défense"],
        broader_terms=["atteintes aux personnes"],
    ),
    TermEntry(
        canonical="garde à vue",
        synonyms=["police custody"],
        related_terms=["arrestation", "procès-verbal", "officier de police judiciaire"],
        broader_terms=["mesures privatives de liberté"],
    ),
    TermEntry(
        canonical="amende",
        synonyms=["fine"],
        related_terms=["peine", "sanction pénale", "contravention"],
        broader_terms=["peine"],
        narrower_terms=["amende pénale", "amende administrative"],
    ),
    TermEntry(
        canonical="légitime défense",
        synonyms=["self-defence", "self-defense"],
        related_terms=["état de nécessité", "coups et blessures"],
        broader_terms=["causes d'irresponsabilité pénale"],
    ),
    # ---- Commercial / OHADA law ----
    TermEntry(
        canonical="société commerciale",
        synonyms=["société", "commercial company"],
        related_terms=["OHADA", "capital social", "associés", "RCCM"],
        broader_terms=["personne morale"],
        narrower_terms=["SARL", "SA", "société en nom collectif", "GIE"],
    ),
    TermEntry(
        canonical="SARL",
        synonyms=["société à responsabilité limitée", "limited liability company"],
        related_terms=["capital social", "gérant", "OHADA"],
        broader_terms=["société commerciale"],
    ),
    TermEntry(
        canonical="RCCM",
        synonyms=["registre du commerce et du crédit mobilier", "trade register"],
        related_terms=["immatriculation", "OHADA", "greffe du tribunal"],
        broader_terms=["formalités des entreprises"],
    ),
    TermEntry(
        canonical="procédure collective",
        synonyms=["faillite", "bankruptcy", "insolvency"],
        related_terms=["règlement préventif", "redressement judiciaire", "liquidation des biens"],
        broader_terms=["droit OHADA"],
    ),
    TermEntry(
        canonical="bail commercial",
        synonyms=["commercial lease"],
        related_terms=["loyer", "renouvellement du bail", "fonds de commerce"],
        broader_terms=["bail"],
    ),
    TermEntry(
        canonical="capital social",
        synonyms=["share capital"],
        related_terms=["apports", "associés", "statuts de la société"],
        broader_terms=["constitution de la société"],
    ),
    # ---- Administrative law ----
    TermEntry(
        canonical="fonction publique",
        synonyms=["civil service", "public service"],
        related_terms=["fonctionnaire", "concours administratif", "statut des fonctionnaires"],
        broader_terms=["administration publique"],
    ),
    TermEntry(
        canonical="fonctionnaire",
        synonyms=["civil servant", "agent de l'État"],
        related_terms=["fonction publique", "avancement", "discipline administrative"],
        narrower_terms=["fonctionnaire stagiaire", "fonctionnaire titulaire"],
    ),
    TermEntry(
        canonical="acte administratif",
        synonyms=["administrative act", "décision administrative"],
        related_terms=["recours administratif", "contentieux administratif"],
        broader_terms=["action administrative"],
        narrower_terms=["arrêté", "décret", "décision individuelle"],
    ),
    TermEntry(
        canonical="recours administratif",
        synonyms=["administrative appeal"],
        related_terms=["recours hiérarchique", "recours gracieux", "contentieux administratif"],
        broader_terms=["voies de recours"],
    ),
    TermEntry(
        canonical="marché public",
        synonyms=["public procurement", "commande publique"],
        related_terms=["appel d'offres", "passation des marchés"],
        narrower_terms=["marché de travaux", "marché de fournitures", "marché de services"],
    ),
    # ---- Tax law ----
    TermEntry(
        canonical="impôt",
        synonyms=["taxe", "tax", "taxation"],
        related_terms=["fiscalité", "contribuable", "administration fiscale", "déclaration fiscale"],
        broader_terms=["prélèvements obligatoires"],
        narrower_terms=["impôt sur le revenu", "impôt sur les sociétés", "TVA"],
    ),
    TermEntry(
        canonical="TVA",
        synonyms=["taxe sur la valeur ajoutée", "VAT"],
        related_terms=["impôt", "facturation", "déclaration fiscale"],
        broader_terms=["impôts indirects"],
    ),
    TermEntry(
        canonical="contribuable",
        synonyms=["taxpayer", "redevable"],
        related_terms=["fiscalité", "déclaration fiscale", "impôt"],
    ),
    # ---- Land law ----
    TermEntry(
        canonical="propriété foncière",
        synonyms=["land ownership", "propriété immobilière"],
        related_terms=["titre foncier", "bornage", "cadastre", "litige foncier"],
        broader_terms=["droit foncier"],
        narrower_terms=["propriété rurale", "propriété urbaine"],
    ),
    TermEntry(
        canonical="titre foncier",
        synonyms=["land title"],
        related_terms=["immatriculation foncière", "cadastre", "bornage", "propriété foncière"],
        broader_terms=["droit foncier"],
    ),
    TermEntry(
        canonical="bornage",
        synonyms=["délimitation", "boundary marking"],
        related_terms=["titre foncier", "litige foncier", "géomètre-expert"],
        broader_terms=["procédure foncière"],
    ),
    TermEntry(
        canonical="expropriation",
        synonyms=["expropriation pour cause d'utilité publique"],
        related_terms=["indemnisation", "utilité publique", "propriété foncière"],
        broader_terms=["droit foncier"],
    ),
]


# ---------------------------------------------------------------------------
# Normalization / lookup
# ---------------------------------------------------------------------------


def _resolve_lexicon_path() -> Path:
    """``settings.terminology_path`` when set, else the bundled JSON file."""
    try:
        from backend.core.config import get_settings

        configured = getattr(get_settings(), "terminology_path", None)
    except Exception:  # settings unavailable: stay on the bundled default
        configured = None
    return Path(configured) if configured else DEFAULT_LEXICON_PATH


def load_lexicon(path: Optional[Union[str, Path]] = None) -> list[TermEntry]:
    """Load the terminology lexicon from a JSON file.

    Resolution order: explicit ``path`` → ``settings.terminology_path`` → the
    bundled ``data/terminology.json``.  A missing/corrupt file falls back to
    the embedded :data:`LEXICON` with a structured warning — never raises.
    """
    resolved = Path(path) if path else _resolve_lexicon_path()
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
        raw_terms = data.get("terms") if isinstance(data, dict) else data
        if not isinstance(raw_terms, list):
            raise ValueError("terminology file must hold a list of terms")
        lexicon: list[TermEntry] = []
        for raw in raw_terms:
            lexicon.append(
                TermEntry(
                    canonical=str(raw["canonical"]),
                    synonyms=[str(s) for s in raw.get("synonyms") or []],
                    related_terms=[str(s) for s in raw.get("related_terms") or []],
                    broader_terms=[str(s) for s in raw.get("broader_terms") or []],
                    narrower_terms=[str(s) for s in raw.get("narrower_terms") or []],
                )
            )
        return lexicon
    except Exception as exc:
        logger.warning(
            "terminology_load_failed",
            extra={"path": str(resolved), "error": str(exc), "fallback": "embedded_lexicon"},
        )
        return list(LEXICON)


_LEXICON_CACHE: dict[str, list[TermEntry]] = {}


def _active_lexicon() -> list[TermEntry]:
    """The effective lexicon for lookup/expansion (cached per resolved path)."""
    key = str(_resolve_lexicon_path())
    if key not in _LEXICON_CACHE:
        _LEXICON_CACHE[key] = load_lexicon(key)
    return _LEXICON_CACHE[key]


def _normalize(text: str) -> str:
    """Lowercase and strip accents (same French-friendly scheme as BM25)."""
    normalized = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in normalized if not unicodedata.combining(c))


def _contains_phrase(text: str, phrase: str) -> bool:
    """Whole-phrase (word-boundary) match on normalized text."""
    if not phrase:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) is not None


def _surface_forms(entry: TermEntry) -> list[str]:
    """Forms a query may use to refer to the entry: canonical + synonyms.

    Related/broader/narrower terms are deliberately NOT match surfaces:
    a user writing "cessation du contrat de travail" (broader) has not asked
    about "licenciement" specifically, and matching on related terms would
    over-expand retrieval.
    """
    return [entry.canonical, *entry.synonyms]


_ALIAS_INDEX: dict[str, dict[str, str]] = {}  # lexicon key -> {normalized form: canonical}


def _alias_index() -> dict[str, str]:
    key = str(_resolve_lexicon_path())
    if key not in _ALIAS_INDEX:
        index: dict[str, str] = {}
        for entry in _active_lexicon():
            for form in _surface_forms(entry):
                index.setdefault(_normalize(form), entry.canonical)
        _ALIAS_INDEX[key] = index
    return _ALIAS_INDEX[key]


def _entry_by_canonical() -> dict[str, TermEntry]:
    return {entry.canonical: entry for entry in _active_lexicon()}


def lookup(term: str) -> TermEntry | None:
    """Return the lexicon entry for ``term`` (accent/case-insensitive), or None.

    Matches canonical terms and their synonyms (e.g. "Licencié", "PREAVIS").
    """
    canonical = _alias_index().get(_normalize(term.strip()))
    if canonical is None:
        return None
    return _entry_by_canonical().get(canonical)


def expand_terms(query: str) -> dict[str, list[str]]:
    """Map canonical terms found in ``query`` to their expansion terms.

    A term group matches when its canonical form or one of its synonyms
    appears in the query (normalized, whole-phrase).  The expansion terms are
    the entry's synonyms + related terms — broader/narrower terms are excluded
    by design because they change the legal meaning (see module docstring).
    Expansion terms already present in the query are dropped so the result is
    purely additive.
    """
    normalized = _normalize(query)
    expansions: dict[str, list[str]] = {}
    for entry in _active_lexicon():
        if not any(_contains_phrase(normalized, _normalize(form)) for form in _surface_forms(entry)):
            continue
        terms = [
            term
            for term in dict.fromkeys([*entry.synonyms, *entry.related_terms])
            if not _contains_phrase(normalized, _normalize(term))
        ]
        if terms:
            expansions[entry.canonical] = terms
    return expansions
