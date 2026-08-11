"""Telegram onboarding and profile contracts for the Seiche desk.

These tests stay offline. They protect the first-run reader experience and
the setup failure path without touching subscriber cadence, alert state or the
network.
"""

import json
import os
import sys

import pytest

_BOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BOT_DIR)

import seiche_bot as bot  # noqa: E402


def _successful_setup_reply(method):
    if method == "getMe":
        return {"ok": True, "result": {"username": bot.BOT_USERNAME}}
    return {"ok": True, "result": True}


def test_profile_metadata_fits_telegram_limits_and_names_the_audience():
    bot._validate_setup_metadata()

    assert bot._telegram_text_units(bot.BOT_DISPLAY_NAME) <= 64
    assert bot._telegram_text_units(bot.BOT_SHORT_DESCRIPTION) <= 120
    assert bot._telegram_text_units(bot.BOT_DESCRIPTION) <= 512
    assert "US Funding Stress" in bot.BOT_DISPLAY_NAME
    assert bot.BOT_DISPLAY_NAME != "Seiche"
    assert "cross-desk" in bot.BOT_DESCRIPTION
    assert "cross-desk" in bot.HELP


def test_setup_registers_name_commands_and_descriptions(monkeypatch, capsys):
    calls = []

    def fake_call(method, payload):
        calls.append((method, payload))
        return _successful_setup_reply(method)

    monkeypatch.setattr(bot, "tg_call", fake_call)
    bot.run_setup()

    assert [method for method, _ in calls] == [
        "setMyName",
        "setMyCommands",
        "setMyShortDescription",
        "setMyDescription",
        "getMe",
    ]
    assert calls[0][1] == {"name": bot.BOT_DISPLAY_NAME}
    assert calls[1][1]["commands"] is bot.BOT_COMMANDS
    assert any(
        command == {"command": "help",
                    "description": "Full command list and desk guide"}
        for command in calls[1][1]["commands"]
    )
    assert "setup done" in capsys.readouterr().out


@pytest.mark.parametrize(
    "failed_method",
    [
        "setMyName",
        "setMyCommands",
        "setMyShortDescription",
        "setMyDescription",
        "getMe",
    ],
)
def test_every_setup_failure_stops_setup(monkeypatch, failed_method):
    calls = []

    def fake_call(method, payload):
        calls.append(method)
        if method == failed_method:
            return {"ok": False, "error_code": 400,
                    "description": "rejected in test"}
        return _successful_setup_reply(method)

    monkeypatch.setattr(bot, "tg_call", fake_call)

    with pytest.raises(bot.TelegramSetupError, match=failed_method):
        bot.run_setup()
    assert calls[-1] == failed_method


@pytest.mark.parametrize("reply", [None, {"ok": True, "result": False}])
def test_setup_rejects_missing_or_unacknowledged_responses(monkeypatch, reply):
    monkeypatch.setattr(bot, "tg_call", lambda method, payload: reply)

    with pytest.raises(bot.TelegramSetupError, match="setMyName"):
        bot.run_setup()


def test_start_is_one_truthful_message_with_live_gauge_and_lab_cta(
        tmp_path, monkeypatch):
    sent = []
    gauge = {
        "regime": "STRAIN",
        "index": 61,
        "tell": 8,
        "generated_at": "2026-08-09T12:30:00Z",
    }
    public = {"conclusion": {"line": "Reserve pressure is leading price."}}

    def fake_api(path):
        return gauge if path == "/api/gauge" else public

    def fake_send(chat_id, text, keyboard=None):
        sent.append((chat_id, text, keyboard))
        return {"ok": True}

    monkeypatch.setattr(bot, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(bot, "api_get", fake_api)
    monkeypatch.setattr(bot, "send", fake_send)

    bot.handle(4242, "/start ref_reader", "private")

    assert len(sent) == 1
    chat_id, text, keyboard = sent[0]
    assert chat_id == 4242
    assert "Early warning for strain in dollar funding" in text
    assert "11:30 UTC" in text
    assert "funding-state alerts" in text
    assert "cross-desk change alerts" in text
    assert "sourced desk news" in text
    assert "Live gauge" in text and "STRAIN" in text and "61/100" in text
    assert "/help" in text and "/stop" in text
    assert "not investment advice or an execution instruction" in text
    assert "/odds" not in text and "/turns" not in text
    assert keyboard is not None and len(keyboard) <= 3
    assert any(
        button.get("url") == bot.LAB_LINK
        for row in keyboard
        for button in row
    )

    subscribers = json.loads((tmp_path / "subscribers.json").read_text())
    assert "4242" in subscribers


def test_help_is_explicit_and_keeps_the_liquidity_lab_path(monkeypatch):
    sent = []

    def fake_send(chat_id, text, keyboard=None):
        sent.append((chat_id, text, keyboard))
        return {"ok": True}

    monkeypatch.setattr(bot, "send", fake_send)

    bot.handle(5150, "/help", "private")

    assert len(sent) == 1
    assert sent[0][1] == bot.HELP
    keyboard = sent[0][2]
    assert keyboard is not None
    assert any(
        button.get("url") == bot.LAB_LINK
        for row in keyboard
        for button in row
    )


def test_stop_names_every_delivery_class_it_disables(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(bot, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(
        bot,
        "send",
        lambda chat_id, text, keyboard=None:
            sent.append((chat_id, text, keyboard)) or {"ok": True},
    )
    bot.save_state("subscribers.json", {"6161": {"since": "now"}})

    bot.handle(6161, "/stop", "private")

    assert len(sent) == 1
    text = sent[0][1]
    assert "daily letter" in text
    assert "funding-state" in text
    assert "cross-desk alerts" in text
    assert "sourced desk news" in text
    assert "6161" not in bot.load_state("subscribers.json", {})


def test_channel_footer_displays_the_same_destination_as_its_button(monkeypatch):
    sent = []

    def fake_send(chat_id, text, keyboard=None):
        sent.append((chat_id, text, keyboard))
        return {"ok": True}

    monkeypatch.setattr(bot, "LAB_CHANNEL", "-100123")
    monkeypatch.setattr(bot, "send", fake_send)

    assert bot.post_channel("A served funding read.", "lab_alert") is True
    _, text, keyboard = sent[0]
    destination = keyboard[0][0]["url"]
    subscribing_buttons = [
        button
        for row in keyboard
        for button in row
        if button.get("url", "").startswith(f"{bot.BOT_URL}?start=")
    ]

    assert destination == f"{bot.BOT_URL}?start=lab_alert"
    assert destination in text
    assert destination.startswith(bot.BOT_URL)
    assert len(keyboard) == 1
    assert len(subscribing_buttons) == 1
    assert all("follow" in button["text"].lower()
               for button in subscribing_buttons)
    assert "11:30 UTC" in text
    assert "state-change alerts" in text
