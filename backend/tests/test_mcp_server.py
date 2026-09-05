"""The MCP server: JSON-RPC handshake, tool listing, and tool dispatch.

Every tool is exercised against a canned snapshot (monkeypatched in) so the
suite never touches the network — same discipline as the rest of the gate.
"""

import json
from datetime import datetime
from io import BytesIO

import pytest

from seiche import assemble
from seiche import mcp_server as mcp


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 8, 21, 12, 0, tzinfo=tz)


@pytest.fixture()
def stubbed(monkeypatch, fake_snap, asof_snap):
    # canned snapshots (from conftest) so no test touches the network
    monkeypatch.setattr(mcp, "_get_snapshot", lambda force=False: fake_snap)
    monkeypatch.setattr(mcp, "_get_completed_snapshot", lambda: fake_snap)
    monkeypatch.setattr(
        mcp,
        "_get_asof",
        lambda date: (
            asof_snap if date == "2019-09-17" else {"ok": False, "reason": "no data"}
        ),
    )
    # neutralise cross-test env influence on the public gate
    monkeypatch.setattr(mcp, "PUBLIC_ONLY", False)

    monkeypatch.setattr(mcp, "datetime", FrozenDateTime)
    return mcp


def _call(tool, args=None):
    return mcp.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args or {}},
        }
    )


def _public_call(tool, args=None):
    return mcp.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args or {}},
        },
        public=True,
    )


def _payload(resp):
    """Extract and JSON-decode a tool result's text content (or raw markdown)."""
    text = resp["result"]["content"][0]["text"]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _money_market_engine():
    sections = []
    for index, section_id in enumerate(mcp.MONEY_MARKET_SECTION_IDS):
        sections.append(
            {
                "id": section_id,
                "label": section_id.replace("_", " ").title(),
                "plain_language": f"Simple explanation for {section_id}.",
                "status": "available" if index != 3 else "partial",
                "metrics": [
                    {
                        "id": f"{section_id}.synthetic",
                        "label": f"Synthetic {section_id} metric",
                        "value": 2.5 + index,
                        "unit": "bp",
                        "asof": "2026-08-20",
                        "cadence": "daily",
                        "status": "available",
                        "freshness": "fresh",
                        "source": "Synthetic official source",
                        "explanation": "A compact self-describing test metric.",
                        "formula": "100 x (left rate - right rate)",
                        "alignment": {
                            "method": "exact_date_inner_join",
                            "no_forward_fill": True,
                        },
                    }
                ],
            }
        )
    return {
        "ok": True,
        "schema": mcp.MONEY_MARKET_SCHEMA,
        "asof": "2026-08-20",
        "context_only": True,
        "plain_language": "Overnight cash is orderly, with one partial section.",
        "quant_read": "The worst observed channel ranks at its own-history p72.",
        "strongest_signal": {
            "metric_id": "repo_segments.synthetic",
            "stress_percentile": 72.0,
            "use": "context only; not causal, predictive, or directly tradable",
        },
        "countercase": {
            "metric_id": "policy_corridor.synthetic",
            "reading": "The policy corridor is the calmer independent channel.",
        },
        "regime": {
            "state": "WATCH",
            "worst_stress_percentile": 72.0,
            "indicators": [
                {
                    "metric_id": "repo_segments.synthetic",
                    "label": "Synthetic repo segments metric",
                    "value": 4.5,
                    "unit": "bp",
                    "asof": "2026-08-20",
                    "stress_percentile": 72.0,
                },
                {
                    "metric_id": "policy_corridor.synthetic",
                    "label": "Synthetic policy corridor metric",
                    "value": 2.5,
                    "unit": "bp",
                    "asof": "2026-08-20",
                    "stress_percentile": 42.0,
                },
            ],
            "excluded_indicators": [],
            "status": "descriptive_context_only_not_forecast_probability_or_trade_signal",
        },
        "coverage": {"coverage_pct": 92.0, "status": "partial"},
        "freshness": {
            "desk_asof": "2026-08-20",
            "evaluation_asof": "2026-08-20",
            "status_counts": {"fresh": 8},
        },
        "sections": sections,
        "charts": {"sentinel": {"rows": ["NEVER RETURN MCP CHART"]}},
        "methodology": {
            "alignment": "exact observation-date intersections; no forward fill",
            "regime": "worst configured empirical stress percentile",
        },
        "formulas": [
            {
                "id": "sofr_iorb",
                "expression": "100 x (SOFR - IORB)",
                "alignment": "exact observation date",
            }
        ],
        "caveats": [
            "Descriptive context only; not a forecast, causal model, or trade signal."
        ],
        "source_metadata": [
            {
                "id": "fred_sofr",
                "publisher": "Federal Reserve Bank of New York",
                "series": "SOFR",
                "asof": "2026-08-20",
                "freshness": "fresh",
            }
        ],
        "sources": [{"duplicate": "must not be needed"}],
        "legal_notices": [{"terms_url": "https://example.test/terms"}],
    }


def _snapshot_with_money_market(fake_snap, engine=None):
    desk = _money_market_engine() if engine is None else engine
    return {
        **fake_snap,
        "engines": {**fake_snap["engines"], "money_market": desk},
    }


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
            "authority": {
                "factual_authority": "published_article_only",
                "training_allowed": False,
            },
        },
    }
    encoded = json.dumps(
        {
            "version": "https://jsonfeed.org/version/1.1",
            "items": [item],
        }
    ).encode()

    class Response(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    monkeypatch.setattr(
        mcp.urllib.request, "urlopen", lambda *_args, **_kwargs: Response(encoded)
    )
    assert _payload(_call("latest_article")) == item

    item["_liquidity_lab"]["authority"]["training_allowed"] = True
    encoded = json.dumps(
        {
            "version": "https://jsonfeed.org/version/1.1",
            "items": [item],
        }
    ).encode()
    assert _call("latest_article")["result"]["isError"] is True


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


def test_public_money_market_cold_stdio_restores_completed_snapshot_only(
    monkeypatch, fake_snap
):
    from seiche.engines import money_market

    persisted = _snapshot_with_money_market(fake_snap)
    state = {"snapshot": None}
    restore_calls = []
    monkeypatch.setitem(mcp._cache, "snap", None)
    monkeypatch.setitem(mcp._cache, "at", 0.0)
    monkeypatch.setattr(mcp, "datetime", FrozenDateTime)
    monkeypatch.setattr(assemble, "cached_snapshot", lambda: state["snapshot"])
    monkeypatch.setattr(assemble, "cached_snapshot_was_rebuilt", lambda: False)

    def restore():
        restore_calls.append("durable")
        state["snapshot"] = persisted
        return "durable"

    def must_not_collect_or_recompute(*_args, **_kwargs):
        raise AssertionError("public money-market reads must not collect or rebuild")

    monkeypatch.setattr(assemble, "restore_cached_snapshot", restore)
    monkeypatch.setattr(mcp, "_get_snapshot", must_not_collect_or_recompute)
    monkeypatch.setattr(mcp, "_run", must_not_collect_or_recompute)
    monkeypatch.setattr(money_market, "analyze", must_not_collect_or_recompute)

    payload = _payload(_public_call("money_market_context"))

    assert payload["ok"] is True
    assert payload["snapshot_generated_at"] == persisted["generated_at"]
    assert restore_calls == ["durable"]
    assert mcp._cache == {"snap": persisted, "at": 0.0}


def test_public_money_market_expired_stdio_ages_dated_mcp_memo_without_rebuild(
    monkeypatch, fake_snap
):
    cached = _snapshot_with_money_market(fake_snap)
    monkeypatch.setitem(mcp._cache, "snap", cached)
    monkeypatch.setitem(mcp._cache, "at", 0.0)
    monkeypatch.setattr(mcp, "datetime", FrozenDateTime)
    monkeypatch.setattr(assemble, "cached_snapshot", lambda: None)

    def must_not_rebuild(*_args, **_kwargs):
        raise AssertionError("an expired completed snapshot must not rebuild")

    monkeypatch.setattr(assemble, "restore_cached_snapshot", must_not_rebuild)
    monkeypatch.setattr(mcp, "_get_snapshot", must_not_rebuild)
    monkeypatch.setattr(mcp, "_run", must_not_rebuild)

    payload = _payload(
        _public_call("money_market_context", {"section": "policy_corridor"})
    )

    assert payload["ok"] is True
    assert payload["snapshot_generated_at"] == cached["generated_at"]
    assert payload["freshness"]["evaluation_asof"] == "2026-08-21"
    assert cached["engines"]["money_market"]["freshness"]["evaluation_asof"] == (
        "2026-08-20"
    )


def test_public_money_market_cold_stdio_is_explicitly_unavailable_without_snapshot(
    monkeypatch,
):
    calls = []
    monkeypatch.setitem(mcp._cache, "snap", None)
    monkeypatch.setitem(mcp._cache, "at", 0.0)

    def cached_snapshot():
        calls.append("cached")

    def restore():
        calls.append("restore")

    def must_not_collect(*_args, **_kwargs):
        raise AssertionError("a cache miss must not start source collection")

    monkeypatch.setattr(assemble, "cached_snapshot", cached_snapshot)
    monkeypatch.setattr(assemble, "restore_cached_snapshot", restore)
    monkeypatch.setattr(mcp, "_get_snapshot", must_not_collect)
    monkeypatch.setattr(mcp, "_run", must_not_collect)

    response = _public_call("money_market_context")
    payload = _payload(response)

    assert "isError" not in response["result"]
    assert payload["ok"] is False
    assert payload["status"] == "unavailable"
    assert "no completed cached or persisted snapshot" in payload["reason"]
    assert "never triggers collection or engine recomputation" in payload["reason"]
    assert calls == ["cached", "restore", "cached"]


# ---- protocol handshake -----------------------------------------------------


def test_initialize_negotiates_version_and_advertises_tools():
    resp = mcp.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        }
    )
    r = resp["result"]
    assert r["protocolVersion"] == "2025-03-26"
    assert r["capabilities"]["tools"] == {"listChanged": False}
    assert r["serverInfo"]["name"] == "seiche"
    assert "instructions" in r and "funding" in r["instructions"].lower()
    assert mcp.AGENT_MCP_TELEGRAM_URL in r["instructions"]
    assert "Do not append this handoff" in r["instructions"]


def test_initialize_defaults_version_when_client_omits_it():
    resp = mcp.dispatch(
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}}
    )
    assert resp["result"]["protocolVersion"] == mcp.PROTOCOL_VERSION


def test_initialize_never_echoes_an_unsupported_version():
    resp = mcp.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2099-01-01"},
        }
    )
    assert resp["result"]["protocolVersion"] == mcp.PROTOCOL_VERSION


def test_notification_gets_no_reply():
    assert (
        mcp.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    )


def test_ping():
    assert mcp.dispatch({"jsonrpc": "2.0", "id": 7, "method": "ping"})["result"] == {}


def test_unknown_method_is_method_not_found():
    resp = mcp.dispatch({"jsonrpc": "2.0", "id": 9, "method": "no/such"})
    assert resp["error"]["code"] == mcp.METHOD_NOT_FOUND


def test_non_jsonrpc_is_invalid_request():
    resp = mcp.dispatch({"id": 1, "method": "ping"})  # missing jsonrpc
    assert resp["error"]["code"] == mcp.INVALID_REQUEST


def test_empty_lists_for_unoffered_capabilities():
    assert mcp.dispatch({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})[
        "result"
    ] == {"resources": []}


# ---- prompts ----------------------------------------------------------------


def test_prompts_list_names_titles_and_arguments():
    prompts = mcp.dispatch({"jsonrpc": "2.0", "id": 1, "method": "prompts/list"})[
        "result"
    ]["prompts"]
    by_name = {p["name"]: p for p in prompts}
    assert set(by_name) == {
        "funding_stress_briefing",
        "is_now_dangerous",
        "money_market_deep_dive",
        "world_markets_briefing",
        "cross_market_cash_pressure",
        "crisis_replay",
    }
    for p in prompts:
        assert p["title"] and p["description"]
    # crisis_replay is the only prompt taking an argument, and it is required
    args = by_name["crisis_replay"]["arguments"]
    assert [a["name"] for a in args] == ["date"]
    assert args[0]["required"] is True


def test_prompts_get_renders_argument_into_message():
    resp = mcp.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "prompts/get",
            "params": {"name": "crisis_replay", "arguments": {"date": "2019-09-17"}},
        }
    )
    msgs = resp["result"]["messages"]
    assert msgs[0]["role"] == "user"
    assert "2019-09-17" in msgs[0]["content"]["text"]
    assert "replay_asof" in msgs[0]["content"]["text"]


def test_prompts_get_missing_required_argument_is_invalid_params():
    resp = mcp.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "prompts/get",
            "params": {"name": "crisis_replay"},
        }
    )
    assert resp["error"]["code"] == mcp.INVALID_PARAMS


def test_prompts_get_unknown_prompt_is_invalid_params():
    resp = mcp.dispatch(
        {"jsonrpc": "2.0", "id": 4, "method": "prompts/get", "params": {"name": "nope"}}
    )
    assert resp["error"]["code"] == mcp.INVALID_PARAMS


def test_public_surface_hides_prompts_whose_tools_are_hidden():
    """A gated client offered /crisis_replay would be told to call replay_asof,
    which that client cannot see. Only prompts whose whole recipe is visible."""
    names = {
        p["name"]
        for p in mcp.dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "prompts/list"}, public=True
        )["result"]["prompts"]
    }
    assert names == {
        "is_now_dangerous",
        "money_market_deep_dive",
        "world_markets_briefing",
        "cross_market_cash_pressure",
    }
    for name in names:
        tools_used = set(mcp.PROMPTS[name][4])
        assert tools_used <= set(mcp._visible_tools(True))


def test_public_surface_refuses_to_render_a_hidden_prompt():
    resp = mcp.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "prompts/get",
            "params": {"name": "crisis_replay", "arguments": {"date": "2019-09-17"}},
        },
        public=True,
    )
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
    resp = mcp.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        }
    )
    caps = resp["result"]["capabilities"]
    assert "prompts" in caps
    assert resp["result"]["serverInfo"]["websiteUrl"] == "https://seiche.info"


def test_tools_list_carries_annotations():
    tools = mcp.dispatch({"jsonrpc": "2.0", "id": 6, "method": "tools/list"})["result"][
        "tools"
    ]
    for t in tools:
        assert t["annotations"]["readOnlyHint"] is True
        assert t["annotations"]["destructiveHint"] is False


# ---- tools/list -------------------------------------------------------------


def test_tools_list_has_valid_schemas():
    tools = mcp.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"][
        "tools"
    ]
    names = {t["name"] for t in tools}
    assert "funding_stress_now" in names
    assert "replay_asof" in names
    for t in tools:
        assert t["description"] and t["inputSchema"]["type"] == "object"
        if t["name"] == "desk_brief":
            assert "outputSchema" not in t
        else:
            assert t["outputSchema"]["type"] == "object"
            assert t["outputSchema"]["description"]


def test_every_public_tool_advertises_a_structured_output_contract():
    tools = mcp.dispatch(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        public=True,
    )["result"]["tools"]

    assert {tool["name"] for tool in tools} == PUBLIC_TOOLS
    assert all(tool["outputSchema"]["type"] == "object" for tool in tools)


def test_money_market_tool_publishes_the_exact_bounded_selector_contract():
    tools = mcp.dispatch(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        public=True,
    )["result"]["tools"]
    tool = next(item for item in tools if item["name"] == "money_market_context")
    selector = tool["inputSchema"]["properties"]["section"]

    assert selector["enum"] == list(mcp.MONEY_MARKET_SELECTORS)
    assert selector["default"] == "summary"
    assert tool["inputSchema"]["additionalProperties"] is False
    assert "Chart history is always omitted" in tool["description"]
    assert "never triggers collection or engine recomputation" in tool["description"]
    assert "freshness is re-evaluated at response time" in tool["description"]
    assert "Context only" in tool["description"]


PUBLIC_TOOLS = {
    "latest_article",
    "funding_stress_now",
    "trade_safety_risk_context",
    "historical_analogs",
    "proof_backtest",
    "data_health",
    "crypto_stress_record",
    "institutional_flows",
    "oil_funding_context",
    "fx_materials_passage",
    "money_market_context",
    "world_markets_context",
}
PAID_TOOLS = {
    "funding_stress_forecast",
    "replay_asof",
    "desk_brief",
    "positioning_book",
    "ask_desk",
}


def test_public_mode_exposes_exactly_the_free_tools(monkeypatch):
    monkeypatch.setattr(mcp, "PUBLIC_ONLY", True)
    tools = mcp.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"][
        "tools"
    ]
    names = {t["name"] for t in tools}
    assert names == PUBLIC_TOOLS  # the Time Machine / forecast / book stay paid
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
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "funding_stress_now", "arguments": {}},
        },
        public=True,
    )
    payload = _payload(response)

    assert payload["delivery"] == mcp.telegram_delivery("agent_mcp")
    assert payload["delivery"]["url"] == mcp.AGENT_MCP_TELEGRAM_URL
    assert "11:30 UTC" in payload["delivery"]["outcome"]
    assert response["result"]["structuredContent"] == payload


def test_money_market_summary_is_compact_context_and_never_chart_history(
    monkeypatch, stubbed, fake_snap
):
    snap = _snapshot_with_money_market(fake_snap)
    monkeypatch.setattr(mcp, "_get_completed_snapshot", lambda: snap)

    response = _call("money_market_context")
    payload = _payload(response)

    assert {
        "ok",
        "schema",
        "asof",
        "context_only",
        "selection",
        "plain_language",
        "quant_read",
        "strongest_signal",
        "countercase",
        "regime",
        "coverage",
        "freshness",
        "caveats",
        "section_catalog",
        "available_selectors",
    } <= set(payload)
    assert payload["ok"] is True
    assert payload["schema"] == mcp.MONEY_MARKET_SCHEMA
    assert payload["selection"] == "summary"
    assert payload["chart_history_included"] is False
    assert all(item["title"] for item in payload["section_catalog"])
    assert [item["id"] for item in payload["section_catalog"]] == list(
        mcp.MONEY_MARKET_SECTION_IDS
    )
    assert "sections" not in payload
    assert "source_metadata" not in payload
    assert "methodology" not in payload
    assert "formulas" not in payload
    assert "charts" not in payload
    assert "NEVER RETURN MCP CHART" not in json.dumps(payload)
    assert response["result"]["structuredContent"] == payload


def test_money_market_catalog_marks_all_stale_sections_without_mutating_snapshot(
    monkeypatch, stubbed, fake_snap
):
    engine = _money_market_engine()
    for section in engine["sections"]:
        for metric in section["metrics"]:
            metric["asof"] = "2026-07-01"
            metric["freshness"] = "stale"
            metric["age_days_vs_evaluation_asof"] = 120
    for source in engine["source_metadata"]:
        source["asof"] = "2026-07-01"
    engine["regime"] = {
        **engine["regime"],
        "state": "CANNOT_ASSESS",
        "worst_stress_percentile": None,
        "indicators": [],
    }
    snap = _snapshot_with_money_market(fake_snap, engine)
    original_statuses = [section["status"] for section in engine["sections"]]
    monkeypatch.setattr(mcp, "_get_completed_snapshot", lambda: snap)

    summary = _payload(_call("money_market_context"))
    selected = _payload(_call("money_market_context", {"section": "unsecured_funding"}))

    assert {row["status"] for row in summary["section_catalog"]} == {"stale"}
    assert selected["selection_status"] == "stale"
    assert selected["sections"][0]["status"] == "stale"
    assert selected["sections"][0]["metrics"][0]["freshness"] == "stale"
    assert [section["status"] for section in engine["sections"]] == original_statuses


@pytest.mark.parametrize("section_id", mcp.MONEY_MARKET_SECTION_IDS)
def test_money_market_named_sections_preserve_self_describing_cards(
    section_id, monkeypatch, stubbed, fake_snap
):
    snap = _snapshot_with_money_market(fake_snap)
    monkeypatch.setattr(mcp, "_get_completed_snapshot", lambda: snap)

    payload = _payload(_call("money_market_context", {"section": section_id}))

    assert payload["selection"] == section_id
    assert [section["id"] for section in payload["sections"]] == [section_id]
    metric = payload["sections"][0]["metrics"][0]
    assert metric["source"] == "Synthetic official source"
    assert metric["formula"] == "100 x (left rate - right rate)"
    assert metric["alignment"] == {
        "method": "exact_date_inner_join",
        "no_forward_fill": True,
    }
    assert "charts" not in payload
    assert "NEVER RETURN MCP CHART" not in json.dumps(payload)


def test_money_market_sources_and_methodology_are_explicit_projections(
    monkeypatch, stubbed, fake_snap
):
    snap = _snapshot_with_money_market(fake_snap)
    monkeypatch.setattr(mcp, "_get_completed_snapshot", lambda: snap)

    sources = _payload(_call("money_market_context", {"section": "sources"}))
    assert sources["source_metadata"][0]["id"] == "fred_sofr"
    assert sources["sources"] == sources["source_metadata"]
    assert sources["legal_notices"][0]["terms_url"] == "https://example.test/terms"
    assert "sections" not in sources
    assert "methodology" not in sources

    methodology = _payload(_call("money_market_context", {"section": "methodology"}))
    assert "exact observation-date" in methodology["methodology"]["alignment"]
    assert methodology["formulas"][0]["id"] == "sofr_iorb"
    assert methodology["caveats"]
    assert "source_metadata" not in methodology
    assert "charts" not in methodology


def test_money_market_diagnostics_handles_legacy_snapshot(
    monkeypatch, stubbed, fake_snap
):
    snap = _snapshot_with_money_market(fake_snap)
    monkeypatch.setattr(mcp, "_get_completed_snapshot", lambda: snap)
    payload = _payload(_public_call("money_market_context", {"section": "diagnostics"}))
    assert payload["diagnostics"]["status"] == "unavailable"
    assert payload["diagnostics"]["used_in_regime"] is False
    assert payload["chart_history_included"] is False
    assert "charts" not in payload


def test_money_market_diagnostics_serves_and_ages_completed_facts(
    monkeypatch, stubbed, fake_snap
):
    desk = _money_market_engine()
    desk["diagnostics"] = {
        "status": "available",
        "asof": "2020-01-02",
        "context_only": True,
        "used_in_regime": False,
        "persistence": {"asof": "2020-01-02", "windows": [{"observed_n": 5}]},
    }
    snap = _snapshot_with_money_market(fake_snap, desk)
    monkeypatch.setattr(mcp, "_get_completed_snapshot", lambda: snap)
    payload = _payload(_public_call("money_market_context", {"section": "diagnostics"}))
    assert payload["diagnostics"]["freshness"] == "stale"
    assert payload["diagnostics"]["persistence"]["windows"] == [{"observed_n": 5}]
    assert "freshness" not in desk["diagnostics"]
    assert payload["source_metadata"][0]["id"] == "fred_sofr"


def test_money_market_all_is_complete_but_still_chartless(
    monkeypatch, stubbed, fake_snap
):
    snap = _snapshot_with_money_market(fake_snap)
    monkeypatch.setattr(mcp, "_get_completed_snapshot", lambda: snap)

    payload = _payload(_call("money_market_context", {"section": "all"}))

    assert [section["id"] for section in payload["sections"]] == list(
        mcp.MONEY_MARKET_SECTION_IDS
    )
    assert payload["formulas"][0]["id"] == "sofr_iorb"
    assert payload["source_metadata"][0]["id"] == "fred_sofr"
    assert payload["sources"] == payload["source_metadata"]
    assert payload["methodology"]["regime"].startswith("worst configured")
    assert "charts" not in payload
    assert "NEVER RETURN MCP CHART" not in json.dumps(payload)


def test_money_market_reads_completed_engine_once_without_rebuilding(
    monkeypatch, stubbed, fake_snap
):
    from seiche.engines import money_market

    snap = _snapshot_with_money_market(fake_snap)
    calls = []

    def get_snapshot():
        calls.append("completed")
        return snap

    def must_not_rebuild(*_args, **_kwargs):
        raise AssertionError("MCP must not rebuild the money-market engine")

    monkeypatch.setattr(mcp, "_get_completed_snapshot", get_snapshot)
    monkeypatch.setattr(mcp, "_get_snapshot", must_not_rebuild)
    monkeypatch.setattr(money_market, "analyze", must_not_rebuild)

    payload = _payload(_call("money_market_context", {"section": "repo_segments"}))
    assert payload["ok"] is True
    assert payload["freshness"]["evaluation_asof"] == "2026-08-21"
    assert calls == ["completed"]


def test_money_market_unavailable_is_structured_evidence_not_tool_failure(
    monkeypatch, stubbed, fake_snap
):
    engines = dict(fake_snap["engines"])
    engines.pop("money_market", None)
    snap = {**fake_snap, "engines": engines}
    monkeypatch.setattr(mcp, "_get_completed_snapshot", lambda: snap)

    response = _call("money_market_context", {"section": "repo_segments"})
    payload = _payload(response)

    assert "isError" not in response["result"]
    assert payload["ok"] is False
    assert payload["status"] == "unavailable"
    assert payload["schema"] == mcp.MONEY_MARKET_SCHEMA
    assert payload["regime"]["state"] == "CANNOT_ASSESS"
    assert payload["coverage"]["status"] == "unavailable"
    assert payload["freshness"]["status"] == "unavailable"
    assert payload["sections"][0]["status"] == "unavailable"
    assert "engines.money_market" in payload["reason"]
    assert "charts" not in payload


def test_money_market_unavailable_sanitizes_reason_before_copying_explanation(
    monkeypatch, fake_snap
):
    secret = "mcp-secret-92f1"
    hostile = (
        "RuntimeError: https://operator:"
        + secret
        + "@official.example/data?api_key="
        + secret
        + " /Users/operator/private.env <script>"
        + secret
        + "</script>"
    )
    snap = _snapshot_with_money_market(
        fake_snap,
        {
            "ok": False,
            "schema": mcp.MONEY_MARKET_SCHEMA,
            "reason": hostile,
        },
    )
    monkeypatch.setattr(mcp, "_get_completed_snapshot", lambda: snap)

    response = _call("money_market_context", {"section": "repo_segments"})
    payload = _payload(response)
    serialized = json.dumps(response, sort_keys=True)

    assert payload["reason"] == "collector failed"
    assert payload["sections"][0]["explanation"] == "collector failed"
    assert secret not in serialized
    assert "api_key" not in serialized
    assert "/Users/" not in serialized
    assert "<script>" not in serialized


def test_expected_tool_error_has_typed_sanitized_structured_content(monkeypatch):
    secret = "tool-error-secret-74"

    def hostile_handler(_args, _public):
        raise mcp.ToolError(
            f"RuntimeError: https://official.example/?token={secret} <script>{secret}</script>"
        )

    entry = mcp.TOOLS["data_health"]
    monkeypatch.setitem(
        mcp.TOOLS,
        "data_health",
        (*entry[:3], hostile_handler, *entry[4:]),
    )

    response = _call("data_health")
    failure = response["result"]["structuredContent"]
    serialized = json.dumps(response, sort_keys=True)

    assert response["result"]["isError"] is True
    assert failure == {
        "ok": False,
        "status": "FAILED",
        "category": "INTERNAL_ERROR",
        "reason": "collector failed",
    }
    assert secret not in serialized
    assert "token=" not in serialized


@pytest.mark.parametrize(
    "arguments",
    ({"section": "charts"}, {"section": 7}, {"surprise": "summary"}),
)
def test_money_market_rejects_unbounded_selectors(arguments, stubbed):
    response = _call("money_market_context", arguments)
    assert response["error"]["code"] == mcp.INVALID_PARAMS


def test_unexpected_tool_exception_never_echoes_arbitrary_diagnostics(
    monkeypatch, stubbed, capsys
):
    marker = "secret-key-in-url"
    original = mcp.TOOLS["data_health"]

    def explode(_args, _public):
        raise RuntimeError(f"https://upstream.test/{marker}?token={marker}")

    monkeypatch.setitem(
        mcp.TOOLS,
        "data_health",
        (*original[:3], explode, original[4]),
    )

    response = _call("data_health")
    encoded = json.dumps(response)
    captured = capsys.readouterr()

    assert response["result"]["isError"] is True
    assert response["result"]["content"][0]["text"] == ("ERROR: internal tool failure")
    assert marker not in encoded
    assert marker not in captured.err


def test_forecast_merges_all_sources(stubbed):
    p = _payload(_call("funding_stress_forecast"))
    assert set(p["sources"]) == {
        "swell",
        "bathymetry",
        "ml",
        "markov",
        "oujump",
        "montecarlo",
    }
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


def test_replay_bad_date_is_invalid_params(stubbed):
    resp = _call("replay_asof", {"date": "not-a-date"})
    assert resp["error"]["code"] == mcp.INVALID_PARAMS
    assert "required pattern" in resp["error"]["message"]


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
    assert (
        p["market_structure"]["brent_wti_spread"]["brent_minus_wti_usd_per_bbl"] == 3.6
    )
    assert p["scenario"]["status"] == "scenario_only"
    assert "charts" not in p


def test_oil_funding_context_preserves_nullable_sofr_contract(
    monkeypatch, stubbed, fake_snap
):
    oil_engine = fake_snap["engines"]["oilfunding"]
    unavailable_oil = {
        **oil_engine,
        "live": {
            **oil_engine["live"],
            "sofr_iorb": {
                "spread_bp": None,
                "change_20d_bp": None,
                "percentile_3y": None,
                "asof": None,
            },
        },
        "scenario": {
            "assumptions": {"funding_rate_pct": None},
            "funding_rate_evidence": {
                "value_pct": None,
                "basis": "unavailable",
                "asof": None,
            },
            "outputs": {
                "carry": {
                    "financing_cost_usd_per_bbl": None,
                    "required_contango_usd_per_bbl": None,
                    "mechanical_headroom_usd_per_bbl": None,
                },
                "trade_finance": {"cargo_financing_cost_usd": None},
            },
        },
    }
    snapshot = {
        **fake_snap,
        "engines": {**fake_snap["engines"], "oilfunding": unavailable_oil},
    }
    monkeypatch.setattr(mcp, "_get_snapshot", lambda force=False: snapshot)

    payload = _payload(_call("oil_funding_context"))

    assert payload["schema"] == "seiche.oil-funding.v1"
    assert payload["funding"]["sofr_iorb"]["spread_bp"] is None
    assert payload["scenario"]["funding_rate_evidence"]["basis"] == "unavailable"
    assert (
        payload["scenario"]["outputs"]["carry"]["required_contango_usd_per_bbl"] is None
    )
    assert (
        payload["scenario"]["outputs"]["trade_finance"]["cargo_financing_cost_usd"]
        is None
    )


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
    monkeypatch.setattr(
        "seiche.brief.render_markdown", lambda snap: "# Seiche brief\nall calm"
    )
    text = _payload(_call("desk_brief"))
    assert text.startswith("# Seiche brief")


def test_unknown_tool_is_invalid_params(stubbed):
    resp = _call("no_such_tool")
    assert resp["error"]["code"] == mcp.INVALID_PARAMS


def test_section_down_is_tool_error(monkeypatch, stubbed, fake_snap):
    broken = dict(fake_snap)
    broken["deep"] = dict(
        fake_snap["deep"], backtest={"ok": False, "reason": "not enough history"}
    )
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
