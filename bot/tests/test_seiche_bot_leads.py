"""Subscription authorization must identify a person, not a shared room.

Telegram gives this handler one shared chat ID for every member's group
command. If /start and /stop mutate that ID, member A can subscribe the room
and member B can remove it. Those commands therefore redirect shared chats to
a neutral private-bot link; private subscription and lead behavior stays the
same, and read-only group commands remain available.
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
    return [json.loads(line) for line in p.read_text().splitlines()
            if line.strip()]


def _assert_private_redirect(sent, chat_id):
    assert len(sent) == 1
    assert sent[0][0] == chat_id
    assert sent[0][1] == bot.PRIVATE_SUBSCRIPTION_PROMPT
    keyboard = sent[0][2]
    assert keyboard == bot.PRIVATE_SUBSCRIPTION_KEYBOARD
    assert keyboard[0][0]["url"] == bot.BOT_URL
    assert "?start=" not in keyboard[0][0]["url"]
    assert "/start" in sent[0][1] and "/stop" in sent[0][1]


def _forbid_state_access(*args, **kwargs):
    pytest.fail("a shared-chat subscription command touched private state")


def test_a_private_start_with_a_ref_still_subscribes_and_books_a_lead(
        _isolated):
    bot.handle(555, "/start lab_letter", "private")

    subscribers = json.loads((_isolated / "subscribers.json").read_text())
    assert "555" in subscribers
    rows = _leads(_isolated)
    assert len(rows) == 1
    assert rows[0]["ref"] == "lab_letter"
    assert rows[0]["chat_id"] == 555


@pytest.mark.parametrize("chat_type", ["group", "supergroup", "channel"])
def test_member_a_cannot_subscribe_a_shared_chat(
        _isolated, monkeypatch, chat_type):
    sent = []
    monkeypatch.setattr(
        bot, "send",
        lambda chat_id, text, keyboard=None:
            sent.append((chat_id, text, keyboard)),
    )
    monkeypatch.setattr(bot, "load_state", _forbid_state_access)
    monkeypatch.setattr(bot, "save_state", _forbid_state_access)
    monkeypatch.setattr(bot, "record_lead", _forbid_state_access)

    bot.handle(-100123, "/start lab_letter", chat_type)

    _assert_private_redirect(sent, -100123)
    assert list(_isolated.iterdir()) == []


@pytest.mark.parametrize("chat_type", ["group", "supergroup", "channel"])
def test_member_b_cannot_unsubscribe_a_shared_chat(
        _isolated, monkeypatch, chat_type):
    sent = []
    subscribers = _isolated / "subscribers.json"
    subscribers.write_text(
        json.dumps({"-100123": {"since": "legacy-group-subscription"}})
    )
    before = subscribers.read_bytes()
    monkeypatch.setattr(
        bot, "send",
        lambda chat_id, text, keyboard=None:
            sent.append((chat_id, text, keyboard)),
    )
    monkeypatch.setattr(bot, "load_state", _forbid_state_access)
    monkeypatch.setattr(bot, "save_state", _forbid_state_access)
    monkeypatch.setattr(bot, "record_lead", _forbid_state_access)

    bot.handle(-100123, "/stop", chat_type)

    _assert_private_redirect(sent, -100123)
    assert subscribers.read_bytes() == before


def test_read_only_commands_remain_available_in_a_group(monkeypatch):
    sent = []
    monkeypatch.setattr(
        bot, "send",
        lambda chat_id, text, keyboard=None:
            sent.append((chat_id, text, keyboard)),
    )
    monkeypatch.setattr(bot, "gauge_history_append", lambda gauge: None)

    bot.handle(-100123, "/now", "group")

    assert len(sent) == 1
    assert sent[0][0] == -100123
    assert sent[0][1] == "board"
