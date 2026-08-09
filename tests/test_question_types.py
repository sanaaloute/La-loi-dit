"""Question-type classifier tests (offline, deterministic)."""

from __future__ import annotations

import pytest

from backend.core.models import QuestionType
from backend.planner.question_types import classify_question_type, detect_temporal_intent


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        # SOURCE_LOOKUP — asking for a specific numbered text
        ("Quel est l'article 341 du Code du travail ?", QuestionType.SOURCE_LOOKUP),
        ("Que dit l'art. 12 de la Constitution ?", QuestionType.SOURCE_LOOKUP),
        ("What does article 12 of the Constitution say?", QuestionType.SOURCE_LOOKUP),
        # HISTORICAL — past law / past-year phrasing
        ("Quelle était la loi applicable en 2018 ?", QuestionType.HISTORICAL),
        ("Quelles étaient les règles avant 2010 ?", QuestionType.HISTORICAL),
        ("What was the law in 2015?", QuestionType.HISTORICAL),
        # CURRENT_LAW — law currently in force
        ("Quelle est la loi actuelle sur le divorce ?", QuestionType.CURRENT_LAW),
        ("Quelle loi est en vigueur en matière de bail ?", QuestionType.CURRENT_LAW),
        ("What is the current law on adoption?", QuestionType.CURRENT_LAW),
        # DOCUMENT_SUMMARY
        ("Peux-tu résumer ce document ?", QuestionType.DOCUMENT_SUMMARY),
        ("Summarize this document please.", QuestionType.DOCUMENT_SUMMARY),
        # COMPARISON
        ("Quelle est la différence entre un CDD et un CDI ?", QuestionType.COMPARISON),
        ("What is the difference between a SARL and a SA?", QuestionType.COMPARISON),
        # CALCULATION
        ("Comment calculer le montant de l'indemnité de licenciement ?", QuestionType.CALCULATION),
        ("Combien de jours de congés payés par an ?", QuestionType.CALCULATION),
        ("How much severance pay am I entitled to?", QuestionType.CALCULATION),
        # PROCEDURE
        ("Quelles sont les démarches pour créer une société ?", QuestionType.PROCEDURE),
        ("Comment faire pour saisir le tribunal du travail ?", QuestionType.PROCEDURE),
        ("What is the procedure to register a company?", QuestionType.PROCEDURE),
        # DEFINITION
        ("Qu'est-ce qu'un licenciement abusif ?", QuestionType.DEFINITION),
        ("Quelle est la définition de la force majeure ?", QuestionType.DEFINITION),
        ("What is a force majeure clause?", QuestionType.DEFINITION),
        # RIGHTS
        ("Quels sont les droits d'un salarié licencié ?", QuestionType.RIGHTS),
        ("Quels sont les droits du locataire ?", QuestionType.RIGHTS),
        ("What are my rights as a tenant?", QuestionType.RIGHTS),
        # OBLIGATIONS
        ("Quelles sont les obligations de l'employeur ?", QuestionType.OBLIGATIONS),
        ("What are the obligations of a landlord?", QuestionType.OBLIGATIONS),
        # CASE_ANALYSIS — personal fact pattern
        ("Mon employeur m'a licencié sans préavis, que puis-je faire ?", QuestionType.CASE_ANALYSIS),
        ("My employer fired me, what can I do?", QuestionType.CASE_ANALYSIS),
        # LEGAL_RULE
        ("Que dit la loi sur le travail des enfants ?", QuestionType.LEGAL_RULE),
        ("What does the law say about child labour?", QuestionType.LEGAL_RULE),
        # FACTUAL — specific factual question
        ("Quelle est la durée du préavis ?", QuestionType.FACTUAL),
        ("When was the Constitution of Burkina Faso adopted?", QuestionType.FACTUAL),
        # GENERAL — fallback
        ("Parle-moi du droit burkinabè.", QuestionType.GENERAL),
    ],
)
def test_classify_question_type(query: str, expected: QuestionType):
    assert classify_question_type(query) == expected


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Quelle est la loi actuellement en vigueur ?", "current"),
        ("Quelle est la loi actuelle sur le divorce ?", "current"),
        ("What is the current law on adoption?", "current"),
        ("Quelle était la loi en 2018 ?", "historical"),
        # past tense wins over "en vigueur"
        ("Quelle loi était en vigueur le 15/03/2020 ?", "historical"),
        ("What was the law in 2015?", "historical"),
        ("Quelle loi s'applique au divorce ?", "any"),
        ("Quels sont les droits d'un salarié licencié ?", "any"),
    ],
)
def test_detect_temporal_intent(query: str, expected: str):
    assert detect_temporal_intent(query) == expected
