"""Every blocking stage of a board build runs on one long-lived thread.

``asyncio.to_thread`` hands each call to the longest-idle worker of the event
loop's shared default executor. Once request offloads (``/api/health`` and
the other ``to_thread`` routes) have grown that pool, successive rebuilds
rotated their engine and deep-layer stages through every worker, and glibc
kept each worker's freed build memory in that worker's own malloc arena, so
the API process grew with the rebuilds.
"""

import asyncio
import threading
import time

import pandas as pd
import pytest

from seiche import ai, assemble, hetzner_runtime, standby

BUILD_STAGES = {
    "_run_engines",
    "_deep_layer",
    "load_pit_records",
    "snapshot_log",
    "_record_pit",
    "_seal_release_evidence",
    "_persist_pending_snapshot",
}


@pytest.mark.asyncio
async def test_rebuilds_run_every_blocking_stage_on_one_thread(monkeypatch):
    threads: dict[str, set[str]] = {}

    def stage(name, result=None):
        def run(*_args, **_kwargs):
            threads.setdefault(name, set()).add(threading.current_thread().name)
            return result() if callable(result) else result
        return run

    async def gather():
        return {}, []

    async def navigator(*_args):
        return {"ok": True}

    spread = pd.Series([1.0], index=pd.DatetimeIndex(["2026-11-11"]))
    monkeypatch.setattr(hetzner_runtime, "enabled", lambda: False)
    monkeypatch.setattr(standby, "enabled", lambda: False)
    monkeypatch.setattr(assemble, "_lock", asyncio.Lock())
    monkeypatch.setattr(assemble, "_build_generation", 0)
    monkeypatch.setattr(assemble, "_cache", dict(assemble._cache))
    monkeypatch.setattr(assemble, "_gather_sources", gather)
    monkeypatch.setattr(assemble, "_rights_eligible_sources", lambda source: source)
    monkeypatch.setattr(assemble, "_derived", lambda _: {"spread_bp": spread})
    monkeypatch.setattr(
        assemble, "_run_engines", stage("_run_engines", lambda: {"composite": {}})
    )
    monkeypatch.setattr(
        assemble, "_deep_layer",
        stage("_deep_layer", lambda: {"historical_evidence": {}}),
    )
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
    monkeypatch.setattr(
        assemble.store, "load_pit_records", stage("load_pit_records", list)
    )
    monkeypatch.setattr(assemble, "_record_pit", stage("_record_pit"))
    monkeypatch.setattr(
        assemble.alerts, "snapshot_log", stage("snapshot_log", dict)
    )
    monkeypatch.setattr(
        assemble, "_seal_release_evidence",
        stage("_seal_release_evidence", lambda: {"products": {}}),
    )
    monkeypatch.setattr(
        assemble, "_persist_pending_snapshot", stage("_persist_pending_snapshot")
    )

    # Concurrent request offloads leave idle workers in the default executor,
    # exactly as health probes do while a long build holds one worker.
    await asyncio.gather(*(asyncio.to_thread(time.sleep, 0.05) for _ in range(4)))
    for _ in range(3):
        await assemble.refresh_snapshot()

    assert set(threads) == BUILD_STAGES
    used = set().union(*threads.values())
    assert len(used) == 1, f"build stages rotated across threads: {threads}"
    assert used != {threading.current_thread().name}
