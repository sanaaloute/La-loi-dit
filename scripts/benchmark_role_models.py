"""Benchmark candidate Afrikia models on classification and generation tasks.

Measures accuracy and latency through the exact production code path
(``LLMClient.complete`` + the real prompts from the registry):

1. query routing (QUERY_ROUTER_SYSTEM -> parse_route): DIRECT vs RETRIEVAL
2. language gate (QUERY_TRANSLATE_SYSTEM -> _parse_translation): language
   detection + French translation
3. planning and grounded synthesis

Candidate models must be served by the Afrikia provider (see /v1/models); the
bearer key comes from AFRIKIA_API_KEY in .env. Run with the project venv:

    .venv/bin/python scripts/benchmark_role_models.py \
        --models qwen2.5-7b gpt-oss-20b

For reasoning models, cap the hidden reasoning effort so it does not burn the
completion budget on classification-style tasks:

    .venv/bin/python scripts/benchmark_role_models.py \
        --models gpt-oss-20b --effort low
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agents.language_gate import _looks_french, _parse_translation
from backend.agents.query_router import parse_route
from backend.core.config import get_settings
from backend.core.llm import LLMClient
from backend.core.prompts import get_prompt

# (prompt, expected route) — borderline cases included on purpose.
ROUTER_CASES: list[tuple[str, str]] = [
    ("Que peux tu faire pour moi ?", "direct"),
    ("Qui es-tu ?", "direct"),
    ("Comment fonctionnes-tu ?", "direct"),
    ("Merci beaucoup pour ton aide", "direct"),
    ("What can you do for me?", "direct"),
    ("Tu peux m'aider s'il te plaît ?", "direct"),
    ("Bonjour, j'espère que tu vas bien", "direct"),
    ("Quels sont mes droits en cas de licenciement ?", "retrieval"),
    ("Que sais-tu du droit du travail ?", "retrieval"),
    ("Bonjour, quels sont les délais de prescription ?", "retrieval"),
    ("What is the statute of limitations in Burkina Faso?", "retrieval"),
    ("Comment faire un testament au Burkina Faso ?", "retrieval"),
    ("Peux-tu m'expliquer l'article 244 du code des assurances ?", "retrieval"),
    ("C'est quoi l'OHADA ?", "retrieval"),
]

# (prompt, expected ISO language) — the unaccented French entries are
# trap cases for language detection and French translation.
LANG_CASES: list[tuple[str, str]] = [
    ("What is the notice period for dismissal?", "en"),
    ("What are my rights as a tenant?", "en"),
    ("I want to divorce my wife, what should I do?", "en"),
    ("¿Cuáles son mis derechos?", "es"),
    ("Was sind meine Rechte?", "de"),
    ("Quais são os meus direitos?", "pt"),
    ("Que peux tu faire pour moi ?", "fr"),
    ("Quels sont les delais de prescription?", "fr"),
]


async def bench_router(llm: LLMClient) -> tuple[int, list[float], list[str]]:
    prompt = get_prompt("QUERY_ROUTER_SYSTEM")
    correct, latencies, misses = 0, [], []
    for query, expected in ROUTER_CASES:
        start = time.perf_counter()
        try:
            raw = await llm.complete(prompt, f"Question: {query}", temperature=0.0)
        except Exception as exc:
            raw, misses = "", [*misses, f"{query!r}: LLM error {exc!r}"]
            latencies.append(time.perf_counter() - start)
            continue
        latencies.append(time.perf_counter() - start)
        got = parse_route(raw or "")
        if got == expected:
            correct += 1
        else:
            misses.append(f"{query!r}: expected {expected}, got {got} ({raw[:60]!r})")
    return correct, latencies, misses


async def bench_language(llm: LLMClient) -> tuple[int, list[float], list[str]]:
    prompt = get_prompt("QUERY_TRANSLATE_SYSTEM")
    correct, latencies, misses = 0, [], []
    for query, expected in LANG_CASES:
        start = time.perf_counter()
        try:
            raw = await llm.complete(prompt, f"Texte: {query}", temperature=0.0)
        except Exception as exc:
            raw, misses = "", [*misses, f"{query!r}: LLM error {exc!r}"]
            latencies.append(time.perf_counter() - start)
            continue
        latencies.append(time.perf_counter() - start)
        payload = _parse_translation(raw or "")
        if payload and payload["language"].startswith(expected) and payload["french"]:
            correct += 1
        else:
            misses.append(f"{query!r}: expected {expected}, got {raw[:80]!r}")
    return correct, latencies, misses


def _stats(latencies: list[float]) -> str:
    if not latencies:
        return "n/a"
    ordered = sorted(latencies)
    p50 = ordered[len(ordered) // 2]
    return f"mean {sum(latencies) / len(latencies):5.2f}s  p50 {p50:5.2f}s  max {max(latencies):5.2f}s"


def _latency_payload(latencies: list[float]) -> dict[str, float]:
    if not latencies:
        return {}
    ordered = sorted(latencies)
    p95_index = min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))
    return {
        "mean_seconds": sum(latencies) / len(latencies),
        "p50_seconds": ordered[len(ordered) // 2],
        "p95_seconds": ordered[p95_index],
        "max_seconds": ordered[-1],
    }


# (question, min_sub_questions) — broad questions must be decomposed.
PLANNER_CASES: list[tuple[str, int]] = [
    ("Quels sont les droits d'un salarié licencié ?", 2),
    ("Quel est le préavis en cas de licenciement ?", 1),
    ("What is the legal age of marriage in Burkina Faso?", 1),
]

# (question, language, required phrases, forbidden phrases). These cases check
# citation presence, language, and exact category-specific numeric fidelity.
_SYNTH_EVIDENCE = (
    "[1] Code du travail, article 33 : La durée du préavis est de huit jours "
    "pour les travailleurs payés à l'heure ou à la journée, un mois pour les "
    "employés et trois mois pour les cadres, agents de maîtrise, techniciens "
    "et assimilés.\n"
    "[2] Code du travail, article 34 : Toute rupture abusive du contrat de "
    "travail peut donner lieu à des dommages et intérêts."
)
SYNTH_CASES: list[tuple[str, str, list[str], list[str]]] = [
    (
        "Quel est le préavis d'un employé mensualisé licencié ?",
        "fr",
        ["un mois"],
        ["trois mois", "huit jours"],
    ),
    (
        "What is the notice period for a monthly-paid employee?",
        "en",
        ["one month"],
        ["three months", "eight days"],
    ),
    (
        "Quel est le préavis d'un cadre licencié ?",
        "fr",
        ["trois mois"],
        ["un mois", "huit jours"],
    ),
]


async def bench_planner(llm: LLMClient) -> tuple[int, list[float], list[str]]:
    prompt = get_prompt("PLANNER_SYSTEM")
    correct, latencies, misses = 0, [], []
    for query, min_subs in PLANNER_CASES:
        start = time.perf_counter()
        try:
            raw = await llm.complete(prompt, f"Question: {query}\n\nBuild a focused retrieval plan.", temperature=0.0)
        except Exception as exc:
            raw, misses = "", [*misses, f"{query!r}: LLM error {exc!r}"]
            latencies.append(time.perf_counter() - start)
            continue
        latencies.append(time.perf_counter() - start)
        payload = _extract_json(raw or "")
        subs = payload.get("sub_questions") if isinstance(payload, dict) else None
        tasks = payload.get("tasks") if isinstance(payload, dict) else None
        if isinstance(subs, list) and len(subs) >= min_subs and isinstance(tasks, list) and tasks:
            correct += 1
        else:
            misses.append(f"{query!r}: invalid plan ({raw[:80]!r})")
    return correct, latencies, misses


def _french_marker_hits(text: str) -> int:
    from backend.agents.tools.planning import _FR_MARKERS

    lowered = f" {text.lower()} "
    return sum(1 for m in _FR_MARKERS if m in lowered)


async def bench_synthesis(llm: LLMClient) -> tuple[int, list[float], list[str]]:
    prompt = get_prompt("RESPONSE_SYSTEM")
    correct, latencies, misses = 0, [], []
    for query, language, required, forbidden in SYNTH_CASES:
        user = f"Question: {query}\nLanguage: {language}\n\nPreuves:\n{_SYNTH_EVIDENCE}"
        start = time.perf_counter()
        try:
            raw = await llm.complete(prompt, user, temperature=0.0)
        except Exception as exc:
            raw, misses = "", [*misses, f"{query!r}: LLM error {exc!r}"]
            latencies.append(time.perf_counter() - start)
            continue
        latencies.append(time.perf_counter() - start)
        lowered = raw.lower()
        cited = bool(re.search(r"\[\d+\]", raw))
        required_ok = all(phrase.lower() in lowered for phrase in required)
        forbidden_ok = all(phrase.lower() not in lowered for phrase in forbidden)
        right_language = (
            _looks_french(raw) if language == "fr" else _french_marker_hits(raw) <= 2
        )
        if cited and required_ok and forbidden_ok and right_language:
            correct += 1
        else:
            misses.append(
                f"{query!r}: cited={cited} required={required_ok} "
                f"forbidden_absent={forbidden_ok} lang_ok={right_language} ({raw[:80]!r})"
            )
    return correct, latencies, misses


async def bench_abstention(llm: LLMClient) -> tuple[int, list[float], list[str]]:
    prompt = get_prompt("RESPONSE_SYSTEM")
    query = "Quel est le taux de la redevance sur les engins spatiaux ?"
    user = f"Question: {query}\nLanguage: fr\n\nPreuves:\n"
    start = time.perf_counter()
    try:
        raw = await llm.complete(prompt, user, temperature=0.0)
    except Exception as exc:
        return 0, [time.perf_counter() - start], [f"abstention: LLM error {exc!r}"]
    latency = time.perf_counter() - start
    lowered = raw.lower()
    no_fabricated_citation = not re.search(r"\[\d+\]", raw)
    admits_gap = any(
        marker in lowered
        for marker in ("insuffis", "ne contiennent pas", "ne peux pas", "cannot", "insufficient")
    )
    correct = int(no_fabricated_citation and admits_gap)
    misses = [] if correct else [f"abstention: unsupported answer ({raw[:100]!r})"]
    return correct, [latency], misses


def _extract_json(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(0))
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


async def bench_model(label: str, llm: LLMClient) -> dict:
    # Warmup: model load time must not pollute the latencies.
    try:
        await llm.complete("Tu es un assistant.", "Dis: ok", temperature=0.0)
    except Exception as exc:
        print(f"\n== {label} == UNAVAILABLE (warmup failed: {exc!r})\n", flush=True)
        return {"label": label, "available": False, "error": repr(exc)}
    r_ok, r_lat, r_miss = await bench_router(llm)
    l_ok, l_lat, l_miss = await bench_language(llm)
    p_ok, p_lat, p_miss = await bench_planner(llm)
    s_ok, s_lat, s_miss = await bench_synthesis(llm)
    a_ok, a_lat, a_miss = await bench_abstention(llm)
    print(f"\n== {label} ==", flush=True)
    print(f"  router    : {r_ok}/{len(ROUTER_CASES)} correct | {_stats(r_lat)}")
    print(f"  language  : {l_ok}/{len(LANG_CASES)} correct | {_stats(l_lat)}")
    print(f"  planner   : {p_ok}/{len(PLANNER_CASES)} valid | {_stats(p_lat)}")
    print(f"  synthesis : {s_ok}/{len(SYNTH_CASES)} grounded | {_stats(s_lat)}")
    print(f"  abstention: {a_ok}/1 correct | {_stats(a_lat)}")
    for miss in [*r_miss, *l_miss, *p_miss, *s_miss, *a_miss]:
        print(f"    miss: {miss}")

    def task(correct, total, latencies, misses):
        return {
            "correct": correct,
            "total": total,
            "accuracy": correct / total if total else 0.0,
            "latency": _latency_payload(latencies),
            "misses": misses,
        }

    return {
        "label": label,
        "available": True,
        "tasks": {
            "router": task(r_ok, len(ROUTER_CASES), r_lat, r_miss),
            "language": task(l_ok, len(LANG_CASES), l_lat, l_miss),
            "planner": task(p_ok, len(PLANNER_CASES), p_lat, p_miss),
            "synthesis": task(s_ok, len(SYNTH_CASES), s_lat, s_miss),
            "abstention": task(a_ok, 1, a_lat, a_miss),
        },
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="*", default=[], help="candidate model ids on the Afrikia provider")
    parser.add_argument(
        "--effort",
        choices=["low", "medium", "high"],
        default=None,
        help="reasoning_effort for reasoning-capable candidates (dropped for the rest)",
    )
    parser.add_argument("--out", help="optional JSON report path")
    args = parser.parse_args()

    settings = get_settings()
    reports = []
    for model in args.models:
        role_options = {"reasoning_effort": args.effort} if args.effort else None
        label = f"afrikia:{model}" + (f" effort={args.effort}" if args.effort else "")
        reports.append(await bench_model(
            label,
            LLMClient(
                settings,
                provider="afrikia",
                model=model,
                role_options=role_options,
            ),
        ))
    if args.out:
        payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "models": reports}
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print(f"JSON report: {args.out}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
