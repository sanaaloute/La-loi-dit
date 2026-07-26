"""Conflict resolution tests (pure logic, offline)."""

from __future__ import annotations

from datetime import date

from backend.agents.conflict_resolver import conflict_resolver_node, resolve_pair
from backend.core.models import AuthorityLevel, EvidenceChunk


def _chunk(
    content: str,
    authority: AuthorityLevel,
    publication: date,
    *,
    effective: date | None = None,
    confidence: float = 0.5,
) -> EvidenceChunk:
    return EvidenceChunk(
        document_name="Code du travail",
        article="95",
        content=content,
        authority=authority,
        publication_date=publication,
        effective_date=effective or publication,
        confidence=confidence,
    )


def test_newer_amended_law_beats_older_decree():
    law = _chunk(
        "Le préavis est de trois mois pour les cadres.",
        AuthorityLevel.AMENDED_LAW,
        date(2023, 5, 1),
    )
    decree = _chunk(
        "Le préavis est d'un mois pour les cadres.",
        AuthorityLevel.DECREE,
        date(2010, 3, 1),
    )
    kept, dropped, reason, resolved = resolve_pair(law, decree, None)
    assert kept is law
    assert dropped is decree
    assert resolved is True


def test_unresolvable_conflict_is_surfaced():
    a = _chunk("Le préavis est d'un mois.", AuthorityLevel.LAW, date(2020, 1, 1), confidence=0.6)
    b = _chunk("Le préavis est de deux mois.", AuthorityLevel.LAW, date(2020, 1, 1), confidence=0.5)
    kept, dropped, reason, resolved = resolve_pair(a, b, None)
    assert resolved is False
    assert kept is a  # higher-confidence source kept, uncertainty surfaced


async def test_node_keeps_both_sources_when_unresolved():
    a = _chunk("Le préavis est d'un mois.", AuthorityLevel.LAW, date(2020, 1, 1), confidence=0.6)
    b = _chunk("Le préavis est de deux mois.", AuthorityLevel.LAW, date(2020, 1, 1), confidence=0.5)
    state = {"query": "préavis ?", "evidence": [a, b], "trace": []}
    update = await conflict_resolver_node(state, ctx=None)
    assert len(update["conflicts"]) == 1
    assert update["conflicts"][0].resolved is False
    # unresolved conflicts keep both sources so the user sees the disagreement
    assert len(update["evidence"]) == 2


def test_scenario_date_picks_version_in_force():
    old = _chunk(
        "Le préavis est d'un mois.",
        AuthorityLevel.LAW,
        date(2010, 1, 1),
        effective=date(2010, 1, 1),
    )
    new = _chunk(
        "Le préavis est de trois mois.",
        AuthorityLevel.LAW,
        date(2020, 1, 1),
        effective=date(2020, 1, 1),
    )
    kept, dropped, reason, resolved = resolve_pair(new, old, date(2015, 6, 1))
    assert kept is old  # only the old version was in force on 2015-06-01
    assert resolved is True
