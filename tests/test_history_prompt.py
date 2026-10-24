"""Prompt-structure regressions for the in-session contamination bug.

Production symptom: an answer about divorce was repeated verbatim at the top
of answers to subsequent unrelated questions. Causes fixed here: conversation
history led the prompt (before Question/evidence), past assistant answers were
injected at full length (2000 chars), and nothing forbade repeating them.
"""

import asyncio
from types import SimpleNamespace

from backend.agents.context_agent import (
    LoadConversationBufferArgs,
    format_memory_sections,
    load_conversation_buffer,
)
from backend.agents.reasoning_agent import ReasoningAgent
from backend.agents.response_generator import ResponseGeneratorAgent
from backend.core.config import Settings
from backend.core.models import EvidenceChunk, RetrievalPlan
from backend.core.prompts import get_prompt


def _settings(tmp_path):
    return Settings(llm_provider="mock", data_dir=tmp_path, guardrails_enabled=False)


def _state_with_history(chunks):
    return {
        "query": "délai de recours licenciement",
        "original_query": "Quel est le délai de recours contre un licenciement ?",
        "language": "fr",
        "route": "retrieval",
        "plan": RetrievalPlan(sub_questions=["délai de recours licenciement"]),
        "ranked_evidence": chunks,
        "conversation_context": [
            {"role": "user", "content": "En quoi consiste la procédure de divorce ?"},
            {"role": "assistant", "content": "La procédure de divorce n'est pas couverte…"},
        ],
        "memories": [],
        "conflicts": [],
        "errors": [],
        "trace": [],
    }


def test_question_and_evidence_lead_the_prompt_history_comes_last(tmp_path):
    ctx = SimpleNamespace(settings=_settings(tmp_path))
    state = _state_with_history([EvidenceChunk(chunk_id="c1", content="article 358 tribunal du travail")])

    message = ResponseGeneratorAgent()._build_user_message(state, ctx)

    assert message.index("Question:") < message.index("Preuves:") < message.index("CONTEXTE DE CONVERSATION")
    assert "NE PAS répéter" in message


def test_reasoning_prompt_also_puts_history_last(tmp_path):
    ctx = SimpleNamespace(settings=_settings(tmp_path))
    state = _state_with_history([EvidenceChunk(chunk_id="c1", content="article 358 tribunal du travail")])

    message = ReasoningAgent()._build_user_message(state, ctx)

    assert message.index("Question:") < message.index("Preuves:") < message.index("CONTEXTE DE CONVERSATION")


def test_no_history_means_no_context_block(tmp_path):
    ctx = SimpleNamespace(settings=_settings(tmp_path))
    state = _state_with_history([EvidenceChunk(chunk_id="c1", content="texte")])
    state["conversation_context"] = []

    message = ResponseGeneratorAgent()._build_user_message(state, ctx)

    assert "CONTEXTE DE CONVERSATION" not in message
    assert message.startswith("Question:")


def test_system_prompt_forbids_repeating_previous_answers():
    system = get_prompt("RESPONSE_SYSTEM")
    assert "ne répète ni ne poursuis" in system
    assert "CONTEXTE DE" in system or "CONTEXTE DE\n  CONVERSATION" in system


def test_assistant_history_is_truncated_tighter_than_user_turns(tmp_path):
    settings = _settings(tmp_path)

    class FakeMemory:
        async def load_buffer(self, session_id, limit):
            return [
                SimpleNamespace(role="user", content="u" * 3000),
                SimpleNamespace(role="assistant", content="a" * 3000),
            ]

    ctx = SimpleNamespace(settings=settings, memory=FakeMemory())
    args = LoadConversationBufferArgs(session_id="s1", limit=10)
    buffer = asyncio.run(load_conversation_buffer(ctx, {}, args))

    assert len(buffer[0]["content"]) == settings.context_message_max_chars
    assert len(buffer[1]["content"]) == settings.context_assistant_max_chars
    assert settings.context_assistant_max_chars < settings.context_message_max_chars


def test_format_memory_sections_renders_both_sections():
    state = {
        "conversation_context": [{"role": "user", "content": "bonjour"}],
        "memories": [{"content": "préfère les réponses courtes"}],
    }
    sections = format_memory_sections(state, max_entry_chars=100)
    assert "Conversation précédente:" in sections
    assert "Souvenirs pertinents" in sections
