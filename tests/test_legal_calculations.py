"""Tests for the deterministic legal calculation engine (spec §32).

Covers notice periods per worker category, severance brackets (partial years,
bracket boundaries), calendar deadlines (days/months, French date input),
simple interest, RuleNotFound for unknown categories, and the honesty
contract: every rule-based result carries a provision citation, the
``verified`` flag and a French explanation.
"""

from datetime import date

import pytest

from backend.tools.legal_calculations import (
    CalculationResult,
    RuleNotFound,
    compute_deadline,
    compute_notice_period,
    compute_severance,
    compute_simple_interest,
    load_rules,
)

# ---------------------------------------------------------------------------
# Rule store
# ---------------------------------------------------------------------------


def test_rules_store_is_honest_about_verification():
    rules = load_rules()
    assert rules["meta"]["verified"] is False
    assert "vérifié" in rules["meta"]["note"]
    for rule in rules["notice_periods"]:
        assert rule["verified"] is False
        assert "loi n°028-2008/AN" in rule["source"]
    assert rules["severance"]["verified"] is False
    assert "loi n°028-2008/AN" in rules["severance"]["source"]


# ---------------------------------------------------------------------------
# Notice period (préavis)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("category", "duration", "unit"),
    [
        ("ouvrier", 8, "days"),
        ("ouvrier journalier", 8, "days"),
        ("employé mensualisé", 1, "months"),
        ("employé", 1, "months"),
        ("cadre", 3, "months"),
        ("agent de maîtrise", 3, "months"),
    ],
)
def test_notice_period_per_category(category, duration, unit):
    result = compute_notice_period(category, seniority_years=2)
    assert result.kind == "notice_period"
    assert result.value == duration
    assert result.unit == unit
    assert result.inputs["category"] == category


def test_notice_period_category_matching_normalizes_accents_and_case():
    result = compute_notice_period("  EMPLOYÉ Mensualisé ")
    assert result.value == 1
    assert result.unit == "months"


def test_notice_period_carries_provision_flag_and_fr_explanation():
    result = compute_notice_period("cadre")
    assert result.rule_id == "preavis_cadre"
    assert "loi n°028-2008/AN" in result.provision
    assert result.verified is False  # never silently authoritative
    assert "Préavis de 3 mois" in result.explanation
    assert "Source" in result.explanation


def test_notice_period_unknown_category_raises_rule_not_found():
    with pytest.raises(RuleNotFound):
        compute_notice_period("stagiaire")


def test_notice_period_custom_rules_override():
    custom = {
        "notice_periods": [
            {
                "rule_id": "custom",
                "label": "custom",
                "categories": ["stagiaire"],
                "duration": 15,
                "unit": "days",
                "source": "Texte fictif de test",
                "verified": True,
                "note": "test",
            }
        ]
    }
    result = compute_notice_period("stagiaire", rules=custom)
    assert result.value == 15
    assert result.verified is True


# ---------------------------------------------------------------------------
# Severance (indemnité de licenciement)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seniority_years", "expected"),
    [
        (0, 0.0),
        (3, 75_000.0),  # 3 × 25 %
        (5, 125_000.0),  # upper boundary of the first bracket
        (7, 185_000.0),  # 5 × 25 % + 2 × 30 %
        (10, 275_000.0),  # bracket boundary is continuous
        (12, 355_000.0),  # 5 × 25 % + 5 × 30 % + 2 × 40 %
        (2.5, 62_500.0),  # partial years prorated
        (7.5, 200_000.0),  # 5 × 25 % + 2.5 × 30 %
    ],
)
def test_severance_marginal_brackets(seniority_years, expected):
    result = compute_severance(
        monthly_salary=100_000, seniority_years=seniority_years
    )
    assert result.kind == "severance"
    assert result.value == expected
    assert result.unit == "currency"


def test_severance_breakdown_covers_full_seniority():
    result = compute_severance(monthly_salary=100_000, seniority_years=12)
    brackets = result.details["brackets"]
    assert [b["rate"] for b in brackets] == [0.25, 0.30, 0.40]
    assert sum(b["years_applied"] for b in brackets) == pytest.approx(12)
    assert sum(b["amount"] for b in brackets) == pytest.approx(result.value)


def test_severance_carries_provision_flag_and_fr_explanation():
    result = compute_severance(
        monthly_salary=100_000, seniority_years=7, category="employé mensualisé"
    )
    assert result.rule_id == "indemnite_licenciement"
    assert "loi n°028-2008/AN" in result.provision
    assert result.verified is False
    assert "Indemnité de licenciement" in result.explanation
    assert "Source" in result.explanation
    assert result.inputs["category"] == "employé mensualisé"


def test_severance_rejects_negative_inputs():
    with pytest.raises(ValueError):
        compute_severance(monthly_salary=-1, seniority_years=3)
    with pytest.raises(ValueError):
        compute_severance(monthly_salary=100_000, seniority_years=-1)


def test_severance_missing_rule_raises_rule_not_found():
    with pytest.raises(RuleNotFound):
        compute_severance(monthly_salary=100_000, seniority_years=3, rules={})


# ---------------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------------


def test_deadline_days():
    result = compute_deadline(date(2025, 1, 1), 30, "days")
    assert result.value == "2025-01-31"
    assert result.details["result_date"] == date(2025, 1, 31)


def test_deadline_months_clamps_to_month_end():
    result = compute_deadline(date(2025, 1, 31), 1, "months")
    assert result.value == "2025-02-28"


def test_deadline_months_multi():
    result = compute_deadline("01/12/2025", 3, "months")
    assert result.value == "2026-03-01"


def test_deadline_french_text_date_input():
    result = compute_deadline("1er janvier 2025", 8, "days")
    assert result.value == "2025-01-09"


def test_deadline_is_arithmetic_no_provision_but_fr_explanation():
    result = compute_deadline(date(2025, 1, 1), 1, "months")
    assert result.kind == "deadline"
    assert result.verified is True  # pure calendar arithmetic
    assert result.provision is None  # no legal rule store involved
    assert "Échéance" in result.explanation


def test_deadline_rejects_bad_inputs():
    with pytest.raises(ValueError):
        compute_deadline(date(2025, 1, 1), -1, "days")
    with pytest.raises(ValueError):
        compute_deadline(date(2025, 1, 1), 1, "years")
    with pytest.raises(ValueError):
        compute_deadline("pas une date", 1, "days")


# ---------------------------------------------------------------------------
# Simple interest
# ---------------------------------------------------------------------------


def test_simple_interest_full_year():
    result = compute_simple_interest(
        principal=100_000,
        annual_rate=0.05,
        start=date(2025, 1, 1),
        end=date(2026, 1, 1),
    )
    assert result.value == 5_000.0
    assert result.details["days"] == 365


def test_simple_interest_partial_period_and_fr_dates():
    result = compute_simple_interest(
        principal=36_500, annual_rate=0.10, start="01/01/2025", end="31/01/2025"
    )
    assert result.details["days"] == 30
    assert result.value == pytest.approx(300.0)


def test_simple_interest_is_arithmetic_no_provision_but_fr_explanation():
    result = compute_simple_interest(1_000, 0.05, date(2025, 1, 1), date(2025, 2, 1))
    assert result.kind == "interest"
    assert result.verified is True  # pure arithmetic
    assert result.provision is None  # the rate must come from a provision
    assert "Intérêts simples" in result.explanation


def test_simple_interest_rejects_bad_inputs():
    with pytest.raises(ValueError):
        compute_simple_interest(-1, 0.05, date(2025, 1, 1), date(2025, 2, 1))
    with pytest.raises(ValueError):
        compute_simple_interest(1_000, -0.05, date(2025, 1, 1), date(2025, 2, 1))
    with pytest.raises(ValueError):
        compute_simple_interest(1_000, 0.05, date(2025, 2, 1), date(2025, 1, 1))


# ---------------------------------------------------------------------------
# Result model contract
# ---------------------------------------------------------------------------


def test_calculation_result_echoes_inputs_and_is_typed():
    result = compute_notice_period("ouvrier", seniority_years=1.5)
    assert isinstance(result, CalculationResult)
    assert result.inputs == {"category": "ouvrier", "seniority_years": 1.5}
    assert result.explanation  # non-empty FR explanation on every result
