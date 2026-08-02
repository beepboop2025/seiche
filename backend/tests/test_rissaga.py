"""Offline tests for Rissaga, the lab news radar. No network: the one raw
HTTP seam (_http_get) is blocked by default and every surface that needs it
is stubbed per test. State is isolated per test."""

import json
import os
import sys
from datetime import datetime, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "bot"))

import rissaga as rz  # noqa: E402

NOW = 1_800_000_000.0
NOW_DT = datetime.fromtimestamp(NOW, timezone.utc)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(rz, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(rz.time, "sleep", lambda s: None)

    def _no_net(*a, **k):
        raise RuntimeError("network blocked in tests")

    monkeypatch.setattr(rz, "_http_get", _no_net)
    yield


@pytest.fixture
def sent(monkeypatch):
    out = []

    def fake_tg(method, payload):
        out.append((method, payload))
        return {"ok": True, "result": {"message_id": len(out)}}

    monkeypatch.setattr(rz, "tg_call", fake_tg)
    return out


def mk(title, key="bbg_markets", tier=0.8, ts=None, snippet="",
       source="Bloomberg", link="https://example.com/a"):
    return {"key": key, "tier": tier, "title": title, "link": link,
            "snippet": snippet, "source_name": source,
            "ts": NOW - 3600 if ts is None else ts}


# ------------------------------------------------------------- scoring ----
def test_beat_scoring_known_headlines():
    beat, base = rz.beat_score("Fed expands standing repo facility counterparties")
    assert beat == "plumbing" and base >= 5
    beat, base = rz.beat_score(
        "FDIC seizes First Valley Bank as regulators begin receivership")
    assert beat == "bank_stress" and base >= 9
    beat, base = rz.beat_score("Celebrity chef opens new restaurant")
    assert beat is None and base == 0


def test_kill_list_drops_routine_previews():
    items = [mk("Week ahead: what to watch in the repo market")]
    assert rz.rank(items, {}, NOW) == []


def test_kill_list_spares_strong_distress():
    items = [mk("Week ahead turns dark as bank run and deposit run hit First Foo")]
    marked = rz.rank(items, {}, NOW)
    assert len(marked) == 1
    assert marked[0]["rep"]["beat"] == "bank_stress"


def test_cluster_merges_same_story_and_counts_outlets():
    t = "Repo market seizes as SOFR spikes overnight"
    items = [mk(t, source="Bloomberg"), mk(t, source="FT"), mk(t, source="WSJ")]
    marked = rz.rank(items, {}, NOW)
    assert len(marked) == 1
    cl = marked[0]
    assert cl["n_sources"] == 3
    later = NOW + 999_999
    solo = rz.rank([mk(t, source="Bloomberg", ts=later - 3600)], {}, later)
    assert cl["final"] > solo[0]["final"]


def test_seen_ledger_suppresses_repeat_and_allows_escalation():
    t = "Repo market seizes as SOFR spikes overnight"
    first = rz.rank([mk(t)], {}, NOW)
    assert len(first) == 1
    again = rz.rank([mk(t)], {}, NOW + 3600)
    assert again == []
    six_sources = [mk(t, source=s) for s in
                   ("Bloomberg", "FT", "WSJ", "CNBC", "Reuters", "Nikkei")]
    escalated = rz.rank(six_sources, {}, NOW + 7200)
    assert len(escalated) == 1 and escalated[0]["n_sources"] == 6


def test_recency_drops_stale_items():
    items = [mk("Discount window borrowing hits record", ts=NOW - 48 * 3600)]
    assert rz.rank(items, {}, NOW) == []


def test_board_boost_raises_score_when_beat_stressed():
    calm = rz.rank([mk("Repo market strains as SOFR spikes")],
                   {"seiche": {"regime": "CALM"}}, NOW)
    later = NOW + 999_999
    stressed = rz.rank([mk("Repo market strains as SOFR spikes", ts=later - 3600)],
                       {"seiche": {"regime": "STRAIN"}}, later)
    assert stressed[0]["final"] > calm[0]["final"]


# -------------------------------------------------------- board events ----
def test_board_event_synthesized_on_regime_flip():
    rz.save_state("last_boards.json", {"seiche": {"regime": "CALM", "index": 30}})
    boards = {"seiche": {"regime": "STRAIN", "index": 50}}
    events = rz.board_events(boards, NOW)
    assert len(events) == 1
    assert events[0]["beat"] == "plumbing"
    assert "CALM to STRAIN" in events[0]["title"]
    assert events[0]["board_event"] is True


def test_board_event_needs_a_prior_reading():
    events = rz.board_events({"seiche": {"regime": "STRAIN", "index": 50}}, NOW)
    assert events == []


def test_absent_board_does_not_fake_a_flip_later():
    rz.save_state("last_boards.json", {"rails": {"state": "CALM"}})
    rz.board_events({"rails": None}, NOW)
    kept = rz.load_state("last_boards.json", {})
    assert kept["rails"] == {"state": "CALM"}


# ---------------------------------------------------------- feed parse ----
RSS_GNEWS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Repo squeeze deepens - Reuters</title>
<link>https://news.google.com/x</link>
<pubDate>Sun, 02 Aug 2026 12:00:00 GMT</pubDate>
<source url="https://reuters.com">Reuters</source></item>
</channel></rss>"""

ATOM_FEED = (b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
             b'<entry><title>Bank reserves fall</title>'
             b'<link href="https://example.org/e"/>'
             b'<updated>2026-08-02T10:00:00Z</updated></entry></feed>')


def test_parse_gnews_extracts_publisher_and_strips_title():
    items = rz.parse_feed(RSS_GNEWS, "gnews_plumbing", 0.65, NOW)
    assert items[0]["title"] == "Repo squeeze deepens"
    assert items[0]["source_name"] == "Reuters"


def test_parse_atom():
    items = rz.parse_feed(ATOM_FEED, "occ", 1.0, NOW)
    assert items[0]["title"] == "Bank reserves fall"
    assert items[0]["link"] == "https://example.org/e"


def test_lenient_parser_fixes_bare_ampersand():
    raw = (b"<rss><channel><item><title>AT&T repo desk grows</title>"
           b"<link>https://x</link></item></channel></rss>")
    items = rz.parse_feed(raw, "bbg_markets", 0.8, NOW)
    assert items[0]["title"] == "AT&T repo desk grows"


def test_dtd_and_entity_documents_rejected():
    evil = (b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY a "b">]>'
            b"<rss><channel><item><title>x</title></item></channel></rss>")
    with pytest.raises(ValueError):
        rz._lenient_root(evil)


def test_undated_items_do_not_read_as_fresh():
    raw = (b"<rss><channel><item><title>Discount window climbs</title>"
           b"<link>https://x</link></item></channel></rss>")
    items = rz.parse_feed(raw, "fed_press", 1.0, NOW)
    assert items[0]["ts"] == pytest.approx(NOW - 12 * 3600)


# --------------------------------------------------------- fetch layer ----
def test_conditional_get_304_serves_cache(monkeypatch):
    cached_item = mk("Reserves debate returns", key="fed_press", tier=1.0)
    rz.save_state("feeds_http.json",
                  {"fed_press": {"etag": "abc", "fetched_ts": NOW - 600,
                                 "items": [cached_item]}})
    monkeypatch.setattr(rz, "all_feeds",
                        lambda: [("fed_press", "https://fed/x", 1.0)])
    monkeypatch.setattr(rz, "_http_get", lambda url, headers=None, timeout=20:
                        (304, {}, b""))
    items, health = rz.fetch_feeds(NOW)
    assert health["fed_press"] == "ok (304)"
    assert items and items[0]["title"] == "Reserves debate returns"


def test_feed_failure_is_isolated_and_declared(monkeypatch):
    good = (b"<rss><channel><item><title>SOFR prints high</title>"
            b"<link>https://x</link></item></channel></rss>")

    def fake(url, headers=None, timeout=20):
        if "bad" in url:
            raise rz.urllib.error.URLError("boom")
        return 200, {}, good

    monkeypatch.setattr(rz, "all_feeds", lambda: [
        ("fed_press", "https://fed/x", 1.0), ("occ", "https://bad/y", 1.0)])
    monkeypatch.setattr(rz, "_http_get", fake)
    items, health = rz.fetch_feeds(NOW)
    assert health["fed_press"] == "ok"
    assert health["occ"].startswith("unavailable")
    assert len(items) == 1


# -------------------------------------------------------------- compose ----
def test_compose_carries_board_line_angle_and_footer():
    marked = rz.rank([mk("Repo market seizes as SOFR spikes overnight")],
                     {"seiche": {"regime": "STRAIN", "index": 46.3}}, NOW)
    text = rz.compose(marked, {"seiche": {"regime": "STRAIN", "index": 46.3}},
                      {"fed_press": "ok"}, NOW_DT)
    assert "Rissaga" in text and "Seiche desk:" in text and "Angle:" in text
    assert "Pangram" in text


def test_low_signal_run_is_honest():
    text = rz.compose([], {}, {"fed_press": "ok"}, NOW_DT)
    assert "Nothing cleared the bar" in text


def test_degraded_feeds_named_in_footer():
    text = rz.compose([], {}, {"fed_press": "ok", "occ": "unavailable: http 404"},
                      NOW_DT)
    assert "1 of 2 feeds answered" in text and "occ" in text


# ------------------------------------------------------------- dispatch ----
def _stub_world(monkeypatch, items):
    monkeypatch.setattr(rz, "fetch_feeds", lambda now_ts: (items, {"fed_press": "ok"}))
    monkeypatch.setattr(rz, "read_boards", lambda: {})
    monkeypatch.setattr(rz, "OWNER_CHAT", "111")


def test_run_dms_owner_and_channels_top_item(monkeypatch, sent):
    _stub_world(monkeypatch, [
        mk("FDIC seizes First Valley Bank as regulators begin receivership",
           key="fdic", tier=1.0, source="FDIC")])
    monkeypatch.setattr(rz, "LAB_CHANNEL", "-100999")
    monkeypatch.setattr(rz, "CHANNEL_MODE", "direct")
    assert rz.run(dry=False) == 0
    chats = [p["chat_id"] for m, p in sent if m == "sendMessage"]
    assert 111 in chats and -100999 in chats
    chan = next(p for m, p in sent if p.get("chat_id") == -100999)
    assert "Rissaga marked this" in chan["text"]
    assert "start=lab_rissaga" in json.dumps(chan.get("reply_markup", {}))
    hist = open(os.path.join(rz.STATE_DIR, "history.jsonl")).read()
    assert '"channel_posted": 1' in hist


def test_run_channel_off_when_env_empty(monkeypatch, sent):
    _stub_world(monkeypatch, [
        mk("FDIC seizes First Valley Bank as regulators begin receivership",
           key="fdic", tier=1.0, source="FDIC")])
    monkeypatch.setattr(rz, "LAB_CHANNEL", "")
    monkeypatch.setattr(rz, "CHANNEL_MODE", "direct")
    assert rz.run(dry=False) == 0
    assert [p["chat_id"] for m, p in sent] == [111]


def test_board_events_never_reach_the_channel(monkeypatch, sent):
    rz.save_state("last_boards.json", {"seiche": {"regime": "CALM", "index": 30}})
    monkeypatch.setattr(rz, "fetch_feeds", lambda now_ts: ([], {"fed_press": "ok"}))
    monkeypatch.setattr(rz, "read_boards",
                        lambda: {"seiche": {"regime": "STRAIN", "index": 50}})
    monkeypatch.setattr(rz, "OWNER_CHAT", "111")
    monkeypatch.setattr(rz, "LAB_CHANNEL", "-100999")
    monkeypatch.setattr(rz, "CHANNEL_MODE", "direct")
    assert rz.run(dry=False) == 0
    chats = [p["chat_id"] for m, p in sent]
    assert chats == [111]
    dm = sent[0][1]["text"]
    assert "CALM to STRAIN" in dm


def test_low_signal_run_still_dms(monkeypatch, sent):
    _stub_world(monkeypatch, [])
    monkeypatch.setattr(rz, "LAB_CHANNEL", "-100999")
    assert rz.run(dry=False) == 0
    assert [p["chat_id"] for m, p in sent] == [111]
    assert "Nothing cleared the bar" in sent[0][1]["text"]


# ---------------------------------------------------------------- shape ----
def test_config_shape():
    assert set(rz.ANGLES) == set(rz.BEATS)
    keys = [k for k, _, _ in rz.all_feeds()]
    assert len(keys) == len(set(keys))
    for _, _, tier in rz.all_feeds():
        assert 0 < tier <= 1
    desks = set()
    for beat, spec in rz.BEATS.items():
        assert spec["desk"] in ("SEICHE", "LIQUILENS", "UNDERTOW",
                                "CORPORATE", "REALECON")
        desks.add(spec["desk"])
        for pat, w in spec["terms"]:
            assert 1 <= w <= 6, (beat, pat)
    assert desks == {"SEICHE", "LIQUILENS", "UNDERTOW", "CORPORATE",
                     "REALECON"}, "all five desks must own at least one beat"
    assert set(rz.DESK_NICE) == desks
    assert len(rz.lexicon_version()) == 12


def test_new_desk_beats_score():
    beat, base = rz.beat_score(
        "Commercial paper spreads blow out as corporate defaults mount")
    assert beat == "corporate_stress" and base >= 8
    beat, base = rz.beat_score(
        "Jobless claims jump as credit card delinquencies hit a decade high")
    assert beat == "real_economy" and base >= 8


def test_board_lines_for_corporate_and_real_economy():
    boards = {"corp": {"verdict": "QUIET", "funding": "CALM", "real": "CALM"},
              "india": {"regime": "ALARM", "off": ["prices", "fiscal"], "n": 7},
              "household": {"regime": "WATCH", "dq": "WATCH"}}
    line = rz.board_line("corporate_stress", boards)
    assert "Corporate transmission QUIET" in line and "funding CALM" in line
    line = rz.board_line("real_economy", boards)
    assert "India macro ALARM" in line and "prices" in line
    assert "US household WATCH" in line and "delinquencies WATCH" in line


def test_hermes_mode_exports_latest_and_skips_channel(monkeypatch, sent):
    _stub_world(monkeypatch, [
        mk("FDIC seizes First Valley Bank as regulators begin receivership",
           key="fdic", tier=1.0, source="FDIC")])
    monkeypatch.setattr(rz, "LAB_CHANNEL", "-100999")
    monkeypatch.setattr(rz, "CHANNEL_MODE", "hermes")
    assert rz.run(dry=False) == 0
    assert [p["chat_id"] for m, p in sent] == [111]   # DM only, no channel
    latest = json.load(open(os.path.join(rz.STATE_DIR, "latest.json")))
    assert latest["channel_mode"] == "hermes"
    assert latest["items"][0]["desk"] == "LIQUILENS"
    assert latest["items"][0]["desk_line"]
    assert latest["channel_candidates"] == [0]


def test_direct_mode_caps_channel_posts(monkeypatch, sent):
    _stub_world(monkeypatch, [
        mk("FDIC seizes First Valley Bank as regulators begin receivership",
           key="fdic", tier=1.0, source="FDIC"),
        mk("Emergency meeting called as central bank launches liquidity facility",
           key="fed_press", tier=1.0, source="Federal Reserve"),
        mk("Treasury market dysfunction forces margin calls and fire sales",
           key="bbg_markets", tier=0.8, source="Bloomberg")])
    monkeypatch.setattr(rz, "LAB_CHANNEL", "-100999")
    monkeypatch.setattr(rz, "CHANNEL_MODE", "direct")
    assert rz.run(dry=False) == 0
    channel_msgs = [p for m, p in sent if p.get("chat_id") == -100999]
    assert len(channel_msgs) == rz.MAX_CHANNEL_POSTS
    assert all("desk:" in p["text"] for p in channel_msgs)


def test_no_em_or_en_dashes_in_user_facing_strings():
    """House prose rule: commas, colons or parentheses, never a dash."""
    src_path = os.path.join(_ROOT, "bot", "rissaga.py")
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    body = src.split('"""', 2)[-1]
    for i, line in enumerate(body.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        assert "—" not in line and "–" not in line, f"dash on line {i}: {line!r}"


def test_send_chunks_on_line_seams(sent):
    long = "\n".join(f"line {i} " + "x" * 90 for i in range(80))
    rz.send(42, long)
    assert len(sent) >= 2
    assert all(len(p["text"]) <= 4000 for _, p in sent)
