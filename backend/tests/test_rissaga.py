"""Offline tests for Rissaga, the lab news radar. No network: the one raw
HTTP seam (_http_get) is blocked by default and every surface that needs it
is stubbed per test. State is isolated per test."""

import hashlib
import json
import os
import runpy
import stat
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
    # Shared-channel candidate tests model the production Hermes handoff.
    # Individual fail-quiet tests override this explicitly with ``off``.
    monkeypatch.setattr(rz, "CHANNEL_MODE", "hermes")
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
       source="Bloomberg", link=None):
    if link is None:
        slug = hashlib.sha256(title.encode()).hexdigest()[:16]
        link = f"https://example.com/{slug}"
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


def test_canonical_story_url_removes_only_presentation_noise():
    left = rz.canonical_story_url(
        "https://WWW.Example.com:443/news//stress/?b=2&utm_source=wire&a=1"
        "&fbclid=tracking#section")
    right = rz.canonical_story_url(
        "https://example.com/news/stress?a=1&b=2")

    assert left == right == "https://example.com/news/stress?a=1&b=2"
    assert rz.canonical_story_url(
        "https://news.google.com/rss/articles/item?hl=en&gl=US&oc=5&ceid=US:en"
    ) == "https://news.google.com/rss/articles/item"
    assert rz.canonical_story_url("javascript:alert(1)") == ""
    assert rz.canonical_story_url("https://user:secret@example.com/story") == ""


def test_canonical_story_url_preserves_semantic_query_values():
    first = rz.canonical_story_url("https://example.com/live?edition=morning&id=1")
    second = rz.canonical_story_url("https://example.com/live?edition=morning&id=2")

    assert first != second


def test_same_canonical_url_merges_headline_variants_in_one_sweep():
    link = "https://example.com/private-credit/ares?utm_campaign=daily"
    items = [
        mk("Ares private credit fund scales back after investors reject loan valuations",
           source="Financial Times", link=link, tier=1.0),
        mk("Investors balk at loan valuations as Ares cuts its private credit vehicle",
           source="FT Alphaville",
           link="https://www.example.com:443/private-credit//ares#latest",
           tier=1.0),
    ]

    marked = rz.rank(items, {}, NOW, persist_seen=False)

    assert len(marked) == 1
    assert len(marked[0]["members"]) == 2
    assert marked[0]["n_sources"] == 2
    assert marked[0]["canonical_url"] == (
        "https://example.com/private-credit/ares")
    assert marked[0]["story_id"] == rz._story_id_for_url(
        marked[0]["canonical_url"])


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


def test_url_identity_suppresses_later_headline_edit_at_equal_score():
    link = "https://example.com/global-food-prices"
    first = rz.rank([
        mk("Global food prices hit a three-year high as supply fears build",
           link=link, tier=1.0),
    ], {}, NOW)
    edited = rz.rank([
        mk("Global food prices rise to a three-year high as supply fears build",
           link=link, tier=1.0, ts=NOW),
    ], {}, NOW + 3600)

    assert first[0]["story_id"] == rz._story_id_for_url(
        rz.canonical_story_url(link))
    assert edited == []


def test_title_alias_still_suppresses_cross_outlet_source_swap():
    headline = "Repo market seizes as SOFR spikes overnight"
    first = rz.rank([
        mk(headline, link="https://first.example/story", tier=1.0),
    ], {}, NOW)
    alternate_outlet = rz.rank([
        mk(headline, link="https://second.example/report", tier=1.0, ts=NOW),
    ], {}, NOW + 3600)

    assert first
    assert alternate_outlet == []


def test_rolling_source_url_reuse_observes_the_48_hour_score_gate():
    link = "https://example.com/live/economy"
    first = rz.rank([
        mk("Jobless claims rise as the unemployment rate climbs",
           link=link, tier=1.0),
    ], {}, NOW)
    within_window = rz.rank([
        mk("Retail sales fall as consumer confidence weakens",
           link=link, tier=1.0, ts=NOW),
    ], {}, NOW + 3600)
    after_window = rz.rank([
        mk("Retail sales fall as consumer confidence weakens",
           link=link, tier=1.0, ts=NOW + 49 * 3600 - 60),
    ], {}, NOW + 49 * 3600)

    assert first
    assert within_window == []
    assert len(after_window) == 1
    assert after_window[0]["story_id"] == first[0]["story_id"]


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


def test_multi_desk_routes_keep_best_beat_per_desk():
    text = ("Repo market margin calls trigger a liquidation cascade, "
            "risk-off volatility spike follows")
    routes = rz.route_beats(text)
    by_desk = {route["desk"]: route for route in routes}
    assert len(by_desk) == len(routes)
    assert by_desk["SEICHE"]["beat"] == "plumbing"
    assert by_desk["UNDERTOW"]["beat"] == "market_liquidity"
    assert by_desk["CRYPTO"]["beat"] == "crypto_stress"
    assert by_desk["RIPTIDE"]["beat"] == "risk_timing"
    weak_secondary = rz.route_beats(
        "Great Firewall censorship expands after a routine downgrade")
    assert [route["desk"] for route in weak_secondary] == ["PALIMPSEST"]
    primary_below_floor = rz.route_beats("A routine downgrade")
    assert [route["desk"] for route in primary_below_floor] == ["LIQUILENS"]


def test_palimpsest_and_riptide_beats_and_source_coverage():
    beat, base = rz.beat_score(
        "Great Firewall expands as internet censorship blocks new websites")
    assert beat == "information_controls" and base >= 9
    beat, base = rz.beat_score(
        "VIX jumps in a volatility spike as an equity selloff turns risk-off")
    assert beat == "risk_timing" and base >= 9
    keys = {key for key, _, _ in rz.all_feeds()}
    assert {"ooni", "citizen_lab", "china_digital_times", "rbi_press"} <= keys
    assert {"gnews_information_controls", "gnews_risk_timing"} <= keys


@pytest.mark.parametrize(("headline", "expected"), [
    ("Bitcoin rallies to a fresh high as spot volume surges", "crypto_market_moves"),
    ("Bitcoin ETF inflows accelerate after a new SEC filing", "crypto_policy_flows"),
    ("Coinbase crypto exchange adds a token listing", "crypto_exchange_custody"),
    ("DeFi bridge exploit drains a protocol after a smart contract vulnerability",
     "crypto_defi_security"),
    ("Ethereum upgrade activates on mainnet", "crypto_chain_ecosystems"),
    ("Pump.fun memecoin reaches its bonding curve graduation",
     "crypto_launches_memes"),
    ("Stablecoin payments drive crypto adoption for a new merchant platform",
     "crypto_adoption_business"),
    ("Crypto phishing scam triggers a wallet drain", "crypto_defi_security"),
])
def test_comprehensive_crypto_beats_score(headline, expected):
    beat, base = rz.beat_score(headline)
    assert beat == expected
    assert base >= 5


def test_crypto_sources_and_queries_cover_primary_and_trade_reporting():
    keys = {key for key, _, _ in rz.all_feeds()}
    assert {
        "cftc_press", "cftc_enforcement", "cointelegraph", "decrypt",
        "blockworks", "the_defiant", "ethereum_blog", "solana_news",
        "kraken_blog", "chainalysis",
    } <= keys
    assert {
        "gnews_crypto_market_moves", "gnews_crypto_policy_flows",
        "gnews_crypto_exchange_custody", "gnews_crypto_defi_security",
        "gnews_crypto_chain_ecosystems", "gnews_crypto_launches_memes",
        "gnews_crypto_adoption_business",
    } <= keys


def test_bare_exchange_brand_does_not_promote_its_own_blog_copy():
    marked = rz.rank([
        mk("Kraken publishes its weekly company update", key="kraken_blog",
           tier=0.85, source="Kraken"),
    ], {}, NOW, persist_seen=False)
    payload = rz.latest_payload(marked, {}, NOW_DT)

    assert payload["desk_channel_candidates"]["CRYPTO"] == []


def test_rank_keeps_above_bar_palimpsest_item_outside_global_digest_cap():
    headlines = (
        "FDIC declares Alpha bank failure and begins receivership",
        "Uninsured deposits flee Bravo during a deposit run",
        "Regional bank Charlie needs emergency capital after deposit outflows",
        "Delta enters a moratorium as a bank run sparks bailout talks",
        "Echo reports a capital shortfall and seeks a bank rescue",
        "FHLB liquidity support arrives after Foxtrot deposit flight",
    )
    busy_finance = [
        mk(title, key=f"finance_{index}", tier=1.0,
           source=f"Source {index}", link=f"https://example.com/{index}")
        for index, title in enumerate(headlines)
    ]
    palimpsest = mk(
        "Great Firewall censorship blocks a new independent news website",
        key="citizen_lab", tier=0.7, source="Citizen Lab",
        link="https://example.com/palimpsest",
    )

    marked = rz.rank(busy_finance + [palimpsest], {}, NOW, persist_seen=False)

    assert len(marked) > rz.MAX_MARKED
    assert any(cl["rep"]["link"].endswith("/palimpsest") for cl in marked)
    owner_text = rz.compose(marked, {}, {"citizen_lab": "ok"}, NOW_DT)
    assert owner_text.count("\n6. ") == 0


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


def test_palimpsest_board_falls_back_to_live_ddti(monkeypatch):
    def fake_get(url, timeout=20):
        if url == rz.PALIMPSEST_DDTI:
            return {"feed_health": {"history_window_covered": True,
                                    "roles_missing": []},
                    "ranked": [{"term": "WeChat", "threat": 0.8938}]}
        return None

    monkeypatch.setattr(rz, "get_json", fake_get)
    boards = rz.read_boards()
    assert boards["palimpsest"] == {
        "headline": "top ranked term WeChat, threat 0.8938",
        "health": "history window covered",
    }
    line = rz.board_line("information_controls", boards)
    assert "health history window covered" in line
    assert "top ranked term WeChat" in line


def test_palimpsest_board_reads_signed_osint_snapshot(monkeypatch):
    snapshot = {
        "schema": "palimpsest-nemesis.public-snapshot",
        "health": {"status": "ok", "ready": True},
        "coverage": {
            "observed_source_count": 1,
            "completeness": "not_measured",
        },
        "ddti": {"ranked": [{
            "term": "social media censorship", "threat": 3.56,
        }]},
    }

    monkeypatch.setattr(
        rz, "get_json", lambda url, timeout=20:
        snapshot if url == rz.PALIMPSEST_BOARD else None)
    boards = rz.read_boards()
    assert boards["palimpsest"] == {
        "headline": "top observed term social media censorship, threat 3.56",
        "health": "ok, 1 observed source, coverage not measured",
    }
    line = rz.board_line("information_controls", boards)
    assert "coverage not measured" in line
    assert "top observed term social media censorship" in line


def test_riptide_board_line_states_authority_boundary():
    line = rz.board_line("risk_timing", {})
    assert "news is advisory only" in line
    assert "paper sizing changes only from permitted cues" in line


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


@pytest.mark.parametrize("bad_mode", ["direct", "unknown", ""])
def test_invalid_channel_mode_exits_before_fetch_or_state_mutation(
        monkeypatch, capsys, bad_mode):
    monkeypatch.setattr(rz, "CHANNEL_MODE", bad_mode)
    monkeypatch.setattr(rz, "TOKEN", "test-token")
    monkeypatch.setattr(
        rz, "gather", lambda *_args, **_kwargs: pytest.fail("fetch attempted")
    )

    assert rz.main(["rissaga.py", "--run"]) == 2
    assert "RISSAGA_CHANNEL_MODE must be hermes or off" in capsys.readouterr().err
    assert os.listdir(rz.STATE_DIR) == []


def test_lab_channel_helper_defaults_to_three_core_desks():
    helper = runpy.run_path(os.path.join(
        _ROOT, "bot", "deploy", "lab-channel-post"))
    urls = json.dumps(helper["KEYBOARD"])
    for handle in ("seiche_desk_bot", "LiquiLens_bot",
                   "undertow_LiquiLens_bot"):
        assert handle in urls
    for handle in ("riptide_anake_bot", "palimpsest_watch_bot",
                   "corporate_stress_bot", "real_economy_desk_bot",
                   "liquilens_crypto_bot"):
        assert handle not in urls
    assert "t.me/share/url?" in urls
    operator = json.dumps(helper["OPERATOR_KEYBOARD"])
    for handle in ("seiche_desk_bot", "LiquiLens_bot",
                   "undertow_LiquiLens_bot", "riptide_anake_bot",
                   "palimpsest_watch_bot", "corporate_stress_bot",
                   "real_economy_desk_bot", "liquilens_crypto_bot"):
        assert handle in operator
    contextual = helper["contextual_keyboard"](
        "\U0001f30a <b>Rissaga</b> [CRYPTO \u00b7 policy and flows]"
    )
    buttons = [button for row in contextual for button in row]
    assert len(buttons) == 2
    assert any(button["text"] == "Open + follow Crypto" for button in buttons)
    assert any("t.me/share/url?" in button["url"] for button in buttons)
    fallback = helper["contextual_keyboard"]("an untagged morning card")
    assert fallback == helper["KEYBOARD"]


def test_rissaga_scans_hourly_and_shared_fallback_runs_four_times_daily():
    deploy = os.path.join(_ROOT, "bot", "deploy")
    with open(os.path.join(deploy, "rissaga.timer"), encoding="utf-8") as fh:
        timer = fh.read()
    with open(os.path.join(deploy, "rissaga-channel-fallback.timer"),
              encoding="utf-8") as fh:
        fallback = fh.read()
    assert "OnCalendar=*-*-* *:50:00 UTC" in timer
    for hour in ("03", "09", "15", "21"):
        assert f"OnCalendar=*-*-* {hour}:15:00 UTC" in fallback
    assert timer.count("OnCalendar=") == 1
    assert fallback.count("OnCalendar=") == 4


def test_off_mode_dms_owner_but_authorizes_no_channel_consumer(monkeypatch, sent):
    _stub_world(monkeypatch, [
        mk("FDIC seizes First Valley Bank as regulators begin receivership",
           key="fdic", tier=1.0, source="FDIC")])
    monkeypatch.setattr(rz, "CHANNEL_MODE", "off")
    assert rz.run(dry=False) == 0
    assert [p["chat_id"] for m, p in sent] == [111]
    with open(os.path.join(rz.STATE_DIR, "latest.json"), encoding="utf-8") as fh:
        latest = json.load(fh)
    assert latest["channel_mode"] == "off"
    assert latest["channel_candidates"] == []
    assert all(
        route["channel_candidate"] is False
        for item in latest["items"]
        for route in item["routes"]
    )
    with open(os.path.join(rz.STATE_DIR, rz.OUTBOX_EXPORT), encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh]
    assert records
    assert all(record["shared_candidate"] is False for record in records)


def test_board_events_never_reach_the_channel(monkeypatch, sent):
    rz.save_state("last_boards.json", {"seiche": {"regime": "CALM", "index": 30}})
    monkeypatch.setattr(rz, "fetch_feeds", lambda now_ts: ([], {"fed_press": "ok"}))
    monkeypatch.setattr(rz, "read_boards",
                        lambda: {"seiche": {"regime": "STRAIN", "index": 50}})
    monkeypatch.setattr(rz, "OWNER_CHAT", "111")
    monkeypatch.setattr(rz, "CHANNEL_MODE", "hermes")
    assert rz.run(dry=False) == 0
    chats = [p["chat_id"] for m, p in sent]
    assert chats == [111]
    dm = sent[0][1]["text"]
    assert "CALM to STRAIN" in dm


def test_low_signal_run_still_dms(monkeypatch, sent):
    _stub_world(monkeypatch, [])
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
                                "CORPORATE", "REALECON", "PALIMPSEST",
                                "RIPTIDE", "CRYPTO")
        desks.add(spec["desk"])
        for pat, w in spec["terms"]:
            assert 1 <= w <= 6, (beat, pat)
    assert desks == {"SEICHE", "LIQUILENS", "UNDERTOW", "CORPORATE",
                     "REALECON", "PALIMPSEST", "RIPTIDE", "CRYPTO"}
    assert set(rz.DESK_NICE) == desks
    assert set(rz.DESK_PERSONAS) == desks
    assert set(rz.FALLBACK_COMMENTARY) == set(rz.BEATS)
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
    monkeypatch.setattr(rz, "CHANNEL_MODE", "hermes")
    assert rz.run(dry=False) == 0
    assert [p["chat_id"] for m, p in sent] == [111]   # DM only, no channel
    with open(os.path.join(rz.STATE_DIR, "latest.json"), encoding="utf-8") as fh:
        latest = json.load(fh)
    assert latest["channel_mode"] == "hermes"
    assert latest["items"][0]["desk"] == "LIQUILENS"
    assert latest["items"][0]["desk_line"]
    assert latest["channel_candidates"] == [0]


def test_v2_latest_preserves_primary_and_caps_route_channel_flags():
    marked = rz.rank([
        mk("FDIC seizes First Valley Bank as regulators begin receivership",
           key="fdic", tier=1.0, source="FDIC"),
        mk("Emergency meeting called as central bank launches liquidity facility",
           key="fed_press", tier=1.0, source="Federal Reserve"),
        mk("Treasury market dysfunction forces margin calls and fire sales",
           key="bbg_markets", tier=1.0, source="Bloomberg"),
    ], {}, NOW, persist_seen=False)
    payload = rz.latest_payload(marked, {}, NOW_DT)
    assert payload["schema"] == "rissaga.news.v2"
    assert len(payload["channel_candidates"]) == rz.MAX_CHANNEL_POSTS
    flagged = []
    for index, item in enumerate(payload["items"]):
        assert item["story_id"].startswith("rissaga-")
        assert item["dispatch_id"].startswith("rissaga-dispatch-")
        assert item["beat"] == marked[index]["rep"]["beat"]
        assert item["routes"]
        selected = [r for r in item["routes"] if r["channel_candidate"]]
        assert len(selected) <= 1
        flagged.extend((index, route) for route in selected)
    assert len(flagged) == rz.MAX_CHANNEL_POSTS
    assert {index for index, _ in flagged} == set(payload["channel_candidates"])


def test_shared_channel_slots_are_globally_ranked_but_desk_diverse():
    marked = rz.rank([
        mk("Bitcoin ETF inflows accelerate after a new SEC filing", tier=1.0),
        mk("DeFi bridge exploit drains a protocol after a smart contract vulnerability",
           tier=1.0),
        mk("FDIC seizes First Valley Bank as regulators begin receivership",
           key="fdic", tier=1.0, source="FDIC"),
    ], {}, NOW, persist_seen=False)
    payload = rz.latest_payload(marked, {}, NOW_DT)

    selected_desks = [
        next(route["desk"] for route in payload["items"][index]["routes"]
             if route["channel_candidate"])
        for index in payload["channel_candidates"]
    ]
    assert selected_desks == ["LIQUILENS"]
    assert len(selected_desks) == len(set(selected_desks))
    assert all(
        route["desk"] != "CRYPTO"
        for item in payload["items"]
        for route in item["routes"]
        if route["channel_candidate"]
    )


def test_crypto_product_channel_gets_its_own_ranked_hourly_slice():
    marked = rz.rank([
        mk("Bitcoin rallies to a fresh high as spot volume surges", tier=1.0),
        mk("Bitcoin ETF inflows accelerate after a new SEC filing", tier=1.0),
        mk("Coinbase crypto exchange adds a token listing", tier=1.0),
        mk("DeFi bridge exploit drains a protocol", tier=1.0),
        mk("Pump.fun memecoin reaches its bonding curve graduation", tier=1.0),
    ], {}, NOW, persist_seen=False)
    payload = rz.latest_payload(marked, {}, NOW_DT)

    selected = payload["desk_channel_candidates"]["CRYPTO"]
    assert len(selected) == rz.DESK_CHANNEL_CAPS["CRYPTO"]
    assert all(
        any(route["desk"] == "CRYPTO" and route["desk_channel_candidate"]
            for route in payload["items"][index]["routes"])
        for index in selected
    )
    assert sum(
        route["desk_channel_candidate"]
        for item in payload["items"]
        for route in item["routes"]
        if route["desk"] == "CRYPTO"
    ) == rz.DESK_CHANNEL_CAPS["CRYPTO"]
    assert all(
        not route["channel_candidate"]
        for item in payload["items"]
        for route in item["routes"]
        if route["desk"] == "CRYPTO"
    )


def test_shared_channel_excludes_side_desks_and_junk_crypto_titles():
    marked = rz.rank([
        mk("Weibo hot search deletions rise after a new directive",
           key="cdt", tier=1.0, source="China Digital Times"),
        mk("Aventus Crypto Price Prediction 2026-2030: Can AVT Recover to $1?",
           tier=1.0),
        mk("FDIC seizes First Valley Bank as regulators begin receivership",
           key="fdic", tier=1.0, source="FDIC"),
    ], {}, NOW, persist_seen=False)
    payload = rz.latest_payload(marked, {}, NOW_DT)
    selected_desks = [
        next(route["desk"] for route in payload["items"][index]["routes"]
             if route["channel_candidate"])
        for index in payload["channel_candidates"]
    ]
    assert selected_desks == ["LIQUILENS"]
    assert all(
        route["desk"] not in rz.SHARED_CHANNEL_EXCLUDED_DESKS
        for item in payload["items"]
        for route in item["routes"]
        if route["channel_candidate"]
    )
    assert rz.crypto_channel_title_ok("US Treasury proposes GENIUS Act rule")
    assert not rz.crypto_channel_title_ok(
        "Aventus Crypto Price Prediction 2026-2030: Can AVT Recover to $1?"
    )


def test_crypto_outbox_keeps_dedicated_flag_without_shared_authorization():
    marked = rz.rank([
        mk("Bitcoin ETF inflows accelerate after a new SEC filing", tier=1.0),
    ], {}, NOW, persist_seen=False)
    payload = rz.latest_payload(marked, {}, NOW_DT)

    assert rz.append_outbox(payload, NOW_DT) == 1
    path = os.path.join(rz.STATE_DIR, rz.OUTBOX_EXPORT)
    with open(path, encoding="utf-8") as fh:
        record = json.loads(fh.readline())
    crypto = next(route for route in record["routes"]
                  if route["desk"] == "CRYPTO")
    assert record["shared_candidate"] is False
    assert crypto["channel_candidate"] is False
    assert crypto["desk_channel_candidate"] is True


def test_recent_legacy_outbox_suppresses_url_identity_rollout_replay():
    link = "https://example.com/private-credit/legacy-rollout"
    headline = "Private credit defaults rise as investors reject loan valuations"
    marked = rz.rank([
        mk(headline, link=link, tier=1.0),
    ], {}, NOW, persist_seen=False)
    score = marked[0]["final"]
    legacy_dispatch = "rissaga-dispatch-legacy-title-key"
    legacy = {
        "schema": "rissaga.news.v2",
        "story_id": "rissaga-legacy-title-key",
        "dispatch_id": legacy_dispatch,
        "generated": (NOW_DT - rz.timedelta(hours=1)).isoformat(),
        "expires_at": (NOW_DT + rz.timedelta(hours=23)).isoformat(),
        "title": headline,
        "link": link,
        "score": score,
    }
    path = os.path.join(rz.STATE_DIR, rz.OUTBOX_EXPORT)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(legacy) + "\n")

    edited = rz.rank([
        mk("Investors balk as private credit loan valuations face new defaults",
           link=link, tier=1.0),
    ], {}, NOW, persist_seen=False)
    retry_payload = rz.latest_payload(marked, {}, NOW_DT)
    suppressed_payload = rz.latest_payload(edited, {}, NOW_DT)
    owner_digest = rz.compose(edited, {}, {"fed_press": "ok"}, NOW_DT)

    assert edited == []
    assert suppressed_payload["items"] == []
    assert suppressed_payload["channel_candidates"] == []
    assert headline not in owner_digest
    assert rz.append_outbox(retry_payload, NOW_DT) == 0
    assert retry_payload["items"][0]["dispatch_id"] == legacy_dispatch
    with open(path, encoding="utf-8") as fh:
        assert len(fh.readlines()) == 1


def test_recent_legacy_outbox_allows_url_identity_escalation():
    link = "https://example.com/private-credit/legacy-escalation"
    headline = "Private credit defaults rise after investors reject loan valuations"
    initial = rz.rank([
        mk(headline, link=link, tier=1.0),
    ], {}, NOW, persist_seen=False)
    legacy = {
        "schema": "rissaga.news.v2",
        "story_id": "rissaga-legacy-title-key",
        "dispatch_id": "rissaga-dispatch-legacy-title-key",
        "generated": (NOW_DT - rz.timedelta(hours=1)).isoformat(),
        "expires_at": (NOW_DT + rz.timedelta(hours=23)).isoformat(),
        "title": headline,
        "link": link,
        "score": initial[0]["final"],
    }
    path = os.path.join(rz.STATE_DIR, rz.OUTBOX_EXPORT)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(legacy) + "\n")

    sources = [
        mk("Private credit defaults rise after investors reject loan valuations",
           link=link, tier=1.0, source=source)
        for source in ("FT", "Bloomberg", "Reuters", "WSJ", "CNBC", "Nikkei")
    ]
    escalated = rz.rank(sources, {}, NOW, persist_seen=False)
    payload = rz.latest_payload(escalated, {}, NOW_DT)

    assert len(escalated) == 1
    assert escalated[0]["final"] >= legacy["score"] * rz.SEEN_ESCALATE
    assert escalated[0]["story_id"] == rz._story_id_for_url(
        rz.canonical_story_url(link))
    assert payload["items"][0]["dispatch_id"] != legacy["dispatch_id"]
    assert rz.append_outbox(payload, NOW_DT) == 1
    with open(path, encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh]
    assert len(records) == 2
    assert records[-1]["canonical_url"] == rz.canonical_story_url(link)


def test_legacy_outbox_migration_bridge_expires_after_48_hours():
    link = "https://example.com/private-credit/old-legacy-record"
    legacy = {
        "schema": "rissaga.news.v2",
        "story_id": "rissaga-legacy-title-key",
        "dispatch_id": "rissaga-dispatch-legacy-title-key",
        "generated": (NOW_DT - rz.timedelta(hours=49)).isoformat(),
        "expires_at": (NOW_DT + rz.timedelta(hours=1)).isoformat(),
        "title": "Old private credit defaults story",
        "link": link,
        "score": 10.0,
    }
    path = os.path.join(rz.STATE_DIR, rz.OUTBOX_EXPORT)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(legacy) + "\n")

    marked = rz.rank([
        mk("Private credit defaults rise after investors reject loan valuations",
           link=link, tier=1.0),
    ], {}, NOW, persist_seen=False)

    assert len(marked) == 1
    assert marked[0]["story_id"] == rz._story_id_for_url(
        rz.canonical_story_url(link))


def test_outbox_is_durable_world_readable_and_idempotent():
    marked = rz.rank([
        mk("FDIC seizes First Valley Bank as regulators begin receivership",
           key="fdic", tier=1.0, source="FDIC"),
    ], {}, NOW, persist_seen=False)
    payload = rz.latest_payload(marked, {}, NOW_DT)
    assert rz.append_outbox(payload, NOW_DT) == 1
    assert rz.append_outbox(payload, NOW_DT) == 0
    path = os.path.join(rz.STATE_DIR, rz.OUTBOX_EXPORT)
    with open(path, encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh]
    assert len(records) == 1
    record = records[0]
    assert record["schema"] == "rissaga.news.v2"
    assert record["dispatch_id"].startswith("rissaga-dispatch-")
    assert record["dispatch_id"] == payload["items"][0]["dispatch_id"]
    assert record["story_id"] == payload["items"][0]["story_id"]
    assert record["routes"] == payload["items"][0]["routes"]
    assert record["shared_candidate"] is True
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o644


def test_outbox_allows_escalation_and_expired_reentry():
    marked = rz.rank([
        mk("Repo market seizes as SOFR spikes overnight",
           key="fed_press", tier=1.0, source="Federal Reserve"),
    ], {}, NOW, persist_seen=False)
    payload = rz.latest_payload(marked, {}, NOW_DT)
    same_revision = rz.latest_payload(marked, {}, NOW_DT)
    assert (same_revision["items"][0]["dispatch_id"]
            == payload["items"][0]["dispatch_id"])
    assert rz.append_outbox(payload, NOW_DT) == 1

    retry_at = NOW_DT + rz.timedelta(minutes=30)
    retry = rz.latest_payload(marked, {}, retry_at)
    assert (retry["items"][0]["dispatch_id"]
            != payload["items"][0]["dispatch_id"])
    assert rz.append_outbox(retry, retry_at) == 0
    assert (retry["items"][0]["dispatch_id"]
            == payload["items"][0]["dispatch_id"])

    escalated_at = NOW_DT + rz.timedelta(hours=1)
    marked[0]["final"] *= 1.31
    escalated = rz.latest_payload(marked, {}, escalated_at)
    first_item, escalated_item = payload["items"][0], escalated["items"][0]
    assert escalated_item["story_id"] == first_item["story_id"]
    assert escalated_item["dispatch_id"] != first_item["dispatch_id"]
    first_route = next(r for r in first_item["routes"]
                       if r["channel_candidate"])
    escalated_route = next(r for r in escalated_item["routes"]
                           if r["channel_candidate"])
    first_delivery_key = f"{first_item['dispatch_id']}:{first_route['desk']}"
    same_delivery_key = (f"{same_revision['items'][0]['dispatch_id']}:"
                         f"{first_route['desk']}")
    escalated_delivery_key = (f"{escalated_item['dispatch_id']}:"
                              f"{escalated_route['desk']}")
    assert same_delivery_key == first_delivery_key
    assert escalated_delivery_key != first_delivery_key
    assert rz.append_outbox(escalated, escalated_at) == 1
    assert rz.append_outbox(escalated, escalated_at) == 0

    reentry_at = escalated_at + rz.timedelta(hours=rz.OUTBOX_TTL_H + 1)
    reentry = rz.latest_payload(marked, {}, reentry_at)
    assert (reentry["items"][0]["dispatch_id"]
            != escalated_item["dispatch_id"])
    assert rz.append_outbox(reentry, reentry_at) == 1
    path = os.path.join(rz.STATE_DIR, rz.OUTBOX_EXPORT)
    with open(path, encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh]
    assert len(records) == 3
    assert [record["dispatch_id"] for record in records] == [
        payload["items"][0]["dispatch_id"],
        escalated["items"][0]["dispatch_id"],
        reentry["items"][0]["dispatch_id"],
    ]
    assert len({record["dispatch_id"] for record in records}) == 3
    assert len({record["story_id"] for record in records}) == 1


def test_outbox_repairs_only_an_interrupted_trailing_record():
    marked = rz.rank([
        mk("Repo market seizes as SOFR spikes overnight",
           key="fed_press", tier=1.0, source="Federal Reserve"),
    ], {}, NOW, persist_seen=False)
    first = rz.latest_payload(marked, {}, NOW_DT)
    assert rz.append_outbox(first, NOW_DT) == 1
    path = os.path.join(rz.STATE_DIR, rz.OUTBOX_EXPORT)
    with open(path, "ab") as fh:
        fh.write(b'{"schema":"rissaga.news.v2","story_id":"cut')

    second = json.loads(json.dumps(first))
    second["generated"] = (NOW_DT + rz.timedelta(hours=1)).isoformat(
        timespec="seconds")
    second["items"][0]["story_id"] += "-other"
    second["items"][0]["title"] = "A separate repo market story"
    second["items"][0]["dispatch_id"] = rz.dispatch_id(
        second["items"][0]["story_id"], second["generated"],
        second["items"][0]["score"], second["items"][0]["n_sources"])
    assert rz.append_outbox(second, NOW_DT + rz.timedelta(hours=1)) == 1
    with open(path, encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh]
    assert len(records) == 2
    assert records[0]["story_id"] == first["items"][0]["story_id"]
    assert records[1]["story_id"] == second["items"][0]["story_id"]


def test_run_publishes_both_handoffs_before_seen_commit(monkeypatch, sent):
    _stub_world(monkeypatch, [
        mk("FDIC seizes First Valley Bank as regulators begin receivership",
           key="fdic", tier=1.0, source="FDIC")])
    original_append = rz.append_outbox
    original_export = rz.export_latest
    observed = []

    def checked_append(payload, now):
        assert not os.path.exists(os.path.join(rz.STATE_DIR, "seen.json"))
        observed.append("outbox")
        return original_append(payload, now)

    def checked_export(marked, boards, now, payload=None):
        assert not os.path.exists(os.path.join(rz.STATE_DIR, "seen.json"))
        assert os.path.exists(os.path.join(rz.STATE_DIR, rz.OUTBOX_EXPORT))
        observed.append("latest")
        return original_export(marked, boards, now, payload=payload)

    monkeypatch.setattr(rz, "append_outbox", checked_append)
    monkeypatch.setattr(rz, "export_latest", checked_export)
    assert rz.run(dry=False) == 0
    assert observed == ["outbox", "latest"]
    assert os.path.exists(os.path.join(rz.STATE_DIR, rz.OUTBOX_EXPORT))
    assert os.path.exists(os.path.join(rz.STATE_DIR, rz.LATEST_EXPORT))
    assert os.path.exists(os.path.join(rz.STATE_DIR, "seen.json"))


def test_outbox_failure_leaves_seen_uncommitted(monkeypatch, sent):
    _stub_world(monkeypatch, [
        mk("FDIC seizes First Valley Bank as regulators begin receivership",
           key="fdic", tier=1.0, source="FDIC")])

    def fail(payload, now):
        raise OSError("disk full")

    monkeypatch.setattr(rz, "append_outbox", fail)
    assert rz.run(dry=False) == 1
    assert not os.path.exists(os.path.join(rz.STATE_DIR, "seen.json"))
    assert sent == []


def test_latest_failure_leaves_seen_uncommitted_and_outbox_durable(
        monkeypatch, sent):
    _stub_world(monkeypatch, [
        mk("FDIC seizes First Valley Bank as regulators begin receivership",
           key="fdic", tier=1.0, source="FDIC")])

    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(rz, "export_latest", fail)
    assert rz.run(dry=False) == 1
    assert os.path.exists(os.path.join(rz.STATE_DIR, rz.OUTBOX_EXPORT))
    assert not os.path.exists(os.path.join(rz.STATE_DIR, "seen.json"))
    assert sent == []


def test_current_canonical_outbox_still_retries_latest_after_crash(
        monkeypatch, sent):
    _stub_world(monkeypatch, [
        mk("FDIC seizes First Valley Bank as regulators begin receivership",
           key="fdic", tier=1.0, source="FDIC")])
    original_export = rz.export_latest

    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(rz, "export_latest", fail)
    assert rz.run(dry=False) == 1
    monkeypatch.setattr(rz, "export_latest", original_export)

    assert rz.run(dry=False) == 0
    outbox_path = os.path.join(rz.STATE_DIR, rz.OUTBOX_EXPORT)
    latest_path = os.path.join(rz.STATE_DIR, rz.LATEST_EXPORT)
    with open(outbox_path, encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh]
    with open(latest_path, encoding="utf-8") as fh:
        latest = json.load(fh)

    assert len(records) == 1
    assert records[0]["canonical_url"]
    assert latest["items"][0]["dispatch_id"] == records[0]["dispatch_id"]
    assert os.path.exists(os.path.join(rz.STATE_DIR, "seen.json"))
    assert len(sent) == 1


def test_dry_run_does_not_mutate_seen_boards_or_outbox(monkeypatch, capsys):
    _stub_world(monkeypatch, [
        mk("FDIC seizes First Valley Bank as regulators begin receivership",
           key="fdic", tier=1.0, source="FDIC")])
    states = {
        "seen.json": b'{"sentinel": {"ts": 1}}',
        "last_boards.json": b'{"seiche": {"regime": "CALM"}}',
        rz.OUTBOX_EXPORT: b'{"story_id": "sentinel"}\n',
    }
    for name, body in states.items():
        with open(os.path.join(rz.STATE_DIR, name), "wb") as fh:
            fh.write(body)
    before = {}
    for name in states:
        with open(os.path.join(rz.STATE_DIR, name), "rb") as fh:
            before[name] = fh.read()
    assert rz.run(dry=True) == 0
    capsys.readouterr()
    after = {}
    for name in states:
        with open(os.path.join(rz.STATE_DIR, name), "rb") as fh:
            after[name] = fh.read()
    assert after == before


def test_rissaga_has_no_direct_channel_publisher_surface():
    assert rz.ALLOWED_CHANNEL_MODES == {"hermes", "off"}
    assert not hasattr(rz, "compose_channel")
    assert not hasattr(rz, "post_channel")


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
