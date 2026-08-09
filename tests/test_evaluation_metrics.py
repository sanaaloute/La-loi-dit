"""Unit tests for the evaluation metrics (pure functions, offline).

Covers the rank-aware retrieval metrics (recall@k, precision@k, MRR, nDCG@k)
and the per-case issue-coverage check backing the spec §38 golden regression
case: an answer discussing only tribunal jurisdiction must fail.
"""

from __future__ import annotations

import math

import pytest

from backend.evaluation import metrics

# ---------------------------------------------------------------------------
# recall_at_k
# ---------------------------------------------------------------------------


def test_recall_at_k_basic():
    ranked = ["Code du travail du Burkina Faso", "Code pénal du Burkina Faso", "Constitution"]
    relevant = ["Code du travail", "Constitution du Burkina Faso"]
    assert metrics.recall_at_k(ranked, relevant, 1) == pytest.approx(0.5)
    assert metrics.recall_at_k(ranked, relevant, 3) == pytest.approx(1.0)


def test_recall_at_k_empty_relevant_is_perfect():
    assert metrics.recall_at_k(["doc a"], [], 5) == 1.0
    assert metrics.recall_at_k([], [], 5) == 1.0


def test_recall_at_k_k_larger_than_list():
    ranked = ["doc a", "doc b"]
    assert metrics.recall_at_k(ranked, ["doc a", "doc b"], 10) == pytest.approx(1.0)


def test_recall_at_k_no_relevant_found():
    assert metrics.recall_at_k(["doc a", "doc b"], ["doc z"], 5) == 0.0
    assert metrics.recall_at_k([], ["doc z"], 5) == 0.0
    assert metrics.recall_at_k(["doc z"], ["doc z"], 0) == 0.0


def test_recall_at_k_accent_insensitive_substring():
    ranked = ["Code général des impôts du Burkina Faso"]
    assert metrics.recall_at_k(ranked, ["code general des impots"], 1) == 1.0


# ---------------------------------------------------------------------------
# precision_at_k
# ---------------------------------------------------------------------------


def test_precision_at_k_basic():
    ranked = ["doc a", "doc b", "doc c", "doc d"]
    relevant = ["doc a", "doc c"]
    assert metrics.precision_at_k(ranked, relevant, 4) == pytest.approx(0.5)
    assert metrics.precision_at_k(ranked, relevant, 1) == pytest.approx(1.0)


def test_precision_at_k_empty_inputs():
    # Nothing retrieved, nothing relevant: nothing wrong was retrieved.
    assert metrics.precision_at_k([], [], 5) == 1.0
    # Nothing retrieved but relevant ids exist: the retrieval missed them.
    assert metrics.precision_at_k([], ["doc a"], 5) == 0.0
    # Retrieved items but nothing is relevant: every slot is wrong.
    assert metrics.precision_at_k(["doc a"], [], 5) == 0.0


def test_precision_at_k_k_larger_than_list():
    # Denominator shrinks to the number of retrieved items.
    ranked = ["doc a", "doc b"]
    assert metrics.precision_at_k(ranked, ["doc a", "doc b"], 10) == pytest.approx(1.0)
    assert metrics.precision_at_k(ranked, ["doc a"], 10) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# mrr
# ---------------------------------------------------------------------------


def test_mrr_basic():
    ranked = ["doc a", "doc b", "doc c"]
    assert metrics.mrr(ranked, ["doc a"]) == pytest.approx(1.0)
    assert metrics.mrr(ranked, ["doc b"]) == pytest.approx(0.5)
    assert metrics.mrr(ranked, ["doc c"]) == pytest.approx(1.0 / 3.0)


def test_mrr_no_relevant_hit():
    assert metrics.mrr(["doc a"], ["doc z"]) == 0.0


def test_mrr_empty_inputs():
    assert metrics.mrr([], ["doc a"]) == 0.0
    assert metrics.mrr(["doc a"], []) == 0.0
    assert metrics.mrr([], []) == 0.0


# ---------------------------------------------------------------------------
# ndcg_at_k
# ---------------------------------------------------------------------------


def test_ndcg_at_k_perfect_ranking():
    ranked = ["doc a", "doc b", "doc c"]
    relevant = ["doc a", "doc b"]
    assert metrics.ndcg_at_k(ranked, relevant, 2) == pytest.approx(1.0)


def test_ndcg_at_k_relevant_at_second_position():
    # DCG = 1/log2(3); IDCG = 1/log2(2) = 1.
    ranked = ["doc x", "doc a"]
    assert metrics.ndcg_at_k(ranked, ["doc a"], 2) == pytest.approx(1.0 / math.log2(3))


def test_ndcg_at_k_empty_relevant_is_perfect():
    assert metrics.ndcg_at_k(["doc a"], [], 5) == 1.0
    assert metrics.ndcg_at_k([], [], 5) == 1.0


def test_ndcg_at_k_no_hit_or_zero_k():
    assert metrics.ndcg_at_k(["doc a"], ["doc z"], 5) == 0.0
    assert metrics.ndcg_at_k(["doc a"], ["doc a"], 0) == 0.0
    assert metrics.ndcg_at_k([], ["doc a"], 5) == 0.0


def test_ndcg_at_k_k_larger_than_list():
    ranked = ["doc a"]
    # Ideal gain for one relevant id is 1.0; it sits at rank 1.
    assert metrics.ndcg_at_k(ranked, ["doc a"], 10) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# issue_coverage (spec §38 regression check)
# ---------------------------------------------------------------------------

_DISMISSAL_ISSUES = [
    {"category": "dismissal_grounds", "keywords": ["motif réel et sérieux", "faute"]},
    {"category": "notice", "keywords": ["préavis", "délai de prévenance"]},
    {"category": "compensation", "keywords": ["indemnité de licenciement", "indemnité"]},
    {"category": "accrued_rights", "keywords": ["droits acquis", "congés payés"]},
    {"category": "unfair_dismissal", "keywords": ["licenciement abusif", "dommages et intérêts"]},
    {"category": "legal_remedies", "keywords": ["conciliation", "recours"]},
    {"category": "jurisdiction", "keywords": ["tribunal du travail", "juridiction compétente"]},
]


def test_issue_coverage_full_answer_passes():
    answer = (
        "Le salarié licencié conserve plusieurs droits [1]. "
        "Le licenciement doit reposer sur un motif réel et sérieux ou une faute [1]. "
        "Un préavis d'un mois est dû à l'employé mensualisé [2]. "
        "L'indemnité de licenciement est calculée sur le salaire moyen [3]. "
        "Les droits acquis (salaires, congés payés) restent dus [4]. "
        "Le licenciement abusif ouvre droit à des dommages et intérêts [5]. "
        "Une conciliation préalable devant l'inspection du travail est possible [6]. "
        "Le tribunal du travail est la juridiction compétente [6]."
    )
    ratio, missing = metrics.issue_coverage(answer, _DISMISSAL_ISSUES)
    assert ratio == 1.0
    assert missing == []


def test_issue_coverage_jurisdiction_only_answer_fails():
    """Spec §38: an answer discussing only tribunal jurisdiction must fail."""
    answer = (
        "Le tribunal du travail est compétent pour connaître des litiges nés "
        "du licenciement ; le salarié peut saisir cette juridiction [1]."
    )
    ratio, missing = metrics.issue_coverage(answer, _DISMISSAL_ISSUES)
    assert ratio == pytest.approx(1.0 / 7.0)
    assert "jurisdiction" not in missing
    for category in (
        "dismissal_grounds",
        "notice",
        "compensation",
        "accrued_rights",
        "unfair_dismissal",
        "legal_remedies",
    ):
        assert category in missing


def test_issue_coverage_empty_issues_is_perfect():
    assert metrics.issue_coverage("réponse quelconque", []) == (1.0, [])


def test_issue_coverage_accent_insensitive_keywords():
    issues = [{"category": "notice", "keywords": ["preavis"]}]
    ratio, missing = metrics.issue_coverage("Le préavis est d'un mois.", issues)
    assert ratio == 1.0
    assert missing == []
