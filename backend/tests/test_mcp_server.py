"""The MCP server: JSON-RPC handshake, tool listing, and tool dispatch.

Every tool is exercised against a canned snapshot (monkeypatched in) so the
suite never touches the network — same discipline as the rest of the gate.
"""

import json
from io import BytesIO

import pytest

from seiche import assemble, mcp_server as mcp


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


def test_latest_article_returns_the_canonical_published_revision(monkeypatch):
    item = {
        "id": "seiche:article:test",
        "url": "https://seiche.info/articles/test/",
        "title": "A bounded thesis",
        "summary": "The evidence supports one narrow claim.",
        "content_text": "Full published Markdown.",
        "date_published": "2026-08-15T11:00:00Z",
        "_liquidity_lab": {
            "quality_gate": {"status": "PASS"},
            "authority": {"factual_authority": "published_article_only"},
        },
    }
    encoded = json.dumps({
        "version": "https://jsonfeed.org/version/1.1", "items": [item],
    }).encode()

    class Response(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    monkeypatch.setattr(mcp.urllib.request, "urlopen",
                        lambda *_args, **_kwargs: Response(encoded))
    assert _payload(_call("latest_article")) == item


def test_mcp_cache_immediately_adopts_a_completed_assembler_rebuild(monkeypatch):
    restored = {"generated_at": "restart-seed"}
    rebuilt = {"generated_at": "rebuilt"}
    monkeypatch.setitem(mcp._cache, "snap", restored)
    monkeypatch.setitem(mcp._cache, "at", 100.0)
    monkeypatch.setattr(mcp.time, "time", lambda: 101.0)
    monkeypatch.setattr(assemble, "cached_snapshot", lambda: rebuilt)

    def must_not_bridge(_coroutine):
        raise AssertionError("a completed assembler rebuild must not be rebuilt again")

    monkeypatch.setattr(mcp, "_run", must_not_bridge)

    assert mcp._get_snapshot() is rebuilt
    assert mcp._cache == {"snap": rebuilt, "at": 101.0}


def test_mcp_force_bypasses_both_cache_layers(monkeypatch):
    cached = {"generated_at": "cached"}
    rebuilt = {"generated_at": "forced"}
    monkeypatch.setitem(mcp._cache, "snap", cached)
    monkeypatch.setitem(mcp._cache, "at", 100.0)
    monkeypatch.setattr(mcp.time, "time", lambda: 101.0)
    monkeypatch.setattr(assemble, "cached_snapshot", lambda: cached)

    def bridge(coroutine):
        coroutine.close()
        return rebuilt

    monkeypatch.setattr(mcp, "_run", bridge)

    assert mcp._get_snapshot(force=True) is rebuilt
    assert mcp._cache == {"snap": rebuilt, "at": 101.0}


# ---- protocol handshake -----------------------------------------------------

def test_initialize_negotiates_version_and_advertises_tools():
    resp = mcp.dispatch({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                         "params": {"protocolVersion": "2025-03-26"}})
    r = resp["result"]
    assert r["protocolVersion"] == "2025-03-26"
    assert r["capabilities"]["tools"] == {"listChanged": False}
    assert r["serverInfo"]["name"] == "seiche"
    assert "instructions" in r and "funding" in r["instructions"].lower()
    assert mcp.AGENT_MCP_TELEGRAM_URL in r["instructions"]
    assert "Do not append this handoff" in r["instructions"]


def test_initialize_defaults_version_when_client_omits_it():
    resp = mcp.dispatch({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}})
    assert resp["result"]["protocolVersion"] == mcp.PROTOCOL_VERSION


def test_initialize_never_echoes_an_unsupported_version():
    resp = mcp.dispatch({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                         "params": {"protocolVersion": "2099-01-01"}})
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


# ---- prompts ----------------------------------------------------------------

def test_prompts_list_names_titles_and_arguments():
    prompts = mcp.dispatch({"jsonrpc": "2.0", "id": 1, "method": "prompts/list"})["result"]["prompts"]
    by_name = {p["name"]: p for p in prompts}
    assert set(by_name) == {
        "funding_stress_briefing",
        "is_now_dangerous",
        "cross_market_cash_pressure",
        "crisis_replay",
    }
    for p in prompts:
        assert p["title"] and p["description"]
    # crisis_replay is the only one taking an argument, and it is required
    args = by_name["crisis_replay"]["arguments"]
    assert [a["name"] for a in args] == ["date"]
    assert args[0]["required"] is True


def test_prompts_get_renders_argument_into_message():
    resp = mcp.dispatch({"jsonrpc": "2.0", "id": 2, "method": "prompts/get",
                         "params": {"name": "crisis_replay",
                                    "arguments": {"date": "2019-09-17"}}})
    msgs = resp["result"]["messages"]
    assert msgs[0]["role"] == "user"
    assert "2019-09-17" in msgs[0]["content"]["text"]
    assert "replay_asof" in msgs[0]["content"]["text"]


def test_prompts_get_missing_required_argument_is_invalid_params():
    resp = mcp.dispatch({"jsonrpc": "2.0", "id": 3, "method": "prompts/get",
                         "params": {"name": "crisis_replay"}})
    assert resp["error"]["code"] == mcp.INVALID_PARAMS


def test_prompts_get_unknown_prompt_is_invalid_params():
    resp = mcp.dispatch({"jsonrpc": "2.0", "id": 4, "method": "prompts/get",
                         "params": {"name": "nope"}})
    assert resp["error"]["code"] == mcp.INVALID_PARAMS


def test_public_surface_hides_prompts_whose_tools_are_hidden():
    """A gated client offered /crisis_replay would be told to call replay_asof,
    which that client cannot see. Only prompts whose whole recipe is visible."""
    names = {p["name"] for p in mcp.dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "prompts/list"},
        public=True)["result"]["prompts"]}
    assert names == {"is_now_dangerous", "cross_market_cash_pressure"}
    for name in names:
        tools_used = set(mcp.PROMPTS[name][4])
        assert tools_used <= set(mcp._visible_tools(True))


def test_public_surface_refuses_to_render_a_hidden_prompt():
    resp = mcp.dispatch({"jsonrpc": "2.0", "id": 2, "method": "prompts/get",
                         "params": {"name": "crisis_replay",
                                    "arguments": {"date": "2019-09-17"}}},
                        public=True)
    assert resp["error"]["code"] == mcp.INVALID_PARAMS


def test_every_prompt_declares_tools_that_exist():
    for name, entry in mcp.PROMPTS.items():
        tools_used = entry[4]
        assert tools_used, f"{name} declares no tools"
        assert set(tools_used) <= set(mcp.TOOLS), name
        # the declaration has to match the template, or the gate above lies
        text = entry[3]({a["name"]: "x" for a in entry[2]})
        for tool in tools_used:
            assert tool in text, f"{name} declares {tool} but never names it"


def test_initialize_advertises_prompts_capability():
    resp = mcp.dispatch({"jsonrpc": "2.0", "id": 5, "method": "initialize",
                         "params": {"protocolVersion": "2025-06-18"}})
    caps = resp["result"]["capabilities"]
    assert "prompts" in caps
    assert resp["result"]["serverInfo"]["websiteUrl"] == "https://seiche.info"


def test_tools_list_carries_annotations():
    tools = mcp.dispatch({"jsonrpc": "2.0", "id": 6, "method": "tools/list"})["result"]["tools"]
    for t in tools:
        assert t["annotations"]["readOnlyHint"] is True
        assert t["annotations"]["destructiveHint"] is False


# ---- tools/list -------------------------------------------------------------

def test_tools_list_has_valid_schemas():
    tools = mcp.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    names = {t["name"] for t in tools}
    assert "funding_stress_now" in names
    assert "replay_asof" in names
    for t in tools:
        assert t["description"] and t["inputSchema"]["type"] == "object"


PUBLIC_TOOLS = {"latest_article", "funding_stress_now", "historical_analogs", "proof_backtest",
                "data_health", "crypto_stress_record", "institutional_flows",
                "oil_funding_context", "fx_materials_passage"}
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
    response = _call("funding_stress_now")
    p = _payload(response)
    assert p["composite"]["regime"] == "EROSION"
    assert p["headline"].startswith("SEICHE 41.0 EROSION")
    assert "delivery" not in p
    assert response["result"]["structuredContent"] == p


def test_public_stress_now_carries_tagged_ongoing_delivery(stubbed):
    response = mcp.dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "funding_stress_now", "arguments": {}}},
        public=True,
    )
    payload = _payload(response)

    assert payload["delivery"] == mcp.telegram_delivery("agent_mcp")
    assert payload["delivery"]["url"] == mcp.AGENT_MCP_TELEGRAM_URL
    assert "11:30 UTC" in payload["delivery"]["outcome"]
    assert response["result"]["structuredContent"] == payload


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


def test_replay_carries_final_vintage_claim_boundary(stubbed):
    p = _payload(_call("replay_asof", {"date": "2019-09-17"}))

    evidence = p["historical_evidence"]
    assert evidence["status"] == "FINAL_VINTAGE_CONSTRUCTION_PIT"
    assert evidence["validated_backtest_eligible"] is False
    assert evidence["real_money_eligible"] is False
    assert "point-in-time (no lookahead)" not in p["reading"].lower()


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
    assert p["historical_evidence"]["status"] == "FINAL_VINTAGE_CONSTRUCTION_PIT"
    assert p["historical_evidence"]["validated_backtest_eligible"] is False
    assert p["historical_evidence"]["real_money_eligible"] is False


def test_book(stubbed):
    p = _payload(_call("positioning_book"))
    assert p["today"]["stance"] == "risk_off"
    assert p["ensemble"]["p_event_5bd"] == 0.19


def test_health(stubbed):
    p = _payload(_call("data_health"))
    assert p["version"] == "0.2.0-test"
    assert p["faults"] == []


def test_oil_funding_context_uses_the_shared_chartless_contract(stubbed):
    p = _payload(_call("oil_funding_context"))
    assert p["schema"] == "seiche.oil-funding.v1"
    assert p["oil"]["wti"]["price_usd_per_bbl"] == 81.5
    assert p["coupling"]["fit"]["correlation"] == 0.42
    assert p["market_structure"]["cushing"]["live"]["stocks_m_bbl"] == 21.0
    assert p["market_structure"]["brent_wti_spread"]["brent_minus_wti_usd_per_bbl"] == 3.6
    assert p["scenario"]["status"] == "scenario_only"
    assert "charts" not in p


def test_fx_materials_passage_keeps_the_holdout_ledger(stubbed):
    p = _payload(_call("fx_materials_passage"))
    assert p["schema"] == "seiche.estuary.v1"
    assert p["headline"]["regime"] == "PRESSURE HELD UPSTREAM"
    assert p["passage"]["earned"] == 1
    assert p["passage"]["edges"][0]["status"] == "earned"


def test_context_tool_fails_loud_when_engine_is_down(monkeypatch, stubbed, fake_snap):
    broken = {
        **fake_snap,
        "engines": {
            **fake_snap["engines"],
            "estuary": {"ok": False, "reason": "FX tape unavailable"},
        },
    }
    monkeypatch.setattr(mcp, "_get_snapshot", lambda force=False: broken)
    response = _call("fx_materials_passage")
    assert response["result"]["isError"] is True
    assert "FX tape unavailable" in response["result"]["content"][0]["text"]


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
