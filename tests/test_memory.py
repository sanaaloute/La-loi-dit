"""Memory store tests (offline, in-memory cache + hash embeddings)."""

from __future__ import annotations

from backend.core.models import ChatMessage, MemoryRecord


async def test_append_turn_load_buffer_round_trip(ctx):
    store = ctx.memory
    messages = [
        ChatMessage(role="user", content="Bonjour, parlez-moi du préavis."),
        ChatMessage(role="assistant", content="Le préavis est d'un mois pour les employés."),
    ]
    await store.append_turn("session-1", "user-1", messages)
    loaded = await store.load_buffer("session-1")
    contents = [m.content for m in loaded]
    assert any("Bonjour" in c for c in contents)
    assert any("préavis" in c for c in contents)


async def test_remember_recall_finds_semantically_related_record(ctx):
    store = ctx.memory
    await store.remember(
        MemoryRecord(
            user_id="user-1",
            kind="semantic",
            content="Le préavis de licenciement est d'un mois pour les employés mensualisés.",
        )
    )
    records = await store.recall("user-1", "durée du préavis de licenciement")
    assert records
    assert "préavis" in records[0].content


async def test_preferences_round_trip(ctx):
    store = ctx.memory
    before = await store.get_preferences("user-1")
    assert isinstance(before, dict)

    await store.remember(
        MemoryRecord(
            user_id="user-1",
            kind="preference",
            content="langue de réponse préférée: français; réponses concises",
        )
    )
    after = await store.get_preferences("user-1")
    assert isinstance(after, dict)
    # the preference record is at least recallable semantically
    recalled = await store.recall("user-1", "langue préférée français")
    assert any("français" in r.content for r in recalled)
