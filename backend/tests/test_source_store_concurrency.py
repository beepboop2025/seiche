"""Source cache contention must leave API health and other coroutines usable."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
import threading

from fastapi.responses import Response
import httpx
import pytest

from seiche import api
from seiche.config import ALL_SERIES
from seiche.sources import fred
from seiche.sources._async_store import run_store


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["is_fresh", "load_series", "save_series"])
async def test_fred_store_contention_does_not_block_peer_coroutines(monkeypatch, operation):
    entered, release, peer = threading.Event(), threading.Event(), threading.Event()
    observations = []
    cached = object()
    returns = {"is_fresh": True, "load_series": cached, "save_series": None}

    def blocked(*args):
        entered.set()
        assert release.wait(3)
        observations.append(peer.is_set())
        return returns[operation]

    def watchdog():
        # A broken synchronous call stalls the event loop. Release that call
        # from outside the loop so the test fails instead of deadlocking.
        if entered.wait(3):
            release.wait(0.25)
        release.set()

    monkeypatch.setattr(fred.store, "is_fresh", lambda *args: operation != "save_series")
    monkeypatch.setattr(fred.store, "load_series", lambda *args: cached)
    monkeypatch.setattr(fred.store, "save_series", lambda *args: None)
    monkeypatch.setattr(fred.store, operation, blocked)
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, text="DATE,SOFR\n2026-09-14,3.5\n", request=request,
    ))
    watchdog_thread = threading.Thread(target=watchdog)
    watchdog_thread.start()
    try:
        async with httpx.AsyncClient(transport=transport) as client:
            fetch = asyncio.create_task(fred.fetch_series(client, ALL_SERIES["SOFR"]))
            try:
                async with asyncio.timeout(3):
                    while not entered.is_set():
                        await asyncio.sleep(0.001)
                peer.set()
            finally:
                release.set()
                result = await fetch
        assert observations == [True]
        if operation == "save_series":
            assert result.points.iloc[0] == 3.5
        else:
            assert result is cached
    finally:
        release.set()
        watchdog_thread.join(timeout=4)
    assert not watchdog_thread.is_alive()


@pytest.mark.parametrize("cancel_queued", [False, True])
def test_saturated_source_pool_preserves_health_default_executor(monkeypatch, cancel_queued):
    release, all_entered, fifth_entered = (
        threading.Event(), threading.Event(), threading.Event()
    )
    count_lock = threading.Lock()
    threads = set()

    def occupy_source_pool():
        with count_lock:
            threads.add(threading.get_ident())
            if len(threads) == 4:
                all_entered.set()
        assert release.wait(5)

    monkeypatch.setattr(api, "_health_response", lambda *args, **kwargs: {"status": "ok"})

    async def exercise():
        # One default worker makes accidental use by a source immediately
        # visible instead of depending on the machine's CPU count.
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        owners = [asyncio.create_task(run_store(occupy_source_pool)) for _ in range(4)]
        queued = None
        try:
            async with asyncio.timeout(3):
                while not all_entered.is_set():
                    await asyncio.sleep(0.001)
            queued = asyncio.create_task(run_store(fifth_entered.set))
            assert await asyncio.wait_for(api.health(Response()), timeout=1) == {"status": "ok"}
            assert not fifth_entered.is_set()
            assert len(threads) == 4
            if cancel_queued:
                queued.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await queued
        finally:
            release.set()
            await asyncio.gather(*owners)
            if queued is not None:
                await asyncio.gather(queued, return_exceptions=True)
        assert fifth_entered.is_set() is not cancel_queued

    asyncio.run(exercise())


@pytest.mark.asyncio
async def test_cancelling_source_waiter_allows_active_transaction_to_finish():
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def transaction():
        entered.set()
        assert release.wait(3)
        finished.set()

    task = asyncio.create_task(run_store(transaction))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not finished.is_set()
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 2)


def test_store_executor_preserves_context_and_works_across_event_loops():
    marker = ContextVar("source-store-marker", default="unset")
    marker.set("first")
    assert asyncio.run(run_store(marker.get)) == "first"
    marker.set("second")
    assert asyncio.run(run_store(marker.get)) == "second"


@pytest.mark.asyncio
async def test_store_exception_reaches_existing_source_failure_handler():
    def fail():
        raise ValueError("fixture storage failure")

    with pytest.raises(ValueError, match="fixture storage failure"):
        await run_store(fail)
