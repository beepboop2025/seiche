"""Subscription authorization must identify a person, not a shared room.

Telegram gives this handler one shared chat ID for every member's group
command. If /start and /stop mutate that ID, member A can subscribe the room
and member B can remove it. Those commands therefore redirect shared chats to
a neutral private-bot link; private subscription and lead behavior stays the
same, and read-only group commands remain available.
"""

import datetime as dt
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
    monkeypatch.setattr(bot, "board_get", lambda url: [])
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


def test_first_touch_attribution_is_stable_and_events_hide_chat_id(_isolated):
    bot.handle(4242, "/start ng26_lab_176984_funding", "private")
    bot.handle(4242, "/start later_ref", "private")

    rows = _leads(_isolated)
    events_text = (_isolated / "events.jsonl").read_text(encoding="utf-8")
    opens = json.loads(
        (_isolated / "start_attribution.json").read_text(encoding="utf-8")
    )

    assert [row["ref"] for row in rows] == ["ng26_lab_176984_funding"]
    assert len(opens) == 1
    assert next(iter(opens.values()))["ref"] == "ng26_lab_176984_funding"
    assert '"chat_id":4242' not in events_text
    assert '"actor"' in events_text


def test_invalid_start_ref_subscribes_without_fabricating_a_lead(_isolated):
    bot.handle(4242, "/start not+a+campaign", "private")

    assert "4242" in bot.load_state("subscribers.json", {})
    assert _leads(_isolated) == []
    events = [
        json.loads(line)
        for line in (_isolated / "events.jsonl").read_text().splitlines()
    ]
    assert events[0]["event"] == "start"
    assert events[0]["ref"] == "direct"


def test_retention_records_exact_elapsed_days_once(_isolated, monkeypatch):
    clock = [dt.datetime(2026, 8, 22, tzinfo=dt.timezone.utc)]
    monkeypatch.setattr(bot, "utcnow", lambda: clock[0])
    monkeypatch.setattr(bot, "ll_get", lambda *_args: {})

    bot.handle(4242, "/start ng26_lab_176984_funding", "private")
    bot.handle(4242, "/now", "private")
    clock[0] += dt.timedelta(hours=25)
    bot.handle(4242, "/letter", "private")
    bot.handle(4242, "/now", "private")
    clock[0] += dt.timedelta(days=6)
    bot.handle(4242, "/tandem", "private")

    state = bot.load_state("start_attribution.json", {})
    record = next(iter(state.values()))
    events = [
        json.loads(line)
        for line in (_isolated / "events.jsonl").read_text().splitlines()
    ]

    assert record["activation"] == "now"
    assert record["active_days"] == [0, 1, 7]
    assert [
        row["day_index"] for row in events if row["event"] == "active_day"
    ] == ["0", "1", "7"]
    assert len([row for row in events if row["event"] == "activation"]) == 1
    assert "4242" not in (_isolated / "events.jsonl").read_text()


def test_growth_failures_do_not_suppress_start_or_useful_replies(
        _isolated, monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(
        bot,
        "send",
        lambda _chat_id, text, *_args, **_kwargs: sent.append(text),
    )
    monkeypatch.setattr(
        bot,
        "_record_first_open",
        lambda *_args: (_ for _ in ()).throw(OSError("analytics disk full")),
    )

    bot.handle(4242, "/start ng26_lab_176984_funding", "private")
    assert (_isolated / "subscribers.json").is_file()
    assert sent

    monkeypatch.setattr(
        bot,
        "_record_activation",
        lambda *_args: (_ for _ in ()).throw(OSError("analytics read-only")),
    )
    bot.handle(4242, "/now", "private")

    assert len(sent) == 2
    assert "cannot record first open" in capsys.readouterr().err


def test_empty_ask_and_metadata_only_china_are_not_activations(
        monkeypatch):
    calls = []
    monkeypatch.setattr(
        bot,
        "_safe_record_activation",
        lambda _chat_id, command: calls.append(command),
    )

    bot.handle(4242, "/ask", "private")
    bot.handle(4242, "/china", "private")

    assert calls == []


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
