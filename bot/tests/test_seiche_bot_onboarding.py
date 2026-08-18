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
    assert "Dollar Funding Desk" in bot.BOT_DISPLAY_NAME
    assert "Seiche" in bot.BOT_DISPLAY_NAME
    assert bot.BOT_DISPLAY_NAME != "Seiche"
    assert "dollar-funding" in bot.BOT_SHORT_DESCRIPTION.lower()
    assert "11:30 UTC" in bot.BOT_SHORT_DESCRIPTION
    assert "state-change alerts" in bot.BOT_SHORT_DESCRIPTION
    assert "research only" in bot.BOT_SHORT_DESCRIPTION.lower()
    assert "joint" not in bot.BOT_SHORT_DESCRIPTION.lower()
    assert "joint" not in bot.BOT_DESCRIPTION.lower()
    assert "11:30 UTC" in bot.BOT_DESCRIPTION


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
    names = [command["command"] for command in calls[1][1]["commands"]]
    assert names == [
        "now", "snap", "ask", "letter", "tandem", "where", "help", "start",
        "stop",
    ]
    assert 8 <= len(names) <= 10
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


def test_start_is_one_letter_glance_with_a_single_follow_action(
        tmp_path, monkeypatch):
    sent = []
    letter = [{
        "title": "Reserve pressure is leading price.",
        "date": "2026-08-18",
        "tag": "STRAIN",
        "summary": "The board reads STRAIN; plumbing leads price.",
        "slug": "2026-08-18-daily",
    }]

    def fake_send(chat_id, text, keyboard=None):
        sent.append((chat_id, text, keyboard))
        return {"ok": True}

    monkeypatch.setattr(bot, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(bot, "board_get", lambda _url: letter)
    monkeypatch.setattr(bot, "send", fake_send)

    bot.handle(4242, "/start ref_reader", "private")

    assert len(sent) == 1
    chat_id, text, keyboard = sent[0]
    assert chat_id == 4242
    assert len(text) <= 1200
    assert "Dollar Funding Desk" in text
    assert "11:30 UTC" in text
    assert "state-change alerts" in text
    assert "Today's letter" in text
    assert "Reserve pressure is leading price." in text
    assert "/help" in text and "/stop" in text
    assert "not investment advice" in text
    assert "/odds" not in text and "/turns" not in text
    assert "joint" not in text.lower()
    assert keyboard == [[{
        "text": "✉️ Read today's letter",
        "url": f"{bot.SITE}/dispatches/",
    }]]

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
    assert "state-change alerts" in text
    assert "6161" not in bot.load_state("subscribers.json", {})


def test_channel_footer_displays_the_same_destination_as_its_button(monkeypatch):
    sent = []

    def fake_send(chat_id, text, keyboard=None, *, _return_first=False):
        assert _return_first is True
        sent.append((chat_id, text, keyboard))
        return {"ok": True, "result": {"message_id": 123}}

    monkeypatch.setattr(bot, "LAB_CHANNEL", "-100123")
    monkeypatch.setattr(bot, "send", fake_send)

    assert bot.post_channel("A served funding read.", "lab_alert") == 123
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


_LAB_AD_FORBIDDEN = (
    "LiquidityLabTalk",
    "creator-intel",
    "creator_intel",
    "LiquidityCryptoDesk",
    "liquilens_crypto_bot",
    "palimpsest_watch_bot",
    "riptide_anake_bot",
    "corporate_stress_bot",
    "real_economy_desk_bot",
    "EvidenceSignalDesk",
    "NarcoScopeEvidenceBot",
)


def test_lab_channel_about_fits_telegram():
    assert len(bot.LAB_CHANNEL_ABOUT) <= 255
    assert "daily card" in bot.LAB_CHANNEL_ABOUT
    assert "fail-closed" in bot.LAB_CHANNEL_ABOUT
    assert "LiquidityLabTalk" not in bot.LAB_CHANNEL_ABOUT
    assert "LiquidityLabTalk" not in bot.LAB_CHANNEL_PIN
    assert "joint score" in bot.LAB_CHANNEL_PIN
    assert "@seiche_desk_bot" in bot.LAB_CHANNEL_PIN
    assert "@LiquiLens_bot" in bot.LAB_CHANNEL_PIN
    assert "@undertow_LiquiLens_bot" in bot.LAB_CHANNEL_PIN
    assert "@palimpsest_watch_bot" not in bot.LAB_CHANNEL_PIN
    assert "@riptide_anake_bot" not in bot.LAB_CHANNEL_PIN


def test_public_storefront_keeps_talk_crypto_and_creator_intel_off_the_lab():
    doors = json.dumps(bot.lab_card_keyboard())
    ad_copy = "\n".join([
        bot.LAB_CHANNEL_ABOUT,
        bot.LAB_CHANNEL_PIN,
        bot.HELP,
        bot.BOT_SHORT_DESCRIPTION,
        bot.BOT_DESCRIPTION,
        doors,
    ])
    for needle in _LAB_AD_FORBIDDEN:
        assert needle not in ad_copy
    assert "https://t.me/seiche_desk_bot" in doors
    assert "https://t.me/LiquiLens_bot" in doors
    assert "https://t.me/undertow_LiquiLens_bot" in doors
    assert "LiquidityLabTalk" not in bot.WHERE_CARD
    assert "creator-intel" not in bot.WHERE_CARD.lower()
    assert "creator_intel" not in bot.WHERE_CARD
    assert "@LiquidityCryptoDesk" in bot.WHERE_CARD
    assert "@liquilens_crypto_bot" in bot.WHERE_CARD
    assert "not the Lab card" in bot.WHERE_CARD
    assert "@palimpsest_watch_bot" not in bot.WHERE_CARD


def test_channel_letter_is_the_six_line_fail_closed_lab_card(monkeypatch):
    monkeypatch.setattr(
        bot,
        "api_get",
        lambda path: {
            "/api/gauge": {
                "regime": "STRAIN",
                "index": 42,
                "tell": 6,
            },
        }.get(path),
    )
    monkeypatch.setattr(bot, "ll_get", lambda _path: None)
    monkeypatch.setattr(bot, "ut_get", lambda _path: None)
    text = bot.fmt_channel_letter()
    lines = text.splitlines()
    assert len(lines) == 6
    assert lines[0].startswith("<b>Liquidity Lab</b> ")
    assert lines[2] == "🌊 Seiche: STRAIN 42 · Tell +6"
    assert lines[3] == "🏦 LiquiLens: board did not answer"
    assert lines[4] == "🌊 Undertow: board dark"
    assert lines[5] == "Screens, not a joint score. Three doors below."
    assert "dangerous quadrant" not in text
    assert "joint score" in text


def test_welcome_clips_a_long_letter_under_telegram_onboarding_budget():
    letter = [{
        "title": "A" * 400,
        "date": "2026-08-18",
        "summary": "B" * 800,
        "slug": "2026-08-18-daily",
    }]
    text = bot.fmt_welcome(letter)
    assert len(text) <= 1200
    assert "Today's letter" in text
    assert "..." in text


def test_channel_letter_keeps_three_named_lanes_and_never_fuses(monkeypatch):
    monkeypatch.setattr(
        bot,
        "api_get",
        lambda path: {"/api/gauge": {"regime": "STRAIN", "index": 42, "tell": 6}}.get(path),
    )
    monkeypatch.setattr(
        bot,
        "ll_get",
        lambda _path: {
            "rows": [
                {"name": "ESAF SFB", "tier": "orange"},
                {"name": "Calm Bank", "tier": "green"},
            ],
        },
    )
    monkeypatch.setattr(
        bot,
        "ut_get",
        lambda _path: {
            "segments": {"EQUITY": {"tier": "DEFICIENT"}},
            "informational": {
                "exit_cost": {
                    "buckets": {
                        "large": {
                            "costs": {
                                "bands": [{
                                    "position_usd": 100_000_000,
                                    "impact_band_bp": "1-10bp",
                                }],
                            },
                        },
                    },
                },
            },
        },
    )
    text = bot.fmt_channel_letter()
    assert "🌊 Seiche: STRAIN 42 · Tell +6" in text
    assert "🏦 LiquiLens: 2 scored · on watch: ESAF SFB" in text
    assert "🌊 Undertow: large-cap $100M exit 1-10bp / DEFICIENT" in text
    assert "Screens, not a joint score." in text
    assert "dangerous quadrant" not in text
    assert "quadrant" not in text.lower()


def test_fmt_tandem_is_labeled_reads_not_a_blended_alarm():
    both = bot.fmt_tandem(
        {"regime": "STRAIN", "index": 70, "tell": 8},
        {"rows": [{"name": "ESAF", "tier": "red"}], "tiers": {"red": 1}},
    )
    assert "Seiche (funding)" in both
    assert "LiquiLens (institutions)" in both
    assert "Seiche reads STRAIN" in both
    assert "LiquiLens worst tier is red" in both
    assert "Not a joint score" in both
    assert "dangerous quadrant" not in both
    assert "quadrant" not in both.lower()
    assert "did not answer" in bot.fmt_tandem(None, {"rows": [{"tier": "green"}]})
    assert "Neither desk answered" in bot.fmt_tandem(None, None)
    assert not hasattr(bot, "_tandem_class")
    assert not hasattr(bot, "_quadrant_verdict")
    assert "quadrant" not in (bot.__doc__ or "").lower()
    assert "quadrant" not in bot.HELP.lower()


def test_fmt_ask_adds_sibling_chips_and_refuses_a_joint_score():
    text = bot.fmt_ask({
        "answer": "Reserves fell.",
        "liquilens": {"available": False, "reading": "UNAVAILABLE"},
        "undertow": {"available": True},
        "seiche": {"regime": "STRAIN"},
    })
    assert "Reserves fell." in text
    assert "Seiche: STRAIN" in text
    assert "LiquiLens: UNAVAILABLE" in text
    assert "Undertow: attached" in text
    assert "Not a joint score" in text
    assert "joint score" in text.lower()


def test_tandem_alert_stays_off_the_public_funding_channel(
        tmp_path, monkeypatch):
    published = []
    sent = []
    monkeypatch.setattr(bot, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(bot, "api_get", lambda _path: {
        "regime": "STRAIN", "index": 70, "tell": 8,
    })
    monkeypatch.setattr(bot, "ll_get", lambda _path: {
        "rows": [{"name": "ESAF", "tier": "red"}],
        "tiers": {"red": 1},
    })
    monkeypatch.setattr(
        bot, "post_channel",
        lambda *args, **kwargs: published.append(args) or 99,
    )
    monkeypatch.setattr(
        bot, "_send_all",
        lambda subs, text, keyboard=None: sent.append((subs, text)) or 1,
    )
    bot.save_state("subscribers.json", {"7": {"since": "now"}})
    bot.save_state("tandem_reads.json", {"seiche": "CALM", "liquilens": "green"})

    bot.run_tandem()

    assert published == []
    assert sent
    assert "dangerous quadrant" not in sent[0][1]
    assert "quadrant" not in sent[0][1].lower()
    assert "Not a joint score" in sent[0][1]
    assert bot.load_state("tandem_reads.json", None) == {
        "seiche": "STRAIN", "liquilens": "red",
    }


def test_set_channel_profile_refuses_an_empty_channel(monkeypatch):
    monkeypatch.setattr(bot, "LAB_CHANNEL", "")
    with pytest.raises(SystemExit, match="LAB_CHANNEL_ID is empty"):
        bot.set_channel_profile()


def test_set_channel_profile_rewrites_about_and_pins(monkeypatch):
    monkeypatch.setattr(bot, "LAB_CHANNEL", "-1001")
    calls = []

    def fake_call(method, payload):
        calls.append((method, payload))
        return {"ok": True, "result": True}

    monkeypatch.setattr(bot, "tg_call", fake_call)
    monkeypatch.setattr(bot, "post_channel", lambda _text, _ref: 42)
    bot.set_channel_profile()
    methods = [method for method, _payload in calls]
    assert methods == ["setChatDescription", "pinChatMessage"]
    assert calls[0][1]["description"] == bot.LAB_CHANNEL_ABOUT
    assert calls[1][1]["message_id"] == 42
