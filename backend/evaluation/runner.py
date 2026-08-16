"""Offline evaluation runner.

Builds a fully offline AppContext (mock LLM, in-memory cache/embeddings/
vector store), seeds the store with the synthetic seed evidence, runs the
compiled LangGraph pipeline over the golden dataset and writes JSON +
markdown reports.

CLI:
    python -m backend.evaluation.runner \
        --dataset backend/evaluation/golden_dataset.json --out eval_report
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.core.cache import InMemoryCache
from backend.core.config import Settings
from backend.core.context import AppContext
from backend.core.embeddings import HashEmbeddings
from backend.core.llm import LLMClient
from backend.core.models import EvalCaseResult
from backend.evaluation import metrics
from backend.evaluation.report import render_markdown
from backend.evaluation.seed_data import seed_evidence

# Pass thresholds for a case to be considered successful.
_MIN_GROUNDEDNESS = 0.8
_MIN_CITATION_ACCURACY = 0.99
_MIN_ANSWER_RELEVANCE = 0.5
_MIN_RECALL_WHEN_EXPECTED = 0.5

# Cutoff for the rank-aware retrieval metrics reported per case.
_RANK_K = 5


async def build_offline_context(settings: Settings) -> AppContext:
    """Wire an AppContext with the in-memory adapters (zero external services).

    Imports of the vector store / retrieval / memory subsystems stay inside
    the function so this module imports cleanly even while those subsystems
    are being built in parallel.
    """
    from backend.memory.store import MemoryStore
    from backend.retrieval.coordinator import RetrievalCoordinator
    from backend.vectorstore.memory_store import InMemoryVectorStore

    cache = InMemoryCache(settings.cache_ttl_seconds)
    embedder = HashEmbeddings(settings.embedding_dimension)
    ctx = AppContext(
        settings=settings,
        llm=LLMClient(settings),
        cache=cache,
        embedder=embedder,
    )
    ctx.vector_store = InMemoryVectorStore()
    ctx.retriever = RetrievalCoordinator(ctx)
    ctx.memory = MemoryStore(settings, cache, embedder)
    return ctx


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, round(pct * (len(ordered) - 1))))
    return ordered[rank]


def _aggregate(
    results: list[EvalCaseResult],
    *,
    dataset_path: str,
    disclaimer: str,
    dataset_note: str = "",
    issue_coverages: list[float] | None = None,
    rank_metrics: dict[str, list[float]] | None = None,
) -> dict[str, Any]:
    def mean(attr: str) -> float:
        return round(sum(getattr(r, attr) for r in results) / len(results), 4) if results else 0.0

    latencies = [r.latency_ms for r in results]
    passed = sum(1 for r in results if r.passed)
    aggregate: dict[str, Any] = {
        "dataset": dataset_path,
        "disclaimer": disclaimer,
        "dataset_note": dataset_note,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_cases": len(results),
        "passed": passed,
        "pass_rate": passed / len(results) if results else 0.0,
        "mean_groundedness": mean("groundedness"),
        "mean_faithfulness": mean("faithfulness"),
        "mean_citation_accuracy": mean("citation_accuracy"),
        "mean_answer_relevance": mean("answer_relevance"),
        "hallucination_rate": (
            round(sum(1 for r in results if r.hallucination_detected) / len(results), 4) if results else 0.0
        ),
        "mean_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
        "p95_latency_ms": round(_percentile(latencies, 0.95), 1),
    }
    # Optional metric families, averaged only over the cases that declare the
    # corresponding expectations (older datasets without them stay compatible).
    if issue_coverages:
        aggregate["mean_issue_coverage"] = round(sum(issue_coverages) / len(issue_coverages), 4)
    for name, values in (rank_metrics or {}).items():
        if values:
            aggregate[f"mean_{name}"] = round(sum(values) / len(values), 4)
    return aggregate


async def evaluate(dataset_path: str, out_path: str) -> dict[str, Any]:
    """Run the full offline evaluation and write JSON + markdown reports."""
    from backend.workflows.graph import build_graph, initial_state, run_query

    dataset_file = Path(dataset_path)
    dataset = json.loads(dataset_file.read_text(encoding="utf-8"))
    disclaimer = dataset.get("disclaimer", "")
    dataset_note = dataset.get("coverage_note", "")
    cases = dataset.get("cases", [])

    settings = Settings(llm_provider="mock", data_dir=Path("./data/eval"))
    ctx = await build_offline_context(settings)
    chunks = seed_evidence()
    vectors = await ctx.embedder.embed([c.content for c in chunks])
    await ctx.vector_store.upsert(chunks, vectors)
    # Hybrid retrieval like production: seed the BM25 corpus too, otherwise
    # offline cases hinge solely on hash-embedding noise (top-k membership for
    # short queries is essentially random as the corpus grows).
    from backend.retrieval.bm25 import BM25Retriever

    bm25 = BM25Retriever()
    bm25.add_documents(chunks)
    ctx.extras["bm25"] = bm25
    graph = build_graph(ctx)

    results: list[EvalCaseResult] = []
    issue_coverages: list[float] = []
    rank_metric_lists: dict[str, list[float]] = {
        f"recall_at_{_RANK_K}": [],
        f"precision_at_{_RANK_K}": [],
        "mrr": [],
        f"ndcg_at_{_RANK_K}": [],
    }
    for case in cases:
        state = initial_state(case["question"], scenario_date=case.get("scenario_date"))
        response = await run_query(graph, ctx, state)
        answer = response.answer

        expected_keywords = case.get("expected_keywords", [])
        expected_documents = case.get("expected_documents", [])
        expected_articles = case.get("expected_articles", [])
        expected_issues = case.get("expected_issues", [])
        forbidden_keywords = case.get("forbidden_keywords", [])
        groundedness = metrics.groundedness(answer)
        citation_accuracy = metrics.citation_accuracy(answer)
        relevance = metrics.answer_relevance(answer.answer, expected_keywords)
        precision = metrics.retrieval_precision(answer.evidence, expected_documents)
        recall = metrics.retrieval_recall(answer.evidence, expected_documents)
        hallucination = metrics.hallucination_detected(answer)
        forbidden_found = metrics.forbidden_terms(answer.answer, forbidden_keywords)

        # Issue coverage (spec §38): the answer must touch every declared issue
        # category — an answer discussing only tribunal jurisdiction fails.
        issue_ratio, missing_issues = metrics.issue_coverage(answer.answer, expected_issues)
        if expected_issues:
            issue_coverages.append(issue_ratio)

        # Rank-aware retrieval metrics over the evidence order, for every
        # expectation kind the case declares (documents and/or articles).
        ranked_expectations: list[tuple[list[str], list[str]]] = []
        if expected_documents:
            ranked_expectations.append(
                ([c.document_name for c in answer.evidence], expected_documents)
            )
        if expected_articles:
            ranked_expectations.append(
                ([c.article or "" for c in answer.evidence], expected_articles)
            )
        case_rank: dict[str, float] = {}
        if ranked_expectations:
            per_kind = {
                f"recall_at_{_RANK_K}": [
                    metrics.recall_at_k(ids, rel, _RANK_K) for ids, rel in ranked_expectations
                ],
                f"precision_at_{_RANK_K}": [
                    metrics.precision_at_k(ids, rel, _RANK_K) for ids, rel in ranked_expectations
                ],
                "mrr": [metrics.mrr(ids, rel) for ids, rel in ranked_expectations],
                f"ndcg_at_{_RANK_K}": [
                    metrics.ndcg_at_k(ids, rel, _RANK_K) for ids, rel in ranked_expectations
                ],
            }
            for name, values in per_kind.items():
                case_rank[name] = sum(values) / len(values)
                rank_metric_lists[name].append(case_rank[name])

        passed = (
            not hallucination
            and not missing_issues
            and not forbidden_found
            and citation_accuracy >= _MIN_CITATION_ACCURACY
            and groundedness >= _MIN_GROUNDEDNESS
            and relevance >= _MIN_ANSWER_RELEVANCE
            and (not expected_documents or recall >= _MIN_RECALL_WHEN_EXPECTED)
        )
        detail = (
            f"precision={precision:.2f} recall={recall:.2f} "
            f"confidence={answer.confidence:.2f} refused={answer.refused} "
            f"evidence={len(answer.evidence)} chunks"
        )
        if forbidden_found:
            detail += f" forbidden_found={','.join(forbidden_found)}"
        if expected_issues:
            covered = len(expected_issues) - len(missing_issues)
            detail += f" issues={covered}/{len(expected_issues)}"
            if missing_issues:
                detail += f" missing_issues={','.join(missing_issues)}"
        if case_rank:
            detail += (
                f" R@{_RANK_K}={case_rank[f'recall_at_{_RANK_K}']:.2f}"
                f" P@{_RANK_K}={case_rank[f'precision_at_{_RANK_K}']:.2f}"
                f" MRR={case_rank['mrr']:.2f}"
                f" NDCG@{_RANK_K}={case_rank[f'ndcg_at_{_RANK_K}']:.2f}"
            )
        results.append(
            EvalCaseResult(
                case_id=case["id"],
                question=case["question"],
                groundedness=round(groundedness, 4),
                # Offline faithfulness uses the same grounding signal: the
                # template/verified-citation pipeline cannot state anything
                # beyond the retrieved evidence.
                faithfulness=round(groundedness, 4),
                citation_accuracy=round(citation_accuracy, 4),
                answer_relevance=round(relevance, 4),
                hallucination_detected=hallucination,
                latency_ms=response.latency_ms,
                passed=passed,
                detail=detail,
            )
        )

    aggregate = _aggregate(
        results,
        dataset_path=str(dataset_file),
        disclaimer=disclaimer,
        dataset_note=dataset_note,
        issue_coverages=issue_coverages,
        rank_metrics=rank_metric_lists,
    )

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True) if out.parent != Path("") else None
    json_path = out.with_suffix(".json")
    md_path = out.with_suffix(".md")
    payload = {
        **aggregate,
        "results": [r.model_dump(mode="json") for r in results],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(results, aggregate), encoding="utf-8")

    print(f"Evaluated {len(results)} cases — pass rate {aggregate['pass_rate']:.0%}")
    print(f"JSON report:     {json_path}")
    print(f"Markdown report: {md_path}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline evaluation runner (mock LLM, in-memory stores).")
    parser.add_argument(
        "--dataset",
        default=str(Path(__file__).with_name("golden_dataset.json")),
        help="Path to the golden dataset JSON file.",
    )
    parser.add_argument(
        "--out",
        default="eval_report",
        help="Output path prefix; <out>.json and <out>.md are written.",
    )
    args = parser.parse_args()
    asyncio.run(evaluate(args.dataset, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
