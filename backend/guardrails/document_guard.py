"""Retrieved-document injection screening (spec §42).

The input guardrail screens the *user query*; this module screens the
*retrieved evidence* before it is formatted into an LLM prompt. A chunk is
never trusted as instructions: sentences matching
``DOCUMENT_INJECTION_PATTERNS`` are neutralized (dropped), and a chunk with
nothing usable left is dropped entirely. Screening never fails closed on the
whole answer — when every chunk is dropped, the pipeline falls back to the
existing insufficient-evidence path.
"""

from __future__ import annotations

import re

from backend.core.models import EvidenceChunk
from backend.guardrails.policies import scan_document_text

# Sentence-ish split: after terminal punctuation or on line breaks. Splitting
# finer than real sentences (e.g. after "art.") is harmless — only matched
# fragments are dropped.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")


def _sanitize_text(text: str) -> tuple[str, list[str]]:
    """Drop fragments containing injection patterns.

    Returns ``(clean_text, matched_pattern_names)``; ``clean_text`` keeps the
    surviving fragments joined by single spaces.
    """
    kept: list[str] = []
    matched: list[str] = []
    for fragment in _SENTENCE_SPLIT.split(text or ""):
        names = scan_document_text(fragment)
        if names:
            matched.extend(n for n in names if n not in matched)
        elif fragment.strip():
            kept.append(fragment.strip())
    return " ".join(kept), matched


def check_evidence(chunks: list[EvidenceChunk]) -> tuple[list[EvidenceChunk], list[str]]:
    """Screen retrieved chunks for embedded instructions. Never raises.

    Returns ``(sanitized_chunks, flagged_chunk_ids)``: flagged chunks had at
    least one fragment neutralized; chunks left with no usable text are
    dropped from the returned list (their ids stay in ``flagged_chunk_ids``).
    Unflagged chunks are returned untouched (same objects).
    """
    sanitized: list[EvidenceChunk] = []
    flagged: list[str] = []
    for chunk in chunks:
        clean, matched = _sanitize_text(chunk.content)
        if not matched:
            sanitized.append(chunk)
            continue
        flagged.append(chunk.chunk_id)
        if not clean.strip():
            continue  # nothing usable remains: drop the chunk
        children = []
        for child in chunk.child_chunks:
            child_clean, child_matched = _sanitize_text(child.content)
            if child_matched and not child_clean.strip():
                continue
            children.append(
                child.model_copy(update={"content": child_clean}) if child_matched else child
            )
        sanitized.append(chunk.model_copy(update={"content": clean, "child_chunks": children}))
    return sanitized, flagged
