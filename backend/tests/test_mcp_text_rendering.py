"""The presentation contract's text block, appended after the MCP JSON block.

Guards, each revert-tested when added (docs/MCP_TEXT_RENDERING.md): the JSON
block and structuredContent are byte-identical to the envelope without the text
block, over dispatch and over HTTP; the vendored presenter is the recorded copy;
every reading's state word appears; a gap never prints a value or a zero; every
number in the text was served in structuredContent; nothing the fault sanitizer
replaced reaches the text; a word with no row is printed under no state and
never drawn calm; the block holds its cap with gaps first; no en or em dash
appears. Every name and figure in a payload built here is synthetic.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from seiche import accounts, api, mcp_server, public_rights, usage
from seiche.mcp_text import adapters, render
from seiche.mcp_text.vendor import PROVENANCE, present

VENDOR = Path(__file__).resolve().parents[1] / "seiche" / "mcp_text" / "vendor"
CONTRACT_COMMIT = "59302ce87ef186ed01fb8ce15f665cf29ad4cfb6"
DECIDED_CAP_BYTES = 4096  # decision 5 ("about 4 KB"), pinned so the module constant cannot drift
DASHES = ("\u2013", "\u2014")
DASH_ESCAPES = ("\\u2013", "\\u2014")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2} UTC)?")
_NUMBER = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?!\w)")
_META = ("Readings: ",)  # the presenter's count line
SYNTHETIC_SOURCE = {
    "publisher": "Synthetic Central Bank", "source_url": "https://example.invalid/synthetic",
    "fetched_at": "2026-11-11T21:00:00+00:00",
    "attribution": "Source: Synthetic Central Bank \u2014 synthetic attribution.",
}

# ------------------------------------------------------------ synthetic payloads
SYNTHETIC_HEALTH = {
    "generated_at": "2026-11-12T00:30:00+00:00", "version": "synthetic", "faults": [
        {"source": "synthetic_source", "status": "FAILED", "category": "upstream_unavailable",
         "detail": "Synthetic: the upstream source failed."}],
    "provenance": [
        {"mnemonic": "SYN_FRESH", "label": "Synthetic fresh series", "asof": "2026-11-11",
         "fetched_at": "2026-11-11T21:00:00+00:00", "staleness": "fresh", "availability": "available"},
        {"mnemonic": "SYN_AGING", "label": "Synthetic aging series", "asof": "2026-11-04",
         "staleness": "aging", "availability": "available"},
        {"mnemonic": "SYN_STALE", "label": "Synthetic stale series", "asof": "2026-09-30",
         "fetched_at": "2026-11-11T21:00:00+00:00", "knowledge_time": "2026-11-11T21:00:00+00:00",
         "staleness": "stale", "availability": "available",
         "attribution": "Source: Synthetic Statistics Office.",
         "freshness_basis": "Synthetic: age of the latest observation versus its cadence."},
        {"mnemonic": "SYN_DEAD", "label": "Synthetic dead series", "asof": "2025-01-31",
         "staleness": "dead", "availability": "available"},
        {"mnemonic": "SYN_GONE", "label": "Synthetic missing series", "asof": None,
         "staleness": "unknown", "availability": "unavailable"},
    ],
    "reading": "Synthetic reading text \u2014 with a dash.",
}
SYNTHETIC_DESK = {
    "ok": True, "schema": mcp_server.MONEY_MARKET_SCHEMA, "asof": "2026-11-09",
    "snapshot_generated_at": "2026-11-12T00:24:38+00:00", "context_only": True, "selection": "summary",
    "regime": {"state": "NORMAL", "indicators": [
        {"metric_id": "syn.fresh", "label": "Synthetic fresh indicator", "value": 9.25, "unit": "$B",
         "asof": "2026-11-04", "freshness": "fresh"},
        {"metric_id": "syn.stale", "label": "Synthetic stale indicator", "value": 3.5, "unit": "bp",
         "asof": "2026-09-01", "freshness": "stale"},
        {"metric_id": "syn.dead", "label": "Synthetic dead indicator", "value": 1.75, "unit": "%",
         "asof": "2025-02-01", "freshness": "dead"}]},
    "coverage": {"unavailable_metrics": ["syn.missing"], "stale_metrics": ["syn.stale"]},
    "freshness": {"evaluation_asof": "2026-11-12"},
    "caveats": ["Synthetic caveat one.", "Synthetic caveat two \u2013 with a dash."],
    "section_catalog": [{"id": "policy_corridor", "title": "Synthetic policy", "status": "available"}],
}
SYNTHETIC_SECTION = {
    **SYNTHETIC_DESK, "selection": "repo_segments", "regime": {}, "coverage": {},
    "sections": [{"id": "repo_segments", "title": "Synthetic repo", "status": "partial", "metrics": [
        {"id": "syn.card.live", "label": "Synthetic live card", "value": 4.4, "unit": "%",
         "asof": "2026-11-09", "status": "available", "freshness": "fresh"},
        {"id": "syn.card.gone", "label": "Synthetic missing card", "value": None, "unit": "%",
         "asof": None, "status": "unavailable",
         "unavailable_reason": "Synthetic: required observations unavailable."}]}],
}
SYNTHETIC_INDIA = {
    "ok": True, "schema": mcp_server.MONEY_MARKET_SCHEMA, "asof": "2026-11-10", "selection": "india",
    "caveats": ["Synthetic India caveat."],
    "india": {
        "schema": "seiche.india-funding-curve.v1", "generated_at": "2026-11-12T00:37:36+00:00",
        "status": "partial", "view": "summary",
        "missing_series": [{"id": "SYN.RESTRICTED", "status": "RESTRICTED"},
                           {"id": "SYN.UNAVAILABLE", "status": "UNAVAILABLE"}],
        "spreads": [
            {"id": "2s5s", "curve_kind": "benchmark_ytm", "status": "unavailable", "asof": None,
             "value_bp": None, "reason": "Synthetic: both tenors are required."},
            {"id": "5s10s", "curve_kind": "benchmark_ytm", "status": "stale", "asof": "2026-10-01",
             "value_bp": 12.5, "reason": "Synthetic: a curve leg is stale."},
            {"id": "10s30s", "curve_kind": "benchmark_ytm", "status": "available", "asof": "2026-11-10",
             "value_bp": 7.25, "reason": None}],
        "monthly_curve": {"spreads": [{"id": "2s10s", "curve_kind": "monthly_sgl_ytm",
                                       "status": "historical_reference", "asof": "2026-08-31",
                                       "value_bp": 34.4, "reason": None}]},
        "policy_comparison": {"status": "dated_reference", "value_bp": 9.0, "asof": "2026-11-09",
                              "interpretation": "Synthetic interpretation."},
        "liquidity_summary": {"status": "stale", "asof": "2026-10-20"},
        "mechanism": {"status": "insufficient_evidence"},
        "curve": {"fixed_tenor_status": "unavailable",
                  "fixed_tenor_reason": "Synthetic: no fixed-tenor curve \u2014 see the monthly view."},
    },
}
SYNTHETIC_INDIA_MISS = {
    "ok": False, "schema": mcp_server.MONEY_MARKET_SCHEMA, "selection": "india",
    "india": {"view": "metric", "metric_id": "SYN.NO_SUCH", "selection_status": "not_found",
              "metric": None, "reason": "Synthetic: no exposed metric card has this exact ID."},
}
SYNTHETIC_DESK_DOWN = {
    "ok": False, "schema": mcp_server.MONEY_MARKET_SCHEMA, "selection": "summary",
    "status": "unavailable", "reason": "Synthetic: the desk is unavailable in this snapshot.",
    "regime": {"state": "CANNOT_ASSESS"},
}
SYNTHETIC_WORLD = {
    "ok": True, "schema": "seiche.world-markets.v1", "status": "derived", "selection": "money_markets",
    "generated_at": "2026-11-12T00:24:38+00:00", "as_of": "2026-11-09",
    "clocks": {"evaluation_at": "2026-11-12T00:37:45+00:00",
               "boundary": "Synthetic: response time never advances a clock."},
    "coverage": {"domains": [
        {"id": "money_markets", "status": "restricted", "as_of": "2026-11-09", "reading": "Synthetic."},
        {"id": "forex", "status": "unavailable", "as_of": None},
        {"id": "capital_markets", "status": "derived", "as_of": "2026-11-09"}]},
    "money_markets": {"status": "restricted", "as_of": "2026-11-09", "regime": {"state": "NORMAL"},
                      "sections": [{"id": "policy_corridor", "status": "observed", "metrics": [
                          {"id": "syn.obs", "label": "Synthetic observed rate", "value": 3.88,
                           "unit": "%", "asof": "2026-11-09", "freshness": "fresh", "status": "observed"},
                          {"id": "syn.drv", "label": "Synthetic derived spread", "value": 6.5,
                           "unit": "bp", "asof": "2026-11-09", "freshness": "aging", "status": "derived"},
                          {"id": "syn.old", "label": "Synthetic old rate", "value": 2.25,
                           "unit": "%", "asof": "2026-08-01", "freshness": "stale", "status": "observed"},
                          {"id": "syn.gap", "label": "Synthetic gap", "value": None, "unit": "%",
                           "status": "unavailable", "reason": "Synthetic: no eligible evidence."}]}]},
    "forex": {"currencies": [{"label": "Synthetic Krona", "status": "derived", "spot": {
        "status": "observed", "value": 10.5, "unit": "SYN per USD", "as_of": "2026-11-09"}}]},
    "disclaimer": "Synthetic research context, not investment advice.",
}


def _fx(pair, value, status, evidence_status, **extra):
    return {"pair": pair, "provider": "synthetic", "value": value, "as_of": "2026-11-11",
            "status": status, "evidence_status": evidence_status, "unit": f"{pair[-3:]} per {pair[:3]}",
            "sources": [SYNTHETIC_SOURCE], "reason": None, **extra}


SYNTHETIC_WORKBENCH = {
    "schema": "seiche.market-workbench.v1", "generated_at": "2026-11-12T00:37:47+00:00",
    "context_only": True, "status": "partial", "selection": {"provider": "synthetic"},
    "forex": {"rows": [
        _fx("USD/SYA", 1.4578952098499147, "fresh", "derived"),
        _fx("EUR/SYB", 0.85, "aging", "observed"),
        _fx("USD/SYC", 2.5, "stale", "derived"),
        _fx("USD/SYD", 3.5, "dead", "derived"),
        _fx("USD/SYE", None, "restricted", "restricted", reason="Synthetic: rights are not established."),
        _fx("USD/SYF", None, "unavailable", "unavailable", reason="Synthetic: no matching dates."),
    ], "methodology": ["Synthetic methodology \u2014 crosses join matching dates only."]},
    "china": {"status": "unavailable", "reason": "Synthetic: no accepted export.", "series": [],
              "fx": _fx("USD/SYA", 1.4578952098499147, "fresh", "derived"),
              "gaps": [{"id": "syn_restricted", "label": "Synthetic licensed fixings",
                        "status": "restricted", "reason": "Synthetic: publisher rights are required."}]},
}
SYNTHETIC_GIFT = {
    "schema": "seiche.gift-city.v1", "generated_at": "2026-11-12T00:37:51.100657+00:00",
    "context_only": True, "status": "partial",
    "funding": [
        {"currency": "SYA", "label": "Synthetic rate A", "instrument": "SYN_A", "value": 5.59, "unit": "%",
         "as_of": "2026-11-09", "status": "FRESH", "source": "synthetic_official", "reason": None,
         "evidence": {"availability": "AVAILABLE", "status": "FRESH",
                      "knowledge_time": "2026-11-11T04:26:50+00:00"}},
        {"currency": "SYB", "label": "Synthetic rate B", "instrument": "SYN_B", "value": 3.25, "unit": "%",
         "as_of": "2026-09-01", "status": "STALE", "source": "synthetic_official", "reason": None,
         "evidence": {"availability": "AVAILABLE", "status": "STALE"}},
        {"currency": "SYC", "label": "Synthetic rate C", "instrument": "SYN_C", "value": None, "unit": "%",
         "as_of": None, "status": "UNAVAILABLE", "source": None,
         "reason": "Synthetic: the canonical funding source could not be read.", "evidence": {}},
        {"currency": "SYD", "label": "Synthetic reference D", "instrument": "SYN_D", "value": 4.025,
         "unit": "%", "as_of": "2026-11-11", "status": "FRESH", "source": "synthetic_reference",
         "reason": None, "evidence": {"evidence_class": "observed",
                                      "attribution": "Source: Synthetic Reference Publisher."}},
    ],
    "forex": {"rows": [_fx("USD/SYA", 0.8298732812078727, "fresh", "derived")]},
    "gold": {"positioning": {"status": "stale", "as_of": "2026-10-20", "contract": "SYNTHETIC GOLD",
                             "boundary": "Synthetic weekly positioning, not a price."}, "price": None},
    "methodology": ["Synthetic methodology note."],
}
SYNTHETIC_RESEARCH = {
    "schema": "seiche.research-network.v1", "status": "available", "context_only": True,
    "evaluated_at": "2026-11-12T00:37:55+00:00", "datasets": [
        {"id": "synthetic-dataset", "evidence_state": "stale", "values_included": False}],
    "boundary": "Synthetic boundary: research context only \u2014 not a signal.",
}
SYNTHETIC_RESEARCH_DOWN = {
    "schema": "seiche.research-network.v1", "status": "unavailable", "context_only": True,
    "evaluated_at": "2026-11-12T00:37:55+00:00", "datasets": [],
    "reason": "Synthetic: the published catalog could not be read.",
    "boundary": "Synthetic boundary.",
}
GOLD_ARGS = {"quantity_kg": "1", "fineness": "0.995", "price_usd_per_oz": "2000",
             "annual_rate_pct": "5", "fx_inr_per_usd": "80", "fees_usd": "10", "days": 30}

# (tool, arguments, synthetic payload or None to run the real handler)
CALLS = [
    ("source_rights_status", {}, None),
    ("gold_inventory_carry", GOLD_ARGS, None),
    ("data_health", {}, SYNTHETIC_HEALTH),
    ("money_market_context", {}, SYNTHETIC_DESK),
    ("money_market_context", {"section": "repo_segments"}, SYNTHETIC_SECTION),
    ("money_market_context", {"section": "india", "view": "summary"}, SYNTHETIC_INDIA),
    ("money_market_context", {"section": "india", "view": "metric", "metric_id": "SYN.NO_SUCH"},
     SYNTHETIC_INDIA_MISS),
    ("money_market_context", {}, SYNTHETIC_DESK_DOWN),
    ("world_markets_context", {"section": "money_markets"}, SYNTHETIC_WORLD),
    ("market_workbench", {}, SYNTHETIC_WORKBENCH),
    ("gift_city_context", {}, SYNTHETIC_GIFT),
    ("research_network", {}, SYNTHETIC_RESEARCH),
    ("research_network", {}, SYNTHETIC_RESEARCH_DOWN),
]
IDS = [f"{name}-{index}" for index, (name, _, _) in enumerate(CALLS)]


def _encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _call(name: str, arguments: dict, *, public: bool = True) -> dict:
    return mcp_server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                "params": {"name": name, "arguments": copy.deepcopy(arguments)}},
                               public=public)


@pytest.fixture()
def serve(monkeypatch):
    """Serve a tool's synthetic payload through the real dispatcher."""

    def install(name: str, payload: dict | None) -> None:
        if payload is None:
            return
        title, desc, schema, _handler, public = mcp_server.TOOLS[name]
        monkeypatch.setitem(mcp_server.TOOLS, name,
                            (title, desc, schema, lambda args, pub: copy.deepcopy(payload), public))

    return install


def _result(serve, name: str, arguments: dict, payload: dict | None) -> dict:
    serve(name, payload)
    response = _call(name, arguments)
    assert "error" not in response, response
    return response["result"]


def _block(result: dict) -> str:
    assert len(result["content"]) == 2 and result["content"][1]["type"] == "text"
    return result["content"][1]["text"]


def _numbers(text: str) -> list[str]:
    body = "\n".join(line for line in text.splitlines() if not line.startswith(_META))
    return _NUMBER.findall(_DATE.sub(" ", body))


def _was_served(token: str, served: str) -> bool:
    return re.search(rf"(?<![\d.]){re.escape(token.lstrip('+'))}(?!\d)", served) is not None


def _mark_word(reading: present.Reading) -> str:
    state = present.DISPLAY_STATES[reading.state]
    return f"{state.mark} {state.word}"


def _view(payload: dict, name: str) -> adapters.View:
    view = adapters.build(name, payload)
    assert view is not None
    return view


# ------------------------------------------------------------ the vendored copy
def test_the_vendored_presenter_is_the_recorded_byte_for_byte_copy():
    record = PROVENANCE["files"]["present.py"]
    data = (VENDOR / "present.py").read_bytes()
    assert record["repository"] == "beepboop2025/liquilens-evidence-carrier"
    assert record["path"] == "src/liquilens_evidence/present.py"
    assert re.fullmatch(r"[0-9a-f]{40}", record["commit"])
    assert len(data) == record["bytes"]
    assert hashlib.sha256(data).hexdigest() == record["sha256"]
    assert present.TOKENS_COMMIT == CONTRACT_COMMIT


def test_the_vendored_presenter_imports_only_the_standard_library():
    tree = ast.parse((VENDOR / "present.py").read_text(encoding="utf-8"))
    imported = {alias.name.split(".")[0] for node in ast.walk(tree)
                if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and node.module and node.level == 0}
    assert imported - {"__future__"} <= set(sys.stdlib_module_names)


def test_every_tool_is_rendered_skipped_with_a_reason_or_legacy():
    tools = set(mcp_server.TOOLS)
    rendered, skipped = set(adapters.ADAPTERS), set(adapters.SKIPPED)
    assert not rendered & skipped and not (rendered | skipped) & public_rights.LEGACY_TOOLS
    assert rendered | skipped | public_rights.LEGACY_TOOLS == tools
    assert all(reason.strip() for reason in adapters.SKIPPED.values())
    assert {name for name, _, _ in CALLS} == rendered
    public = set(mcp_server._visible_tools(public=True))
    assert public <= rendered


# ------------------------------------------------- decision 1: nothing replaced
@pytest.mark.parametrize("name,arguments,payload", CALLS, ids=IDS)
def test_the_json_block_and_structured_content_are_byte_identical(serve, monkeypatch, name,
                                                                  arguments, payload):
    after = _result(serve, name, arguments, payload)
    monkeypatch.setattr(render, "result_block", lambda *args, **kwargs: None)
    before = _call(name, arguments)["result"]
    assert _encode(after["structuredContent"]) == _encode(before["structuredContent"])
    assert after["content"][0] == before["content"][0]
    assert after["content"][0]["text"] == json.dumps(after["structuredContent"], indent=2, default=str)
    assert len(before["content"]) == 1 and len(after["content"]) == 2
    assert {k: v for k, v in after.items() if k != "content"} == \
        {k: v for k, v in before.items() if k != "content"}


@pytest.fixture()
def http(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "DB_PATH", tmp_path / "usage.sqlite")
    monkeypatch.setattr(accounts, "DB_PATH", tmp_path / "accounts.sqlite")
    monkeypatch.setattr(api, "_mcp_limiter", api._RateLimiter(10_000))
    monkeypatch.setenv("SEICHE_AUTH_SECRET", "test-secret-not-for-prod")
    return TestClient(api.app)


@pytest.mark.parametrize("name,arguments,payload", CALLS, ids=IDS)
def test_the_served_http_envelope_keeps_its_json_block(serve, monkeypatch, http, name, arguments,
                                                       payload):
    serve(name, payload)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}
    after = http.post("/mcp", json=body)
    monkeypatch.setattr(render, "result_block", lambda *args, **kwargs: None)
    before = http.post("/mcp", json=body)
    assert after.status_code == before.status_code == 200
    new, old = after.json()["result"], before.json()["result"]
    assert new["content"][0] == old["content"][0]
    assert _encode(new["structuredContent"]) == _encode(old["structuredContent"])
    assert len(new["content"]) == 2 and len(old["content"]) == 1


def test_the_legacy_refusal_keeps_its_text_and_appends_unavailable():
    for name in sorted(public_rights.LEGACY_TOOLS):
        result = _call(name, {})["result"]
        payload = public_rights.legacy_unavailable(name)
        assert result["isError"] is True and result["structuredContent"] == payload
        assert result["content"][0] == {"type": "text", "text": payload["reason"]}
        text = _block(result)
        assert text.startswith(f"{name}: ▨ Unavailable\nreason: {payload['reason']} |")
        assert text.split("\n\n")[-1] == present.DEFAULT_BOUNDARY


def test_tool_failures_and_agent_room_results_stay_single_block(monkeypatch):
    def fail(args, public):
        raise mcp_server.ToolError("Synthetic: the request failed.")

    title, desc, schema, _handler, public = mcp_server.TOOLS["data_health"]
    monkeypatch.setitem(mcp_server.TOOLS, "data_health", (title, desc, schema, fail, public))
    failed = _call("data_health", {})["result"]
    assert failed["isError"] is True and len(failed["content"]) == 1
    for name in adapters.SKIPPED:
        assert adapters.build(name, {"ok": True}) is None
        assert render.result_block(name, {"ok": True}) is None


# ------------------------------------------------------- the reading guards
@pytest.mark.parametrize("name,arguments,payload", CALLS, ids=IDS)
def test_every_reading_prints_its_state_word(serve, name, arguments, payload):
    result = _result(serve, name, arguments, payload)
    view = _view(result["structuredContent"], name)
    text = render.compose(view, cap=10**9)
    for reading in view.readings:
        assert text.count(_mark_word(reading)) >= sum(
            1 for other in view.readings if other.state == reading.state)


@pytest.mark.parametrize("name,arguments,payload", CALLS, ids=IDS)
def test_a_gap_never_prints_a_value_or_a_zero(serve, name, arguments, payload):
    result = _result(serve, name, arguments, payload)
    for reading in _view(result["structuredContent"], name).readings:
        if reading.state not in present.GAP_STATES:
            continue
        assert reading.value is None and reading.numeric is None
        first = present.render_reading(reading).splitlines()[0]
        assert not re.search(r"\d", first[first.rindex(_mark_word(reading)):]), first


@pytest.mark.parametrize("name,arguments,payload", CALLS, ids=IDS)
def test_every_number_in_the_text_was_served(serve, name, arguments, payload):
    result = _result(serve, name, arguments, payload)
    served = json.dumps(result["structuredContent"], ensure_ascii=False, default=str)
    for token in _numbers(_block(result)):
        assert _was_served(token, served), token


@pytest.mark.parametrize("name,arguments,payload", CALLS, ids=IDS)
def test_the_block_holds_its_cap_ends_with_the_boundary_and_carries_no_dash(serve, name, arguments,
                                                                            payload):
    result = _result(serve, name, arguments, payload)
    text = _block(result)
    assert render.BLOCK_CAP_BYTES == DECIDED_CAP_BYTES
    assert len(text.encode("utf-8")) <= DECIDED_CAP_BYTES
    assert not any(dash in text for dash in DASHES)
    assert not any(escape in text for escape in DASH_ESCAPES)
    served = result["structuredContent"]
    sentences = [served.get("boundary")] if name in ("source_rights_status", "research_network") else []
    if name == "world_markets_context":
        sentences = [served.get("disclaimer"), served.get("clocks", {}).get("boundary")]
    expected = " ".join(s for s in sentences if s) or present.DEFAULT_BOUNDARY
    assert text.split("\n\n")[-1] == expected.replace("\u2014", "-").replace("\u2013", "-")


def test_nothing_the_fault_sanitizer_replaces_reaches_the_text(serve):
    canary = "Traceback (most recent call last): synthetic-secret-token /Users/synthetic/app.py"
    payload = {**SYNTHETIC_RESEARCH_DOWN, "reason": canary}
    result = _result(serve, "research_network", {}, payload)
    assert canary not in result["content"][0]["text"]
    assert canary not in json.dumps(result["structuredContent"])
    text = _block(result)
    assert "synthetic-secret-token" not in text and "/Users/synthetic" not in text
    assert f"reason: {result['structuredContent']['reason']} |" in text


# ------------------------------------------- what each mapped word draws (D2, D5)
def test_freshness_decides_first_and_a_current_word_keeps_the_claim():
    text = render.compose(_view(SYNTHETIC_WORKBENCH, "market_workbench"))
    assert "USD/SYA, synthetic: 1.4578952098499147 SYA per USD\n◆ Derived | period 2026-11-11" in text
    assert "source Synthetic Central Bank" in text
    assert "attribution Source: Synthetic Central Bank - synthetic attribution." in text
    assert "EUR/SYB, synthetic: 0.85 SYB per EUR\n● Observed" in text
    assert "USD/SYC, synthetic: ○ Stale" in text and "2.5" not in text
    assert "USD/SYE, synthetic: ⊘ Restricted\nreason: Synthetic: rights are not established." in text
    assert "USD/SYF, synthetic: ▨ Unavailable\nreason: Synthetic: no matching dates." in text
    assert "Synthetic licensed fixings, syn_restricted: ⊘ Restricted" in text
    assert "china: ▨ Unavailable\nreason: Synthetic: no accepted export." in text
    assert text.count("USD/SYA, synthetic:") == 1  # china.fx repeats a served row once


def test_a_word_with_no_row_is_printed_under_no_state_and_never_drawn():
    view = _view(SYNTHETIC_WORKBENCH, "market_workbench")
    text = render.compose(view)
    assert "USD/SYD" not in text and "3.5" not in text
    assert "Not mapped to a display state: status dead." in text
    assert view.incomplete and render.INCOMPLETE in text
    desk = render.compose(_view(SYNTHETIC_DESK, "money_market_context"))
    assert "Not mapped to a display state: regime NORMAL, freshness dead." in desk
    for calm in ("● Observed", "◆ Derived"):
        assert calm not in desk
    assert "Synthetic dead indicator" not in desk and "1.75" not in desk


def test_seiche_stale_rules_and_atlas_words_are_gaps_with_their_clocks():
    health = render.compose(_view(SYNTHETIC_HEALTH, "data_health"))
    assert ("Synthetic stale series, SYN_STALE: ○ Stale\nreason: Not stated | period 2026-09-30 | "
            "known 2026-11-11 21:00 UTC | attribution Source: Synthetic Statistics Office. | "
            "checked 2026-11-12 00:30 UTC\n"
            "Limits: Synthetic: age of the latest observation versus its cadence.") in health
    # A mapped gap still wins beside a word with no row; the word is printed too.
    assert "Synthetic missing series, SYN_GONE: ▨ Unavailable" in health
    assert ("Not mapped to a display state: staleness dead, staleness unknown, "
            "fault status FAILED.") in health
    assert "Synthetic dead series" not in health
    assert "Synthetic fresh series" not in health and "Synthetic aging series" not in health
    gift = render.compose(_view(SYNTHETIC_GIFT, "gift_city_context"))
    assert "Synthetic rate B, SYN_B: ○ Stale" in gift and "3.25" not in gift
    assert "Synthetic rate C, SYN_C: ▨ Unavailable\nreason: Synthetic: the canonical funding" in gift
    assert "Synthetic reference D, SYN_D: 4.025 %\n● Observed | period 2026-11-11" in gift
    assert "Not mapped to a display state: availability AVAILABLE." in gift
    assert "5.59" not in gift
    assert "SYNTHETIC GOLD: ○ Stale" in gift


def test_india_desk_gaps_and_open_words():
    text = render.compose(_view(SYNTHETIC_INDIA, "money_market_context"))
    assert "SYN.RESTRICTED: ⊘ Restricted" in text and "SYN.UNAVAILABLE: ▨ Unavailable" in text
    assert "2s5s, benchmark_ytm: ▨ Unavailable\nreason: Synthetic: both tenors are required." in text
    assert "5s10s, benchmark_ytm: ○ Stale\nreason: Synthetic: a curve leg is stale." in text
    assert "liquidity_summary: ○ Stale" in text
    assert "fixed_tenor_curve: ▨ Unavailable\nreason: Synthetic: no fixed-tenor curve - see" in text
    assert ("Not mapped to a display state: spread available, spread historical_reference, "
            "policy comparison dated_reference, mechanism insufficient_evidence.") in text
    for hidden in ("12.5", "7.25", "34.4", "9.0"):
        assert hidden not in text


def test_a_metric_lookup_miss_is_a_notice_not_a_state():
    view = _view(SYNTHETIC_INDIA_MISS, "money_market_context")
    assert view.notices == ["SYN.NO_SUCH: No match for this name"] and not view.readings
    assert "Not covered" not in render.compose(view)


def test_ok_false_is_unavailable_with_the_producers_reason():
    text = render.compose(_view(SYNTHETIC_DESK_DOWN, "money_market_context"))
    assert text.count("▨ Unavailable") == 1
    assert "reason: Synthetic: the desk is unavailable in this snapshot." in text
    assert "CANNOT_ASSESS" not in text


def test_world_domains_and_items_use_seiches_five_words():
    text = render.compose(_view(SYNTHETIC_WORLD, "world_markets_context"))
    assert text.count("money_markets: ⊘ Restricted") == 1  # the domain prints once
    assert "forex: ▨ Unavailable" in text
    assert "capital_markets" not in text  # a derived domain summary has no value to draw
    assert "Synthetic observed rate: 3.88 %\n● Observed" in text
    assert "Synthetic derived spread: 6.5 bp\n◆ Derived" in text
    assert "Synthetic old rate: ○ Stale" in text and "2.25" not in text
    assert "Synthetic gap: ▨ Unavailable\nreason: Synthetic: no eligible evidence." in text
    assert "Synthetic Krona, spot: 10.5 SYN per USD\n● Observed" in text
    assert "NORMAL" not in text  # a regime block summarises; it is not walked


def test_research_network_and_rights_status_follow_their_boundaries():
    down = render.compose(_view(SYNTHETIC_RESEARCH_DOWN, "research_network"))
    assert "Seiche: ▨ Unavailable\nreason: Synthetic: the published catalog could not be read." in down
    assert "checked 2026-11-12 00:37 UTC" in down
    up = _view(SYNTHETIC_RESEARCH, "research_network")
    assert not up.readings and up.incomplete
    rights = _view(public_rights.rights_status(), "source_rights_status")
    assert {r.state for r in rights.readings} == {present.RESTRICTED}
    assert {r.label for r in rights.readings} == set(public_rights.WITHHELD_SOURCES)
    assert {r.reason for r in rights.readings} == set(public_rights.WITHHELD_SOURCES.values())


# ------------------------------------------------- decision 5: cap and order
def _crowd(calm: int, gaps: int) -> adapters.View:
    observed = present.state_for("seiche", "observed")
    stale = present.state_for("seiche", "stale")
    view = adapters.View(open_words=["status dead"], limits="Synthetic limit. " * 40, incomplete=True)
    for index in range(calm):
        view.readings.append(present.reading_from_native(
            observed, label=f"Synthetic calm {index}", value=float(index), unit="%", period="2026-11-09"))
    for index in range(gaps):
        view.readings.append(present.reading_from_native(
            stale, label=f"Synthetic gap {index}", period="2026-09-30", reason="Synthetic: stale."))
    return view


@pytest.mark.parametrize("calm,gaps", [(200, 5), (200, 30), (3, 3)])
def test_the_cap_holds_and_nothing_is_cut_before_a_gap(calm, gaps):
    view = _crowd(calm, gaps)
    text = render.compose(view)
    assert len(text.encode("utf-8")) <= DECIDED_CAP_BYTES
    assert text.splitlines()[0] == "Not mapped to a display state: status dead." or gaps >= 30
    shown_gaps, shown_calm = text.count("○ Stale"), text.count("● Observed")
    assert shown_calm == 0 or shown_gaps == gaps
    if shown_gaps < gaps:  # gaps alone overflow: every optional line has given way
        assert "Not mapped" not in text and "Limits:" not in text
    if shown_calm and shown_gaps:
        assert text.rindex("○ Stale") < text.index("● Observed")
    if calm + gaps > 10:
        assert present.FULL_LIST_LINE in text.split("\n\n")
    assert text.split("\n\n")[-1] == present.DEFAULT_BOUNDARY


def test_limits_give_way_before_a_gap_and_the_open_line_before_a_gap():
    view = _crowd(0, 12)
    view.limits = "Synthetic limit. " * 180  # about 3 KB: with it, not every gap fits
    text = render.compose(view)
    assert text.count("○ Stale") == 12
    assert "Not mapped to a display state: status dead." in text
    assert "Limits:" not in text
    view.limits = "Synthetic short limit."
    assert "Limits: Synthetic short limit." in render.compose(view)


def test_the_tools_list_is_unchanged_by_the_text_block(monkeypatch):
    listing = mcp_server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, public=True)
    monkeypatch.setattr(render, "result_block", lambda *args, **kwargs: pytest.fail("called"))
    again = mcp_server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, public=True)
    assert listing == again
    assert "presentation" not in json.dumps(listing).lower()


def _served_clocks(value, found: set[str]) -> set[str]:
    if isinstance(value, dict):
        for item in value.values():
            _served_clocks(item, found)
    elif isinstance(value, list):
        for item in value:
            _served_clocks(item, found)
    elif isinstance(value, str):
        try:
            found.update((present.format_period(value), present.format_instant(value)))
        except (ValueError, TypeError):
            pass
    return found


@pytest.mark.parametrize("name,arguments,payload", CALLS, ids=IDS)
def test_every_printed_clock_was_served_never_the_render_time(serve, name, arguments, payload):
    result = _result(serve, name, arguments, payload)
    clocks = _served_clocks(result["structuredContent"], set())
    raw = json.dumps(result["structuredContent"], ensure_ascii=False, default=str)
    for stamp in _DATE.findall(_block(result)):
        assert stamp in clocks or (len(stamp) == 10 and stamp in raw), stamp


def test_the_block_is_the_same_at_a_later_render_time(serve, monkeypatch):
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2031, 1, 1, tzinfo=tz)

    first = _block(_result(serve, "data_health", {}, SYNTHETIC_HEALTH))
    monkeypatch.setattr(mcp_server, "datetime", Later)
    assert _block(_result(serve, "data_health", {}, SYNTHETIC_HEALTH)) == first
    assert "2031" not in first
