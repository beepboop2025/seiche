from __future__ import annotations

import asyncio

import pytest

from seiche import api


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
