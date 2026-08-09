"""Deterministic legal calculation engine (spec §32).

Never ask an LLM to perform a calculation that deterministic code can do:
each public function combines a parameterized legal rule (``legal_rules.json``)
with structured inputs and returns a :class:`CalculationResult` carrying the
computed value, the provision citation, a ``verified`` flag and a
human-readable French explanation.

Honesty contract
----------------
The rules in ``legal_rules.json`` encode the commonly documented structure of
the Burkina Faso Code du travail (loi n°028-2008/AN) but are flagged
``verified: false``: every duration and rate MUST be checked against the
current official text (Journal Officiel) and applicable collective agreements
before production use. The flag is propagated to every result so callers can
never silently treat these values as authoritative. When a category or
bracket is not covered by the rules, the engine raises :class:`RuleNotFound`
— it never guesses a value.

Integration point (not wired here)
----------------------------------
These functions are pure library code. Wiring them into the agent graph's
tool loop belongs to the planner/tool registry layer
(``backend/agents/tools/`` registry for ``QuestionType.CALCULATION`` plans);
this module deliberately stays out of that loop.
"""

from __future__ import annotations

import calendar
import json
import unicodedata
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from backend.tools.date_tool import parse_fr_date

RULES_PATH = Path(__file__).with_name("legal_rules.json")


def _configured_rules_path() -> Optional[str]:
    """``settings.legal_rules_path`` override, when set (None = bundled file)."""
    try:
        from backend.core.config import get_settings

        return getattr(get_settings(), "legal_rules_path", None) or None
    except Exception:  # settings unavailable: stay on the bundled default
        return None

DurationUnit = Literal["days", "weeks", "months"]

_UNIT_LABELS_FR = {"days": "jours", "weeks": "semaines", "months": "mois"}


class RuleNotFound(LookupError):
    """No rule covers the requested category/bracket — never guess instead."""


class CalculationResult(BaseModel):
    """Outcome of a deterministic legal calculation (spec §32)."""

    model_config = ConfigDict(frozen=True)

    kind: str
    value: Union[float, str] = Field(
        description="Numeric result, or the ISO date for deadline computations."
    )
    unit: str
    rule_id: Optional[str] = None
    provision: Optional[str] = Field(
        default=None,
        description="Citation of the legal provision defining the calculation.",
    )
    verified: bool = Field(
        description="True only if the underlying rule was checked against the official text."
    )
    explanation: str = Field(description="Human-readable explanation (French).")
    inputs: dict[str, Any] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)


def _normalize(text: str) -> str:
    """Lowercase, strip accents and collapse whitespace for category matching."""
    decomposed = unicodedata.normalize("NFD", text.strip().lower())
    ascii_only = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return " ".join(ascii_only.split())


@lru_cache(maxsize=4)
def load_rules(path: Optional[str] = None) -> dict[str, Any]:
    """Load the legal rule store (treat the returned mapping as read-only).

    Resolution order: explicit ``path`` → ``settings.legal_rules_path`` → the
    bundled :data:`RULES_PATH` (current default behavior).
    """
    with open(path or _configured_rules_path() or RULES_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def compute_notice_period(
    category: str,
    seniority_years: float = 0.0,
    rules: Optional[dict[str, Any]] = None,
) -> CalculationResult:
    """Notice period (préavis) for a worker category.

    Raises :class:`RuleNotFound` when the category is not covered by the rule
    store — an unknown category must never yield an invented duration.
    """
    rules = rules if rules is not None else load_rules()
    wanted = _normalize(category)
    for rule in rules.get("notice_periods", []):
        aliases = {_normalize(c) for c in rule.get("categories", [])}
        if wanted in aliases:
            duration = float(rule["duration"])
            unit = rule["unit"]
            unit_fr = _UNIT_LABELS_FR.get(unit, unit)
            display = int(duration) if duration.is_integer() else duration
            explanation = (
                f"Préavis de {display} {unit_fr} pour la catégorie "
                f"« {rule['label']} » (ancienneté déclarée : {seniority_years:g} an(s)). "
                f"Source : {rule['source']}. "
                "Valeur non vérifiée : à confirmer contre le texte officiel en vigueur."
            )
            return CalculationResult(
                kind="notice_period",
                value=duration,
                unit=unit,
                rule_id=rule["rule_id"],
                provision=rule["source"],
                verified=bool(rule.get("verified", False)),
                explanation=explanation,
                inputs={"category": category, "seniority_years": seniority_years},
                details={"label": rule["label"], "note": rule.get("note", "")},
            )
    raise RuleNotFound(
        f"aucune règle de préavis ne couvre la catégorie {category!r} ; "
        "aucune valeur n'est devinée"
    )


def compute_severance(
    monthly_salary: float,
    seniority_years: float,
    category: Optional[str] = None,
    rules: Optional[dict[str, Any]] = None,
) -> CalculationResult:
    """Severance pay (indemnité de licenciement) via marginal seniority brackets.

    Each bracket applies its rate (fraction of the monthly salary per year of
    service) to the slice of seniority falling inside it; partial years are
    prorated linearly. Bracket boundaries are continuous, so a seniority of
    exactly ``to_years`` yields the same amount from either side.
    """
    if monthly_salary < 0:
        raise ValueError("monthly_salary must be >= 0")
    if seniority_years < 0:
        raise ValueError("seniority_years must be >= 0")
    rules = rules if rules is not None else load_rules()
    rule = rules.get("severance")
    if rule is None:
        raise RuleNotFound("aucune règle d'indemnité de licenciement dans le référentiel")

    total = 0.0
    breakdown: list[dict[str, Any]] = []
    for bracket in rule.get("brackets", []):
        lower = float(bracket["from_years"])
        upper = bracket["to_years"]
        slice_years = min(seniority_years, float("inf") if upper is None else float(upper)) - lower
        if slice_years <= 0:
            continue
        amount = slice_years * float(bracket["rate"]) * monthly_salary
        total += amount
        breakdown.append(
            {
                "from_years": lower,
                "to_years": upper,
                "rate": bracket["rate"],
                "years_applied": round(slice_years, 4),
                "amount": round(amount, 2),
            }
        )

    value = round(total, 2)
    parts = " ; ".join(
        f"{b['years_applied']:g} an(s) à {b['rate']:.0%} = {b['amount']:,.2f}"
        for b in breakdown
    )
    explanation = (
        f"Indemnité de licenciement de {value:,.2f} pour un salaire mensuel de "
        f"{monthly_salary:,.2f} et une ancienneté de {seniority_years:g} an(s)"
        + (f" ({parts})" if parts else "")
        + f". Source : {rule['source']}. "
        "Valeur non vérifiée : à confirmer contre le texte officiel en vigueur."
    )
    return CalculationResult(
        kind="severance",
        value=value,
        unit="currency",
        rule_id=rule["rule_id"],
        provision=rule["source"],
        verified=bool(rule.get("verified", False)),
        explanation=explanation,
        inputs={
            "monthly_salary": monthly_salary,
            "seniority_years": seniority_years,
            "category": category,
        },
        details={"brackets": breakdown, "note": rule.get("note", "")},
    )


def _add_months(day: date, months: int) -> date:
    """Calendar month addition, clamping the day to the target month's length."""
    total = day.month - 1 + months
    year = day.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def compute_deadline(
    start_date: Union[date, str],
    duration: int,
    unit: DurationUnit,
) -> CalculationResult:
    """Calendar deadline: ``start_date`` + ``duration`` in days/weeks/months.

    ``start_date`` accepts a ``date`` or any French date string supported by
    :func:`backend.tools.date_tool.parse_fr_date` (``31/12/2025``,
    ``1er janvier 2025``...). This is a pure calendar computation — no legal
    rule store is involved, hence ``provision`` is ``None`` and the result is
    marked ``verified=True`` (arithmetic), with the explanation stating that
    the computation rule itself must come from the applicable text.
    """
    start = parse_fr_date(start_date) if isinstance(start_date, str) else start_date
    if duration < 0:
        raise ValueError("duration must be >= 0")
    if unit == "days":
        result = start + timedelta(days=duration)
    elif unit == "weeks":
        result = start + timedelta(weeks=duration)
    elif unit == "months":
        result = _add_months(start, duration)
    else:
        raise ValueError(f"unsupported unit: {unit!r} (expected days/weeks/months)")

    explanation = (
        f"Échéance calculée : {start.isoformat()} + {duration} "
        f"{_UNIT_LABELS_FR[unit]} = {result.isoformat()}. "
        "Calcul calendaire déterministe ; le point de départ et le mode de "
        "compte du délai doivent être confirmés par le texte applicable."
    )
    return CalculationResult(
        kind="deadline",
        value=result.isoformat(),
        unit="date",
        rule_id=None,
        provision=None,
        verified=True,
        explanation=explanation,
        inputs={
            "start_date": start.isoformat(),
            "duration": duration,
            "unit": unit,
        },
        details={"result_date": result},
    )


def compute_simple_interest(
    principal: float,
    annual_rate: float,
    start: Union[date, str],
    end: Union[date, str],
) -> CalculationResult:
    """Simple interest over an actual/365 day count.

    ``annual_rate`` is a decimal fraction (``0.05`` = 5 %). Pure arithmetic:
    the applicable rate must be supplied from the relevant legal or
    contractual provision — this function never picks one.
    """
    if principal < 0:
        raise ValueError("principal must be >= 0")
    if annual_rate < 0:
        raise ValueError("annual_rate must be >= 0")
    start_date = parse_fr_date(start) if isinstance(start, str) else start
    end_date = parse_fr_date(end) if isinstance(end, str) else end
    days = (end_date - start_date).days
    if days < 0:
        raise ValueError("end must not precede start")

    value = round(principal * annual_rate * days / 365, 2)
    explanation = (
        f"Intérêts simples : {principal:,.2f} × {annual_rate:.2%} × {days}/365 "
        f"= {value:,.2f} (base 365 jours, du {start_date.isoformat()} au "
        f"{end_date.isoformat()}). Le taux applicable doit être confirmé par "
        "la disposition légale ou contractuelle pertinente."
    )
    return CalculationResult(
        kind="interest",
        value=value,
        unit="currency",
        rule_id=None,
        provision=None,
        verified=True,
        explanation=explanation,
        inputs={
            "principal": principal,
            "annual_rate": annual_rate,
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        details={"days": days},
    )
