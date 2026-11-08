"""Owner decisions, the per-engine public projection and the direct-source rewiring.

Policy seiche.public-source-rights.2026-11-09: a reading is public only when
every input it reads is an allowed direct or open source and the snapshot's
own provenance proves it. Each test here also proves the closed side: a FRED
copy, a blocked series, a missing decision or an unverifiable row withholds.
"""
from __future__ import annotations

import copy
import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from seiche import api, assemble, mcp_server, methodology, public_rights, source_decisions, store
from seiche.config import ALL_SERIES
from seiche.engines import money_market
from seiche.sources import fed_releases as fed, fred, official_statistics as stats, original_publishers
from test_money_market import _card, _world

OWNER_WORDS = "go with them all just make sure everything starts working"
DECISIONS = {
    source_decisions.FED_CP: ("fed_cp", {"CP_NONFIN_3M", "CP_FIN_3M"}),
    source_decisions.BLS_API: ("bls_original", {"CORE_CPI", "ENERGY_CPI"}),
    source_decisions.BOJ_TOTAL_ASSETS: ("boj_original", {"BOJ_ASSETS"}),
    source_decisions.BOE_SONIA: ("boe_original", {"SONIA"}),
}
BLOCKED = ("VIX", "HY_OAS", "IG_OAS", "SP500", "NASDAQ", "WTI_SPOT", "BRENT_SPOT",
           "HENRY_HUB_SPOT", "NATGAS_SPOT", "COMMODITY_ALL", "COPPER", "ALUMINUM", "NICKEL",
           "COAL", "WHEAT", "CORN", "TED", "NFCI")
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "projection.sqlite")
    monkeypatch.setattr(methodology, "DB_PATH", tmp_path / "projection.sqlite")
    public_rights._MEMO.update(src=None, board=None)
    yield tmp_path
    public_rights._MEMO.update(src=None, board=None)


def without_decision(monkeypatch, decision_id):
    """The registry as if this one owner decision had never been recorded."""
    full = source_decisions.registry()
    reduced = {**full, "decisions": [row for row in full["decisions"] if row["id"] != decision_id]}
    monkeypatch.setattr(source_decisions, "registry", lambda: reduced)
    public_rights._MEMO.update(src=None, board=None)


def provenance(*, fred_copies=(), drop=(), status=None, duplicate=()):
    """Rows as a completed build writes them, with every capture qualified."""
    rows = []
    for mnemonic, spec in ALL_SERIES.items():
        if mnemonic in drop:
            continue
        qualified = public_rights._qualified_source(mnemonic)
        direct = qualified is not None and mnemonic not in fred_copies
        row = {"mnemonic": mnemonic, "source": qualified if direct else spec.source,
               "remote_id": spec.remote_id, "freq": spec.freq, "asof": "2026-11-05",
               "availability": "available"}
        if direct:
            row["redistribution_status"] = (status or {}).get(mnemonic, "allowed")
        rows.append(row)
        if mnemonic in duplicate:
            rows.append(dict(row))
    return rows


def engine_payload(name, value=777.123):
    return {"ok": True, "asof": "2026-11-05", "value": value, "label": f"{name} sentinel"}


def board(**kwargs):
    engines = {name: engine_payload(name) for name in public_rights.ENGINE_INPUTS
               if name != "money_market"}
    deep = {name: engine_payload(name) for name in public_rights.DEEP_INPUTS}
    deep.update(ok=True, historical_evidence={"validated_backtest_eligible": False})
    return {
        "generated_at": "2026-11-09T00:00:00+00:00", "version": "test",
        "engines": engines, "deep": deep, "faults": [],
        "headline": {key: {"value": 777.123, "asof": "2026-11-05"} for key in public_rights.HEADLINE_INPUTS},
        "calendar": {key: [{"date": "2026-11-20"}] for key in public_rights.CALENDAR_INPUTS},
        "editorial": {"thesis": "777.123 sentinel thesis"},
        "navigator": {"ok": True, "p_event_5bd": 777.123},
        "system": {"editorial_config": {"composite_weights": {"tails": 0.1}},
                   "alert_log": {"alerts": [{"value": 777.123}]}},
        "provenance": provenance(**kwargs),
    }


def statuses(projected, section="engines"):
    return {name: verdict["status"] for name, verdict in projected["rights_projection"][section].items()}


# ---------------------------------------------------------------------------
# The owner's decisions
# ---------------------------------------------------------------------------


def test_four_decisions_are_dated_quote_the_owner_and_name_their_sources():
    registry = source_decisions.registry()
    assert registry["schema"] == "seiche.source-use-decisions.v1"
    recorded = {row["id"]: row for row in registry["decisions"]}
    assert set(recorded) == set(DECISIONS)
    for decision_id, (source, series) in DECISIONS.items():
        row = recorded[decision_id]
        assert row["owner_words"] == OWNER_WORDS
        assert row["decided_on"] == "2026-11-08" and row["recorded_at"].startswith("2026-11-08T22:40")
        assert row["redistribution_status"] == "allowed" and source_decisions.allows(decision_id)
        assert source in row["sources"] and set(row["series"]) == series
        assert all(url.startswith("https://") for url in row["terms_urls"])
        assert public_rights.SOURCES[source]["decision"] == decision_id
        assert series <= set(original_publishers.CONTRACTS)
    doc = (ROOT / "docs" / "rights" / "owner-source-decisions-20261108.md").read_text()
    assert f'"{OWNER_WORDS}"' in doc
    assert all(decision_id in doc for decision_id in DECISIONS)
    assert chr(0x2013) not in doc and chr(0x2014) not in doc  # no en or em dashes


@pytest.mark.parametrize("decision_id", sorted(DECISIONS))
def test_each_decision_gates_its_adapter_its_export_and_its_readings(isolated, monkeypatch, decision_id):
    source, series = DECISIONS[decision_id]
    mnemonic = sorted(series)[0]
    contract = original_publishers.CONTRACTS[mnemonic]
    expected = (fed.contract(mnemonic) if "release" in contract else stats.contract(mnemonic))
    assert expected["source_use_decision"] == decision_id
    status = fed.redistribution_status if "release" in contract else stats.redistribution_status
    assert status(expected) == "allowed" and public_rights.source_allowed(source)
    allowed = public_rights._Ledger({"provenance": provenance()}).check(mnemonic)
    assert allowed[0] is True

    without_decision(monkeypatch, decision_id)
    assert status(expected) == "owner_decision_required"
    assert public_rights.source_allowed(source) is False
    assert public_rights.input_can_be_public(mnemonic) is False
    ok, seen, reason = public_rights._Ledger({"provenance": provenance()}).check(mnemonic)
    assert ok is False and seen == source and "decision" in reason


def test_a_capture_without_its_decision_decodes_but_cannot_be_exported(isolated, monkeypatch):
    body = BOJ_CSV.encode()
    rows, extra = stats.parse_boj_assets(body)
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    doc = {"source_url": stats.source_url("BOJ_ASSETS"), "bytes": len(body),
           "sha256": __import__("hashlib").sha256(body).hexdigest(), "retrieved_at": stamp}
    store.save_blob(stats._key("BOJ_ASSETS"), stats.make_capture("BOJ_ASSETS", {"BOJ_ASSETS": (rows, extra)}, [doc], stamp))
    assert methodology.csv_restriction("BOJ_ASSETS") is None
    assert TestClient(api.app).get("/api/series/BOJ_ASSETS.csv").status_code == 200

    without_decision(monkeypatch, source_decisions.BOJ_TOTAL_ASSETS)
    series = original_publishers.load_series("BOJ_ASSETS")
    assert series is not None
    assert series.provenance()["redistribution_status"] == "owner_decision_required"
    assert "decision" in methodology.csv_restriction("BOJ_ASSETS")
    assert TestClient(api.app).get("/api/series/BOJ_ASSETS.csv").status_code == 403


def test_bank_of_japan_data_never_reaches_a_paid_surface():
    assert public_rights.source_allowed("boj_original")
    assert public_rights.source_allowed("boj_original", surface="paid") is False
    assert public_rights.source_allowed("boj", surface="paid") is False
    ledger = public_rights._Ledger({"provenance": provenance()}, surface="paid")
    assert ledger.check("BOJ_ASSETS")[0] is False
    assert ledger.check("WALCL")[0] is True


# ---------------------------------------------------------------------------
# The per-engine projection
# ---------------------------------------------------------------------------

PUBLIC_ENGINES = {
    "money_market", "kink", "rdenowcast", "weather", "supplydesk", "runway", "tails", "stigma",
    "echo", "rvxray", "crowding", "officialbid", "auctions", "reportcard", "ledger", "resonance",
    "undertow", "phasemap", "edetect", "communique", "breakwater", "hydrophone", "merian",
    "roguewave", "caesar", "warehouse", "thermohaline", "spillover", "stationkeeping", "composite",
}
WITHHELD_ENGINES = {"basins", "harbors", "farbasin", "moorings", "cpsentinel", "oilfunding",
                    "ballast", "estuary", "windfetch", "sonar", "scuttlebutt"}
PUBLIC_DEEP = {"history", "turn", "microseism", "leakaudit", "tidetables", "swell", "bathymetry",
               "markov", "oujump", "montecarlo", "funding_pop", "riptide", "gyre", "seastate"}
WITHHELD_DEEP = {"tell", "playbook", "backtest", "refereegli", "ml", "stacker", "regatta",
                 "searoom", "book", "modelcourt"}


def test_static_feasibility_matches_the_reviewed_scope():
    assert PUBLIC_ENGINES | WITHHELD_ENGINES == set(public_rights.ENGINE_INPUTS)
    assert PUBLIC_DEEP | WITHHELD_DEEP == set(public_rights.DEEP_INPUTS)
    assert {name for name in public_rights.ENGINE_INPUTS
            if public_rights.engine_can_be_public(name)} == PUBLIC_ENGINES
    assert {name for name in public_rights.DEEP_INPUTS
            if public_rights.engine_can_be_public(name, public_rights.DEEP_INPUTS)} == PUBLIC_DEEP
    for mnemonic in BLOCKED + ("CALL_IN", "CALL_JP", "CALL_KR"):
        assert public_rights.input_can_be_public(mnemonic) is False, mnemonic


def test_direct_board_publishes_exactly_the_readings_whose_inputs_are_all_allowed():
    snapshot = board()
    before = copy.deepcopy(snapshot)
    projected = public_rights.public_board(snapshot)
    assert snapshot == before

    engines, deep = statuses(projected), statuses(projected, "deep")
    assert {n for n, s in engines.items() if s == "public"} == PUBLIC_ENGINES - {"money_market"}
    assert {n for n, s in engines.items() if s == "withheld"} == WITHHELD_ENGINES
    assert {n for n, s in deep.items() if s == "public"} == PUBLIC_DEEP
    assert {n for n, s in deep.items() if s == "withheld"} == WITHHELD_DEEP
    assert projected["engines"]["composite"] == snapshot["engines"]["composite"]
    assert projected["deep"]["tidetables"] == snapshot["deep"]["tidetables"]
    assert projected["deep"]["historical_evidence"] == snapshot["deep"]["historical_evidence"]

    for name in WITHHELD_ENGINES:
        block = projected["engines"][name]
        assert public_rights.is_withheld_block(block) and block["ok"] is False
        assert "777.123" not in json.dumps(block) and "asof" not in block
    assert projected["engines"]["oilfunding"]["restricted_inputs"] == ["BRENT_SPOT", "WTI_SPOT"]
    assert "VIX" in projected["deep"]["tell"]["restricted_inputs"]
    assert "NASDAQ" in projected["deep"]["refereegli"]["restricted_inputs"]
    assert set(projected["engines"]["moorings"]["restricted_inputs"]) == {"crypto"}

    assert projected["headline"]["sofr_pct"]["value"] == 777.123
    assert projected["headline"]["vix"] is None and projected["headline"]["hy_oas_pct"] is None
    assert projected["calendar"]["next_turn"] and projected["calendar"]["crunch_windows"]
    for section in ("editorial", "navigator"):
        assert public_rights.is_withheld_block(projected[section])
    assert projected["system"]["alert_log"]["alerts"] == []
    assert projected["system"]["editorial_config"] == snapshot["system"]["editorial_config"]
    assert "777.123 sentinel thesis" not in json.dumps(projected)

    sources = projected["rights_projection"]["sources"]
    assert {"fed_prates", "fed_h41", "nyfed_original", "nyfed_rates", "ofr", "tga"} <= set(sources)
    for note in sources.values():
        assert note["terms_url"].startswith("https://") and note["attribution"]
    assert "fred" not in sources
    assert public_rights.public_board(projected)["engines"] == projected["engines"]


@pytest.mark.parametrize("change, expect_withheld, expect_public", [
    ({"fred_copies": ("WRESBAL",)}, {"kink", "weather", "runway", "ledger", "composite", "echo"}, {"tails", "resonance"}),
    ({"fred_copies": ("SOFR",)}, {"resonance", "echo", "composite"}, {"tails", "ledger", "officialbid"}),
    ({"fred_copies": ("IOER",)}, {"tails", "stigma", "composite"}, {"ledger", "warehouse"}),
    ({"drop": ("GDP",)}, {"kink", "composite"}, {"tails"}),
    ({"duplicate": ("EFFR",)}, {"echo", "hydrophone", "spillover"}, {"kink"}),
    ({"status": {"CP_NONFIN_3M": "owner_decision_required"}}, set(), {"composite"}),
    ({"fred_copies": ("SRF_CEILING",)}, {"stigma"}, {"tails"}),
])
def test_one_unproven_input_withholds_every_reading_that_reads_it(change, expect_withheld, expect_public):
    engines = statuses(public_rights.public_board(board(**change)))
    assert {name for name in expect_withheld if engines[name] == "withheld"} == expect_withheld
    assert {name for name in expect_public if engines[name] == "public"} == expect_public


def test_rows_that_cannot_prove_their_identity_fail_closed():
    rows = provenance()
    row = next(item for item in rows if item["mnemonic"] == "VIX")
    row.update(source="fed_h15", redistribution_status="allowed")   # a blocked series cannot borrow an identity
    ledger = public_rights._Ledger({"provenance": rows})
    assert ledger.check("VIX")[0] is False
    walcl = next(item for item in rows if item["mnemonic"] == "WALCL")
    walcl.pop("redistribution_status")
    assert public_rights._Ledger({"provenance": rows}).check("WALCL")[0] is False
    walcl.update(redistribution_status="allowed", availability="unavailable")
    assert public_rights._Ledger({"provenance": rows}).check("WALCL")[0] is False
    for legacy in ({"WALCL": {"asof": "2026-11-05"}}, None, "rows"):
        assert public_rights._Ledger({"provenance": legacy}).check("WALCL")[0] is False
    projected = public_rights.public_board({**board(), "engines": {"novel_engine": engine_payload("x")}})
    assert public_rights.is_withheld_block(projected["engines"]["novel_engine"])
    assert projected["rights_projection"]["engines"]["novel_engine"]["status"] == "withheld"


def test_restrictions_and_shapeless_snapshots_stay_restrictions():
    restriction = public_rights.legacy_unavailable("/api/overview")
    assert public_rights.public_board(restriction) == restriction
    for value in (None, [], {"generated_at": "x"}, {"engines": []}):
        assert public_rights.is_restriction(public_rights.public_board(value))


# ---------------------------------------------------------------------------
# Routes and tools
# ---------------------------------------------------------------------------


@pytest.fixture
def served(isolated, monkeypatch):
    holder = {"snap": board()}

    async def snapshot(force=False):
        return holder["snap"]

    monkeypatch.setattr(assemble, "snapshot", snapshot)
    monkeypatch.setattr(mcp_server, "_get_snapshot", lambda force=False: holder["snap"])
    monkeypatch.setattr(mcp_server, "_get_in_memory_completed_snapshot", lambda: holder["snap"])
    return holder


def test_engine_routes_serve_public_readings_and_refuse_the_rest(served, monkeypatch):
    client = TestClient(api.app)
    response = client.get("/api/engines/kink")
    assert response.status_code == 200
    body = response.json()
    assert body["value"] == 777.123
    assert body["publication_rights"]["status"] == "public"
    assert "fed_h41" in body["publication_rights"]["sources"]
    assert all(note["terms_url"] for note in body["publication_rights"]["sources"].values())

    withheld = client.get("/api/engines/tails")
    assert withheld.status_code == 200
    served["snap"] = board(fred_copies=("IOER",))
    public_rights._MEMO.update(src=None, board=None)
    refused = client.get("/api/engines/tails")
    assert refused.status_code == 403 and refused.headers["cache-control"] == "no-store"
    assert refused.json()["category"] == "source_rights_restricted"
    assert refused.json()["restricted_inputs"] == ["IOER"]
    assert "777.123" not in refused.text
    assert client.get("/api/engines/not_an_engine").status_code == 404

    async def forbidden(*args, **kwargs):
        pytest.fail("a statically withheld engine must not load a snapshot")
    monkeypatch.setattr(assemble, "snapshot", forbidden)
    for name in ("oilfunding", "estuary", "sonar", "basins", "harbors"):
        assert client.get(f"/api/engines/{name}").status_code == 403


def test_board_routes_serve_only_the_projection(served):
    client = TestClient(api.app)
    overview = client.get("/api/overview").json()
    assert public_rights.is_projection(overview)
    assert overview["engines"]["composite"]["value"] == 777.123
    assert overview["engines"]["oilfunding"]["availability"] == "withheld"
    assert "777.123 sentinel thesis" not in json.dumps(overview)
    deep = client.get("/api/deep").json()
    assert deep["tidetables"]["value"] == 777.123 and deep["tell"]["availability"] == "withheld"
    gauge = client.get("/api/gauge").json()
    assert gauge["index"] == 777.123 and gauge["tell"] is None and gauge["p_event_5bd"] is None
    assert gauge["publication_rights"]["composite"] == "public"
    assert {"tell", "p_event_5bd", "navigator", "modelcourt"} <= set(gauge["publication_rights"]["withheld"])
    for path in ("/api/public", "/api/book", "/api/brief", "/api/pit", "/api/asof/2026-10-01"):
        assert client.get(path).status_code == 403, path


def test_projection_ready_tools_stay_refused_over_mcp_and_their_handlers_project(served):
    """The anonymous MCP surface stays at eight tools until the owner grows it.

    The three projection-ready tools are still refused before any snapshot
    loads, and their handlers already serve only the projection, so opening
    one cannot expose a withheld reading.
    """
    def call(name):
        return mcp_server.dispatch({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                    "params": {"name": name, "arguments": {}}}, public=True)["result"]

    listed = {tool["name"] for tool in mcp_server.dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, public=True)["result"]["tools"]}
    assert len(listed) == 8 and not listed & public_rights.LEGACY_TOOLS
    assert set(public_rights.PROJECTED_TOOLS) <= public_rights.LEGACY_TOOLS
    for name in public_rights.PROJECTED_TOOLS:
        refused = call(name)
        assert refused["isError"] is True and refused["structuredContent"]["availability"] == "unavailable"

    now = mcp_server.tool_stress_now({}, False)
    assert now["composite"]["value"] == 777.123
    assert now["tell"]["availability"] == "withheld"
    assert "777.123 sentinel thesis" not in json.dumps(now)
    envelope = mcp_server.tool_stress_now({}, True)
    assert envelope["schema"] == "seiche.public.v2"
    assert "777.123 sentinel thesis" not in json.dumps(envelope)
    assert mcp_server.tool_analogs({}, True)["as_of"] == served["snap"]["generated_at"]
    assert mcp_server.tool_trade_safety_risk_context({}, True)["status"] in {"available", "unavailable"}

    served["snap"] = board(fred_copies=("SOFR",))
    public_rights._MEMO.update(src=None, board=None)
    for handler in (mcp_server.tool_stress_now, mcp_server.tool_analogs):
        with pytest.raises(mcp_server.StructuredToolError) as refused:
            handler({}, True)
        assert refused.value.payload["category"] == "source_rights_restricted"
        assert "SOFR" in refused.value.payload["restricted_inputs"]
        assert "777.123" not in json.dumps(refused.value.payload)
    assert mcp_server.tool_trade_safety_risk_context({}, True)["status"] == "unavailable"


def test_rights_status_lists_the_projectable_scope_and_decisions():
    status = public_rights.rights_status()
    assert status["policy"] == public_rights.POLICY
    assert set(status["publicly_projectable_engines"]) == PUBLIC_ENGINES
    assert set(status["publicly_projectable_deep"]) == PUBLIC_DEEP
    assert {row["id"] for row in status["decisions"]} == set(DECISIONS)
    assert all(row["owner_words"] == OWNER_WORDS for row in status["decisions"])
    assert "fred" in status["withheld_sources"]


# ---------------------------------------------------------------------------
# Direct-source rewiring (path A)
# ---------------------------------------------------------------------------


def _board_zip(release, observations):
    root = ET.Element("Data")
    for mnemonic, rows in observations.items():
        node = ET.SubElement(root, "Series", fed.CONTRACTS[mnemonic]["dimensions"])
        for day, value in rows:
            ET.SubElement(node, "Obs", TIME_PERIOD=day, OBS_STATUS="A", OBS_VALUE=str(value))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(release.upper() + "_data.xml", ET.tostring(root))
    return out.getvalue()


def _nyfed_rates(kind, rows):
    out = []
    for day, rate in rows:
        row = {"type": kind, "effectiveDate": day, "percentRate": rate, "revisionIndicator": ""}
        if kind == "EFFR":
            row.update(targetRateFrom=3.75, targetRateTo=4.0)
        out.append(row)
    return json.dumps({"refRates": out}).encode()


def test_spread_and_ceiling_inputs_are_fetched_direct_and_never_from_fred(isolated):
    days = ["2020-06-03", "2021-07-28", "2022-01-05", "2026-11-05"]
    prates = _board_zip("prates", {"IOER": [("2020-06-03", 0.10), ("2021-07-28", 0.15)],
                                   "IORB": [("2022-01-05", 0.15), ("2026-11-05", 3.90)]})
    requested = []

    def handler(request):
        url = str(request.url)
        requested.append(url)
        assert "stlouisfed" not in request.url.host, "FRED must never be called for direct aliases"
        if url == fed.URLS["prates"]:
            # The Board adapter streams raw bytes, so serve an unread stream.
            return httpx.Response(200, stream=httpx.ByteStream(prates))
        if url == stats.source_url("SOFR"):
            return httpx.Response(200, content=_nyfed_rates("SOFR", [(d, 4.0) for d in days]))
        if url == stats.source_url("EFFR"):
            return httpx.Response(200, content=_nyfed_rates("EFFR", [(d, 3.88) for d in days]))
        return httpx.Response(404)

    async def collect():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            faults = []
            return await fred.fetch_many(client, ["SOFR", "EFFR", "IORB", "IOER", "SRF_CEILING"], faults), faults

    out, faults = __import__("asyncio").run(collect())
    assert faults == []
    assert {m: s.source for m, s in out.items()} == {
        "SOFR": "nyfed_original", "EFFR": "nyfed_original", "SRF_CEILING": "nyfed_original",
        "IORB": "fed_prates", "IOER": "fed_prates"}
    assert all(isinstance(s, original_publishers.QUALIFIED_TYPES) for s in out.values())
    assert out["SRF_CEILING"].points.tolist() == [4.0] * len(days)
    assert len({url for url in requested if "unsecured/effr" in url}) == 1

    derived = assemble._derived({"fred": out})
    assert derived["spread_bp"].round(6).tolist() == [390.0, 385.0, 385.0, 10.0]
    splice = fred.splice_iorb(out["IORB"], out["IOER"])
    assert splice.source == "fed_prates" and splice.remote_id == "RESBME_N.D+RESBM_N.D"

    rows = assemble._provenance({"fred": out})
    by_name = {row["mnemonic"]: row for row in rows}
    for mnemonic in out:
        assert by_name[mnemonic]["availability"] == "available"
        assert by_name[mnemonic]["source"] == out[mnemonic].source
        assert by_name[mnemonic]["redistribution_status"] == "allowed"
        assert by_name[mnemonic]["history_export_allowed"] is True
    assert by_name["VIX"]["source"] == "fred" and by_name["VIX"]["availability"] == "unavailable"


def _qualified(mnemonic, values):
    spec = ALL_SERIES[mnemonic]
    source = public_rights._qualified_source(mnemonic)
    cls = fed.FedSeries if source.startswith("fed_") else stats.StatisticsSeries
    return cls(mnemonic, source, "REMOTE", spec.label, spec.unit, spec.freq,
               "2026-11-09T00:00:00+00:00", values,
               {"redistribution_status": "allowed", "terms_url": "https://example.org/terms",
                "attribution": "Source: test publisher."})


def _direct_desk():
    inputs = _world(120)
    src = {"fred": {}, "fred_cp_rates": {}}
    for key, (group, mnemonics, _label, _short) in assemble._DESK_INPUTS.items():
        for mnemonic in mnemonics:
            src[group][mnemonic] = _qualified(mnemonic, inputs[key])
    sources = assemble._desk_input_sources(src)
    assert set(sources) == set(money_market.LEGACY_INPUT_SOURCES)
    return money_market.analyze(**inputs, input_sources=sources)


def test_money_desk_core_reads_and_labels_the_direct_feeds():
    desk = _direct_desk()
    sofr, iorb, spread = (_card(desk, name) for name in ("policy.sofr", "policy.iorb", "policy.sofr_minus_iorb"))
    assert sofr["source"] == "Federal Reserve Bank of New York SOFR, Markets API (direct)"
    assert sofr["source_ids"] == ["nyfed_original:SOFR"]
    assert iorb["source_ids"] == ["fed_prates:IORB"]
    assert spread["source"] == "NY Fed SOFR + Board PRATES IORB"
    assert "via FRED" not in json.dumps([s["metrics"] for s in desk["sections"]])
    assert not any(str(row["id"]).startswith("fred_") for row in desk["source_metadata"])
    assert desk["diagnostics"]["source_ids"] == ["nyfed_original:SOFR", "fed_prates:IORB", "nyfed_original:EFFR"]

    projected = public_rights.money_desk(desk)
    assert projected["publication_projection"]["status"] == "public"
    assert projected["regime"] == desk["regime"] and projected["quant_read"] == desk["quant_read"]
    assert projected["charts"] == desk["charts"]
    for name in ("policy.sofr", "unsecured.cp_nonfinancial_3m", "liquidity.reserves", "bills.bill_3m"):
        assert _card(projected, name)["value"] == _card(desk, name)["value"] is not None
    assert any(note.get("terms_url") == "https://www.federalreserve.gov/disclaimer.htm"
               for note in projected["legal_notices"])
    assert public_rights.money_desk(projected) == projected


def test_money_desk_without_the_cp_decision_withholds_cp_and_the_desk_narrative(monkeypatch):
    desk = _direct_desk()
    without_decision(monkeypatch, source_decisions.FED_CP)
    projected = public_rights.money_desk(desk)
    assert projected["regime"]["state"] == "CANNOT_ASSESS"
    for name in ("unsecured.cp_nonfinancial_3m", "unsecured.financial_minus_nonfinancial_cp",
                 "unsecured.nonfinancial_cp_minus_treasury"):
        assert _card(projected, name)["value"] is None
    for name in ("policy.sofr", "policy.sofr_minus_iorb", "unsecured.treasury_3m", "liquidity.reserves"):
        assert _card(projected, name)["value"] == _card(desk, name)["value"] is not None


def test_legacy_desk_without_acquisitions_keeps_its_fred_labels_and_stays_withheld():
    desk = money_market.analyze(**_world(120))
    assert _card(desk, "policy.sofr")["source"] == "New York Fed SOFR via FRED"
    assert "source_ids" not in _card(desk, "policy.sofr") and "input_sources" not in desk
    projected = public_rights.money_desk(desk)
    assert _card(projected, "policy.sofr")["value"] is None
    assert projected["regime"]["state"] == "CANNOT_ASSESS"


# ---------------------------------------------------------------------------
# Parsers added on this branch
# ---------------------------------------------------------------------------

BOJ_CSV = (
    "STATUS,200\nMESSAGEID,M181000I\nMESSAGE,Successfully completed\nDATE,2026-11-09T07:40:37.520+09:00\n"
    "PARAMETER,FORMAT,CSV\nPARAMETER,LANG,EN\nPARAMETER,DB,BS01\nPARAMETER,STARTDATE,200001\n"
    "PARAMETER,ENDDATE,\nPARAMETER,STARTPOSITION,\nNEXTPOSITION,\n"
    "SERIES_CODE,NAME_OF_TIME_SERIES,UNIT,FREQUENCY,CATEGORY,LAST_UPDATE,SURVEY_DATES,VALUES\n"
    'MABJMTA,"Bank of Japan Accounts/Assets/Total (Assets, or Liabilities and Net Assets) (s)",100 million yen,MONTHLY,Bank of Japan Accounts,20261105,202609,6250253\n'
    'MABJMTA,"Bank of Japan Accounts/Assets/Total (Assets, or Liabilities and Net Assets) (s)",100 million yen,MONTHLY,Bank of Japan Accounts,20261105,202610,6261882\n'
)


def test_boj_total_assets_parse_is_exact_and_fails_closed():
    rows, extra = stats.parse_boj_assets(BOJ_CSV.encode())
    assert rows == [["2026-09-01", 6250253.0], ["2026-10-01", 6261882.0]]
    assert extra == {"source_publication_date": "2026-11-05"}
    assert stats.source_url("BOJ_ASSETS").endswith("db=BS01&code=MABJMTA&startDate=200001")
    for old, new in (("100 million yen", "billion yen"), ("MONTHLY", "QUARTERLY"), ("STATUS,200", "STATUS,400"),
                     ("NEXTPOSITION,", "NEXTPOSITION,2"), (",6261882", ","), ("20261105,202610", "20261106,202610"),
                     ("MABJMTA,", "MABJMTB,")):
        with pytest.raises(ValueError):
            stats.parse_boj_assets(BOJ_CSV.replace(old, new, 1).encode())


def test_boj_capture_carries_its_credit_condition_and_decision(isolated):
    body = BOJ_CSV.encode()
    rows, extra = stats.parse_boj_assets(body)
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    doc = {"source_url": stats.source_url("BOJ_ASSETS"), "bytes": len(body),
           "sha256": __import__("hashlib").sha256(body).hexdigest(), "retrieved_at": stamp}
    capture = stats.make_capture("BOJ_ASSETS", {"BOJ_ASSETS": (rows, extra)}, [doc], stamp)
    series = stats.decode("BOJ_ASSETS", capture)["BOJ_ASSETS"]
    p = series.provenance()
    assert series.source == "boj_original" and series.unit == ALL_SERIES["BOJ_ASSETS"].unit
    assert p["attribution"].startswith("Source: Bank of Japan") and "2026-11-05" in p["attribution"]
    assert any("free, no-paywall" in notice for notice in p["required_notices"])
    assert p["source_use_decision"]["id"] == source_decisions.BOJ_TOTAL_ASSETS
    assert p["source_use_decision"]["owner_words"] == OWNER_WORDS
    assert p["terms_url"] == "https://www.stat-search.boj.or.jp/info/notice_en.html"


def test_target_range_ceiling_comes_from_complete_effr_rows():
    body = _nyfed_rates("EFFR", [("2026-11-04", 3.88), ("2026-11-05", 3.87)])
    parsed = stats.parse_nyfed_effr_group(body)
    assert parsed["SRF_CEILING"][0] == [["2026-11-04", 4.0], ["2026-11-05", 4.0]]
    assert parsed["EFFR"][0] == [["2026-11-04", 3.88], ["2026-11-05", 3.87]]
    for change in ({"targetRateTo": None}, {"targetRateFrom": 4.25}, {"targetRateFrom": 2.5}):
        rows = json.loads(body)
        rows["refRates"][0].update(change)
        with pytest.raises(ValueError):
            stats.parse_nyfed_effr_group(json.dumps(rows).encode())
    assert stats.source_url("SRF_CEILING") == stats.source_url("EFFR")
    assert "term" not in stats.source_url("RRPONTSYD") and "reverserepo" in stats.source_url("RRPONTSYD")


def test_term_reverse_repos_are_excluded_from_the_overnight_total():
    def operation(ident, term, amount):
        return {"operationId": ident, "operationType": "Reverse Repo", "term": term,
                "operationMethod": "Fixed Rate", "auctionStatus": "Results", "settlementType": "Same Day",
                "operationDate": "2026-10-30", "settlementDate": "2026-10-30", "maturityDate": "2026-11-02",
                "lastUpdated": "2026-10-30 13:15:24", "details": [{"securityType": "Treasury", "amtAccepted": amount}]}
    ops = [operation("RP 1", "Overnight", 4_000_000_000), operation("RP 2", "Term", 9_000_000_000)]
    parsed = stats.parse_nyfed_rrp(json.dumps({"repo": {"operations": ops}}).encode())
    assert parsed["RRPONTSYD"][0] == [["2026-10-30", 4.0]]


def test_bls_capture_states_the_terms_of_service_sentence():
    contract = stats.contract("CORE_CPI")
    assert contract["terms_url"] == "https://www.bls.gov/developers/termsOfService.htm"
    assert contract["source_use_decision"] == source_decisions.BLS_API
    assert stats.BLS_REQUIRED_STATEMENT == (
        "BLS.gov cannot vouch for the data or analyses derived from these data after the data "
        "have been retrieved from BLS.gov.")


def test_prates_excludes_a_pre_announced_rate_until_it_takes_effect():
    body = _board_zip("prates", {"IOER": [("2021-07-28", 0.15)],
                                 "IORB": [("2026-11-05", 3.90), ("2099-01-02", 9.99)]})
    capture = fed.parse_capture("prates", body, datetime.now(UTC).isoformat(timespec="seconds"))
    iorb = fed.decode_capture("prates", capture, ["IORB"])["IORB"]
    assert iorb.points.tolist() == [3.90]
    assert iorb.provenance()["excluded_future_effective_observations"] == 1


def test_board_data_uses_only_release_page_xml_zips():
    for release, url in fed.URLS.items():
        assert re.fullmatch(rf"https://www\.federalreserve\.gov/releases/{release}/data/FRB_\w+_xml\.zip", url)
    offenders = [path for path in (ROOT / "backend" / "seiche").rglob("*.py")
                 if re.search(r"datadownload|Build Your Package", path.read_text(encoding="utf-8"), re.I)]
    assert offenders == []
