"""Freshness loop: event store + monitor wiring (fully offline)."""

from __future__ import annotations

from types import SimpleNamespace

from backend.ingestion.freshness import (
    EVENTS_CAP,
    ChangeEvent,
    FreshnessMonitor,
    SourceSpec,
    append_event,
    read_events,
)


def _event(name: str, n: int = 0) -> ChangeEvent:
    return ChangeEvent(source_name=name, url=f"https://example.com/{n}", kind="rss")


def test_event_store_roundtrip_newest_first(tmp_path):
    assert read_events(tmp_path) == []  # missing file -> empty
    append_event(tmp_path, _event("A", 1))
    append_event(tmp_path, _event("B", 2))
    events = read_events(tmp_path)
    assert [e.source_name for e in events] == ["B", "A"]
    assert read_events(tmp_path, limit=1)[0].source_name == "B"


def test_event_store_is_capped(tmp_path):
    for i in range(EVENTS_CAP + 25):
        append_event(tmp_path, _event("X", i))
    events = read_events(tmp_path, limit=EVENTS_CAP + 100)
    assert len(events) == EVENTS_CAP
    assert events[0].url.endswith(str(EVENTS_CAP + 24))  # newest survived


def test_event_store_dedupes_racing_workers(tmp_path):
    """N uvicorn workers detect the same change at once -> one stored event."""
    for _ in range(3):
        append_event(tmp_path, _event("OHADA", 1))
    assert len(read_events(tmp_path)) == 1
    # A genuinely new change (different detail) still lands.
    append_event(tmp_path, ChangeEvent(source_name="OHADA", url="https://example.com/1", kind="rss", detail="new issue"))
    assert len(read_events(tmp_path)) == 2


async def test_monitor_notifies_and_dedupes(tmp_path):
    """A changed fingerprint fires on_change once; a stable one never does."""
    ctx = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))
    received: list[ChangeEvent] = []
    monitor = FreshnessMonitor(ctx, on_change=received.append)

    calls = {"n": 0}

    async def fake_check(source):
        calls["n"] += 1
        return f"fp:{(calls['n'] - 1) // 2}", "changed"  # same fp across two runs of pair

    monitor._check_one = fake_check  # type: ignore[method-assign]
    registry = [SourceSpec(name="JO", url="https://jo.example.com", kind="rss")]

    first = await monitor.check_sources(registry)
    assert len(first) == 1 and received and received[0].source_name == "JO"

    same = await monitor.check_sources(registry)  # same fingerprint -> no event
    assert same == [] and len(received) == 1

    second = await monitor.check_sources(registry)  # new fingerprint -> event
    assert len(second) == 1 and len(received) == 2
