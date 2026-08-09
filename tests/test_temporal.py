"""Temporal scoring / filtering tests (spec §10, §24) — offline, pure functions
plus coordinator and evidence-ranking wiring."""

from __future__ import annotations

from datetime import date, timedelta

from backend.agents.evidence_ranking import EvidenceRankingAgent, final_score
from backend.core.models import (
    AuthorityLevel,
    EvidenceChunk,
    RetrievalPlan,
    SearchKind,
    SearchTask,
)
from backend.core.config import Settings
from backend.retrieval.temporal import (
    passes_temporal_filter,
    temporal_score,
)

TODAY = date(2026, 8, 9)
YEAR = timedelta(days=365)


def _chunk(**kwargs) -> EvidenceChunk:
    kwargs.setdefault("content", "Le préavis de licenciement est d'un mois.")
    kwargs.setdefault("document_name", "Code du travail")
    return EvidenceChunk(**kwargs)


# ---------------------------------------------------------------------- score


def test_any_intent_always_scores_one():
    repealed = _chunk(status="repealed", valid_until=date(2000, 1, 1))
    assert temporal_score(repealed, "any", today=TODAY) == 1.0
    assert temporal_score(repealed, "", today=TODAY) == 1.0


def test_current_intent_active_in_window_scores_one():
    chunk = _chunk(
        status="active",
        valid_from=TODAY - YEAR,
        valid_until=TODAY + YEAR,
    )
    assert temporal_score(chunk, "current", today=TODAY) == 1.0


def test_current_intent_legacy_default_chunk_scores_one():
    # Chunks ingested before the temporal fields: status "active", no dates.
    assert temporal_score(_chunk(), "current", today=TODAY) == 1.0


def test_current_intent_unknown_status_and_dates_scores_low():
    chunk = _chunk(status="unknown")
    assert temporal_score(chunk, "current", today=TODAY) == Settings().temporal_score_unknown


def test_current_intent_repealed_expired_future_score_zero():
    assert temporal_score(_chunk(status="repealed"), "current", today=TODAY) == 0.0
    assert temporal_score(
        _chunk(valid_until=TODAY - timedelta(days=1)), "current", today=TODAY
    ) == 0.0
    assert temporal_score(
        _chunk(valid_from=TODAY + timedelta(days=1)), "current", today=TODAY
    ) == 0.0
    assert temporal_score(_chunk(status="future"), "current", today=TODAY) == 0.0


def test_historical_intent_scores_against_scenario_date():
    scenario = date(2015, 1, 1)
    in_force = _chunk(valid_from=date(2010, 1, 1), valid_until=date(2020, 1, 1))
    assert temporal_score(in_force, "historical", scenario_date=scenario, today=TODAY) == 1.0

    not_yet = _chunk(valid_from=date(2016, 1, 1))
    assert temporal_score(not_yet, "historical", scenario_date=scenario, today=TODAY) == 0.0

    already_repealed = _chunk(valid_from=date(1990, 1, 1), valid_until=date(2000, 1, 1))
    assert temporal_score(already_repealed, "historical", scenario_date=scenario, today=TODAY) == 0.1

    unknown = _chunk(status="unknown")
    assert temporal_score(unknown, "historical", scenario_date=scenario, today=TODAY) == 0.5

    # No scenario date: nothing to discriminate on.
    assert temporal_score(not_yet, "historical", today=TODAY) == 1.0


# --------------------------------------------------------------------- filter


def test_filter_any_intent_keeps_everything():
    assert passes_temporal_filter(_chunk(status="repealed"), "any", today=TODAY)


def test_filter_current_drops_only_clearly_inapplicable():
    assert not passes_temporal_filter(_chunk(status="repealed"), "current", today=TODAY)
    assert not passes_temporal_filter(
        _chunk(valid_until=TODAY - timedelta(days=1)), "current", today=TODAY
    )
    assert not passes_temporal_filter(
        _chunk(valid_from=TODAY + timedelta(days=1)), "current", today=TODAY
    )
    # active, unknown and legacy-default chunks are kept
    assert passes_temporal_filter(_chunk(status="active"), "current", today=TODAY)
    assert passes_temporal_filter(_chunk(status="unknown"), "current", today=TODAY)
    assert passes_temporal_filter(_chunk(), "current", today=TODAY)


def test_filter_historical_drops_not_yet_in_force_only():
    scenario = date(2015, 1, 1)
    assert not passes_temporal_filter(
        _chunk(valid_from=date(2016, 1, 1)), "historical", scenario_date=scenario, today=TODAY
    )
    # repealed before the scenario date: kept (it may be the question's subject)
    assert passes_temporal_filter(
        _chunk(status="repealed", valid_from=date(1990, 1, 1), valid_until=date(2000, 1, 1)),
        "historical",
        scenario_date=scenario,
        today=TODAY,
    )
    # without a scenario date, historical filtering is a no-op
    assert passes_temporal_filter(
        _chunk(valid_from=date(2016, 1, 1)), "historical", today=TODAY
    )


# ------------------------------------------------- evidence ranking (spec §24)


def _scored_chunk(**kwargs) -> EvidenceChunk:
    kwargs.setdefault("retrieval_score", 0.8)
    kwargs.setdefault("rerank_score", 0.8)
    kwargs.setdefault("authority", AuthorityLevel.LAW)
    kwargs.setdefault("confidence", 0.8)
    return _chunk(**kwargs)


def test_final_score_any_intent_unchanged():
    chunk = _scored_chunk()
    assert final_score(chunk) == final_score(chunk, temporal_intent="any")


def test_final_score_current_intent_sinks_repealed_evidence():
    import pytest

    active = _scored_chunk(status="active")
    repealed = _scored_chunk(status="repealed")
    base = final_score(active)
    # In-force evidence keeps its full contribution: the blend adds the
    # temporal weight with temporal_score == 1.0 (renormalized average).
    assert final_score(active, temporal_intent="current") == pytest.approx(
        (base + 0.15 * 1.0) / 1.15
    )
    # repealed evidence keeps its base score diluted by a 0 temporal component
    assert final_score(repealed, temporal_intent="current") == pytest.approx(base / 1.15)
    assert final_score(repealed, temporal_intent="current") < final_score(repealed)
    assert (
        final_score(active, temporal_intent="current")
        > final_score(repealed, temporal_intent="current")
    )


async def test_ranking_agent_prefers_in_force_evidence_for_current_intent(ctx):
    active = _scored_chunk(status="active")
    repealed = _scored_chunk(status="repealed")
    state = {
        "evidence": [repealed, active],
        "trace": [],
        "plan": RetrievalPlan(temporal_intent="current"),
    }
    result = await EvidenceRankingAgent().run(state, ctx)
    ranked = result["ranked_evidence"]
    assert ranked and ranked[0].status == "active"


# ----------------------------------------------------------- coordinator wiring


async def test_coordinator_filters_repealed_chunks_for_current_intent(ctx):
    active = _chunk(content="préavis licenciement indemnité employé", status="active")
    repealed = _chunk(content="préavis licenciement indemnité employé", status="repealed")
    vectors = await ctx.embedder.embed([c.content for c in (active, repealed)])
    await ctx.vector_store.upsert([active, repealed], vectors)

    tasks = [SearchTask(kind=SearchKind.VECTOR, query="préavis licenciement", top_k=5)]
    unfiltered = await ctx.retriever.retrieve(tasks)
    assert unfiltered, "seeded chunks should be retrievable"

    filtered = await ctx.retriever.retrieve(tasks, temporal_intent="current")
    assert filtered
    assert all(c.status != "repealed" for c in filtered)
    assert any(c.chunk_id == active.chunk_id for c in filtered)


async def test_coordinator_never_returns_empty_after_temporal_filter(ctx):
    repealed = _chunk(content="préavis licenciement indemnité employé", status="repealed")
    vectors = await ctx.embedder.embed([repealed.content])
    await ctx.vector_store.upsert([repealed], vectors)

    tasks = [SearchTask(kind=SearchKind.VECTOR, query="préavis licenciement", top_k=5)]
    filtered = await ctx.retriever.retrieve(tasks, temporal_intent="current")
    # The filter removed every candidate: unfiltered results are kept, never
    # an empty answer (a warning is logged instead).
    assert filtered
    assert any(c.chunk_id == repealed.chunk_id for c in filtered)
