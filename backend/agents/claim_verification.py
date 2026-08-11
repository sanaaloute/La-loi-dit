"""Claim Verification Agent (spec §20, §21, §44).

Post-synthesis, pre-citation-stripping pass that checks every substantive
statement of the drafted answer against the retrieved evidence — without any
LLM call:

1. :func:`extract_claims` splits the answer into substantive legal statements
   (headings, source-list sections and very short sentences are skipped).
2. :func:`classify_support` grades one claim against one evidence chunk using
   the discriminative-term matching of the coverage auditor plus a
   conservative number/date conflict check.
3. :func:`verify_claims` resolves each claim's ``[n]`` markers to the cited
   chunks (claims without markers are matched against all evidence by term
   overlap) and aggregates per-source levels into one overall level.

The node QUALIFIES and FLAGS — it never rewrites or removes answer text
(citation_verification, which runs right after, already strips unverifiable
``[n]`` markers).  Unsupported claims raise bilingual warnings, contradictory
claims additionally set ``requires_human_review`` (a contradicted legal
statement is high-impact), and ``confidence_breakdown.legal_support_confidence``
is recomputed as the fraction of supported claims, dampened by the share of
contradicted ones (see the node docstring).  The aggregate
``FinalAnswer.confidence`` semantics stay untouched.

Extension point: ``classify_support`` is a pure function, so an LLM-backed
refinement (entailment scoring per claim/chunk pair) can replace or gate the
heuristic without touching extraction, aggregation or the node wiring.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from backend.agents.agent import Agent
from backend.agents.coverage_auditor import discriminative_terms
from backend.core.config import get_settings
from backend.core.context import AppContext
from backend.core.models import (
    Claim,
    ClaimSource,
    EvidenceChunk,
    SupportLevel,
)
from backend.core.state import GraphState

_CITATION_RE = re.compile(r"\[(\d+)\]")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+")
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")

# FR + EN legal vocabulary marking a sentence as a substantive statement.
_LEGAL_KEYWORDS_RE = re.compile(
    r"\b(articles?|lois?|droits?|obligations?|prévoit|prévue?|dispose|"
    r"peut|peuvent|doit|doivent|interdits?|interdite|interdiction|"
    r"autorise|autorisée?|exige|stipule|"
    r"laws?|rights?|obligations?|provides?|may|must|shall|"
    r"prohibits?|prohibited|forbidden|allows?|requires?)\b",
    re.IGNORECASE,
)

# Term-coverage bars (fraction of the claim's discriminative terms found in
# the chunk content) live in Settings: ``claim_direct_term_coverage`` (DIRECT
# requires substantial coverage AND matching numbers),
# ``claim_indirect_term_coverage`` (partial topical overlap) and
# ``claim_contradiction_term_coverage`` (sits between them so only a chunk
# clearly on the same legal topic can contradict).


def extract_claims(answer_text: str, settings: Optional[Any] = None) -> list[str]:
    """Split an answer into substantive legal statements (deterministic).

    Heuristics:
    - Heading lines (``# ...``) are skipped; a heading mentioning "source" or
      "référence" opens a reference-list section whose lines are skipped until
      the next heading (those lines name documents, they assert nothing).
    - Remaining lines (list markers stripped) are split on sentence-ending
      punctuation.
    - A sentence is a claim when it is at least
      ``settings.claim_min_chars`` long AND carries an ``[n]`` citation marker
      or a legal keyword (article, loi, droit, obligation, prévoit, peut,
      doit, interdit and EN variants).
      Pure citation/source lines are short and keyword-free, so they drop out.
    """
    settings = settings or get_settings()
    claims: list[str] = []
    in_sources_section = False
    for line in (answer_text or "").splitlines():
        if _HEADING_RE.match(line):
            heading = _HEADING_RE.sub("", line).strip().lower()
            in_sources_section = "source" in heading or "référence" in heading
            continue
        if in_sources_section:
            continue
        stripped = _LIST_MARKER_RE.sub("", line).strip()
        if not stripped:
            continue
        for sentence in _SENTENCE_SPLIT_RE.split(stripped):
            sentence = sentence.strip()
            if len(sentence) < settings.claim_min_chars:
                continue
            if _CITATION_RE.search(sentence) or _LEGAL_KEYWORDS_RE.search(sentence):
                claims.append(sentence)
    return claims


def _numbers(text: str) -> list[str]:
    """Numeric literals (amounts, durations, dates) with [n] markers removed."""
    return _NUMBER_RE.findall(_CITATION_RE.sub(" ", text))


def _contains_number(content: str, number: str) -> bool:
    """True when ``number`` appears as a standalone numeric token in content."""
    return re.search(rf"(?<!\d){re.escape(number)}(?!\d)", content) is not None


def classify_support(
    claim_text: str,
    chunk: EvidenceChunk,
    settings: Optional[Any] = None,
) -> SupportLevel:
    """Grade how well ``chunk`` supports ``claim_text`` (pure heuristic).

    - CONTRADICTORY: the chunk is clearly on the same legal topic (term
      coverage >= ``settings.claim_contradiction_term_coverage``) AND the
      claim asserts numbers/dates of which NONE appear in the chunk.
      Conservative by design: a single shared number, or weaker topical
      overlap, never counts as a contradiction.
    - DIRECT: discriminative-term coverage >=
      ``settings.claim_direct_term_coverage`` AND every number in the claim
      also appears in the chunk (vacuously true for number-less claims).
    - INDIRECT: coverage >= ``settings.claim_indirect_term_coverage`` —
      topical overlap with partially covered discriminative terms.
    - INSUFFICIENT: below the indirect bar.
    """
    settings = settings or get_settings()
    terms = set(discriminative_terms(_CITATION_RE.sub(" ", claim_text), settings))
    if not terms:
        return SupportLevel.INSUFFICIENT
    content = chunk.content.lower()
    coverage = sum(1 for t in terms if t in content) / len(terms)
    claim_numbers = _numbers(claim_text)
    matched = [n for n in claim_numbers if _contains_number(content, n)]
    if claim_numbers and not matched and coverage >= settings.claim_contradiction_term_coverage:
        return SupportLevel.CONTRADICTORY
    if coverage >= settings.claim_direct_term_coverage and len(matched) == len(claim_numbers):
        return SupportLevel.DIRECT
    if coverage >= settings.claim_indirect_term_coverage:
        return SupportLevel.INDIRECT
    return SupportLevel.INSUFFICIENT


def _overall_level(sources: list[ClaimSource]) -> SupportLevel:
    """Aggregate per-source levels: a contradiction dominates, else best wins."""
    levels = {s.support_level for s in sources}
    if SupportLevel.CONTRADICTORY in levels:
        return SupportLevel.CONTRADICTORY
    if SupportLevel.DIRECT in levels:
        return SupportLevel.DIRECT
    if SupportLevel.INDIRECT in levels:
        return SupportLevel.INDIRECT
    return SupportLevel.INSUFFICIENT


def verify_claims(
    answer_text: str,
    evidence: list[EvidenceChunk],
    settings: Optional[Any] = None,
) -> list[Claim]:
    """Build a :class:`Claim` per extracted statement of the answer.

    A claim's ``[n]`` markers designate its primary source candidates (markers
    resolving outside the evidence list are ignored — citation_verification
    rejects them separately).  A claim WITHOUT markers is matched against all
    evidence by term overlap and keeps only supporting (DIRECT/INDIRECT)
    chunks as sources; with no marker and no supporting chunk it stays
    INSUFFICIENT.  CONTRADICTORY is only ever recorded for marker-cited
    sources: a number mismatch against a chunk the answer never cited is
    ambiguous (the chunk may simply state a different provision), so the
    conservative reading is "not supported", never "contradicted".
    """
    settings = settings or get_settings()
    claims: list[Claim] = []
    for index, text in enumerate(extract_claims(answer_text, settings), start=1):
        marker_idxs = [int(m.group(1)) for m in _CITATION_RE.finditer(text)]
        if marker_idxs:
            candidates = [
                evidence[n - 1] for n in dict.fromkeys(marker_idxs) if 1 <= n <= len(evidence)
            ]
        else:
            candidates = list(evidence)
        sources: list[ClaimSource] = []
        for chunk in candidates:
            level = classify_support(text, chunk, settings)
            if not marker_idxs and level not in (SupportLevel.DIRECT, SupportLevel.INDIRECT):
                continue  # overlap-matched claims only record supporting chunks
            sources.append(
                ClaimSource(
                    chunk_id=chunk.chunk_id,
                    document_name=chunk.document_name,
                    article=chunk.article,
                    support_level=level,
                )
            )
        claims.append(
            Claim(
                claim_id=f"claim-{index:02d}",
                text=text,
                support_level=_overall_level(sources),
                sources=sources,
            )
        )
    return claims


class ClaimVerificationAgent(Agent):
    """Grades every substantive statement of the draft against the evidence.

    Runs between response_generator and citation_verification: claims are
    built on the draft WITH its markers (they identify the intended sources);
    if citation_verification later strips a marker, the claim simply keeps its
    recorded support — the qualification, not the marker, is the verdict.
    """

    name = "claim_verification"
    system_prompt = (
        "You are the claim verification agent. Split the drafted answer into "
        "substantive legal statements and grade each against the retrieved "
        "evidence (direct / indirect / insufficient / contradictory). Flag "
        "unsupported or contradicted statements; never invent support."
    )

    async def run(self, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        final = state.get("final_answer")
        evidence = list(state.get("ranked_evidence", []))
        text = (final.answer if final is not None else state.get("draft_answer", "")) or ""

        claims = (
            verify_claims(text, evidence, settings=ctx.settings if ctx is not None else None)
            if text.strip() and evidence
            else []
        )
        # No evidence means the answer took the insufficient-evidence path,
        # which already declares that nothing could be verified — extracting
        # "claims" from that message would only produce noise warnings.
        direct = sum(1 for c in claims if c.support_level is SupportLevel.DIRECT)
        indirect = sum(1 for c in claims if c.support_level is SupportLevel.INDIRECT)
        insufficient = sum(1 for c in claims if c.support_level is SupportLevel.INSUFFICIENT)
        contradictory = sum(1 for c in claims if c.support_level is SupportLevel.CONTRADICTORY)

        warnings: list[str] = []
        if final is not None:
            final.claims = claims
            language = final.language or state.get("language", "") or "fr"
            english = language.startswith("en")
            if insufficient:
                warnings.append(
                    f"{insufficient} statement(s) could not be verified against the available sources."
                    if english
                    else f"Certaines affirmations n'ont pas pu être vérifiées dans les sources ({insufficient})."
                )
            if contradictory:
                # A contradicted legal statement is high-impact: always
                # escalate for human review.
                final.requires_human_review = True
                warnings.append(
                    f"{contradictory} statement(s) contradict the available sources; human review required."
                    if english
                    else f"Certaines affirmations contredisent les sources ({contradictory}) ; révision humaine requise."
                )
            final.warnings.extend(warnings)
            if claims and final.confidence_breakdown is not None:
                # Legal-support dimension: fraction of claims grounded in the
                # evidence (DIRECT or INDIRECT), dampened by the share of
                # contradicted claims so a contradiction always pushes the
                # score toward 0: support * (1 - contradicted/total).
                supported = (direct + indirect) / len(claims)
                dampening = 1 - contradictory / len(claims)
                final.confidence_breakdown.legal_support_confidence = round(
                    supported * dampening, 2
                )

        return {
            "claims": claims,
            **({"final_answer": final} if final is not None else {}),
            "trace": [
                *state.get("trace", []),
                f"claim_verification: {len(claims)} claims "
                f"({direct} direct, {indirect} indirect, "
                f"{insufficient} insufficient, {contradictory} contradictory)",
            ],
        }


claim_verification_node = ClaimVerificationAgent().run
