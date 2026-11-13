"""Rates research reads: stated arithmetic, date alignment, provenance, routes, MCP. Offline only.

Curve and spread tests use real ECB yield curve rows (verbatim fixtures parsed by the
production adapter). Funding-condition tests use constructed rows whose values are
chosen to make the arithmetic checkable by hand; they are test data, not observations.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import math
from pathlib import Path

import pytest

from seiche import mcp_server, rates_desk
from seiche.domain.observation import Observation, QualityState, StalenessState, evidence_sha256
from seiche.markets.registry import default_registry
from seiche.sources import rates_sources
from seiche.sources.canonical import FetchedDocument, FunctionalCanonicalAdapter

FIXTURES = Path(__file__).parent / "fixtures" / "rates"
CAPTURE = datetime(2026, 11, 13, 19, 12, 44, tzinfo=UTC)
NOW = datetime(2026, 11, 13, 19, 30, tzinfo=UTC)
YC_FIXTURES = {
    "EA.ECB.YC_AAA_SPOT_10Y": "ecb-yc-aaa-sr10y-2026-11-09-to-12.csv",
    "EA.ECB.YC_AAA_SPOT_2Y": "ecb-yc-aaa-sr2y-2026-11-09-to-12.csv",
    "EA.ECB.YC_AAA_SPOT_3M": "ecb-yc-aaa-sr3m-2026-11-09-to-12.csv",
    "EA.ECB.YC_ALL_SPOT_10Y": "ecb-yc-all-sr10y-2026-11-09-to-12.csv",
}


# ----------------------------------------------------------------------------- arithmetic

def test_percentile_rank_uses_mid_rank_for_ties():
    assert rates_desk.percentile_rank([1.0, 2.0, 3.0, 4.0, 5.0]) == 90.0
    assert rates_desk.percentile_rank([3.0, 1.0, 2.0]) == 50.0
    assert rates_desk.percentile_rank([1.0, 1.0, 1.0]) == 50.0
    assert rates_desk.percentile_rank([5.0, 4.0, 1.0]) == pytest.approx(100 * 0.5 / 3)
    assert rates_desk.percentile_rank([]) is None


def test_robust_z_scales_the_median_absolute_deviation():
    # median 3, absolute deviations 2,1,0,1,7 -> MAD 1 -> scale 1.4826
    assert rates_desk.robust_z([1.0, 2.0, 3.0, 4.0, 10.0]) == pytest.approx(7 / 1.4826)
    assert rates_desk.robust_z([2.0, 2.0, 2.0]) == 0.0
    assert rates_desk.robust_z([2.0, 2.0, 2.0, 2.0, 9.0]) is None


def test_continuous_forward_is_exact_under_continuous_compounding():
    forward = rates_desk.continuous_forward(2.0, 1.0, 3.0, 2.0)
    assert forward == pytest.approx(4.0)
    # exp(s2 t2) = exp(s1 t1) exp(f (t2 - t1)) with rates in decimals
    assert math.exp(0.03 * 2) == pytest.approx(math.exp(0.02 * 1) * math.exp(forward / 100 * 1))
    assert rates_desk.continuous_forward(3.0, 5.0, 3.5, 10.0) == pytest.approx(4.0)
    with pytest.raises(ValueError):
        rates_desk.continuous_forward(3.0, 5.0, 3.5, 5.0)


def test_alignment_skips_dates_missing_a_leg_and_never_fills():
    class Row:
        def __init__(self, day, value):
            self.event_time = datetime(2026, 1, day, tzinfo=UTC)
            self.value = Decimal(value)
    long = [Row(1, "300"), Row(2, "310"), Row(3, "320")]
    short = [Row(2, "200"), Row(3, "205"), Row(4, "210")]
    assert rates_desk._aligned([(long, 1), (short, -1)]) == [(date(2026, 1, 2), 110.0), (date(2026, 1, 3), 115.0)]


def test_regime_states_its_window_and_refuses_short_history():
    start = date(2020, 1, 1)
    short = [(start + timedelta(days=i), float(i)) for i in range(59)]
    assert rates_desk.regime(short, 1095)["status"] == "insufficient_history"
    assert rates_desk.regime(short, 1095)["percentile"] is None
    values = [(start + timedelta(days=i), float(i % 10)) for i in range(400)]
    values.append((start + timedelta(days=400), 100.0))
    result = rates_desk.regime(values, 300)
    assert result["status"] == "ok" and result["window_days"] == 300
    # 2021-02-04 minus 300 calendar days is 2020-04-10 (2020 is a leap year)
    assert result["window"] == {"requested_from": "2020-04-10", "from": "2020-04-10", "through": "2021-02-04",
                                "observations": 301}
    assert result["percentile"] == pytest.approx(round(100 * 300.5 / 301, 1))
    assert result["max"] == {"value_bp": 100.0, "date": "2021-02-04"}
    assert result["minimum_observations"] == 60 and "percentile = 100" in result["formula"]


# ----------------------------------------------------------------------------- store-backed

def _store(tmp_path, monkeypatch):
    from seiche import store
    from seiche.repository import SQLiteMarketRepository, reset_repository_cache
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "rates.sqlite")
    monkeypatch.delenv("SEICHE_DATABASE_URL", raising=False)
    reset_repository_cache()
    rates_desk.clear_cache()
    return SQLiteMarketRepository()


def _load_yc(repository):
    documents = tuple(FetchedDocument("https://data-api.ecb.europa.eu/fixture", "text/csv",
                                      (FIXTURES / name).read_bytes(), label)
                      for label, name in YC_FIXTURES.items())

    async def fetch(_client):
        return documents

    adapter = FunctionalCanonicalAdapter(
        pack=default_registry().get("EA-EUR"), adapter_id="ecb_yield_curve", source="ecb_yield_curve",
        fetcher=fetch, parser=rates_sources.parse_ecb_yield_curve, repository=repository, clock=lambda: CAPTURE)
    repository.save_observations(asyncio.run(adapter.collect()).observations)


def _row(market, instrument, day, value_bp):
    pack = default_registry().get(market)
    spec = pack.instrument_map[instrument]
    adapter = pack.adapter_map[spec.source_adapter_id]
    event = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    return Observation(
        market_id=market, monetary_area_id=pack.monetary_area_id, jurisdiction_codes=pack.jurisdiction_codes,
        currency=pack.currency, instrument_id=instrument, semantic_role=spec.semantic_role,
        value=Decimal(str(value_bp)), canonical_unit=spec.canonical_unit, rate_compounding=spec.rate_compounding,
        day_count=spec.day_count, event_time=event, source_publication_time=event + timedelta(hours=31),
        knowledge_time=CAPTURE, revision_id=f"test:{instrument}:{day}", source=adapter.adapter_id,
        evidence_hash=evidence_sha256(f"{instrument}:{day}:{value_bp}"), connector_classification=adapter.classification,
        redistribution_status=adapter.redistribution_status, quality=QualityState.VERIFIED,
        staleness=StalenessState.FRESH,
    )


def test_curve_uses_one_date_states_slopes_and_never_interpolates(tmp_path, monkeypatch):
    repository = _store(tmp_path, monkeypatch)
    _load_yc(repository)
    result = rates_desk.curve("EA_AAA_SPOT", compare=date(2026, 11, 10), repository=repository, now=NOW)
    assert result["status"] == "ok" and result["asof"] == "2026-11-12"
    nodes = {node["tenor"]: node for node in result["nodes"]}
    assert nodes["10Y"]["value"] == 3.367246 and nodes["10Y"]["canonical_value"] == 336.7246
    assert nodes["10Y"]["published_at"] == "2026-11-13T11:00:00+00:00"
    assert nodes["10Y"]["knowledge_time"] == CAPTURE.isoformat()
    # 336.72459164 - 334.00829714 basis points
    assert nodes["10Y"]["previous"] == {"asof": "2026-11-11", "value": 3.340083, "change_bp": 2.7163}
    assert nodes["5Y"]["value"] is None and "nothing is filled" in nodes["5Y"]["reason"]
    slopes = {slope["id"]: slope for slope in result["slopes"]}
    # 336.72459164 - 307.51415729 and 333.08865271 - 301.85936813 basis points
    assert slopes["2s10s"]["value_bp"] == 29.2104 and slopes["2s10s"]["status"] == "dated"
    assert slopes["2s10s"]["comparison_value_bp"] == 31.2293 and slopes["2s10s"]["change_bp"] == -2.0189
    assert slopes["3m10y"]["value_bp"] == 73.1491
    assert slopes["5s30s"]["status"] == "unavailable" and slopes["5s30s"]["value_bp"] is None
    assert result["comparison"] == {"requested": "2026-11-10", "used": "2026-11-10",
                                    "rule": "latest observation date on or before the requested date"}
    # Forward pairs need both printed tenors; none are filled in.
    assert result["forwards"] == []
    provenance = result["provenance"]
    assert [entry["source"] for entry in provenance] == ["ecb_yield_curve"]
    assert provenance[0]["terms_url"].startswith("https://www.ecb.europa.eu/services/using-our-site/disclaimer")
    assert provenance[0]["fetched_at"] == CAPTURE.isoformat() and provenance[0]["asof"] == "2026-11-12"
    assert provenance[0]["staleness"] == "fresh" and provenance[0]["instruments"] == 3


def test_curve_date_rule_uses_latest_date_on_or_before_request(tmp_path, monkeypatch):
    repository = _store(tmp_path, monkeypatch)
    _load_yc(repository)
    assert rates_desk.curve("EA_AAA_SPOT", on=date(2026, 11, 11), repository=repository, now=NOW)["asof"] == "2026-11-11"
    before = rates_desk.curve("EA_AAA_SPOT", on=date(2026, 11, 8), repository=repository, now=NOW)
    assert before["status"] == "unavailable" and before["asof"] is None
    with pytest.raises(ValueError, match="future"):
        rates_desk.curve("EA_AAA_SPOT", on=date(2026, 11, 14), repository=repository, now=NOW)
    with pytest.raises(ValueError, match="unknown curve"):
        rates_desk.curve("EA_AAA", repository=repository, now=NOW)


def test_spread_history_regime_and_issuer_sample_spread(tmp_path, monkeypatch):
    repository = _store(tmp_path, monkeypatch)
    _load_yc(repository)
    slope = rates_desk.spread("EA_AAA_2S10S", repository=repository, now=NOW)
    assert slope["latest"] == {"value_bp": 29.2104, "asof": "2026-11-12", "status": "FRESH"}
    assert len(slope["history"]) == 4 and slope["history_unit"] == "bp"
    assert slope["regime"]["status"] == "insufficient_history" and slope["regime"]["window"]["observations"] == 4
    issuer = rates_desk.spread("EA_ALL_MINUS_AAA_10Y", repository=repository, now=NOW)
    # 395.16211543 - 336.72459164 basis points
    assert issuer["latest"]["value_bp"] == 58.4375
    assert issuer["formula"].startswith("EA.ECB.YC_ALL_SPOT_10Y minus EA.ECB.YC_AAA_SPOT_10Y")
    with pytest.raises(ValueError, match="window_days"):
        rates_desk.spread("EA_AAA_2S10S", window_days=100, repository=repository, now=NOW)
    listed = rates_desk.spreads(repository=repository, now=NOW)
    by_id = {item["spread_id"]: item for item in listed["spreads"]}
    assert by_id["EA_AAA_2S10S"]["latest"]["value_bp"] == 29.2104
    assert by_id["US_TREASURY_PAR_2S10S"]["latest"] is None


def test_series_window_counts_and_display_units(tmp_path, monkeypatch):
    repository = _store(tmp_path, monkeypatch)
    _load_yc(repository)
    result = rates_desk.series("EA.ECB.YC_AAA_SPOT_10Y", days=1, repository=repository, now=NOW)
    assert result["observations"] == [["2026-11-11", 3.340083], ["2026-11-12", 3.367246]]
    assert result["window"]["stored_observations"] == 4 and result["window"]["first_stored"] == "2026-11-09"
    assert result["observation_unit"] == "%" and result["instrument"]["family"] == "government_curve"
    assert result["instrument"]["tenor"] == "10Y" and result["instrument"]["tenor_years"] == 10.0
    assert result["instrument"]["label"] == "Euro area AAA government spot rate, 10 years"
    with pytest.raises(ValueError, match="unknown or non-public"):
        rates_desk.series("US.NYFED.SOFR", repository=repository, now=NOW)
    with pytest.raises(ValueError, match="days"):
        rates_desk.series("EA.ECB.YC_AAA_SPOT_10Y", days=0, repository=repository, now=NOW)


def test_catalog_counts_only_stored_dates_and_lists_absence(tmp_path, monkeypatch):
    repository = _store(tmp_path, monkeypatch)
    _load_yc(repository)
    result = rates_desk.catalog(repository=repository, now=NOW)
    assert result["totals"] == {"markets": 1, "instruments": 4, "observations": 16, "sources": 1}
    market = result["markets"][0]
    assert market["market_id"] == "EA-EUR" and market["declared_without_observations"] > 0
    item = next(i for i in market["instruments"] if i["instrument_id"] == "EA.ECB.YC_ALL_SPOT_10Y")
    assert (item["first_observation"], item["last_observation"], item["observations"]) == ("2026-11-09", "2026-11-12", 4)
    assert item["knowledge_time"] == CAPTURE.isoformat() and item["status"] == "FRESH"
    assert {curve["curve_id"] for curve in result["curves"]} == {"EA_AAA_SPOT", "EA_ALL_SPOT"}


def _funding_rows(days=80):
    rows = []
    start = date(2026, 8, 1)
    for offset in range(days):
        day = start + timedelta(days=offset)
        estr = 192 + (offset % 5) * 0.5        # 192.0 to 194.0 bp
        rows += [_row("EA-EUR", "EA.ECB.ESTR", day, estr), _row("EA-EUR", "EA.ECB.DFR", day, 200),
                 _row("EA-EUR", "EA.ECB.MLF", day, 240)]
    # one benchmark date without policy legs must not be filled in
    rows.append(_row("EA-EUR", "EA.ECB.ESTR", start + timedelta(days=days), 190))
    return rows


def test_funding_conditions_spread_corridor_and_percentile(tmp_path, monkeypatch):
    repository = _store(tmp_path, monkeypatch)
    repository.save_observations(_funding_rows())
    # Evaluated after the rows' capture time: rows are never visible before Seiche knew them.
    early = rates_desk.funding_conditions(markets="EA-EUR", window_days=250, repository=repository,
                                          now=datetime(2026, 10, 21, 12, tzinfo=UTC))
    assert early["markets"][0]["status"] == "unavailable"
    result = rates_desk.funding_conditions(markets="ea-eur", window_days=250, repository=repository, now=NOW)
    market = result["markets"][0]
    spread = market["spread_to_policy"]
    # last shared date is offset 79: ESTR 192 + (79 % 5) * 0.5 = 194.0, DFR 200 -> -6.0 bp
    assert spread["asof"] == "2026-10-19" and spread["value_bp"] == -6.0 and spread["status"] == "dated"
    assert spread["legs"] == ["EA.ECB.ESTR", "EA.ECB.DFR"]
    # -6.0 is the top of the sample: 64 values below, 16 equal -> 100 * (64 + 8) / 80
    assert spread["percentile"]["status"] == "ok" and spread["percentile"]["percentile"] == 90.0
    corridor = market["corridor"]
    # (194 - 200) / (240 - 200)
    assert corridor == {"position": -0.15, "width_bp": 40.0, "asof": "2026-10-19", "status": "dated", "reason": None}
    assert market["overnight"]["asof"] == "2026-10-20"  # the benchmark's own latest date stays visible
    assert "No published distribution percentiles are stored for this benchmark." in market["known_gaps"]
    assert result["formulas"]["corridor_position"].startswith("(overnight minus floor)")


def test_funding_conditions_lists_markets_without_data_and_rejects_unknown(tmp_path, monkeypatch):
    repository = _store(tmp_path, monkeypatch)
    result = rates_desk.funding_conditions(markets="EA-EUR,HK-HKD,SG-SGD", repository=repository, now=NOW)
    by_market = {m["market_id"]: m for m in result["markets"]}
    assert set(by_market) == {"EA-EUR", "HK-HKD", "SG-SGD"} and result["status"] == "unavailable"
    assert by_market["HK-HKD"]["status"] == "unavailable"
    assert "No redistributable overnight benchmark is declared for this market." in by_market["HK-HKD"]["known_gaps"]
    assert by_market["SG-SGD"]["spread_to_policy"]["reason"] == "No public overnight benchmark." or \
        by_market["SG-SGD"]["spread_to_policy"]["reason"] == "No public policy rate anchor is declared."
    with pytest.raises(ValueError, match="unknown market"):
        rates_desk.funding_conditions(markets="ZZ-ZZZ", repository=repository, now=NOW)


def test_rest_routes_serve_payloads_and_plain_422s(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from seiche import api
    repository = _store(tmp_path, monkeypatch)
    _load_yc(repository)
    monkeypatch.setattr(api._market_series_limiter, "allow", lambda _key: True)
    client = TestClient(api.app)
    response = client.get("/api/v2/rates/curves/EA_AAA_SPOT")
    assert response.status_code == 200 and response.headers["cache-control"] == "public, max-age=60"
    assert response.json()["schema"] == "seiche.rates.curve.v1"
    assert client.get("/api/v2/rates").json()["totals"]["instruments"] == 4
    assert client.get("/api/v2/rates/series/EA.ECB.YC_AAA_SPOT_2Y?days=1").json()["window"]["observations"] == 2
    assert client.get("/api/v2/rates/spreads/EA_AAA_2S10S").json()["latest"]["value_bp"] == 29.2104
    for path, detail in (
        ("/api/v2/rates?x=1", "unknown query parameter(s): x"),
        ("/api/v2/rates/series/EA.ECB.YC_AAA_SPOT_2Y?days=abc", "days must be a whole number"),
        ("/api/v2/rates/curves/EA_AAA_SPOT?date=2026-13-01", "date must be an ISO date YYYY-MM-DD"),
        ("/api/v2/rates/spreads/EA_AAA_2S10S?window_days=10", "window_days must be an integer from 250 to 7300"),
        ("/api/v2/rates/funding-conditions?markets=ZZ-ZZZ", "unknown market id(s): ZZ-ZZZ"),
        ("/api/v2/rates/curves/NOPE", "unknown curve; see /api/v2/rates/curves"),
    ):
        failed = client.get(path)
        assert failed.status_code == 422 and failed.json()["detail"] == detail


def test_mcp_sections_keep_the_envelope_and_trim_history(tmp_path, monkeypatch):
    repository = _store(tmp_path, monkeypatch)
    _load_yc(repository)
    out = mcp_server.tool_money_market({"section": "curve", "curve": "EA_AAA_SPOT"}, True)
    assert out["ok"] and out["selection"] == "curve" and out["curve"]["asof"] == "2026-11-12"
    assert out["sources"][0]["publisher"] == "European Central Bank"
    listed = mcp_server.tool_money_market({"section": "curve"}, True)
    assert listed["curve"]["schema"] == "seiche.rates.curves.v1"
    spread = mcp_server.tool_money_market({"section": "spread", "spread": "EA_AAA_2S10S", "window_days": 365}, True)
    assert spread["spread"]["regime"]["window_days"] == 365
    trimmed = mcp_server._rates_trim({"history": [[str(i), i] for i in range(45)]})
    assert len(trimmed["history"]) == 30 and trimmed["history_points_omitted"] == 15
    for bad in ({"section": "curve", "spread": "EA_AAA_2S10S"}, {"section": "summary", "curve": "EA_AAA_SPOT"},
                {"section": "rate_series"}, {"section": "spread", "window_days": "365"},
                {"section": "curve", "date": "2026-11-12"}):
        with pytest.raises(mcp_server.ToolError):
            mcp_server.tool_money_market(bad, True)
