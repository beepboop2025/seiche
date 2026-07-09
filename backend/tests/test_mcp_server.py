"""The MCP server: JSON-RPC handshake, tool listing, and tool dispatch.

Every tool is exercised against a canned snapshot (monkeypatched in) so the
suite never touches the network — same discipline as the rest of the gate.
"""

import json

import pytest

from seiche import mcp_server as mcp


@pytest.fixture()
def stubbed(monkeypatch, fake_snap, asof_snap):
    # canned snapshots (from conftest) so no test touches the network
    monkeypatch.setattr(mcp, "_get_snapshot", lambda force=False: fake_snap)
    monkeypatch.setattr(mcp, "_get_asof", lambda date: asof_snap if date == "2019-09-17"
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


def test_forecast_merges_all_sources(stubbed):
    p = _payload(_call("funding_stress_forecast"))
    assert set(p["sources"]) == {"swell", "bathymetry", "ml",
                                 "markov", "oujump", "montecarlo"}
    assert p["sources"]["ml"]["p_event_5bd"] == 0.17
    assert p["sources"]["markov"]["current_regime"] == "EROSION"
    assert p["sources"]["montecarlo"]["level_now"] == 44.7


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


def test_section_down_is_tool_error(monkeypatch, stubbed, fake_snap):
    broken = dict(fake_snap)
    broken["deep"] = dict(fake_snap["deep"], backtest={"ok": False, "reason": "not enough history"})
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
