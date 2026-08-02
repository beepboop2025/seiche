"""Offline tests for the per-chat /ask pace limit and 429 rendering.

The backend keys its /api/ask limiter on the client IP and the bot reaches it
over loopback, so without a bot-side per-chat window every Telegram user
shares one server bucket. These tests assert the user-visible property: one
chat cannot spend another chat's answers, and a limiter refusal is never
rendered as an outage.

No network. urlopen and tg_call are both monkeypatched.
"""

import io
import json
import os
import sys
import urllib.error

import pytest

_BOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BOT_DIR)

import seiche_bot as bot  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(bot, "LAB_CHANNEL", "")
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    yield


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(bot, "tg_call",
                        lambda m, p: out.append((m, p)) or {"ok": True})
    return out


@pytest.fixture
def backend_calls(monkeypatch):
    """Count every question that actually reaches the shared backend bucket."""
    calls = []

    def _fake(question):
        calls.append(question)
        return {"answer": "The regime is EROSION because the Tell is positive."}

    monkeypatch.setattr(bot, "ask_desk", _fake)
    return calls


def _bodies(sent):
    return [p.get("text", "") for m, p in sent if m == "sendMessage"]


# ------------------------------------------------ the property: one chat
# cannot spend another chat's answers

def test_one_chat_cannot_drain_the_shared_backend_bucket(sent, backend_calls):
    for i in range(bot.ASK_PER_CHAT_LIMIT + 4):
        bot.handle(4242, f"/ask question {i}", "private")
    assert len(backend_calls) == bot.ASK_PER_CHAT_LIMIT


def test_a_flooding_chat_does_not_take_the_desk_from_another_chat(
        sent, backend_calls):
    for i in range(bot.ASK_PER_CHAT_LIMIT + 5):
        bot.handle(1111, f"/ask flood {i}", "private")
    flooded = len(backend_calls)

    bot.handle(2222, "/ask is the regime STRAIN?", "private")
    assert len(backend_calls) == flooded + 1, \
        "a second chat was refused because of the first chat's traffic"
    assert "is the regime STRAIN?" in backend_calls[-1]


def test_a_single_chat_cannot_send_every_question_it_wants_to_the_backend(
        sent, monkeypatch):
    """The 3.9 property, written against the seam that exists in BOTH the
    fixed and the unfixed bot: _get_json is what actually reaches the shared
    backend bucket. Unfixed, all eleven questions arrive there; the assertion
    then fails on the count rather than on a missing attribute, which is the
    only way this test proves the fix rather than the refactor.
    """
    urls = []

    def _fake(url, timeout=25, tries=2):
        urls.append(url)
        return {"answer": "grounded", "citations": []}

    monkeypatch.setattr(bot, "_get_json", _fake)
    for i in range(11):
        bot.handle(606, f"/ask q{i}", "private")
    asks = [u for u in urls if "/api/ask" in u]
    assert asks, "the desk stopped answering entirely, which is not the fix"
    assert len(asks) < 11, (
        "one chat put all 11 of its questions through to the shared backend "
        "bucket, so a single flooding chat still drains the desk")

    bot.handle(707, "/ask is the regime STRAIN?", "private")
    assert any("STRAIN" in u for u in urls), \
        "a second chat was refused because of the first chat's traffic"


def test_a_plain_message_is_gated_too_not_only_the_slash_command(
        sent, backend_calls):
    """Any non-slash private message routes to /ask, so the flood needs no
    slash command; the gate has to sit on that path as well."""
    bot.handle(7777, "why is funding tight", "private")
    assert backend_calls == ["why is funding tight"]
    assert "The regime is EROSION" in _bodies(sent)[-1]

    for i in range(bot.ASK_PER_CHAT_LIMIT + 4):
        bot.handle(7777, f"why is funding tight {i}", "private")
    assert len(backend_calls) == bot.ASK_PER_CHAT_LIMIT


def test_a_group_plain_message_never_spends_the_bucket(sent, backend_calls):
    bot.handle(7777, "hello everyone", "group")
    assert backend_calls == []
    assert sent == []


def test_the_window_slides_so_a_throttled_chat_recovers(monkeypatch):
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(bot.time, "time", lambda: clock["t"])

    for _ in range(bot.ASK_PER_CHAT_LIMIT):
        assert bot.ask_quota(99)[0] is True
    allowed, retry = bot.ask_quota(99)
    assert allowed is False
    assert 0 < retry <= bot.ASK_PER_CHAT_WINDOW_S + 1

    clock["t"] += bot.ASK_PER_CHAT_WINDOW_S + 1
    assert bot.ask_quota(99)[0] is True


def test_a_refusal_does_not_push_the_chats_own_window_forward(monkeypatch):
    """A refused hit must not be recorded, or a chat that keeps knocking can
    never leave the window."""
    clock = {"t": 500.0}
    monkeypatch.setattr(bot.time, "time", lambda: clock["t"])

    for _ in range(bot.ASK_PER_CHAT_LIMIT):
        bot.ask_quota(5)
    for _ in range(30):                      # keeps knocking through the window
        clock["t"] += 1
        assert bot.ask_quota(5)[0] is False
    clock["t"] += bot.ASK_PER_CHAT_WINDOW_S
    assert bot.ask_quota(5)[0] is True


def test_the_state_file_does_not_accrue_a_key_per_chat_forever(monkeypatch):
    clock = {"t": 900.0}
    monkeypatch.setattr(bot.time, "time", lambda: clock["t"])
    for chat in range(200):
        bot.ask_quota(chat)
    assert len(bot.load_state("ask_rate.json", {})) == 200
    clock["t"] += bot.ASK_PER_CHAT_WINDOW_S + 1
    bot.ask_quota(999)
    assert list(bot.load_state("ask_rate.json", {})) == ["999"]


# ------------------------------------------------ the property: a 429 is
# never rendered as an outage

def test_the_refusal_copy_says_per_user_limit_and_denies_an_outage(sent,
                                                                   backend_calls):
    for i in range(bot.ASK_PER_CHAT_LIMIT + 1):
        bot.handle(31337, f"/ask q{i}", "private")
    refusal = _bodies(sent)[-1]
    assert "not an outage" in refusal
    assert "per-user" in refusal
    assert "did not answer" not in refusal
    assert "/now" in refusal                 # points at an ungated way through
    assert "no paywall" in refusal           # and keeps the free claim intact


def test_a_backend_429_renders_differently_from_a_real_outage():
    busy = bot.fmt_ask(bot.ASK_BUSY)
    outage = bot.fmt_ask(None)
    assert busy != outage
    assert "not an outage" in busy
    assert "did not answer" not in busy
    assert "did not answer" in outage


def test_ask_desk_maps_a_429_to_busy_and_does_not_retry_into_the_limiter(
        monkeypatch):
    calls = []

    def _fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 429, "too many questions",
                                     {"Retry-After": "60"}, None)

    monkeypatch.setattr(bot.urllib.request, "urlopen", _fake_urlopen)
    assert bot.ask_desk("why is the regime STRAIN?") is bot.ASK_BUSY
    assert len(calls) == 1, "a retry on 429 spends the bucket the 429 protects"


def test_ask_desk_still_returns_the_answer_on_a_normal_reply(monkeypatch):
    payload = {"answer": "Reserves are draining into the TGA.",
               "citations": ["H.4.1"]}

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(bot.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(json.dumps(payload).encode()))
    out = bot.fmt_ask(bot.ask_desk("where did reserves go?"))
    assert "Reserves are draining into the TGA." in out
    assert "H.4.1" in out


def test_a_real_outage_is_still_reported_as_one(monkeypatch):
    def _fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(bot.urllib.request, "urlopen", _fake_urlopen)
    assert bot.ask_desk("anything") is None


# ------------------------------------------------ the free-public-good rule

def test_the_pace_limit_never_touches_the_board_commands(sent, monkeypatch):
    """Seiche stays free and unpaywalled: only the LLM call is paced, and the
    board reads are not gated at all."""
    monkeypatch.setattr(bot, "api_get", lambda p: None)
    monkeypatch.setattr(bot, "ll_get", lambda p: None)
    monkeypatch.setattr(bot, "_get_json", lambda *a, **k: None)
    for _ in range(bot.ASK_PER_CHAT_LIMIT + 5):
        bot.handle(8080, "/now", "private")
    assert len(_bodies(sent)) == bot.ASK_PER_CHAT_LIMIT + 5
    assert bot.load_state("ask_rate.json", {}) == {}
