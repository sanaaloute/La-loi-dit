#!/usr/bin/env python3
"""Live regression harness for the chat pipeline.

Runs the sessions of ``data/eval/regression_failures.json`` against the REAL
pipeline (build_context: Milvus/Redis/real LLM per the environment, with the
usual offline fallbacks) and checks per-turn assertions. Multi-turn sessions
share one ``session_id`` so the memory buffer carries over — this reproduces
the in-session history contamination reported in production.

The harness keeps its OWN abstention phrase list (instead of importing the
pipeline's detector) so baseline runs work on code that predates the
answerability gate, and before/after reports stay comparable.

Usage:
    python scripts/run_regression_live.py \
        --dataset data/eval/regression_failures.json \
        --out data/eval/regression_report [--trace]

Exit code: 0 when every assertion passes, 1 otherwise (before/after diffs).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow running as ``python scripts/run_regression_live.py`` without installing
# the package: put the repo root on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---------------------------------------------------------------------------
# Self-contained signal detection (do NOT import pipeline modules for these —
# the baseline code has no answerability gate).
# ---------------------------------------------------------------------------

_ABSTENTION_PHRASES = [
    # FR — model-written insufficiency declarations
    "n'est pas couverte",
    "ne sont pas couverts",
    "pas couverte par les extraits",
    "pas couverte par ces extraits",
    "ne permettent pas de repondre",
    "ne permet pas de repondre",
    "ne contiennent aucune disposition",
    "ne contiennent pas de disposition",
    "ne sont pas mentionne",
    "pas explicitement mentionne",
    "n'est pas mentionne",
    "il est necessaire de consulter",
    "il convient de consulter",
    # FR — fixed pipeline messages (honest insufficiency paths)
    "preuves disponibles sont insuffisantes",
    "suffisamment d'informations fiables",
    # EN
    "not covered by the provided excerpts",
    "not covered by the excerpts",
    "do not contain any provision",
    "insufficient to answer",
    "available evidence is insufficient",
]


def _fold(text: str) -> str:
    """Lowercase, strip accents, unify apostrophes — matching is language-noise free."""
    text = text.lower().replace("’", "'")
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn"
    )


_FOLDED_PHRASES = [_fold(p) for p in _ABSTENTION_PHRASES]

# Matches the pipeline detector: caveats inside the mandated uncertainty
# section are NOT abstentions (that section exists to declare gaps).
_ANY_HEADING_RE = re.compile(r"^\s*#{1,6}\s+")
_UNCERTAINTY_TITLE_RE = re.compile(
    r"^\s*(?:#{1,6}\s+|\*\*)?\s*(points? d'incertitudes?|incertitudes?|points? of uncertainty|uncertainties)\s*:?\s*(?:\*\*)?\s*$",
    re.IGNORECASE,
)


def _strip_uncertainty_sections(text: str) -> str:
    kept: list[str] = []
    in_uncertainty = False
    for line in text.splitlines():
        if _ANY_HEADING_RE.match(line) or _UNCERTAINTY_TITLE_RE.match(line):
            in_uncertainty = bool(_UNCERTAINTY_TITLE_RE.match(line))
            continue
        if not in_uncertainty:
            kept.append(line)
    return "\n".join(kept)


def detect_abstention(answer_text: str) -> list[str]:
    folded = _fold(_strip_uncertainty_sections(answer_text or ""))
    return [p for p in _FOLDED_PHRASES if p in folded]


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------

CONFIDENCE_THRESHOLD = 0.55  # mirrors Settings.confidence_threshold default


def check_assertions(
    assertions: dict[str, Any],
    *,
    answer_text: str,
    confidence: float,
    evidence_docs: list[str],
    abstaining: bool,
    low_confidence_fallback: bool,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    folded_answer = _fold(answer_text)

    for keyword in assertions.get("forbidden_keywords", []):
        present = _fold(keyword) in folded_answer
        checks.append(
            {
                "check": f"forbidden_keyword:{keyword}",
                "passed": not present,
                "detail": "found in answer" if present else "absent",
            }
        )

    for pattern in assertions.get("expected_document_patterns", []):
        rx = re.compile(_fold(pattern))
        matched = [doc for doc in evidence_docs if rx.search(_fold(doc))]
        checks.append(
            {
                "check": f"expected_document:{pattern}",
                "passed": bool(matched),
                "detail": f"matched {matched[:3]}" if matched else f"no match in {evidence_docs[:8]}",
            }
        )

    if assertions.get("abstention_consistent") and abstaining:
        checks.append(
            {
                "check": "abstention_consistent",
                "passed": confidence < CONFIDENCE_THRESHOLD,
                "detail": f"abstaining answer with confidence {confidence:.2f} "
                f"(must be < {CONFIDENCE_THRESHOLD})",
            }
        )

    if assertions.get("expect_answered"):
        checks.append(
            {
                "check": "expect_answered",
                "passed": not abstaining and not low_confidence_fallback,
                "detail": f"abstaining={abstaining} low_confidence_fallback={low_confidence_fallback}",
            }
        )

    return checks


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run(dataset_path: str, out_prefix: str, show_trace: bool) -> dict[str, Any]:
    from backend.core.context import build_context
    from backend.workflows.graph import build_graph, initial_state, run_query

    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    ctx = await build_context()
    graph = build_graph(ctx)

    run_id = uuid.uuid4().hex[:8]
    report_sessions: list[dict[str, Any]] = []
    total_checks = failed_checks = 0

    for session in dataset.get("sessions", []):
        session_id = f"reg-{run_id}-{session['id']}"
        turns_report: list[dict[str, Any]] = []
        for turn in session.get("turns", []):
            question = turn["question"]
            state = initial_state(question, session_id=session_id, user_id="regression", language="fr")
            response = await run_query(graph, ctx, state)
            answer = response.answer

            abstention_hits = detect_abstention(answer.answer)
            low_conf_fallback = bool(answer.metadata.get("low_confidence_fallback"))
            evidence_docs = sorted({c.document_name for c in answer.evidence})
            checks = check_assertions(
                turn.get("assertions", {}),
                answer_text=answer.answer,
                confidence=answer.confidence,
                evidence_docs=evidence_docs,
                abstaining=bool(abstention_hits),
                low_confidence_fallback=low_conf_fallback,
            )
            for check in checks:
                total_checks += 1
                failed_checks += 0 if check["passed"] else 1

            entry: dict[str, Any] = {
                "question": question,
                "confidence": answer.confidence,
                "confidence_breakdown": (
                    answer.confidence_breakdown.model_dump(mode="json")
                    if answer.confidence_breakdown
                    else None
                ),
                "abstention_detected": abstention_hits,
                "low_confidence_fallback": low_conf_fallback,
                "refused": answer.refused,
                "requires_human_review": answer.requires_human_review,
                "evidence_documents": evidence_docs,
                "evidence_count": len(answer.evidence),
                "warnings": answer.warnings,
                "latency_ms": response.latency_ms,
                "answer_excerpt": answer.answer[:600],
                "checks": checks,
            }
            if show_trace:
                entry["trace"] = response.trace
            turns_report.append(entry)

            status = "PASS" if all(c["passed"] for c in checks) else "FAIL"
            print(
                f"[{status}] {session['id']}: {question[:70]!r} "
                f"conf={answer.confidence:.2f} abstain={bool(abstention_hits)} "
                f"evidence={len(answer.evidence)} ({response.latency_ms:.0f} ms)"
            )
            for check in checks:
                mark = "ok" if check["passed"] else "KO"
                print(f"    {mark} {check['check']}: {check['detail']}")

        report_sessions.append({"id": session["id"], "turns": turns_report})

    report = {
        "dataset": dataset_path,
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_checks": total_checks,
        "failed_checks": failed_checks,
        "passed": failed_checks == 0,
        "sessions": report_sessions,
    }
    out = Path(out_prefix).with_suffix(".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{total_checks - failed_checks}/{total_checks} checks passed — report: {out}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="data/eval/regression_failures.json")
    parser.add_argument("--out", default="data/eval/regression_report")
    parser.add_argument("--trace", action="store_true", help="include per-node traces in the report")
    args = parser.parse_args()
    report = asyncio.run(run(args.dataset, args.out, args.trace))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
