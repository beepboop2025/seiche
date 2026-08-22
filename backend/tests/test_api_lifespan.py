from __future__ import annotations

import asyncio

import pytest

from seiche import api


@pytest.mark.asyncio
async def test_keep_warm_coalesces_builds_and_retries_one_minute_after_completion(
    monkeypatch,
) -> None:
    calls: list[str] = []
    delays: list[float] = []
    clock = iter((100.0, 700.0))

    async def refresh_snapshot():
        calls.append("refresh")
        return {"generated_at": "fresh"}

    async def stop_after_delay(delay: float) -> None:
        delays.append(delay)
        raise asyncio.CancelledError

    monkeypatch.setattr(api.assemble, "refresh_snapshot", refresh_snapshot)
    monkeypatch.setattr(api, "monotonic", lambda: next(clock))
    monkeypatch.setattr(api.asyncio, "sleep", stop_after_delay)

    with pytest.raises(asyncio.CancelledError):
        await api._keep_warm()

    assert calls == ["refresh"]
    assert delays == [60]


@pytest.mark.asyncio
async def test_keep_warm_retries_failed_build_at_minimum_delay(monkeypatch) -> None:
    delays: list[float] = []
    clock = iter((100.0, 101.0))

    async def fail_snapshot():
        raise RuntimeError("synthetic rebuild failure")

    async def stop_after_delay(delay: float) -> None:
        delays.append(delay)
        raise asyncio.CancelledError

    monkeypatch.setattr(api.assemble, "refresh_snapshot", fail_snapshot)
    monkeypatch.setattr(api, "monotonic", lambda: next(clock))
    monkeypatch.setattr(api.asyncio, "sleep", stop_after_delay)

    with pytest.raises(asyncio.CancelledError):
        await api._keep_warm()

    assert delays == [60]


@pytest.mark.asyncio
async def test_keep_warm_warns_when_build_exhausts_freshness_budget(
    monkeypatch, caplog
) -> None:
    clock = iter((100.0, 941.0))

    async def refresh_snapshot():
        return {"generated_at": "late"}

    async def stop_after_delay(delay: float) -> None:
        assert delay == 60
        raise asyncio.CancelledError

    monkeypatch.setattr(api.assemble, "refresh_snapshot", refresh_snapshot)
    monkeypatch.setattr(api, "monotonic", lambda: next(clock))
    monkeypatch.setattr(api.asyncio, "sleep", stop_after_delay)

    with pytest.raises(asyncio.CancelledError):
        await api._keep_warm()

    assert "exceeding the 840.0s build budget" in caplog.text


@pytest.mark.asyncio
async def test_lifespan_binds_mcp_loop_without_dev_refresh(monkeypatch) -> None:
    bound_loops: list[asyncio.AbstractEventLoop] = []
    monkeypatch.setattr(api, "_PROD", False)
    monkeypatch.delenv("SEICHE_BG_REFRESH", raising=False)
    monkeypatch.setattr(api.mcp_server, "set_main_loop", bound_loops.append)

    async with api._lifespan(api.app):
        assert bound_loops == [asyncio.get_running_loop()]


@pytest.mark.asyncio
async def test_lifespan_cancels_forced_refresh_on_shutdown(monkeypatch) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_keep_warm() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(api, "_PROD", False)
    monkeypatch.setenv("SEICHE_BG_REFRESH", "1")
    monkeypatch.setattr(api.mcp_server, "set_main_loop", lambda _loop: None)
    monkeypatch.setattr(api, "_keep_warm", fake_keep_warm)

    async with api._lifespan(api.app):
        await asyncio.wait_for(started.wait(), timeout=1)

    assert cancelled.is_set()
