"""Offline tests for the Seiche Telegram bot — formatters, routing, the
alert-scan hysteresis, state accrual, chunking, pruning. No network: every
HTTP surface (tg_call / api_get / ll_get / _get_json) is monkeypatched.
Pillow-dependent render tests skip themselves when PIL is absent (the box
installs python3-pil; CI need not)."""

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "bot"))

import seiche_bot as bot  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Every test gets its own STATE_DIR and a cold inline cache; nothing
    touches /var/lib and no test can hit the network by accident."""
    monkeypatch.setattr(bot, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(bot, "_inline_cache", {"ts": 0.0, "data": None})
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    yield


@pytest.fixture
def sent(monkeypatch):
    out = []

    def fake_tg_call(method, payload):
        out.append((method, payload))
        if method == "getMe":
            result = {"id": 1, "is_bot": True, "username": "seiche_test_bot"}
        elif method == "sendMessage":
            result = {"message_id": 123}
        else:
            result = True
        return {"ok": True, "result": result}

    monkeypatch.setattr(bot, "tg_call", fake_tg_call)
    return out


def _gauge(idx=42, regime="EROSION", tell=12):
    return {"index": idx, "regime": regime, "tell": tell,
            "coverage_pct": 96, "generated_at": "2026-07-18T10:00:00Z",
            "next_turn": {"date": "2026-07-31", "mode": "month_end",
                          "forecast_bp": 11, "severity": 2},
            "crunch_windows": [{"date": "2026-09-15",
                                "reason": "corporate tax + auction settle",
                                "forecast_reserves_b": 2810,
                                "worst_case_b": 2650}]}


def _pub():
    return {"conclusion": {"line": "Erosion continues; the Tell is positive."},
            "proof": {"n_events": 13, "recall": 0.62,
                      "recall_ci95": [0.36, 0.82], "precision_runs": 0.2,
                      "base_rate": 0.05, "median_lead_d": 42.0,
                      "historical_evidence": {
                          "status": "FINAL_VINTAGE_CONSTRUCTION_PIT",
                          "validated_backtest_eligible": False,
                          "real_money_eligible": False,
                          "reason": "final/current-vintage public history",
                      }}}


def _ll_board(tier="orange"):
    return {"as_of": "2026-07-18",
            "tiers": {"red": 0, "orange": 1, "yellow": 1, "green": 9},
            "rows": [{"slug": "esaf", "name": "ESAF SFB", "tier": tier,
                      "hazard": {"pd_12m": 0.021}},
                     {"slug": "ujjivan", "name": "Ujjivan SFB", "tier": "green",
                     "hazard": {"pd_12m": 0.004}}]}


def _oil_context():
    return {
        "ok": True,
        "as_of": "2026-07-18",
        "oil": {
            "wti": {"price_usd_per_bbl": 81.5, "change_5d_usd": 2.4,
                    "change_20d_pct": 6.2},
            "brent": {"price_usd_per_bbl": 85.1, "change_5d_usd": 2.0},
        },
        "funding": {
            "cp_nonfinancial": {"spread_bp": 24.0, "change_20d_bp": 3.0},
            "cp_financial": {"spread_bp": 31.0, "change_20d_bp": 2.0},
            "sofr_iorb": {"spread_bp": 2.0, "change_20d_bp": 1.0},
        },
        "india": {"inr": {"per_usd": 84.2, "change_20d_pct": 0.7}},
        "coupling": {"fit": {"n": 120, "correlation": 0.42,
                              "slope_bp_per_usd": 0.31}},
        "ballast": {
            "ok": True,
            "headline": {
                "state": "TIGHT",
                "worst_channel_percentile": 91.0,
                "dominant_channel": {"label": "WTI gross mark displacement"},
            },
            "contracts": [{
                "key": "WTI",
                "cash_transfer_scale": {
                    "gross_mark_displacement_usd": 5_400_000_000,
                },
                "positioning": {"top4_paying_side_pct": 23.0},
            }],
        },
        "market_structure": {
            "ok": True,
            "cushing": {
                "working_capacity_m_bbl": 78.410,
                "capacity_asof": "2024-03-31",
                "stress_reference_m_bbl": 20.0,
                "live": {
                    "stocks_m_bbl": 21.0,
                    "fill_of_last_working_capacity_pct": 26.8,
                    "buffer_to_20m_reference_m_bbl": 1.0,
                    "asof": "2026-07-04",
                },
            },
            "brent_wti_spread": {
                "brent_minus_wti_usd_per_bbl": 3.6,
                "average_5d_usd_per_bbl": 3.3,
            },
            "benchmark_architecture": [
                {"benchmark": "WTI", "settlement": "physical delivery at Cushing"},
                {"benchmark": "Brent", "settlement": "cargo-based benchmark complex"},
            ],
            "chokepoints": {
                "rows": [{"name": "Strait of Hormuz", "q1_2026_mbd": 14.6}],
                "latest_period": "1Q26",
            },
            "india": {"crude_import_dependence_pct": 88.5},
        },
    }


def _estuary_context():
    return {
        "ok": True,
        "as_of": "2026-07-18",
        "headline": {
            "regime": "PRESSURE HELD UPSTREAM",
            "upstream_pressure": 72.0,
            "funding_priced": 43.0,
            "transmission_gap": 29.0,
            "fx_pressure": 75.0,
            "materials_pressure": 68.0,
            "coverage_pct": 96.0,
            "verdict": "Upstream cash pressure leads funding pricing.",
        },
        "leaders": {
            "fx": [{"key": "INR", "label": "Indian rupee", "pressure": 89.0}],
            "materials": [{"key": "WTI", "label": "WTI crude", "pressure": 88.0}],
        },
        "passage": {
            "earned": 1,
            "tentative": 1,
            "not_earned": 4,
            "edges": [{"source": "EM dollar", "target": "SOFR−IORB",
                       "lag_bd": 3, "status": "earned", "corr_holdout": 0.31}],
        },
    }


# ------------------------------------------------------------ pure helpers --

def test_spark_shapes():
    assert bot.spark([]) == ""
    assert bot.spark([1.0]) == ""
    s = bot.spark([0.0, 0.5, 1.0])
    assert len(s) == 3 and s[0] == "▁" and s[-1] == "█"
    assert bot.spark([2.0, 2.0]) == "▄▄"
    assert bot.spark([None, 1, 2]) == bot.spark([1, 2])


def test_meter_bounds():
    assert bot.meter(0) == "░" * 20
    assert bot.meter(100) == "█" * 20
    assert bot.meter(None) == "?" * 20
    assert len(bot.meter(37)) == 20


# -------------------------------------------------------------- formatters --

def test_fmt_now_full_absent_and_degraded():
    txt = bot.fmt_now(_gauge(), _pub())
    assert "EROSION" in txt and "42" in txt and "2026-07-31" in txt
    assert "absence is not calm" in bot.fmt_now(None, None)
    g = _gauge(idx=None)
    g["coverage_pct"] = None
    degraded = bot.fmt_now(g, None)
    assert "None" not in degraded


def test_fmt_snap_absent_and_degraded():
    assert "absence is not calm" in bot.fmt_snap(None, None)
    txt = bot.fmt_snap(_gauge(), _pub())
    assert "<pre>" in txt and "EROSION" in txt and "recall" in txt
    assert "FINAL_VINTAGE_CONSTRUCTION_PIT" in txt
    assert "validated eligible NO" in txt
    g = _gauge(idx=None)
    g["next_turn"] = {"date": "2026-07-31"}
    degraded = bot.fmt_snap(g, None)
    assert "None/100" not in degraded and "Nonebp" not in degraded
    assert "?/100" in degraded


def test_fmt_proof_survives_missing_median_lead():
    pub = _pub()
    pub["proof"]["median_lead_d"] = None
    txt = bot.fmt_proof(pub)          # regression: this used to TypeError
    assert "Recall" in txt and "n/a" in txt
    assert "62%" in txt
    assert "FINAL_VINTAGE_CONSTRUCTION_PIT" in txt
    assert "Validated-backtest eligible: <b>NO</b>" in txt
    assert "real-money eligible: <b>NO</b>" in txt


def test_fmt_proof_fails_closed_when_api_omits_evidence_boundary():
    pub = _pub()
    del pub["proof"]["historical_evidence"]
    txt = bot.fmt_proof(pub)
    assert "FINAL_VINTAGE_CONSTRUCTION_PIT" in txt
    assert "Validated-backtest eligible: <b>NO</b>" in txt


def test_latest_article_requires_complete_published_receipt():
    item = {
        "title": "A bounded thesis",
        "summary": "The evidence supports one narrow claim.",
        "url": "https://seiche.info/articles/test/",
        "content_text": "The complete reviewed article body.",
        "_liquidity_lab": {
            "quality_gate": {"status": "PASS"},
            "authority": {
                "factual_authority": "published_article_only",
                "training_allowed": False,
            },
        },
    }
    feed = {"version": "https://jsonfeed.org/version/1.1", "items": [item]}
    assert bot.latest_article(feed) is item

    item["content_text"] = ""
    assert bot.latest_article(feed) is None
    item["content_text"] = "The complete reviewed article body."
    item["_liquidity_lab"]["authority"]["training_allowed"] = True
    assert bot.latest_article(feed) is None
    item["_liquidity_lab"]["authority"]["training_allowed"] = False
    item["_liquidity_lab"]["authority"]["factual_authority"] = "training_output"
    assert bot.latest_article(feed) is None


def test_fmt_odds_ok_and_down():
    assert "did not answer" in bot.fmt_odds(None)
    assert "did not answer" in bot.fmt_odds({"navigator": {"ok": False}})
    txt = bot.fmt_odds({"navigator": {"ok": True, "asof": "2026-07-18",
                                      "p_event_5bd": 0.07,
                                      "caveats": ["few events"],
                                      "method": "analog"}})
    assert "7%" in txt and "few events" in txt


def test_fmt_oil_keeps_observation_and_scenario_boundaries_visible():
    txt = bot.fmt_oil(_oil_context())
    assert "WTI" in txt and "$81.50" in txt
    assert "24.0bp" in txt and "r <b>+0.42</b>" in txt
    assert "Ballast futures-cash ledger" in txt and "$5.40bn" in txt
    assert "Oil market structure" in txt and "Cushing <b>21.0m bbl" in txt
    assert "Brent−WTI <b>+3.60 USD/bbl" in txt
    assert "Strait of Hormuz 14.6mbd" in txt
    assert "not an observed margin call" in txt
    assert "associational" in txt and "editable scenarios" in txt
    assert "absence is not calm" in bot.fmt_oil(None)
    assert "tape stale" in bot.fmt_oil({"ok": False, "reason": "tape stale"})


def test_fmt_estuary_carries_gap_and_holdout_verdict():
    txt = bot.fmt_estuary(_estuary_context())
    assert "PRESSURE HELD UPSTREAM" in txt
    assert "Passage gap <b>+29.0</b>" in txt
    assert "1 earned" in txt and "holdout r +0.31" in txt
    assert "never enters" in txt
    assert "absence is not calm" in bot.fmt_estuary(None)


def test_context_formatters_escape_served_labels_and_verdicts():
    estuary = _estuary_context()
    estuary["headline"]["verdict"] = "<script>bad</script>"
    estuary["leaders"]["fx"][0]["label"] = "<b>fake</b>"
    txt = bot.fmt_estuary(estuary)
    assert "<script>" not in txt and "&lt;script&gt;" in txt
    assert "<b>fake</b>" not in txt and "&lt;b&gt;fake&lt;/b&gt;" in txt


def test_fmt_institutions_defensive_rows():
    txt = bot.fmt_institutions(_ll_board())
    assert "ESAF" in txt
    broken = _ll_board()
    del broken["rows"][0]["hazard"]       # the other desk's schema drifts
    txt = bot.fmt_institutions(broken)
    assert "ESAF" in txt and "n/a" in txt
    assert "did not answer" in bot.fmt_institutions(None)


def test_fmt_ask_variants():
    assert "did not answer" in bot.fmt_ask(None)
    txt = bot.fmt_ask({"answer": "Reserves fell.", "citations": ["h41"]})
    assert "Reserves fell." in txt and "h41" in txt
    assert bot.fmt_ask("plain") == "plain"


def test_html_escaping_of_served_text():
    g = _gauge()
    g["crunch_windows"][0]["reason"] = "<script>alert(1)</script>"
    txt = bot.fmt_now(g, None)
    assert "<script>" not in txt and "&lt;script&gt;" in txt


# --------------------------------------------------------------- cross-desk --

def test_tandem_class_grid():
    assert bot._tandem_class(2, 3) == 3
    assert bot._tandem_class(2, 2) == 2
    assert bot._tandem_class(2, 0) == 1
    assert bot._tandem_class(0, 2) == 1
    assert bot._tandem_class(0, 0) == 0


def test_fmt_tandem_partial_desks():
    both = bot.fmt_tandem(_gauge(regime="STRAIN"), _ll_board("red"))
    assert "dangerous quadrant" in both
    assert "did not answer" in bot.fmt_tandem(None, _ll_board())
    assert "Neither desk answered" in bot.fmt_tandem(None, None)


# ------------------------------------------------------ history + sparkline --

def test_gauge_history_accrues_and_caps():
    hist = {f"2026-{m:02d}-{d:02d}": {"index": m + d, "regime": "CALM", "tell": 0}
            for m in range(1, 6) for d in range(1, 27)}
    bot.save_state("gauge_history.json", hist)
    bot.gauge_history_append(_gauge(idx=99))
    hist = bot.load_state("gauge_history.json", {})
    assert len(hist) <= 120
    assert bot.gauge_spark()


def test_gauge_history_none_is_noop():
    bot.gauge_history_append(None)
    bot.gauge_history_append({"index": None})
    assert bot.load_state("gauge_history.json", {}) == {}


# ------------------------------------------------- alert decision (pure) ----

def _st(regime="CALM", index=20, ts=0.0):
    return {"seen": {"regime": regime, "index": index},
            "alerted": {"regime": regime, "index": index, "ts": ts},
            "pending": {}}


def test_alert_decision_seeds_quietly():
    lines, ns = bot._alert_decision({}, {"regime": "CALM", "index": 20}, 100.0)
    assert lines == [] and ns["alerted"]["regime"] == "CALM"


def test_alert_decision_escalation_pings_inside_cooldown():
    st = _st("CALM", 20, ts=1000.0)
    lines, _ = bot._alert_decision(st, {"regime": "EROSION", "index": 30}, 1030.0)
    assert lines and "Regime flip" in lines[0]


def test_alert_decision_deescalation_needs_dwell_and_cooldown():
    st = _st("EROSION", 30, ts=1000.0)
    g = {"regime": "CALM", "index": 20}
    lines, st2 = bot._alert_decision(st, g, 1000.0 + bot.ALERT_COOLDOWN_S + 1)
    assert not lines and st2["pending"] == {"regime": "CALM", "n": 1}
    lines, _ = bot._alert_decision(st2, g, 1000.0 + bot.ALERT_COOLDOWN_S + 1800)
    assert lines and "Regime flip" in lines[0]


def test_alert_decision_boundary_oscillation_stays_quiet():
    st = _st("EROSION", 27, ts=0.0)
    t, pings = 10 * 3600.0, []
    for regime, idx in [("CALM", 24), ("EROSION", 26)] * 3:
        lines, st = bot._alert_decision(st, {"regime": regime, "index": idx}, t)
        pings += lines
        t += 1800
    assert pings == []           # the flap failure mode, pinned dead


def test_alert_decision_jump_needs_drift_and_cooldown():
    # +9 in one scan but net +1 vs the last announced level: jitter, silence
    st = {"seen": {"regime": "EROSION", "index": 30},
          "alerted": {"regime": "EROSION", "index": 38, "ts": 0.0},
          "pending": {}}
    lines, _ = bot._alert_decision(st, {"regime": "EROSION", "index": 39}, 1e6)
    assert not lines
    # a genuine move clears both gates
    lines, _ = bot._alert_decision(_st("EROSION", 30), {"regime": "EROSION", "index": 39}, 1e6)
    assert lines and "moved" in lines[0]


# --------------------------------------------------- alert scan (wired) -----

def _prime(regime="CALM", index=20, ts=0.0):
    bot.save_state("alert_state.json", _st(regime, index, ts))
    bot.save_state("subscribers.json", {"7": {"since": "x"}})


def test_alert_scan_fires_on_escalation(monkeypatch, sent):
    _prime("CALM", 20)
    monkeypatch.setattr(bot, "api_get", lambda p: _gauge(idx=44, regime="STRAIN"))
    bot.run_alert_scan()
    msgs = [p["text"] for m, p in sent if m == "sendMessage"]
    assert any("Regime flip" in t for t in msgs)
    assert bot.load_state("alert_state.json", {})["alerted"]["regime"] == "STRAIN"


def test_alert_scan_quiet_when_unchanged(monkeypatch, sent):
    _prime("EROSION", 42)
    monkeypatch.setattr(bot, "api_get", lambda p: _gauge(idx=43))
    bot.run_alert_scan()
    assert not [m for m in sent if m[0] == "sendMessage"]


def test_alert_scan_gauge_down_keeps_state(monkeypatch, sent):
    _prime("EROSION", 42)
    monkeypatch.setattr(bot, "api_get", lambda p: None)
    bot.run_alert_scan()
    assert bot.load_state("alert_state.json", {})["alerted"]["regime"] == "EROSION"
    assert not [m for m in sent if m[0] == "sendMessage"]


def test_alert_scan_migrates_flat_state(monkeypatch, sent):
    bot.save_state("alert_state.json", {"regime": "CALM", "index": 20})
    bot.save_state("subscribers.json", {"7": {"since": "x"}})
    monkeypatch.setattr(bot, "api_get", lambda p: _gauge(idx=50, regime="STRAIN"))
    bot.run_alert_scan()
    msgs = [p["text"] for m, p in sent if m == "sendMessage"]
    assert any("Regime flip" in t for t in msgs)
    assert "seen" in bot.load_state("alert_state.json", {})


def test_alert_scan_retries_after_failed_delivery(monkeypatch):
    _prime("CALM", 20)
    monkeypatch.setattr(bot, "api_get", lambda p: _gauge(idx=44, regime="STRAIN"))
    monkeypatch.setattr(bot, "tg_call", lambda m, p: None)   # network down
    bot.run_alert_scan()
    st = bot.load_state("alert_state.json", {})
    assert st["alerted"]["regime"] == "CALM"    # not marked announced
    out = []
    monkeypatch.setattr(bot, "tg_call", lambda m, p: out.append((m, p)) or {"ok": True})
    bot.run_alert_scan()                        # next scan delivers it
    assert any("Regime flip" in p["text"] for m, p in out if m == "sendMessage")


# ------------------------------------------------------------- delivery -----

def test_send_chunks_on_line_seams(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "tg_call", lambda m, p: calls.append(p) or {"ok": True})
    text = "\n".join(f"line {i} " + "x" * 60 for i in range(120))
    bot.send(1, text, keyboard=[[{"text": "b", "callback_data": "/now"}]])
    assert len(calls) >= 2
    assert all(len(c["text"]) <= 4000 for c in calls)
    assert all(c["text"].startswith("line") for c in calls)
    assert "reply_markup" in calls[-1]
    assert all("reply_markup" not in c for c in calls[:-1])


def test_send_retries_plain_on_parse_error(monkeypatch):
    calls = []

    def fake(method, payload):
        calls.append((method, payload))
        if len(calls) == 1:
            return {"ok": False, "error_code": 400,
                    "description": "can't parse entities"}
        return {"ok": True}

    monkeypatch.setattr(bot, "tg_call", fake)
    res = bot.send(1, "<b>broken")
    assert len(calls) == 2 and "parse_mode" not in calls[1][1]
    assert res == {"ok": True}


def test_send_all_prunes_blocked(monkeypatch):
    bot.save_state("subscribers.json", {"1": {}, "2": {}})

    def fake(method, payload):
        if payload.get("chat_id") == 1:
            return {"ok": False, "error_code": 403,
                    "description": "bot was blocked by the user"}
        return {"ok": True}

    monkeypatch.setattr(bot, "tg_call", fake)
    n = bot._send_all({"1": {}, "2": {}}, "hi")
    assert n == 1
    assert bot.load_state("subscribers.json", {}) == {"2": {}}


# ------------------------------------------------------------ dispatching ---

def test_plain_text_routes_to_ask(monkeypatch, sent):
    urls = []
    monkeypatch.setattr(bot, "_get_json",
                        lambda url, timeout=25, tries=2:
                        urls.append(url) or {"answer": "grounded", "citations": []})
    bot.handle(7, "why is the regime EROSION", "private")
    assert any("/api/ask" in u for u in urls)
    msgs = [p["text"] for m, p in sent if m == "sendMessage"]
    assert any("grounded" in t for t in msgs)


def test_group_plain_text_stays_silent(sent):
    bot.handle(7, "hello everyone", "group")
    assert sent == []            # command discipline: no help-wall spam


def test_foreign_bot_command_ignored(sent):
    bot.handle(7, "/now@LiquiLens_bot", "group")
    assert sent == []


def test_own_suffix_command_answered(monkeypatch, sent):
    monkeypatch.setattr(bot, "api_get",
                        lambda p: _gauge() if "gauge" in p else _pub())
    bot.handle(7, "/now@seiche_desk_bot", "group")
    msgs = [p["text"] for m, p in sent if m == "sendMessage"]
    assert any("funding stress" in t for t in msgs)


@pytest.mark.parametrize(
    ("command", "path", "needle", "payload"),
    (
        ("/oil", "/api/oil-funding", "Oil × Funding", _oil_context()),
        ("/estuary", "/api/estuary", "PRESSURE HELD UPSTREAM", _estuary_context()),
    ),
)
def test_context_commands_use_the_compact_public_routes(
    monkeypatch, sent, command, path, needle, payload
):
    requested = []

    def fake_api(candidate):
        requested.append(candidate)
        return payload

    monkeypatch.setattr(bot, "api_get", fake_api)
    bot.handle(7, command, "private")
    assert requested == [path]
    msgs = [p["text"] for method, p in sent if method == "sendMessage"]
    assert any(needle in text for text in msgs)


def test_start_subscribes_and_records_ref(monkeypatch, sent):
    monkeypatch.setattr(bot, "api_get",
                        lambda p: _gauge() if "gauge" in p else _pub())
    bot.handle(9, "/start ref_hnwave", "private")
    assert "9" in bot.load_state("subscribers.json", {})
    with open(bot._state_path("leads.jsonl"), encoding="utf-8") as fh:
        assert "ref_hnwave" in fh.read()


def test_stop_unsubscribes(sent):
    bot.save_state("subscribers.json", {"9": {"since": "x"}})
    bot.handle(9, "/stop", "private")
    assert "9" not in bot.load_state("subscribers.json", {})


def test_keyboard_shapes():
    for cmd in ("/start", "/now", "/snap", "/odds", "/oil", "/estuary", "/share",
                "/where"):
        kb = bot.keyboard_for(cmd)
        assert kb and all(("callback_data" in b) or ("url" in b)
                          for row in kb for b in row)
    assert bot.keyboard_for("/nope") is None


def test_where_card_names_the_three_public_doors():
    assert "t.me/LiquidityLabDesk" in bot.WHERE_CARD
    assert "@seiche_desk_bot" in bot.WHERE_CARD
    assert "@LiquiLens_bot" in bot.WHERE_CARD
    assert "@undertow_LiquiLens_bot" in bot.WHERE_CARD
    assert "liquilens.in/access/" in bot.WHERE_CARD
    assert "Public goods for grants" in bot.WHERE_CARD
    assert "not this channel" in bot.WHERE_CARD
    assert bot.PUBLIC_GOOD_FUND in bot.WHERE_CARD
    assert bot.EVIDENCE_CHANNEL in bot.WHERE_CARD
    assert "LiquidityCryptoDesk" not in bot.WHERE_CARD
    assert "—" not in bot.WHERE_CARD
    assert "–" not in bot.WHERE_CARD
    assert "@real_economy_desk_bot" not in bot.LAB_CHANNEL_PIN
    assert "@EvidenceSignalDesk" not in bot.LAB_CHANNEL_PIN
    assert "@LiquiLens_bot" in bot.LAB_CHANNEL_PIN
    assert "@undertow_LiquiLens_bot" in bot.LAB_CHANNEL_PIN
    assert "@LiquidityLabTalk" in bot.LAB_CHANNEL_PIN
    assert "@LiquidityLabTalk" in bot.LAB_CHANNEL_ABOUT
    assert len(bot.LAB_CHANNEL_ABOUT) <= 255


def test_where_command_sends_the_three_door_card(sent):
    bot.handle(9, "/where", "private")
    method, payload = sent[-1]
    assert method == "sendMessage"
    assert "LiquidityLabDesk" in payload["text"]
    assert any(
        button.get("url") == "https://liquilens.in/access/"
        for row in payload["reply_markup"]["inline_keyboard"]
        for button in row
    )
    assert any(
        button.get("url") == bot.PUBLIC_GOOD_FUND
        for row in payload["reply_markup"]["inline_keyboard"]
        for button in row
    )
    assert not any(
        "LiquidityCryptoDesk" in (button.get("url") or "")
        for row in payload["reply_markup"]["inline_keyboard"]
        for button in row
    )


# ---------------------------------------------------------------- letter ----

def test_daily_letter_carries_scuttlebutt_flag(monkeypatch):
    overview = {"navigator": {"ok": True, "p_event_5bd": 0.04},
                "engines": {"scuttlebutt": {
                    "flags": ["repo chatter surging (z 2.3 vs own baseline)"]}}}

    def fake_api(p):
        if "gauge" in p:
            return _gauge()
        if "public" in p:
            return _pub()
        return overview

    monkeypatch.setattr(bot, "api_get", fake_api)
    monkeypatch.setattr(bot, "ll_get", lambda p: _ll_board())
    monkeypatch.setattr(bot, "_get_json", lambda url, timeout=25, tries=2: [])
    txt = bot.fmt_daily_letter()
    assert "chatter surging" in txt and "display only" in txt


# ------------------------------------------------------------- image card ---

def test_render_snap_card_is_optional():
    try:
        import PIL  # noqa: F401
    except ImportError:
        assert bot.render_snap_card(_gauge()) is None
        return
    png = bot.render_snap_card(_gauge())
    assert png and png[:4] == b"\x89PNG"
    assert bot.render_snap_card(None) is None
    assert bot.render_snap_card({"index": None}) is None
    g = _gauge()
    g["next_turn"] = {"date": "2026-07-31"}     # bp/severity absent
    assert bot.render_snap_card(g)


# ----------------------------------------------------------------- inline ---

def test_answer_inline_serves_filters_and_caches(monkeypatch):
    fetches, calls = [], []

    def fake_api(p):
        fetches.append(p)
        if "gauge" in p:
            return _gauge()
        if "public" in p:
            return _pub()
        return {"navigator": {"ok": False}}

    monkeypatch.setattr(bot, "api_get", fake_api)
    monkeypatch.setattr(bot, "tg_call",
                        lambda m, p: calls.append((m, p)) or {"ok": True})
    bot.answer_inline({"id": "iq1", "query": ""})
    m, p = calls[-1]
    assert m == "answerInlineQuery"
    ids = [r["id"] for r in p["results"]]
    assert "snap" in ids and "now" in ids
    for r in p["results"]:
        assert len(r["input_message_content"]["message_text"]) <= 4000
    n_first = len(fetches)
    bot.answer_inline({"id": "iq2", "query": "proof"})
    assert len(fetches) == n_first          # served from the 60s cache
    assert [r["id"] for r in calls[-1][1]["results"]] == ["proof"]


def test_setup_registers_cross_market_commands(sent):
    bot.run_setup()

    command_call = next(payload for method, payload in sent
                        if method == "setMyCommands")
    commands = {item["command"] for item in command_call["commands"]}
    assert {"oil", "estuary"} <= commands


def test_setup_discovery_metadata_respects_telegram_limits(sent):
    """Keep setup payloads inside Bot API profile-field boundaries."""
    bot.run_setup()
    short = next(payload["short_description"] for method, payload in sent
                 if method == "setMyShortDescription")
    description = next(payload["description"] for method, payload in sent
                       if method == "setMyDescription")
    assert len(short) <= 120
    assert len(description) <= 512


# ------------------------------------------- Liquidity Lab channel publishing


def test_post_channel_is_off_without_env(monkeypatch, sent):
    """No LAB_CHANNEL_ID means no publishing. Guards every laptop run and
    every timer on a box that has not opted in."""
    monkeypatch.setattr(bot, "LAB_CHANNEL", "")
    assert bot.post_channel("hello", "lab_letter") is None
    assert sent == []


def test_post_channel_carries_deep_links_with_ref(monkeypatch, sent):
    monkeypatch.setattr(bot, "LAB_CHANNEL", "-1004297805949")
    assert bot.post_channel("the read", "lab_letter") == 123
    m, p = sent[-1]
    assert m == "sendMessage"
    assert p["chat_id"] == -1004297805949
    assert "the read" in p["text"]
    urls = [b["url"] for row in p["reply_markup"]["inline_keyboard"]
            for b in row]
    # One letter, one conversion decision. Sibling discovery follows inside
    # the bot rather than competing with the attributed subscription action.
    assert urls == ["https://t.me/seiche_desk_bot?start=lab_letter"]
    assert "11:30 UTC" in p["text"]


def test_post_channel_survives_a_rejection(monkeypatch):
    monkeypatch.setattr(bot, "LAB_CHANNEL", "-1004297805949")
    monkeypatch.setattr(bot, "tg_call",
                        lambda m, p: {"ok": False, "error_code": 403,
                                      "description": "bot is not a member"})
    assert bot.post_channel("x", "lab_alert") is None


def test_letter_publishes_at_zero_subscribers(monkeypatch, sent):
    """The bootstrap case: the channel must carry the letter on a day when
    nobody has subscribed yet, which is precisely when it earns subscribers."""
    monkeypatch.setattr(bot, "LAB_CHANNEL", "-1004297805949")
    monkeypatch.setattr(bot, "fmt_daily_letter", lambda: "today's letter")
    monkeypatch.setattr(bot, "fmt_channel_letter", lambda: "today's letter")
    bot.run_letter()
    channel_posts = [p for _, p in sent if p.get("chat_id") == -1004297805949]
    assert len(channel_posts) == 1, "conversion work must not increase post volume"
    assert "today's letter" in channel_posts[0].get("text", "")
