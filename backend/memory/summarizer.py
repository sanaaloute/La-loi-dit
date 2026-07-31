"""Buffer compaction: when a session's short-term buffer grows past
``max_turns``, the oldest turns are compressed into a single ``summary``
MemoryRecord so long conversations stay within the context window.

Uses the LLM when one is provided and its provider is not ``mock``;
otherwise falls back to a deterministic extractive summary (first sentence
of each old turn), keeping the whole path offline-capable.
"""

from __future__ import annotations

import re
from typing import Optional

from backend.core.config import get_settings
from backend.core.models import MemoryRecord, plain_message_content

_SUMMARY_SYSTEM = (
    "You compress conversation history into a concise factual summary. "
    "Keep user intent, decisions, names, dates and legal references. "
    "Answer in the conversation's language, at most 12 lines."
)


def _settings():
    return get_settings()


def _first_sentence(text: str, max_len: Optional[int] = None) -> str:
    """Extractive helper: first sentence of a message, truncated."""
    max_len = max_len if max_len is not None else _settings().memory_summary_max_len
    text = " ".join(text.split())
    if not text:
        return ""
    m = re.search(r"[.!?…](?:\s|$)", text)
    sentence = text[: m.end()].strip() if m else text
    return sentence[:max_len]


def _extractive_summary(turns) -> str:
    lines = [
        f"- [{t.role}] {_first_sentence(plain_message_content(t.content))}"
        for t in turns
        if t.content.strip()
    ]
    return "Résumé des échanges précédents / Summary of earlier turns:\n" + "\n".join(lines)


async def maybe_summarize(
    store,
    session_id: str,
    llm=None,
    max_turns: Optional[int] = None,
    user_id: str = "",
) -> Optional[MemoryRecord]:
    """Summarize the oldest buffer turns when the buffer exceeds ``max_turns``.

    Returns the created summary record, or None when no summarization was
    needed. Never raises — failures simply skip summarization.
    """
    max_turns = max_turns if max_turns is not None else _settings().memory_summary_max_turns
    try:
        buffer = await store.load_buffer(session_id, limit=max_turns * 4)
    except Exception:
        return None
    if len(buffer) <= max_turns:
        return None

    old_turns = buffer[:-max_turns]
    transcript = "\n".join(f"[{t.role}] {plain_message_content(t.content)}" for t in old_turns)

    summary_text = ""
    if llm is not None and getattr(llm, "provider", "mock") != "mock":
        try:
            summary_text = (await llm.complete(_SUMMARY_SYSTEM, transcript)).strip()
        except Exception:
            summary_text = ""
    if not summary_text:
        summary_text = _extractive_summary(old_turns)

    record = MemoryRecord(
        user_id=user_id or "unknown",
        session_id=session_id,
        kind="summary",
        content=summary_text,
        importance=0.6,
        metadata={"compressed_turns": len(old_turns), "session_id": session_id},
    )
    try:
        await store.remember(record)
    except Exception:
        pass
    return record
