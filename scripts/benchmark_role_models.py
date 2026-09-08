"""Benchmark candidate models on the two classification-role tasks.

Measures accuracy and latency through the exact production code path
(``LLMClient.complete`` + the real prompts from the registry):

1. query routing (QUERY_ROUTER_SYSTEM -> parse_route): DIRECT vs RETRIEVAL
2. language gate (QUERY_TRANSLATE_SYSTEM -> _parse_translation): language
   detection + French translation

Local models are served by the host Ollama (http://localhost:11434); the
cloud model (e.g. gpt-oss:20b) goes through the provider configured in
``.env`` — its credentials are read by ``get_settings()`` and never printed.

Run with the project venv:

    .venv/bin/python scripts/benchmark_role_models.py \
        --local qwen2.5:3b qwen3:1.7b qwen3:4b qwen3:8b \
        --cloud gpt-oss:20b
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time

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

# (prompt, expected ISO language) — the unaccented French entries are the
# trap cases the 3b classifier failed in production.
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


# (question, min_sub_questions) — broad questions must be decomposed.
PLANNER_CASES: list[tuple[str, int]] = [
    ("Quels sont les droits d'un salarié licencié ?", 2),
    ("Quel est le préavis en cas de licenciement ?", 1),
    ("What is the legal age of marriage in Burkina Faso?", 1),
]

# (question, answer language, expected keyword) — graded deterministically:
# the answer must carry an [n] citation, the keyword, and the right language.
_SYNTH_EVIDENCE = (
    "[1] Code du travail, article 33 : La durée du préavis est de huit jours "
    "pour les travailleurs payés à l'heure ou à la journée, un mois pour les "
    "employés et trois mois pour les cadres, agents de maîtrise, techniciens "
    "et assimilés.\n"
    "[2] Code du travail, article 34 : Toute rupture abusive du contrat de "
    "travail peut donner lieu à des dommages et intérêts."
)
SYNTH_CASES: list[tuple[str, str, str]] = [
    ("Quel est le préavis en cas de licenciement ?", "fr", "mois"),
    ("What is the notice period for dismissal?", "en", "month"),
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
    for query, language, keyword in SYNTH_CASES:
        user = f"Question: {query}\nLanguage: {language}\n\nPreuves:\n{_SYNTH_EVIDENCE}"
        start = time.perf_counter()
        try:
            raw = await llm.complete(prompt, user, temperature=0.0)
        except Exception as exc:
            raw, misses = "", [*misses, f"{query!r}: LLM error {exc!r}"]
            latencies.append(time.perf_counter() - start)
            continue
        latencies.append(time.perf_counter() - start)
        cited = bool(re.search(r"\[\d+\]", raw))
        has_keyword = keyword.lower() in raw.lower()
        # Allow a couple of French markers when answering in English: the
        # answer may legitimately name « Code du travail » etc.
        right_language = (
            _looks_french(raw) if language == "fr" else _french_marker_hits(raw) <= 2
        )
        if cited and has_keyword and right_language:
            correct += 1
        else:
            misses.append(
                f"{query!r}: cited={cited} keyword={has_keyword} lang_ok={right_language} ({raw[:60]!r})"
            )
    return correct, latencies, misses


def _extract_json(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(0))
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


class OllamaNoThinkClient:
    """Direct Ollama /api/chat with think=False — same .complete() shape as
    LLMClient, for thinking models (qwen3/qwen3.5) whose reasoning otherwise
    burns the whole completion budget on classification-style tasks."""

    def __init__(self, model: str, host: str = "http://localhost:11434"):
        self._model = model
        self._host = host

    async def complete(self, system: str, user: str, temperature=None) -> str:
        from ollama import AsyncClient

        resp = await AsyncClient(host=self._host).chat(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            think=False,
            options={"temperature": temperature if temperature is not None else 0.0},
        )
        return resp.message.content or ""


async def bench_model(label: str, llm: LLMClient) -> None:
    # Warmup: model load time must not pollute the latencies.
    try:
        await llm.complete("Tu es un assistant.", "Dis: ok", temperature=0.0)
    except Exception as exc:
        print(f"\n== {label} == UNAVAILABLE (warmup failed: {exc!r})\n", flush=True)
        return
    r_ok, r_lat, r_miss = await bench_router(llm)
    l_ok, l_lat, l_miss = await bench_language(llm)
    p_ok, p_lat, p_miss = await bench_planner(llm)
    s_ok, s_lat, s_miss = await bench_synthesis(llm)
    print(f"\n== {label} ==", flush=True)
    print(f"  router    : {r_ok}/{len(ROUTER_CASES)} correct | {_stats(r_lat)}")
    print(f"  language  : {l_ok}/{len(LANG_CASES)} correct | {_stats(l_lat)}")
    print(f"  planner   : {p_ok}/{len(PLANNER_CASES)} valid | {_stats(p_lat)}")
    print(f"  synthesis : {s_ok}/{len(SYNTH_CASES)} grounded | {_stats(s_lat)}")
    for miss in [*r_miss, *l_miss, *p_miss, *s_miss]:
        print(f"    miss: {miss}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", nargs="*", default=[], help="model names on the host Ollama")
    parser.add_argument("--cloud", nargs="*", default=[], help="models via the .env-configured provider")
    parser.add_argument(
        "--no-think-local",
        action="store_true",
        help="call local models via native Ollama chat with think=False (thinking models)",
    )
    args = parser.parse_args()

    settings = get_settings()
    for model in args.cloud:
        await bench_model(f"cloud:{model}", LLMClient(settings, model=model))
    for model in args.local:
        if args.no_think_local:
            await bench_model(f"local-nothink:{model}", OllamaNoThinkClient(model))
        else:
            await bench_model(
                f"local:{model}",
                LLMClient(settings, model=model, api_key="", api_base="http://localhost:11434"),
            )


if __name__ == "__main__":
    asyncio.run(main())
