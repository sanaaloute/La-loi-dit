"""Deterministic question-type classification (spec §30).

Heuristic, regex/keyword based, French-first with English support.  Never
hallucinates, never calls an LLM: when nothing matches, the question is
classified GENERAL.
"""

from __future__ import annotations

import re

from backend.core.models import QuestionType

# Ordered (most specific first): the first pattern list that matches wins.
_QUESTION_TYPE_PATTERNS: tuple[tuple[QuestionType, tuple[str, ...]], ...] = (
    (
        QuestionType.SOURCE_LOOKUP,
        (
            r"\barticle\s*(?:n[°o]|num(?:[ée]ro)?\.?)?\s*\d+",
            r"\bart\.?\s*\d+",
            r"\bloi\s*n[°o]\s*\d",
            r"\bd[ée]cret\s*n[°o]\s*\d",
            r"\barr[êe]t[ée]\s*n[°o]\s*\d",
        ),
    ),
    (
        QuestionType.HISTORICAL,
        (
            r"\b[ée]tait\b",
            r"\b[ée]taient\b",
            r"\bavant\s+(?:le\s+)?(?:19|20)\d{2}\b",
            r"\ben\s+(?:19|20)\d{2}\b",
            r"\bin\s+(?:19|20)\d{2}\b",
            r"\bwas the law\b",
            r"\bwere the rules\b",
            "ancienne loi",
            "anciennement",
            "auparavant",
            "former law",
            "previously in force",
        ),
    ),
    (
        QuestionType.CURRENT_LAW,
        (
            "loi actuelle",
            "droit actuel",
            "actuellement en vigueur",
            "en vigueur",
            "droit positif",
            "current law",
            "currently in force",
            "currently applicable",
            "law in force",
        ),
    ),
    (
        QuestionType.DOCUMENT_SUMMARY,
        (
            "résume",
            "résumer",
            "résumé de",
            "synthèse de",
            "summarize",
            "summarise",
            "summary of",
            "sum up",
        ),
    ),
    (
        QuestionType.COMPARISON,
        (
            "différence entre",
            "différences entre",
            "comparer",
            "comparaison",
            r"\bvs\b",
            "versus",
            "difference between",
            "compare",
            "compared to",
        ),
    ),
    (
        QuestionType.CALCULATION,
        (
            "calcul",
            "combien",
            "montant",
            "barème",
            "taux",
            "how much",
            "how many",
            "amount of",
            "calculate",
        ),
    ),
    (
        QuestionType.PROCEDURE,
        (
            "procédure",
            "procedure",
            "démarche",
            "comment faire",
            "comment saisir",
            "comment déposer",
            "comment introduire",
            "comment obtenir",
            "étapes pour",
            "how to",
            "what steps",
            "process to",
            "process for",
        ),
    ),
    (
        QuestionType.DEFINITION,
        (
            "qu'est-ce que",
            "qu'est-ce qu",
            "qu'est ce que",
            "qu'est ce qu",
            "que signifie",
            "définition",
            "définir",
            "c'est quoi",
            "what is a",
            "what is an",
            "what is the definition",
            r"what does .+ mean",
            "define",
        ),
    ),
    (
        QuestionType.RIGHTS,
        (
            r"\bdroits?\s+(?:d['’]|de|des|du|à|au|aux)\b",
            "quels droits",
            "mes droits",
            r"\brights\b",
            "entitled",
        ),
    ),
    (
        QuestionType.OBLIGATIONS,
        (
            "obligation",
            "devoirs",
            "obligé de",
            "tenu de",
            "doit-on",
            "obligations",
            "must the",
            "required to",
            "duty",
            "duties",
        ),
    ),
    (
        QuestionType.CASE_ANALYSIS,
        (
            r"\bmon\s+(?:employeur|patron|mari|femme|voisin|locataire|propri[ée]taire|conjoint)",
            r"\bma\s+(?:femme|locataire|propri[ée]taire|conjointe)",
            "je suis licencié",
            "j'ai été licencié",
            "on m'a licencié",
            "que puis-je faire",
            "que dois-je faire",
            "puis-je",
            "my employer",
            "my landlord",
            "my wife",
            "my husband",
            "i was fired",
            "i have been dismissed",
            "what can i do",
        ),
    ),
    (
        QuestionType.LEGAL_RULE,
        (
            "que dit la loi",
            "que dit le code",
            "que dit le texte",
            "que prévoit la loi",
            "que prévoit",
            "dispositions légales",
            "est-il légal",
            "est-ce légal",
            "est-ce autorisé",
            "est-ce interdit",
            "what does the law say",
            "what does the code provide",
            "is it legal",
            "is it allowed",
            "legal rule",
        ),
    ),
    (
        QuestionType.FACTUAL,
        (
            r"^\s*(?:quel|quelle|quels|quelles|quand|où|combien|qui)\b",
            "qui est",
            r"\bwhen\b",
            r"\bwhere\b",
            "who is",
            "what is the",
        ),
    ),
)

# Temporal intent patterns: historical is checked first so that e.g.
# "Quelle loi était en vigueur en 2020 ?" is historical, not current.
_HISTORICAL_PATTERNS = _QUESTION_TYPE_PATTERNS[1][1]
_CURRENT_PATTERNS = _QUESTION_TYPE_PATTERNS[2][1] + (
    "actuellement",
    "aujourd'hui",
    "currently",
    r"\bnow\b",
    r"\btoday\b",
)


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(p, text) for p in patterns)


def classify_question_type(query: str, language: str = "fr") -> QuestionType:
    """Classify a user question into the QuestionType taxonomy.

    Heuristic and fully deterministic; French-first with English support.
    Both French and English patterns are always evaluated (`language` only
    documents the expected input language and is kept for interface
    stability).  Falls back to GENERAL when nothing matches.
    """
    q = query.lower()
    for question_type, patterns in _QUESTION_TYPE_PATTERNS:
        if _matches_any(q, patterns):
            return question_type
    return QuestionType.GENERAL


def detect_temporal_intent(query: str) -> str:
    """Return "current", "historical" or "any" for the question's timeline anchor."""
    q = query.lower()
    if _matches_any(q, _HISTORICAL_PATTERNS):
        return "historical"
    if _matches_any(q, _CURRENT_PATTERNS):
        return "current"
    return "any"
