"""A full disk must not corrupt bot state or kill a poller (2026-10-31 ENOSPC).

Every long poller saves offset.json at the end of each cycle. Before this
contract the save raised out of poll_loop on ENOSPC, so all desk bots crash
looped together while the disk was full, and a oneshot left a zero-byte
``<name>.tmp`` beside its state file.
"""
from __future__ import annotations

import errno
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
POLLERS = ("seiche",)


class StopPolling(Exception):
    """Raised by the fake Telegram API to end poll_loop after a few cycles."""


def load_bot(name: str):
    path = ROOT / "bot" / f"{name}_bot.py"
    spec = importlib.util.spec_from_file_location(f"durable_{name}_bot", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=POLLERS)
def bot(request, tmp_path, monkeypatch):
    module = load_bot(request.param)
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(module, "STATE_DIR",
                        state if isinstance(module.STATE_DIR, Path)
                        else str(state))
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    return module, state


def _disk_full(*_args, **_kwargs):
    raise OSError(errno.ENOSPC, "No space left on device")


def test_failed_state_write_keeps_the_previous_file_and_no_temp(
    bot, monkeypatch
) -> None:
    module, state = bot
    module.save_state("offset.json", 41)
    assert json.loads((state / "offset.json").read_text()) == 41

    monkeypatch.setattr(module.os, "fsync", _disk_full)
    with pytest.raises(OSError) as raised:
        module.save_state("offset.json", 42)

    assert raised.value.errno == errno.ENOSPC
    assert json.loads((state / "offset.json").read_text()) == 41
    assert sorted(path.name for path in state.iterdir()) == ["offset.json"]


def test_successful_state_write_replaces_atomically_without_leftovers(bot) -> None:
    module, state = bot
    for value in (1, {"subscribers": [1, 2]}, 3):
        module.save_state("offset.json", value)
    assert json.loads((state / "offset.json").read_text()) == 3
    assert sorted(path.name for path in state.iterdir()) == ["offset.json"]


def _install_fake_telegram(module, monkeypatch, batches):
    """Serve one getUpdates batch per call, then stop the loop."""
    offsets: list[int] = []

    def tg_call(method: str, payload: dict | None = None, *args, **kwargs):
        if method != "getUpdates":
            return {"ok": True, "result": True}
        offsets.append(int((payload or {}).get("offset") or 0))
        if not batches:
            raise StopPolling
        return {"ok": True, "result": batches.pop(0)}

    monkeypatch.setattr(module, "tg_call", tg_call)
    handled: list[str] = []
    monkeypatch.setattr(module, "handle",
                        lambda _chat, text, *_args, **_kwargs: handled.append(text))
    for name, value in (
        ("publisher_tick", lambda: None),
        ("channel_retention_configured", lambda: False),
        ("mcp_preflight", lambda **_kwargs: True),
    ):
        if hasattr(module, name):
            monkeypatch.setattr(module, name, value)
    return offsets, handled


def _update(update_id: int) -> dict:
    return {"update_id": update_id, "message": {
        "chat": {"id": 4242, "type": "private"}, "text": f"/help {update_id}",
    }}


def test_poll_loop_survives_a_failed_offset_save_and_retries_next_cycle(
    bot, monkeypatch, capsys
) -> None:
    module, state = bot
    offsets, handled = _install_fake_telegram(
        module, monkeypatch, [[_update(7)], [_update(8)]])
    real_save = module.save_state
    attempts: list[int] = []

    def flaky_save(name: str, value) -> None:
        if name == "offset.json":
            attempts.append(value)
            if len(attempts) == 1:
                _disk_full()
        real_save(name, value)

    monkeypatch.setattr(module, "save_state", flaky_save)

    with pytest.raises(StopPolling):
        module.poll_loop()

    assert handled == ["/help 7", "/help 8"]
    # The in-memory offset kept confirming updates while the disk was full.
    assert offsets == [0, 8, 9]
    assert attempts[0] == 8 and attempts[-1] == 9
    assert json.loads((state / "offset.json").read_text()) == 9
    err = capsys.readouterr().err
    assert "offset save failed (1 in a row)" in err
    assert "retrying next cycle" in err
    assert "offset save recovered after 1 failed attempt(s)" in err


def test_poll_loop_logs_a_persistent_disk_fault_without_flooding(
    bot, monkeypatch, capsys
) -> None:
    module, _state = bot
    batches = [[_update(index)] for index in range(1, 63)]
    offsets, _handled = _install_fake_telegram(module, monkeypatch, batches)
    monkeypatch.setattr(
        module, "save_state",
        lambda name, value: _disk_full() if name == "offset.json" else None)

    with pytest.raises(StopPolling):
        module.poll_loop()

    assert offsets[-1] == 63
    err = capsys.readouterr().err
    assert err.count("offset save failed") == 2  # first failure and the 60th
    assert "offset save failed (60 in a row)" in err
