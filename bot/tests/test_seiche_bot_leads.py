"""Lead attribution must count people, not rooms.

Each channel post carries its own `?start=ref` deep link so record_lead can
attribute an arrival to the post that earned it, and that ref count is the
number meant to decide what the desks publish more of. A group /start books
one lead for a whole room under whoever added the bot, which inflates that
number in the one direction nobody would notice: upward, on the metric being
optimised.

Subscribing a group is deliberately still allowed. Seiche is a free public
good and a room reading the daily letter is distribution working.
"""

import json
import os
import sys

import pytest

_BOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BOT_DIR)

import seiche_bot as bot  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(bot, "LAB_CHANNEL", "")
    monkeypatch.setattr(bot, "send", lambda *a, **k: None)
    monkeypatch.setattr(bot, "api_get", lambda path: {})
    monkeypatch.setattr(bot, "fmt_now", lambda *a, **k: "board")
    yield tmp_path


def _leads(tmp_path):
    p = tmp_path / "leads.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def test_a_private_start_with_a_ref_books_a_lead(_isolated):
    bot.handle(555, "/start lab_letter", "private")

    rows = _leads(_isolated)
    assert len(rows) == 1
    assert rows[0]["ref"] == "lab_letter"
    assert rows[0]["chat_id"] == 555


@pytest.mark.parametrize("chat_type", ["group", "supergroup", "channel"])
def test_a_group_start_never_books_a_lead(_isolated, chat_type):
    bot.handle(-100123, "/start lab_letter", chat_type)

    assert _leads(_isolated) == [], (
        "a room was booked as an arrival, crediting one person's ref with a "
        "whole channel")


def test_a_group_can_still_subscribe(_isolated):
    """The guard must cost the room nothing except the false lead."""
    bot.handle(-100123, "/start lab_letter", "group")

    subs = json.loads((_isolated / "subscribers.json").read_text())
    assert "-100123" in subs, "a free public good should still serve a group"
