"""Response Generator.

Composes the final answer strictly from ranked evidence (grounded answer
policy): every substantive statement carries a numeric citation. When the
LLM is unavailable it falls back to a deterministic template that quotes
only real evidence — it can never invent legal content. Evidence is
retrieved in French; when the user asked in another language the LLM
translates, and the fallback template states the language limitation.
"""

from __future__ import annotations

from typing import Any, Optional

from backend.core.context import AppContext
from backend.core.models import EvidenceChunk, FinalAnswer
from backend.core.state import GraphState

_SYSTEM = """You are the response generator of an expert legal research assistant for
Burkina Faso. Write the answer using ONLY the numbered evidence excerpts below.
Cite every substantive statement with [n] referring to its evidence excerpt.
If the evidence is insufficient, say so explicitly instead of guessing.
Respond in {language}."""


def _confidence(evidence: list[EvidenceChunk], citation_accuracy: float, settings=None) -> float:
    if not evidence:
        return 0.0
    from backend.core.config import get_settings

    cfg = settings or get_settings()
    top_n = 5
    top = evidence[:top_n]
    relevance = sum(max(c.rerank_score, c.retrieval_score) for c in top) / len(top)
    coverage = min(1.0, len(evidence) / top_n)
    return round(min(1.0, 0.5 * relevance + 0.3 * coverage + 0.2 * citation_accuracy), 3)


def compose_template_answer(
    query: str,
    evidence: list[EvidenceChunk],
    language: str,
    max_bullets: Optional[int] = None,
    excerpt_max_len: Optional[int] = None,
) -> str:
    """Deterministic grounded answer — only restates actual evidence."""
    from backend.core.config import get_settings

    cfg = get_settings()
    max_bullets = max_bullets if max_bullets is not None else cfg.answer_max_bullets
    excerpt_max_len = excerpt_max_len if excerpt_max_len is not None else 300
    if not evidence:
        if language.startswith("en"):
            return (
                "I could not find verifiable evidence in the indexed official sources "
                "to answer this question. Rather than guessing, I must state that the "
                "available evidence is insufficient. Please consult the Official Gazette "
                "(Journal Officiel du Burkina Faso) or a licensed legal professional."
            )
        return (
            "Je n'ai pas trouvé de preuves vérifiables dans les sources officielles indexées "
            "pour répondre à cette question. Plutôt que de conjecturer, je dois déclarer que "
            "les preuves disponibles sont insuffisantes. Veuillez consulter le Journal "
            "Officiel du Burkina Faso ou un professionnel du droit agréé."
        )
    if language.startswith("en"):
        lines = ["Based on the verified official sources retrieved (in French):"]
    else:
        lines = ["Sur la base des sources officielles vérifiées suivantes :"]
    for i, chunk in enumerate(evidence[:max_bullets], start=1):
        excerpt = chunk.content.strip().replace("\n", " ")
        if len(excerpt) > excerpt_max_len:
            excerpt = excerpt[: excerpt_max_len - 3] + "..."
        lines.append(f"- {excerpt} [{i}]")
    return "\n".join(lines)


async def response_generator_node(state: GraphState, ctx: AppContext) -> dict[str, Any]:
    settings = ctx.settings
    evidence = list(state.get("ranked_evidence", []))
    plan = state.get("plan")
    language = state.get("language") or (plan.response_language if plan else "fr")
    conflicts = list(state.get("conflicts", []))
    reflection = state.get("reflection")
    warnings: list[str] = []
    if reflection:
        warnings.extend(reflection.issues)
    for conflict in conflicts:
        if conflict.resolved:
            warnings.append(f"conflit résolu ({conflict.topic}): {conflict.reason}")
        else:
            warnings.append(f"CONFLIT NON RÉSOLU ({conflict.topic}): {conflict.reason}")

    draft = state.get("draft_answer", "")
    max_evidence = settings.answer_max_evidence
    if not draft:
        if ctx.llm.provider == "mock" or not evidence:
            draft = compose_template_answer(state["query"], evidence, language, max_bullets=settings.answer_max_bullets)
        else:
            evidence_text = "\n\n".join(
                f"[{i}] {c.citation_label()}: {c.content[:800]}" for i, c in enumerate(evidence[:max_evidence], 1)
            )
            try:
                draft = await ctx.llm.complete(
                    _SYSTEM.format(language=language),
                    f"Question: {state['query']}\n\nPreuves:\n{evidence_text}",
                )
            except Exception:
                draft = compose_template_answer(state["query"], evidence, language, max_bullets=settings.answer_max_bullets)

    from backend.agents.citation_verification import extract_citations

    verified, _rejected = extract_citations(draft, evidence)
    accuracy = state.get("citation_accuracy", 1.0)
    answer = FinalAnswer(
        answer=draft,
        citations=verified,
        evidence=evidence[:max_evidence],
        confidence=_confidence(evidence, accuracy, settings),
        language=language,
        warnings=warnings,
        conflicts=conflicts,
    )
    return {
        "draft_answer": draft,
        "final_answer": answer,
        "trace": [
            *state.get("trace", []),
            f"response_generator: answer drafted ({len(draft)} chars, confidence {answer.confidence})",
        ],
    }
