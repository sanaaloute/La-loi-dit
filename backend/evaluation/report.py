"""Markdown report rendering for evaluation runs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.core.models import EvalCaseResult

_DISCLAIMER = (
    "_Illustrative sample cases for pipeline testing only — "
    "not verified legal advice._"
)


def render_markdown(results: list[EvalCaseResult], aggregate: dict[str, Any]) -> str:
    """Render a full markdown evaluation report from case results + aggregates."""
    generated = aggregate.get("generated_at") or datetime.now(timezone.utc).isoformat()
    lines: list[str] = [
        "# Burkina Faso Legal AI — Evaluation Report",
        "",
        _DISCLAIMER,
        "",
        f"Generated: {generated}  ",
        f"Dataset: `{aggregate.get('dataset', 'unknown')}`  ",
        f"Cases: {aggregate.get('total_cases', len(results))} — "
        f"passed: {aggregate.get('passed', 0)} "
        f"({aggregate.get('pass_rate', 0.0):.0%})",
        "",
    ]
    if aggregate.get("dataset_note"):
        lines += [f"_{aggregate['dataset_note']}_", ""]
    lines += [
        "## Aggregate metrics",
        "",
        "| Metric | Value |",
        "| --- | --- |",
    ]
    for key in (
        "mean_groundedness",
        "mean_faithfulness",
        "mean_citation_accuracy",
        "mean_answer_relevance",
        "mean_issue_coverage",
        "mean_recall_at_5",
        "mean_precision_at_5",
        "mean_mrr",
        "mean_ndcg_at_5",
        "hallucination_rate",
        "mean_latency_ms",
        "p95_latency_ms",
    ):
        if key in aggregate:
            value = aggregate[key]
            rendered = f"{value:.3f}" if isinstance(value, float) else str(value)
            lines.append(f"| {key} | {rendered} |")

    lines += [
        "",
        "## Per-case results",
        "",
        "| Case | Grounded. | Cit. acc. | Relevance | Halluc. | Latency (ms) | Passed |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        lines.append(
            f"| {r.case_id} | {r.groundedness:.2f} | {r.citation_accuracy:.2f} | "
            f"{r.answer_relevance:.2f} | {'yes' if r.hallucination_detected else 'no'} | "
            f"{r.latency_ms:.0f} | {'PASS' if r.passed else 'FAIL'} |"
        )

    failures = [r for r in results if not r.passed]
    if failures:
        lines += ["", "## Failure details", ""]
        for r in failures:
            lines.append(f"- **{r.case_id}** — {r.question}")
            if r.detail:
                lines.append(f"  - {r.detail}")
    lines.append("")
    return "\n".join(lines)
