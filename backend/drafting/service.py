"""Draft generation: fill skeleton -> ground in retrieved law -> verify.

Pipeline (``generate_draft``):

1. **Fill** — the template skeleton is completed deterministically from the
   caller's fields; unknown/empty optional fields become "________" blanks.
   Works with zero LLM and zero retrieval.
2. **Ground** — a handful of citable provisions are retrieved with the
   existing ``RetrievalCoordinator`` (vector + keyword tasks built from the
   template's legal domain query), exactly like the chat graph does.
3. **Enrich** — the per-request (tier-gated) LLM rewrites the draft to fit
   the fields and free-form instructions, citing ONLY the retrieved
   provisions with ``[n]`` markers. The mock provider skips this step, so
   offline runs keep the deterministic skeleton.
4. **Verify** — ``extract_citations`` (shared with the citation-verification
   graph node) splits draft citations into verified/rejected against the
   retrieved evidence; rejected markers are stripped and become warnings.

No evidence (offline/empty store) -> the plain skeleton is returned with a
French warning and ``requires_human_review=True``. Citations are NEVER
fabricated: the only ``[n]`` markers a draft can carry point at chunks the
retriever actually returned.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from pydantic import BaseModel, Field

from backend.agents.citation_verification import extract_citations
from backend.core.context import AppContext
from backend.core.models import Citation, EvidenceChunk, SearchKind, SearchTask

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")

_GROUNDING_TOP_K = 5  # provisions offered to the LLM / references section

_NO_EVIDENCE_WARNING = (
    "Aucune source juridique n'a pu être vérifiée : les clauses sont génériques "
    "et doivent être relues par un professionnel du droit avant usage."
)


class DraftResult(BaseModel):
    """Outcome of one draft generation."""

    title: str
    template_id: str
    draft_markdown: str
    citations: list[Citation] = Field(default_factory=list)  # verified only
    warnings: list[str] = Field(default_factory=list)
    requires_human_review: bool = False


# ---------------------------------------------------------------------------
# Step 1: deterministic fill
# ---------------------------------------------------------------------------


def fill_skeleton(template: dict[str, Any], fields: dict[str, str]) -> str:
    """Substitute ``{{field}}`` placeholders; empty values become blanks."""

    def _replace(match: re.Match) -> str:
        value = (fields.get(match.group(1)) or "").strip()
        return value if value else "________"

    return _PLACEHOLDER_RE.sub(_replace, template["skeleton"]).strip()


# ---------------------------------------------------------------------------
# Step 2: grounding retrieval
# ---------------------------------------------------------------------------


async def _retrieve_grounding(ctx: AppContext, template: dict[str, Any]) -> list[EvidenceChunk]:
    """Retrieve citable provisions for the template's legal domain."""
    if ctx.retriever is None:
        return []
    query = template["legal_query"]
    tasks = [
        SearchTask(kind=SearchKind.VECTOR, query=query, top_k=_GROUNDING_TOP_K, filters={}),
        SearchTask(kind=SearchKind.KEYWORD, query=query, top_k=_GROUNDING_TOP_K, filters={}),
    ]
    try:
        return (await ctx.retriever.retrieve(tasks))[:_GROUNDING_TOP_K]
    except Exception:
        logger.warning("drafting grounding retrieval failed", exc_info=True)
        return []


def _references_section(evidence: list[EvidenceChunk]) -> str:
    """Deterministic 'Références légales' block citing retrieved provisions.

    The ``[n]`` markers index into ``evidence`` — the same convention the
    citation-verification pass validates, so these citations are real.
    """
    lines = ["", "## Références légales", ""]
    for idx, chunk in enumerate(evidence, 1):
        excerpt = " ".join(chunk.content.split())[:250]
        lines.append(f"- [{idx}] {chunk.citation_label()} — « {excerpt} »")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Step 3: LLM enrichment (skipped for the offline mock provider)
# ---------------------------------------------------------------------------

_ENRICH_SYSTEM = """Tu es un juriste rédacteur burkinabè. Tu améliores un projet de document juridique.

Règles impératives :
- Garde la structure markdown (titres, articles numérotés, bloc de signatures).
- Intègre les valeurs des champs et les instructions du demandeur.
- Tu peux citer UNIQUEMENT les dispositions listées ci-dessous, avec leurs marqueurs [n] exacts.
- N'invente JAMAIS de numéro d'article, de loi ou de référence.
- Conserve la section « Références légales » telle quelle.
- Rédige en français juridique clair et prudent.

Dispositions vérifiées citables :
{provisions}"""


async def _enrich_with_llm(draft: str, instructions: str, evidence: list[EvidenceChunk], llm: Any) -> str:
    """Rewrite/enrich the draft with the gated LLM; mock -> unchanged."""
    if llm is None or getattr(llm, "provider", "mock") == "mock":
        return draft
    provisions = "\n".join(
        f"[{idx}] {chunk.citation_label()} — {' '.join(chunk.content.split())[:400]}"
        for idx, chunk in enumerate(evidence, 1)
    )
    user = f"Projet de document :\n\n{draft}"
    if instructions.strip():
        user += f"\n\nInstructions du demandeur :\n{instructions.strip()}"
    try:
        enriched = (await llm.complete(_ENRICH_SYSTEM.format(provisions=provisions), user)).strip()
    except Exception:
        logger.warning("drafting LLM enrichment failed; keeping skeleton", exc_info=True)
        return draft
    return enriched or draft


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def generate_draft(
    ctx: AppContext,
    template: dict[str, Any],
    fields: dict[str, str],
    instructions: str,
    llm: Any,
) -> DraftResult:
    """Run the full fill -> ground -> enrich -> verify pipeline."""
    draft = fill_skeleton(template, fields)
    evidence = await _retrieve_grounding(ctx, template)
    warnings: list[str] = []
    citations: list[Citation] = []

    if not evidence:
        warnings.append(_NO_EVIDENCE_WARNING)
        return DraftResult(
            title=template["label"],
            template_id=template["id"],
            draft_markdown=draft,
            citations=[],
            warnings=warnings,
            requires_human_review=True,
        )

    draft += _references_section(evidence)
    draft = await _enrich_with_llm(draft, instructions, evidence, llm)

    # Step 4: strip unverifiable citations, keep only verified ones.
    verified, rejected = extract_citations(draft, evidence)
    for bad in rejected:
        draft = draft.replace(bad.label, "")
    warnings.extend(f"citation rejetée (non vérifiable): {c.label}" for c in rejected)
    citations = verified

    return DraftResult(
        title=template["label"],
        template_id=template["id"],
        draft_markdown=draft.strip(),
        citations=citations,
        warnings=warnings,
        requires_human_review=bool(rejected),
    )
