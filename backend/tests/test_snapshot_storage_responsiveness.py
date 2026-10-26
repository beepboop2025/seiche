"""HTTP reads stay responsive while late snapshot storage waits on disk."""

import asyncio
import threading

import pytest
from fastapi import Response

from seiche import ai, api, assemble, hetzner_runtime, standby


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["pit_write", "navigator_history"])
async def test_health_serves_previous_board_during_snapshot_storage(
    monkeypatch, fake_snap, operation,
):
    entered = threading.Event()
    release = threading.Event()
    events = []

    def blocked_storage(*_args):
        events.append("storage_entered")
        entered.set()
        assert release.wait(3), "snapshot storage blocked the serving event loop"
        events.append("storage_finished")
        return []

    async def gather():
        return {}, []

    async def navigator(*_args):
        return {"ok": True}

    async def publish(*_args):
        events.append("published")

    monkeypatch.setattr(hetzner_runtime, "enabled", lambda: False)
    monkeypatch.setattr(standby, "enabled", lambda: False)
    monkeypatch.setattr(assemble, "_lock", asyncio.Lock())
    monkeypatch.setattr(assemble, "_build_generation", 0)
    monkeypatch.setattr(assemble, "_gather_sources", gather)
    monkeypatch.setattr(assemble, "_rights_eligible_sources", lambda source: source)
    spread = assemble.pd.Series(
        [1.0], index=assemble.pd.DatetimeIndex(["2026-10-26"])
    ) if operation == "navigator_history" else assemble.pd.Series(dtype=float)
    monkeypatch.setattr(assemble, "_derived", lambda _: {"spread_bp": spread})
    monkeypatch.setattr(assemble, "_run_engines", lambda *_: {"composite": {}})
    monkeypatch.setattr(assemble, "_deep_layer", lambda *_: {"historical_evidence": {}})
    monkeypatch.setattr(assemble.eng_history, "vintage_evidence", lambda _: {})
    monkeypatch.setattr(assemble.eng_modelcourt, "convene", lambda *_, **__: {})
    monkeypatch.setattr(assemble, "_odds_ledger", lambda: [])
    monkeypatch.setattr(assemble, "_headline", lambda *_: {})
    monkeypatch.setattr(assemble, "_calendar", lambda *_: {})
    monkeypatch.setattr(assemble, "_provenance", lambda *_: [])
    monkeypatch.setattr(assemble, "_assert_snapshot_rights", lambda _: None)
    monkeypatch.setattr(assemble.editorial, "build_editorial", lambda **_: {})
    monkeypatch.setattr(assemble.editorial, "build_data_quality", lambda **_: {})
    monkeypatch.setattr(ai, "context_pack", lambda _: {})
    monkeypatch.setattr(assemble.eng_navigator, "commit", navigator)
    monkeypatch.setattr(assemble.eng_navigator, "score_record", lambda *_: {})
    monkeypatch.setattr(assemble.store, "load_pit_records", blocked_storage)
    monkeypatch.setattr(
        assemble, "_record_pit",
        blocked_storage if operation == "pit_write" else lambda *_: None,
    )
    monkeypatch.setattr(assemble.alerts, "snapshot_log", lambda *_, **__: {})
    monkeypatch.setattr(assemble, "_seal_release_evidence", lambda _: None)
    monkeypatch.setattr(assemble, "_publish_rebuilt_snapshot", publish)
    monkeypatch.setattr(assemble, "cached_snapshot", lambda: fake_snap)
    monkeypatch.setattr(api, "_PROD", False)

    task = asyncio.create_task(assemble.refresh_snapshot())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        response = await asyncio.wait_for(api.health(Response(), False), timeout=1)
        assert response["generated_at"] == fake_snap["generated_at"]
        assert not task.done()
        assert assemble._lock.locked()
        assert events == ["storage_entered"]
    finally:
        release.set()
        await task

    assert events == ["storage_entered", "storage_finished", "published"]
    assert assemble._build_generation == 1
