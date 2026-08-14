"""Context & memory effectiveness: per-sub-question evidence selection,
prompt injection of conversation/memories, and the summarizer hook.

Offline: no LLM, no DB — fake stores and SimpleNamespace contexts.
"""

from __future__ import annotations

from types import SimpleNamespace

from backend.agents.context_agent import format_memory_sections
from backend.agents.evidence_ranking import EvidenceRankingAgent
from backend.agents.reasoning_agent import ReasoningAgent
from backend.agents.response_generator import ResponseGeneratorAgent
from backend.core.config import Settings
from backend.core.models import (
    AuthorityLevel,
    ChatMessage,
    EvidenceChunk,
    RetrievalPlan,
)
from backend.memory.summarizer import maybe_summarize


def _ctx(settings: Settings):
    return SimpleNamespace(settings=settings)


def _chunk(chunk_id: str, article: str, score: float, **kwargs) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        document_id="doc-1",
        document_name="Code du travail",
        article=article,
        content=f"contenu de l'article {article}",
        retrieval_score=score,
        rerank_score=score,
        authority=AuthorityLevel.LAW,
        confidence=score,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Per-sub-question evidence selection
# ---------------------------------------------------------------------------


def _ranking_state() -> dict:
    """Two sub-questions with 7 strong chunks each, plus one weak chunk."""
    chunks = []
    membership = []
    for group, prefix in (("sous-question A", "a"), ("sous-question B", "b")):
        for i in range(7):
            # Descending scores so the expected survivors are unambiguous.
            score = 0.9 - i * 0.05
            cid = f"{prefix}-{i}"
            chunks.append(_chunk(cid, f"{prefix}-art-{i}", score))
            membership.append({"chunk_id": cid, "query": group})
    weak = _chunk("weak-1", "weak-art", 0.05)
    chunks.append(weak)
    membership.append({"chunk_id": "weak-1", "query": "sous-question A"})
    return {
        "evidence": chunks,
        "branch_membership": membership,
        "plan": RetrievalPlan(sub_questions=["sous-question A", "sous-question B"]),
        "trace": [],
    }


async def test_ranking_caps_evidence_per_sub_question():
    settings = Settings(min_evidence_score=0.5, answer_max_evidence_per_subquestion=5)
    result = await EvidenceRankingAgent().run(_ranking_state(), _ctx(settings))
    ranked = result["ranked_evidence"]

    a = [c.chunk_id for c in ranked if c.chunk_id.startswith("a-")]
    b = [c.chunk_id for c in ranked if c.chunk_id.startswith("b-")]
    # 5 best per sub-question, in score order — no global starvation.
    assert a == [f"a-{i}" for i in range(5)]
    assert b == [f"b-{i}" for i in range(5)]
    # The weak chunk (below min_evidence_score) is dropped.
    assert all(c.chunk_id != "weak-1" for c in ranked)
    assert "max 5 per sub-question" in result["trace"][-1]


async def test_ranking_counts_shared_chunk_once():
    """A chunk returned by two branches is selected only once."""
    shared = _chunk("shared", "art-commun", 0.9)
    state = {
        "evidence": [shared, _chunk("a-only", "art-a", 0.8)],
        "branch_membership": [
            {"chunk_id": "shared", "query": "q1"},
            {"chunk_id": "shared", "query": "q2"},
            {"chunk_id": "a-only", "query": "q2"},
        ],
        "plan": RetrievalPlan(sub_questions=["q1", "q2"]),
        "trace": [],
    }
    result = await EvidenceRankingAgent().run(state, _ctx(Settings()))
    ids = [c.chunk_id for c in result["ranked_evidence"]]
    assert ids.count("shared") == 1
    assert "a-only" in ids


async def test_ranking_expanded_parent_inherits_child_group():
    """A parent expanded from a child counts toward the child's sub-question."""
    child = EvidenceChunk(
        chunk_id="child-1",
        document_id="doc-1",
        document_name="Code du travail",
        article="10",
        content="passage enfant",
        retrieval_score=0.9,
        rerank_score=0.9,
        authority=AuthorityLevel.LAW,
        confidence=0.9,
    )
    parent = EvidenceChunk(
        chunk_id="parent-1",
        document_id="doc-1",
        document_name="Code du travail",
        article="10",
        content="article complet",
        retrieval_score=0.9,
        rerank_score=0.9,
        authority=AuthorityLevel.LAW,
        confidence=0.9,
        child_chunks=[child],
        metadata={"expansion": "parent"},
    )
    state = {
        "evidence": [parent],
        # Only the child carries membership (branches ran pre-expansion).
        "branch_membership": [{"chunk_id": "child-1", "query": "q1"}],
        "plan": RetrievalPlan(sub_questions=["q1", "q2"]),
        "trace": [],
    }
    result = await EvidenceRankingAgent().run(state, _ctx(Settings()))
    assert [c.chunk_id for c in result["ranked_evidence"]] == ["parent-1"]


async def test_ranking_without_membership_keeps_trailing_group():
    """No membership info (direct calls, legacy state): chunks still ranked."""
    state = {
        "evidence": [_chunk(f"c-{i}", f"art-{i}", 0.9 - i * 0.05) for i in range(3)],
        "trace": [],
        "plan": None,
    }
    result = await EvidenceRankingAgent().run(state, _ctx(Settings()))
    assert len(result["ranked_evidence"]) == 3


# ---------------------------------------------------------------------------
# Prompt injection of conversation + memories
# ---------------------------------------------------------------------------


def _memory_state() -> dict:
    return {
        "query": "Et pour une SARL ?",
        "conversation_context": [
            {"role": "user", "content": "Quelle forme de société choisir ?"},
            {"role": "assistant", "content": "La SARL est la plus courante."},
        ],
        "memories": [
            {"kind": "summary", "content": "L'utilisateur crée une entreprise au Burkina.", "importance": 0.6},
        ],
        "ranked_evidence": [_chunk("e-1", "art-1", 0.9)],
        "plan": None,
        "trace": [],
    }


def test_format_memory_sections_renders_both_tiers():
    text = format_memory_sections(_memory_state(), max_entry_chars=2000)
    assert "Conversation précédente:" in text
    assert "[utilisateur] Quelle forme de société choisir ?" in text
    assert "[assistant] La SARL est la plus courante." in text
    assert "Souvenirs pertinents" in text
    assert "crée une entreprise" in text


def test_format_memory_sections_empty_when_nothing_loaded():
    assert format_memory_sections({"query": "q"}, max_entry_chars=2000) == ""


def test_reasoning_prompt_includes_conversation_and_memories():
    msg = ReasoningAgent()._build_user_message(_memory_state(), _ctx(Settings()))
    assert "Conversation précédente:" in msg
    assert "Souvenirs pertinents" in msg
    assert "Quelle forme de société choisir ?" in msg


def test_response_prompt_includes_conversation_and_memories():
    msg = ResponseGeneratorAgent()._build_user_message(_memory_state(), _ctx(Settings()))
    assert "Conversation précédente:" in msg
    assert "Souvenirs pertinents" in msg
    assert "crée une entreprise" in msg


async def test_direct_route_loads_conversation_for_continuity():
    """The direct route bypasses context_agent, so it loads the buffer itself."""

    class _FakeMemory:
        async def load_buffer(self, session_id, limit=20):
            return [
                ChatMessage(role="user", content="bonjour"),
                ChatMessage(role="assistant", content="Bonjour ! Que puis-je pour vous ?"),
            ]

    class _FakeLLM:
        def __init__(self):
            self.user_message = ""

        async def complete(self, system, user_message, temperature=None):
            self.user_message = user_message
            return "réponse directe"

    llm = _FakeLLM()
    ctx = SimpleNamespace(settings=Settings(), memory=_FakeMemory(), llm=llm)
    state = {"query": "et ensuite ?", "session_id": "s-1", "language": "fr", "trace": [], "errors": []}
    result = await ResponseGeneratorAgent()._run_direct(state, ctx)
    assert "Conversation précédente:" in llm.user_message
    assert "bonjour" in llm.user_message
    assert result["final_answer"].answer == "réponse directe"


# ---------------------------------------------------------------------------
# Summarizer hook (long-term memory writes)
# ---------------------------------------------------------------------------


class _FakeStore:
    def __init__(self, messages):
        self._messages = messages
        self.remembered = []

    async def load_buffer(self, session_id, limit=20):
        return self._messages[-limit:]

    async def remember(self, record):
        # Upsert by id, like MemoryStore.remember.
        self.remembered[:] = [r for r in self.remembered if r.id != record.id]
        self.remembered.append(record)


def _turns(n: int) -> list[ChatMessage]:
    return [
        ChatMessage(role="user" if i % 2 == 0 else "assistant", content=f"message {i}")
        for i in range(n)
    ]


async def test_summarizer_skips_short_buffers():
    store = _FakeStore(_turns(4))
    assert await maybe_summarize(store, "s-1", max_turns=4, user_id="u") is None
    assert store.remembered == []


async def test_summarizer_writes_one_record_per_session():
    store = _FakeStore(_turns(5))  # overflow = 1 -> trigger
    record = await maybe_summarize(store, "s-1", max_turns=4, user_id="u")
    assert record is not None
    assert record.id == "summary:s-1"
    assert record.kind == "summary"
    assert record.user_id == "u"


async def test_summarizer_does_not_fire_every_turn():
    """Overflow beyond the first crossing skips until the next multiple."""
    store = _FakeStore(_turns(7))  # overflow = 3, not a fresh multiple of 4
    assert await maybe_summarize(store, "s-1", max_turns=4, user_id="u") is None
    store = _FakeStore(_turns(9))  # overflow = 5 -> fresh multiple crossing
    assert await maybe_summarize(store, "s-1", max_turns=4, user_id="u") is not None


async def test_summarizer_refresh_upserts_same_record():
    store = _FakeStore(_turns(5))
    first = await maybe_summarize(store, "s-1", max_turns=4, user_id="u")
    store._messages = _turns(9)
    second = await maybe_summarize(store, "s-1", max_turns=4, user_id="u")
    assert first is not None and second is not None
    assert len(store.remembered) == 1  # upserted, not duplicated
    assert store.remembered[0].id == "summary:s-1"
