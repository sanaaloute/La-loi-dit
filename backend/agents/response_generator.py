"""Response Generator Agent.

Composes the final answer strictly from ranked evidence (grounded answer
policy).  Every substantive statement carries a numeric citation.  The answer
text is ALWAYS produced by an LLM — never by a pre-written template: when
every provider fails (see the failover chain in ``backend.core.llm``), the
user gets an honest unavailability message, not fabricated legal content.
Confidence blends citation accuracy with sub-question coverage, capped when
conflicts stay unresolved or the reflection step flags unanswered parts, so a
fully-cited but partial or contested answer never displays "100%".
"""

from __future__ import annotations

import re
from typing import Any, Optional

from backend.agents.citation_verification import extract_citations
from backend.agents.agent import CompletionAgent
from backend.core.config import get_settings
from backend.core.context import AppContext
from backend.core.models import Citation, EvidenceChunk, FinalAnswer
from backend.core.state import GraphState
from backend.ingestion.text_cleaning import repair_extraction_artifacts

# Function words ignored when matching a sub-question against evidence content.
_COVERAGE_STOPWORDS = {
    "quels", "quelles", "quel", "quelle", "sont", "est", "être", "avoir",
    "dans", "pour", "avec", "sans", "sous", "entre", "leur", "leurs", "selon",
    "code", "droits", "droit", "burkina", "faso", "comment", "quoi", "quand",
    "après", "avant", "contre", "chez", "tout", "tous", "toute", "toutes",
    "faire", "fait", "doit", "peut", "plus", "très", "aussi", "ainsi",
}


def _question_coverage(state: GraphState, evidence: list[EvidenceChunk]) -> float:
    """Fraction of planned sub-questions backed by at least one evidence chunk.

    A sub-question counts as covered when half of its DISCRIMINATIVE terms
    (those not already in the main query — "partage", "biens", not "divorce")
    appear in a single chunk. Without sub-questions the query itself is used,
    so the score degrades gracefully for heuristic plans.
    """

    def _terms(text: str) -> list[str]:
        return [
            t for t in re.findall(r"[a-zàâäçéèêëîïôöùûü]{4,}", text.lower())
            if t not in _COVERAGE_STOPWORDS
        ]

    plan = state.get("plan")
    sub_questions = [q for q in (plan.sub_questions if plan else []) if q.strip()]
    if not sub_questions:
        sub_questions = [state.get("query", "")]
    if not evidence:
        return 0.0
    query_terms = set(_terms(state.get("query", "")))
    covered = 0
    for question in sub_questions:
        terms = [t for t in _terms(question) if t not in query_terms] or _terms(question)
        if not terms:
            covered += 1
            continue
        threshold = max(1, len(terms) // 2)
        for chunk in evidence:
            content = chunk.content.lower()
            if sum(1 for t in terms if t in content) >= threshold:
                covered += 1
                break
    return covered / len(sub_questions)


class ResponseGeneratorAgent(CompletionAgent):
    """Drafts the final grounded answer from ranked evidence."""

    name = "response_generator"
    system_prompt = """You are the response generator of an expert legal research assistant for Burkina Faso.

SCOPE
- Corpus: official sources of Burkina Faso (Constitution, codes, lois, décrets,
  Journal Officiel) and OHADA uniform acts, provided to you as numbered excerpts.
- You answer ONLY from these excerpts: no outside knowledge, no invented provisions,
  article numbers or dates.

TASK
ANSWER the user's question directly. Synthesize the numbered evidence excerpts into a
coherent legal explanation — do NOT merely list the excerpts.

RULES
- Cite every substantive statement with [n] referring to the evidence excerpt number
  in the list (e.g. [1], [2]). Do NOT use article numbers, page numbers or any other
  identifiers as citations.
- When you quote an article, recopy it IN FULL, word for word, and NEVER truncate a
  quote with "...". The only changes allowed inside a quote are formatting repairs:
  rejoin words split by PDF extraction and remove spurious line breaks. The words
  themselves must stay exactly those of the source.
  Example: if an excerpt reads « Pour les litiges nés d'un licenciement, le travaill
  eur a le choix », write « Pour les litiges nés d'un licenciement, le travailleur a
  le choix ».
- Write clean, correct, well-formatted French (or the user's language).
- If the evidence is insufficient to answer the question, say so explicitly instead
  of guessing.

FORMAT
1. A direct answer to the question in one or two sentences.
2. Numbered paragraphs or bullet points developing the answer (e.g. the steps,
   conditions or rules the user asked about), each grounded in the evidence and
   cited with [n].
3. A short conclusion when appropriate.

FEW-SHOT EXAMPLES (imitate the form, never the content):

Example 1 — well-grounded answer:
Question: Quel est le préavis en cas de licenciement ?
Réponse: La durée du préavis dépend de la catégorie professionnelle du salarié
et de son ancienneté [1].
1. Durée du préavis — Le Code du travail dispose que « ...recopie intégrale et
   verbatim de l'article, sans aucune troncature... » [1].
2. Exception — En cas de faute lourde, le contrat peut être rompu sans préavis,
   sous réserve de l'appréciation de la juridiction compétente [2].
En résumé : la durée exacte se détermine selon la catégorie du salarié et son
ancienneté [1][2].

Example 2 — honest answer when the evidence does not cover the question:
Question: Le port du casque est-il obligatoire pour les motards ?
Réponse: Les sources officielles indexées ne contiennent pas de disposition
répondant à cette question. Je ne peux donc pas l'affirmer ; veuillez consulter
le Journal Officiel du Burkina Faso ou un professionnel du droit.

Respond in the user's requested language."""

    # Evidence excerpts are capped for context-window safety, but the cap is
    # generous so full articles reach the LLM (and the user) untruncated.
    max_excerpt_chars: int = 4000

    async def run(self, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        """LLM-first answer with one corrective retry for missing citations.

        If the LLM produces prose without any [n] citation, ask it once to
        rewrite with citations before falling back to the deterministic
        template.  This keeps the final answer an actual answer to the
        question rather than a bare list of articles whenever possible.
        """
        user_message = self._build_user_message(state)
        try:
            text = await ctx.llm.complete(
                self.system_prompt,
                user_message,
                temperature=ctx.settings.llm_temperature,
            )
            if (
                text.strip()
                and state.get("ranked_evidence")
                and not re.search(r"\[\d+\]", text)
            ):
                corrective = (
                    f"{user_message}\n\n"
                    "Your previous answer did not cite the evidence with [n] markers. "
                    "Rewrite it so that every substantive statement is cited with [n] "
                    "referring to the numbered evidence excerpts.\n\n"
                    f"Previous answer:\n{text}"
                )
                text = await ctx.llm.complete(
                    self.system_prompt,
                    corrective,
                    temperature=ctx.settings.llm_temperature,
                )
        except Exception as exc:
            return self._fallback(state, f"LLM completion failed: {exc!r}")
        return self._parse_final(text, state, ctx)

    def _build_user_message(self, state: GraphState) -> str:
        evidence = list(state.get("ranked_evidence", []))
        plan = state.get("plan")
        language = state.get("language") or (plan.response_language if plan else "fr")
        evidence_text = self._format_evidence(evidence, state.get("settings"))
        return f"Question: {state['query']}\nLanguage: {language}\n\nPreuves:\n{evidence_text}"

    def _format_evidence(self, evidence: list[EvidenceChunk], settings: Optional[Any] = None) -> str:
        max_evidence = (settings or get_settings()).answer_max_evidence
        lines = []
        for i, chunk in enumerate(evidence[:max_evidence], start=1):
            label = chunk.citation_label()
            # Repair PDF hyphenation artifacts so both the LLM and the user
            # see clean text, even for chunks ingested before the cleaning fix.
            content = repair_extraction_artifacts(chunk.content)
            # If this is a parent expanded from child chunks, include a note about them.
            if chunk.child_chunks:
                children = " | ".join(repair_extraction_artifacts(c.content)[:200] for c in chunk.child_chunks)
                content = f"{content}\n  (preuves détaillées : {children})"
            lines.append(f"[{i}] {label}: {content[:self.max_excerpt_chars]}")
        return "\n\n".join(lines)

    def _parse_final(self, text: str, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        evidence = list(state.get("ranked_evidence", []))
        plan = state.get("plan")
        language = state.get("language") or (plan.response_language if plan else "fr")
        draft = text
        if not evidence:
            draft = self._insufficient_message(language)
        elif not text.strip() or not re.search(r"\[\d+\]", text):
            # LLM-only policy: never substitute a pre-written article list for
            # the model's synthesis. If every provider failed to produce a
            # grounded answer, say so plainly and flag the answer for review.
            draft = self._unavailable_message(language)

        verified, rejected = extract_citations(draft, evidence)
        # Strip unverifiable citations from the final answer.
        if rejected:
            for bad in rejected:
                draft = draft.replace(bad.label, "")
            draft = draft.strip()

        # Build Citation models from the verified list.
        citations: list[Citation] = []
        for c in verified:
            idx_match = re.match(r"\[(\d+)\]", c.label)
            idx = int(idx_match.group(1)) if idx_match else 0
            chunk = evidence[idx - 1] if 1 <= idx <= len(evidence) else None
            citations.append(
                Citation(
                    label=c.label,
                    chunk_id=chunk.chunk_id if chunk else None,
                    document_name=chunk.document_name if chunk else "",
                    article=chunk.article if chunk else None,
                    url=chunk.url if chunk else None,
                    verified=True,
                )
            )

        warnings: list[str] = []
        if not evidence:
            confidence = 0.0
            if language.startswith("en"):
                warnings.append(
                    "No verifiable evidence is available in the indexed official sources."
                )
            else:
                warnings.append("Aucune preuve vérifiable disponible")
        else:
            # Confidence blends citation accuracy with answer completeness:
            # how many planned sub-questions are actually backed by evidence.
            # A fully-cited answer that only covers part of the question must
            # NOT display "100% confidence".
            accuracy = state.get("citation_accuracy", 1.0) or 1.0
            coverage = _question_coverage(state, evidence)
            confidence = round(0.4 * accuracy + 0.6 * coverage, 2)
            if coverage < 0.6:
                if language.startswith("en"):
                    warnings.append(
                        "Potentially incomplete answer: some aspects of the question are not "
                        "covered by the indexed official sources."
                    )
                else:
                    warnings.append(
                        "Réponse potentiellement incomplète : certaines dimensions de la "
                        "question ne sont pas couvertes par les sources indexées."
                    )
            # Trust caps: unresolved contradictions and reflection-flagged gaps
            # bound how confident the answer may claim to be.
            unresolved = [c for c in state.get("conflicts", []) if not c.resolved]
            if unresolved:
                confidence = min(confidence, 0.6)
                if language.startswith("en"):
                    warnings.append(
                        "Conflicting sources could not be resolved; the answer is capped "
                        "at reduced confidence."
                    )
                else:
                    warnings.append(
                        "Des sources se contredisent sans que le conflit ait pu être "
                        "résolu ; la confiance est plafonnée."
                    )
            reflection = state.get("reflection")
            if reflection is not None and not reflection.answered_all_questions:
                confidence = min(confidence, 0.75)
                if language.startswith("en"):
                    warnings.append(
                        "The self-review indicates that not every part of the question "
                        "was answered."
                    )
                else:
                    warnings.append(
                        "L'auto-évaluation indique que la réponse ne couvre pas toutes "
                        "les parties de la question."
                    )

        answer = FinalAnswer(
            answer=draft,
            citations=citations,
            evidence=evidence,
            confidence=confidence,
            language=language,
            warnings=warnings,
            conflicts=state.get("conflicts", []),
        )
        return {
            "final_answer": answer,
            "draft_answer": draft,
            "trace": [
                *state.get("trace", []),
                f"response_generator: answer drafted ({len(draft)} chars, {len(citations)} citations)",
            ],
        }

    @staticmethod
    def _insufficient_message(language: str) -> str:
        """Honest status message when no verifiable evidence was retrieved."""
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

    @staticmethod
    def _unavailable_message(language: str) -> str:
        """Honest status message when every LLM provider failed to answer."""
        if language.startswith("en"):
            return (
                "The language models are temporarily unable to synthesize an answer from "
                "the retrieved official sources. Please try again in a moment; the relevant "
                "sources remain attached below for reference."
            )
        return (
            "Les modèles de langage sont momentanément incapables de synthétiser une réponse "
            "à partir des sources officielles récupérées. Veuillez réessayer dans un instant ; "
            "les sources pertinentes restent jointes ci-dessous pour référence."
        )


    def _fallback(self, state: GraphState, reason: str) -> dict[str, Any]:
        """Always emit a valid FinalAnswer, even when every LLM call fails."""
        return self._parse_final("", state, None) | {
            "errors": [*state.get("errors", []), f"{self.name}: {reason}"],
            "trace": [
                *state.get("trace", []),
                f"{self.name}: fallback ({reason})",
            ],
        }


response_generator_node = ResponseGeneratorAgent().run

