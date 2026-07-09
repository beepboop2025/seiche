"""The MCP server: JSON-RPC handshake, tool listing, and tool dispatch.

Every tool is exercised against a canned snapshot (monkeypatched in) so the
suite never touches the network — same discipline as the rest of the gate.
"""

import json

import pytest

from seiche import mcp_server as mcp


# A snapshot shaped like the real assemble.snapshot() output, trimmed to the
# fields the tools actually read.
FAKE_SNAP = {
    "generated_at": "2026-07-10T00:00:00Z",
    "version": "0.2.0-test",
    "faults": [],
    "provenance": {"WALCL": {"fresh": True, "age_h": 3}},
    "engines": {
        "composite": {
            "value": 41.0,
            "regime": "EROSION",
            "coverage_pct": 96,
            "decomposition": [
                {"component": "repo", "score": 55.0, "status": "OK"},
                {"component": "reserves", "score": 30.0, "status": "OK"},
            ],
        },
        "weather": {"crunch_windows": [{"date": "2026-07-31", "reason": "month-end + settlement"}]},
    },
    "deep": {
        "tell": {"ok": True, "tell": 12.0},
        "backtest": {
            "ok": True,
            "sample": {"start": "2018-01-01", "end": "2026-07-01", "n_events": 14},
            "event_capture": {"recall": 0.79, "precision_runs": 0.61, "base_rate": 0.06,
                              "median_lead_d": 42, "runs_hit": 8, "n_alert_runs": 13},
            "orthogonal": {"ok": True, "event_capture": {"recall": 0.69}},
            "episodes": [{"date": "2019-09-17", "episode": "repo spike", "in_sample": True,
                          "first_alert_lead_d": 5, "max_pctl_30d_before": 98}],
            "caveats": ["small event count; CIs are wide"],
        },
        "tidetables": {
            "ok": True,
            "event_odds": {"p": 0.4, "n": 25, "base_rate": 0.06, "lift": 6.7, "ci95": [0.22, 0.61]},
            "novelty": {"verdict": "charted", "pctl": 44},
            "skill": {"ok": True, "brier": 0.05, "brier_climatology": 0.06},
            "analogs": [{"end_date": "2019-09-10", "distance": 0.21, "max_move_5bd_bp": 30.0,
                         "event_within_5bd": True, "episode": "pre-repo-spike"}],
            "fan": [{"p25": 2, "median": 5, "p75": 12}],
            "horizon_bd": 21,
            "spread_now_bp": 4,
        },
        "swell": {"ok": True, "event_by_horizon": {"h5": 0.18, "h10": 0.25, "h21": 0.4},
                  "peak": {"date": "2026-07-31", "bucket": "month-end", "p10": 0.3},
                  "validation": {"ok": True, "auroc": 0.82, "brier": 0.04, "brier_climatology": 0.06}},
        "bathymetry": {"ok": True, "p_by_horizon": {"h1": 0.02, "h5": 0.15, "h10": 0.22},
                       "mfpt_bd": 38, "state_now": {"in_event_bin": False},
                       "validation": {"ok": True, "auroc": 0.8}},
        "ml": {"ok": True, "p_event_5bd": 0.17, "verdict": "elevated but not acute",
               "validation": {"auroc": 0.81, "brier": 0.04}},
        "book": {
            "ok": True,
            "today": {"stance": "risk_off", "rationale": "erosion + month-end",
                      "positions": [{"label": "front-end steepener", "weight": 0.3,
                                     "direction": "long", "vol_ann_pct": 8, "tcost_bp": 2}]},
            "backtest": {"sample": {"start": "2018", "end": "2026"}, "sharpe": 0.9, "verdict": "positive net of costs"},
            "live": {"n_days": 30, "since": "2026-06-10", "cum_return_pct": 1.2, "note": "early"},
            "caveats": [],
        },
        "stacker": {"ok": True, "p_now": 0.19, "published": "0.19", "dispersion_now": 0.03, "verdict": "consensus"},
    },
    "navigator": {"ok": True, "p_event_5bd": 0.2, "asof": "2026-07-10", "rationale": "test"},
}

ASOF_SNAP = {
    "ok": True,
    "asof": "2019-09-17",
    "engines": {
        "composite": {"value": 88.0, "regime": "STRESS", "coverage_pct": 92,
                      "decomposition": [{"component": "repo", "score": 99.0, "status": "OK"}]},
        "weather": {"crunch_windows": []},
    },
    "vintage_note": "reconstructed point-in-time",
}


@pytest.fixture()
def stubbed(monkeypatch):
    monkeypatch.setattr(mcp, "_get_snapshot", lambda force=False: FAKE_SNAP)
    monkeypatch.setattr(mcp, "_get_asof", lambda date: ASOF_SNAP if date == "2019-09-17"
                        else {"ok": False, "reason": "no data"})
    # neutralise cross-test env influence on the public gate
    monkeypatch.setattr(mcp, "PUBLIC_ONLY", False)
    return mcp


def _call(tool, args=None):
    return mcp.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": tool, "arguments": args or {}}})


def _payload(resp):
    """Extract and JSON-decode a tool result's text content (or raw markdown)."""
    text = resp["result"]["content"][0]["text"]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


# ---- protocol handshake -----------------------------------------------------

def test_initialize_negotiates_version_and_advertises_tools():
    resp = mcp.dispatch({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                         "params": {"protocolVersion": "2025-03-26"}})
    r = resp["result"]
    assert r["protocolVersion"] == "2025-03-26"        # echoes the client's version
    assert r["capabilities"]["tools"] == {"listChanged": False}
    assert r["serverInfo"]["name"] == "seiche"
    assert "instructions" in r and "funding" in r["instructions"].lower()


def test_initialize_defaults_version_when_client_omits_it():
    resp = mcp.dispatch({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}})
    assert resp["result"]["protocolVersion"] == mcp.PROTOCOL_VERSION


def test_notification_gets_no_reply():
    assert mcp.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_ping():
    assert mcp.dispatch({"jsonrpc": "2.0", "id": 7, "method": "ping"})["result"] == {}


def test_unknown_method_is_method_not_found():
    resp = mcp.dispatch({"jsonrpc": "2.0", "id": 9, "method": "no/such"})
    assert resp["error"]["code"] == mcp.METHOD_NOT_FOUND


def test_non_jsonrpc_is_invalid_request():
    resp = mcp.dispatch({"id": 1, "method": "ping"})   # missing jsonrpc
    assert resp["error"]["code"] == mcp.INVALID_REQUEST


def test_empty_lists_for_unoffered_capabilities():
    assert mcp.dispatch({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})["result"] == {"resources": []}
    assert mcp.dispatch({"jsonrpc": "2.0", "id": 1, "method": "prompts/list"})["result"] == {"prompts": []}


# ---- tools/list -------------------------------------------------------------

def test_tools_list_has_valid_schemas():
    tools = mcp.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    names = {t["name"] for t in tools}
    assert "funding_stress_now" in names
    assert "replay_asof" in names
    for t in tools:
        assert t["description"] and t["inputSchema"]["type"] == "object"


PUBLIC_TOOLS = {"funding_stress_now", "historical_analogs", "proof_backtest", "data_health"}
PAID_TOOLS = {"funding_stress_forecast", "replay_asof", "desk_brief",
              "positioning_book", "ask_desk"}


def test_public_mode_exposes_exactly_the_free_tools(monkeypatch):
    monkeypatch.setattr(mcp, "PUBLIC_ONLY", True)
    tools = mcp.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == PUBLIC_TOOLS               # the Time Machine / forecast / book stay paid
    assert not (names & PAID_TOOLS)


# ---- tools/call -------------------------------------------------------------

def test_stress_now(stubbed):
    p = _payload(_call("funding_stress_now"))
    assert p["composite"]["regime"] == "EROSION"
    assert p["headline"].startswith("SEICHE 41.0 EROSION")


def test_forecast_merges_three_models(stubbed):
    p = _payload(_call("funding_stress_forecast"))
    assert set(p["sources"]) == {"swell", "bathymetry", "ml"}
    assert p["sources"]["ml"]["p_event_5bd"] == 0.17


def test_analogs(stubbed):
    p = _payload(_call("historical_analogs"))
    assert p["event_odds"]["n"] == 25
    assert p["nearest_analogs"][0]["event_within_5bd"] is True


def test_replay_valid_date(stubbed):
    p = _payload(_call("replay_asof", {"date": "2019-09-17"}))
    assert p["composite"]["regime"] == "STRESS"
    assert p["as_of"] == "2019-09-17"


def test_replay_bad_date_is_tool_error(stubbed):
    resp = _call("replay_asof", {"date": "not-a-date"})
    assert resp["result"]["isError"] is True


def test_replay_missing_data_is_tool_error(stubbed):
    resp = _call("replay_asof", {"date": "1900-01-01"})
    assert resp["result"]["isError"] is True


def test_proof(stubbed):
    p = _payload(_call("proof_backtest"))
    assert p["event_capture"]["recall"] == 0.79
    assert p["caveats"]


def test_book(stubbed):
    p = _payload(_call("positioning_book"))
    assert p["today"]["stance"] == "risk_off"
    assert p["ensemble"]["p_event_5bd"] == 0.19


def test_health(stubbed):
    p = _payload(_call("data_health"))
    assert p["version"] == "0.2.0-test"
    assert p["faults"] == []


def test_brief_returns_markdown(stubbed, monkeypatch):
    monkeypatch.setattr("seiche.brief.render_markdown", lambda snap: "# Seiche brief\nall calm")
    text = _payload(_call("desk_brief"))
    assert text.startswith("# Seiche brief")


def test_unknown_tool_is_invalid_params(stubbed):
    resp = _call("no_such_tool")
    assert resp["error"]["code"] == mcp.INVALID_PARAMS


def test_section_down_is_tool_error(monkeypatch, stubbed):
    broken = dict(FAKE_SNAP)
    broken["deep"] = dict(FAKE_SNAP["deep"], backtest={"ok": False, "reason": "not enough history"})
    monkeypatch.setattr(mcp, "_get_snapshot", lambda force=False: broken)
    resp = _call("proof_backtest")
    assert resp["result"]["isError"] is True
    assert "not enough history" in resp["result"]["content"][0]["text"]


def test_ask_requires_llm(monkeypatch, stubbed):
    monkeypatch.setattr("seiche.ai.ask", _fake_ai_unconfigured)
    resp = _call("ask_desk", {"question": "is repo tight?"})
    assert resp["result"]["isError"] is True


async def _fake_ai_unconfigured(q, snap):
    return {"ok": False, "reason": "LLM endpoint not configured"}
