"""Two collectors refresh at once after a start; losing the commit race is not a fault.

On the native runtime the API's first board build and the legacy source worker
(``ingest_runtime.run_legacy_worker``) each run ``assemble._gather_sources`` as
soon as they start. When a cache is past its TTL, both refresh it. The capture
manifest check (``store.save_series_batch(expected_blobs=...)``) keeps the
later commit from replacing the earlier one. Before this change the loser also
recorded ``SeriesBatchConflictError: capture manifest changed before
publication``, which the board sanitizes to INTERNAL_ERROR "collector failed",
next to the winner's retained capture: a fault on the first board after a start
that cleared on the next one, once the cache was fresh.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from unittest.mock import AsyncMock

import httpx
import pytest

from seiche import assemble, store
from seiche.public_faults import sanitize_fault_record
from seiche.sources import cbuae_donia as donia
from seiche.sources import cbuae_fx, ecb_fx
from seiche.sources.base import SourceFault

BEFORE_START = "2026-10-06T11:00:00+00:00"
WORKER_CAPTURE = "2026-10-06T12:00:00+00:00"
BOARD_CAPTURE = "2026-10-06T12:00:05+00:00"
CANARY = "private-canary-token https://user:secret@example.invalid/path"

# Every other source group in assemble._gather_sources, answered empty.
OTHER_SOURCES = {
    "fred": ("fetch_many",), "eia_petroleum": ("fetch_many",), "ofr": ("fetch_many",),
    "llamahacks": ("fetch_all",), "windfetch": ("fetch_all",), "ecb": ("fetch_many",),
    "ecb_fx": ("fetch",), "cbuae_fx": ("fetch",), "boj": ("fetch_many",), "bis": ("fetch_many",),
    "crypto": ("fetch_all",),
    "nyfed": ("fetch_secured_rates", "fetch_srf_ops", "fetch_pd_positions", "fetch_fx_swaps"),
    "nyfed_rde": ("fetch_rde",), "fiscaldata": ("fetch_tga_daily", "fetch_auctions", "fetch_upcoming_auctions"),
    "td_auctions": ("fetch_mspd_maturities",), "cftc": ("fetch_tff_ust", "fetch_disaggregated_commodities"),
    "palimpsest": ("fetch_all",), "fedtext": ("fetch_all",), "gdelt": ("fetch_all",),
}


def html():
    chart = {name: {"labels": ["2026-10-05", "2026-10-06"], "donia": [4.2187, 4.047]} for name in donia._PANELS}
    return ('<script type="application/json" id="ki-chart-data">' + json.dumps(chart) + "</script>").encode()


@pytest.fixture
def started(tmp_path, monkeypatch):
    """An isolated store and clock; every other source group answers empty."""
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "seiche.sqlite")
    clock = {"now": BEFORE_START}
    monkeypatch.setattr(donia, "utcnow_iso", lambda: clock["now"])
    for module, names in OTHER_SOURCES.items():
        for name in names:
            monkeypatch.setattr(getattr(assemble, module), name, AsyncMock(return_value={}))
    return clock


async def capture_before_start(monkeypatch):
    """The last good DONIA capture, taken before the start and now past its TTL."""
    monkeypatch.setattr(donia, "_download", AsyncMock(return_value=html()))
    result = await donia.fetch(httpx.AsyncClient(), force=True)
    assert result["DONIA"].fetched_at == BEFORE_START


@pytest.mark.asyncio
async def test_the_board_build_that_loses_the_donia_race_serves_the_winner_without_a_fault(started, monkeypatch):
    await capture_before_start(monkeypatch)
    board_in_flight, worker_done = asyncio.Event(), asyncio.Event()

    async def download():
        if not board_in_flight.is_set():
            # The board build: its refresh stays in flight until the worker commits.
            board_in_flight.set()
            await worker_done.wait()
            started["now"] = BOARD_CAPTURE
        else:
            started["now"] = WORKER_CAPTURE
        return html()

    monkeypatch.setattr(donia, "_download", download)
    board = asyncio.create_task(assemble._gather_sources())
    await asyncio.wait_for(board_in_flight.wait(), timeout=10)
    worker_sources, worker_faults = await assemble._gather_sources()
    winner = store.load_blob(donia.LATEST_CAPTURE_KEY)
    worker_done.set()
    board_sources, board_faults = await asyncio.wait_for(board, timeout=10)

    assert worker_faults == [] and board_faults == []
    assert winner["fetched_at"] == WORKER_CAPTURE
    # The losing capture never replaced the committed generation.
    assert store.load_blob(donia.LATEST_CAPTURE_KEY) == winner
    for sources in (worker_sources, board_sources):
        assert sources["cbuae_donia"]["DONIA"].fetched_at == WORKER_CAPTURE
    row = next(r for r in assemble._provenance(board_sources) if r["mnemonic"] == "DONIA")
    assert row["availability"] == "available" and row["fetched_at"] == WORKER_CAPTURE


@pytest.mark.asyncio
async def test_a_conflict_without_a_validated_competing_generation_still_faults(started, monkeypatch, caplog):
    await capture_before_start(monkeypatch)
    in_flight, overwritten = asyncio.Event(), asyncio.Event()

    async def download():
        in_flight.set()
        await overwritten.wait()
        return html()

    monkeypatch.setattr(donia, "_download", download)
    faults = []
    refresh = asyncio.create_task(donia.fetch(httpx.AsyncClient(), faults, force=True))
    await asyncio.wait_for(in_flight.wait(), timeout=10)
    # Something other than a validated capture replaces the manifest meanwhile.
    store.save_blob(donia.LATEST_CAPTURE_KEY, {"schema": "not-a-capture"})
    overwritten.set()
    caplog.set_level(logging.WARNING)
    with pytest.raises(SourceFault):
        await asyncio.wait_for(refresh, timeout=10)
    assert [fault["source"] for fault in faults] == ["cbuae_donia"]
    assert faults[0]["detail"].startswith("SeriesBatchConflictError:")
    assert "error_type=SeriesBatchConflictError" in caplog.text


@pytest.mark.asyncio
async def test_a_store_error_before_the_refresh_reaches_the_guard_and_leaves_no_donia_row(
    started, monkeypatch, caplog,
):
    """The pre-try reads are outside DONIA's own fault path.

    Such an error reaches the guard's generic branch as INTERNAL_ERROR, and the
    board then has no DONIA observation at all. A board that shows a DONIA
    capture next to a DONIA fault came from the adapter's own except branch.
    """

    await capture_before_start(monkeypatch)

    def locked(mnemonic, ttl_minutes):
        raise sqlite3.OperationalError("database is locked " + CANARY)

    monkeypatch.setattr(store, "is_fresh", locked)
    caplog.set_level(logging.WARNING)
    sources, faults = await assemble._gather_sources()
    assert [(fault["source"], fault["detail"].split(":")[0]) for fault in faults] == [
        ("cbuae_donia", "OperationalError"),
    ]
    assert sanitize_fault_record(faults[0])["category"] == "INTERNAL_ERROR"
    assert "cbuae_donia" not in sources
    row = next(r for r in assemble._provenance(sources) if r["mnemonic"] == "DONIA")
    assert row["availability"] == "unavailable" and row["fetched_at"] is None
    assert "group=cbuae_donia error_type=OperationalError" in caplog.text
    assert "private-canary-token" not in caplog.text and "database is locked" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("module", [donia, ecb_fx, cbuae_fx], ids=lambda module: module.SOURCE)
async def test_a_refresh_failure_logs_only_its_exception_type(tmp_path, monkeypatch, caplog, module):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "seiche.sqlite")

    async def unexpected(*_args, **_kwargs):
        raise RuntimeError(CANARY)

    monkeypatch.setattr(module, "_download", unexpected)
    caplog.set_level(logging.WARNING)
    faults = []
    with pytest.raises(SourceFault):
        await module.fetch(httpx.AsyncClient(), faults, force=True)
    assert [fault["source"] for fault in faults] == [module.SOURCE]
    assert f"source={module.SOURCE} error_type=RuntimeError" in caplog.text
    assert "private-canary-token" not in caplog.text
