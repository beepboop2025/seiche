"""Research workflows preserve source clocks and private subscription ownership."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import seiche_bot as bot


def funding(freshness="fresh", value=3.5):
    return {"schema": "seiche.money-market-desk.v1", "ok": True, "asof": "2026-09-24",
            "sections": [{"metrics": [{"id": "policy.sofr", "label": "SOFR", "value": value,
            "unit": "%", "status": "available", "asof": "2026-09-24", "freshness": freshness,
            "source": "New York Fed", "change_1d": 2, "change_unit": "bp"}]}]}


def test_funding_preserves_units_dates_missingness_and_changes():
    text = bot.fmt_funding(funding())
    assert "3.5 %" in text and "+2 bp" in text and "2026-09-24" in text
    assert "New York Fed" in text and "unavailable" in text


@pytest.mark.parametrize("freshness,value", [("stale", 3.5), ("unknown", 3.5), ("fresh", float("nan")), ("fresh", True)])
def test_unusable_funding_does_not_become_a_live_reading(freshness, value):
    text = bot.fmt_funding(funding(freshness, value))
    assert "3.5 %" not in text and "unavailable" in text


def test_trend_rejects_wrong_identity_and_gaps():
    payload = {"provenance": {"mnemonic": "SOFR", "staleness": "stale", "unit": "%"},
               "points": [["2026-09-23", 3.5], ["2026-09-24", 3.6]]}
    text = bot.fmt_series_trend(payload, "SOFR")
    assert "not confirmed fresh" in text and "3.6 %" in text
    assert "did not identify" in bot.fmt_series_trend(payload, "EFFR")
    payload["points"].append(["2026-09-25", None])
    assert "No continuous trend" in bot.fmt_series_trend(payload, "SOFR")


def test_tool_deep_link_does_not_subscribe_and_invalid_series_never_fetches(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "STATE_DIR", str(tmp_path))
    sent, reads = [], []
    monkeypatch.setattr(bot, "research_get", lambda path: reads.append(path) or funding())
    monkeypatch.setattr(bot, "send", lambda chat, text, keyboard=None: sent.append((text, keyboard)))
    bot.handle(42, "/start tool_funding")
    assert reads == ["/api/money-markets"]
    assert not (tmp_path / "subscribers.json").exists()
    bot.handle(42, "/trend ../../secret")
    assert len(reads) == 1 and "Use /trend" in sent[-1][0]
    bot.handle(-42, "/settings", "supergroup")
    assert sent[-1][1] == bot.PRIVATE_SUBSCRIPTION_KEYBOARD


def test_navigation_buttons_and_share_payload_are_bounded():
    from urllib.parse import urlsplit, parse_qs
    buttons = [b for r in bot.community_keyboard() for b in r]
    assert any(b.get("url") == bot.COMMUNITY_URL for b in buttons)
    share = next(b["url"] for b in buttons if b.get("url", "").startswith("https://t.me/share/"))
    assert parse_qs(urlsplit(share).query)["url"] == [bot.BOT_URL + "?start=tool_funding"]
    assert all(len(b.get("callback_data", "").encode()) <= 64 for r in bot.funding_keyboard() for b in r)


def test_public_research_cache_is_bounded_and_keeps_source_clocks(monkeypatch):
    bot._RESEARCH_CACHE.clear()
    calls = []
    monkeypatch.setattr(bot, "_get_json", lambda *args, **kwargs: calls.append((args, kwargs)) or {"asof": "2026-09-24"})
    first = bot.research_get("/public/test")
    second = bot.research_get("/public/test")
    assert first == second == {"asof": "2026-09-24"}
    assert len(calls) == 1 and calls[0][1] == {"timeout": 8, "tries": 1}
    for i in range(40):
        bot.research_get("/public/test/" + str(i))
    assert len(bot._RESEARCH_CACHE) == 32
    bot._RESEARCH_CACHE.clear()
