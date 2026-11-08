"""Country projections preserve source identity, rights and observation periods."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json

import pytest

from seiche import country_funding as desk
from seiche.domain.observation import (
    Observation, QualityState, RedistributionStatus, StalenessState, evidence_sha256,
)
from seiche.markets.funding_reference import (
    COUNTRIES, COUNTRY_MAP, EURO_COUNTRIES, OECD_ADAPTER, OECD_DATA_URL, OECD_SERIES,
)
from seiche.markets.registry import default_registry

NOW = datetime(2026, 10, 6, 8, tzinfo=UTC)


def row(market, instrument, day, value, **changes):
    pack = default_registry().get(market)
    spec = pack.instrument_map[instrument]
    source = pack.adapter_map[spec.source_adapter_id]
    observation = Observation(
        market_id=market, monetary_area_id=pack.monetary_area_id, jurisdiction_codes=pack.jurisdiction_codes,
        currency=pack.currency, instrument_id=instrument, semantic_role=spec.semantic_role,
        value=Decimal(str(value)), canonical_unit=spec.canonical_unit, rate_compounding=spec.rate_compounding,
        day_count=spec.day_count, event_time=datetime.fromisoformat(day).replace(tzinfo=UTC),
        source_publication_time=None, knowledge_time=NOW, revision_id="test", source=source.adapter_id,
        evidence_hash=evidence_sha256(f"{market}:{instrument}:{day}:{value}"), connector_classification=source.classification,
        redistribution_status=source.redistribution_status, quality=QualityState.ESTIMATED, staleness=StalenessState.FRESH,
    )
    return replace(observation, **changes)


def japan_rows():
    return [row("JP-JPY", f"JP.MOF.JGB_{tenor}Y", day, value)
            for tenor, values in ((5, (100, 110)), (10, (150, 154)))
            for day, value in zip(("2026-10-02", "2026-10-05"), values)]


def test_dated_japan_curve_uses_canonical_basis_points_and_unknown_publication():
    result = desk.build("JP", {"JP-JPY": japan_rows()}, now=NOW)
    curve = result["curves"][0]
    spread = next(s for s in curve["spreads"] if s["id"] == "5s10s")
    assert (spread["value_bp"], spread["change_bp"]) == (44, -6)
    assert spread["movement"] == "bear_flattening"
    assert spread["status"] == "historical_reference"
    assert spread["previous_asof"] == "2026-10-02" and spread["asof"] == "2026-10-05"
    metric = next(n for n in curve["nodes"] if n["tenor_years"] == 10)
    assert metric["value"] == 1.54 and metric["canonical_value"] == 154
    assert metric["status"] == "UNKNOWN" and metric["published_at"] is None
    assert result["coverage"]["current"] == 0 and result["coverage"]["complete"] is False
    assert '"history":' not in json.dumps(result)


def test_curve_never_joins_mismatched_latest_or_previous_dates():
    original = japan_rows()
    for index in (2, 3):
        altered = [replace(r, event_time=r.event_time - timedelta(days=1)) if i == index else r
                   for i, r in enumerate(original)]
        curve = desk.build("JP", {"JP-JPY": altered}, now=NOW)["curves"][0]
        spread = next(s for s in curve["spreads"] if s["id"] == "5s10s")
        assert spread["status"] == "unaligned" and spread["change_bp"] is None
        assert spread["movement"] == "unavailable"


@pytest.mark.parametrize("changes", [
    {"market_id": "KR-KRW"}, {"currency": "KRW"}, {"source": "other"},
    {"jurisdiction_codes": ("CN",)}, {"monetary_area_id": "CN"},
    {"redistribution_status": RedistributionStatus.PROHIBITED},
    {"knowledge_time": NOW + timedelta(seconds=1)},
    {"source_publication_time": NOW + timedelta(seconds=1), "knowledge_time": NOW + timedelta(seconds=1)},
    {"event_time": NOW + timedelta(seconds=1)},
])
def test_foreign_restricted_or_future_rows_cannot_enter_country(changes):
    bad = replace(japan_rows()[-1], **changes)
    result = desk.build("JP", {"JP-JPY": [bad]}, now=NOW)
    assert result["coverage"]["available"] == 0


def test_euro_policy_is_shared_but_national_yields_and_monthly_spreads_are_distinct():
    registry = default_registry()
    policy = next(i for i in registry.get("EA-EUR").instruments if i.source_adapter_id == "ecb_policy")
    observations = {
        "EA-EUR": [row("EA-EUR", policy.instrument_id, "2026-10-05", 250)],
        "DE-EUR": [row("DE-EUR", "DE.ECB.LONG_TERM_10Y_MONTHLY", "2026-08-31", "235.123456")],
        "FR-EUR": [row("FR-EUR", "FR.ECB.LONG_TERM_10Y_MONTHLY", "2026-08-31", "315.123459")],
    }
    result = desk.build("FR", observations, now=NOW)
    assert result["funding_market_id"] == "EA-EUR" and result["market_id"] == "FR-EUR"
    assert result["sections"][0]["scope"] == "Euro area"
    metric = result["curves"][0]["nodes"][0]
    assert metric["id"].startswith("FR.") and metric["observation_period"] == "2026-08"
    assert metric["aggregation"] == "monthly_average"
    spread = result["sovereign_spread_to_germany"]
    assert spread["value_bp"] == 80.000003 and spread["status"] == "historical_reference"
    observations["DE-EUR"][0] = replace(observations["DE-EUR"][0], event_time=datetime(2026, 7, 31, tzinfo=UTC))
    assert desk.build("FR", observations, now=NOW)["sovereign_spread_to_germany"]["status"] == "unaligned"


def test_monthly_oecd_term_rate_is_not_relabelled_as_chinese_overnight():
    result = desk.build("CN", {"CN-CNY": [row("CN-CNY", "CN.OECD.INTERBANK_3M_MONTHLY", "2026-07-31", 154)]}, now=NOW)
    metric = next(m for s in result["sections"] for m in s["metrics"] if m["id"].startswith("CN.OECD"))
    assert metric["cadence"] == "P1M" and metric["semantic_role"] == "TERM_3M"
    assert metric["observation_period"] == "2026-07" and metric["value"] == 1.54
    assert metric["source_url"] == OECD_DATA_URL + "CHN.M.IR3TIB.PA._Z._Z._Z._Z.N"
    assert metric["source_reference"]["national_source"] == "The Central Bank of the People's Republic of China"
    assert result["curves"] == [] and result["coverage"]["complete"] is False


def oecd_rows():
    rows = {}
    for item in OECD_SERIES:
        market = COUNTRY_MAP[item.country].market_id
        rows.setdefault(market, []).append(row(market, item.instrument_id, "2026-09-30", 300 + len(rows)))
    return rows


def test_every_oecd_row_cites_the_oecd_with_its_access_date_and_never_fred():
    observations = oecd_rows()
    for country in COUNTRIES:
        result = desk.build(country.code, observations, now=NOW)
        assert "stlouisfed" not in json.dumps(result).lower()
        assert all("fred" not in json.dumps(source).lower() for source in result["sources"])
    served = 0
    for item in OECD_SERIES:
        result = desk.build(item.country, observations, now=NOW)
        metric = next(m for m in [*(m for s in result["sections"] for m in s["metrics"]),
                                  *(n for c in result["curves"] for n in c["nodes"])]
                      if m["id"] == item.instrument_id)
        reference = metric["source_reference"]
        assert metric["availability"] == "AVAILABLE" and metric["source"] == OECD_ADAPTER
        assert metric["source_url"] == reference["source_url"] == OECD_DATA_URL + item.key
        assert reference["rights_url"] == "https://www.oecd.org/en/about/terms-conditions.html"
        assert reference["attribution"] == metric["attribution"]
        assert metric["attribution"].startswith("OECD (2026), Financial market (OECD.SDD.STES:DSD_STES@DF_FINMARK(4.0))")
        assert (OECD_DATA_URL + item.key + " (accessed on 2026-10-06)") in metric["attribution"]
        assert reference["accessed_at"] == NOW.isoformat()
        served += 1
    assert served == 11
    from seiche.markets.atlas import build_global_money_market_atlas
    atlas = build_global_money_market_atlas(default_registry().list(), observations, as_of=NOW)
    metrics = [m for market in atlas["markets"] for m in market["metrics"] if ".OECD." in m["id"]]
    assert len(metrics) == 11 and all(m["source_url"].startswith(OECD_DATA_URL) for m in metrics)
    assert all(m["attribution"].startswith("OECD (2026)") for m in metrics)
    assert "stlouisfed" not in json.dumps(atlas["country_funding"]).lower()


def test_rest_and_mcp_serve_the_same_cited_oecd_rows(monkeypatch):
    from fastapi import Request, Response
    from seiche import api, mcp_server
    expected = desk.build("KR", oecd_rows(), now=NOW)
    monkeypatch.setattr(desk, "read", lambda country=None, **kwargs: expected if country else desk.catalog())
    request = Request({"type": "http", "query_string": b"", "headers": [], "client": ("127.0.0.1", 23456)})
    served = mcp_server.tool_money_market({"section": "countries", "country": "KR"}, True)["countries"]
    assert served == expected == api.country_funding_v2(request, Response(), "KR")
    oecd = [m for s in served["sections"] for m in s["metrics"] if ".OECD." in m["id"]]
    oecd += [n for c in served["curves"] for n in c["nodes"] if ".OECD." in n["id"]]
    assert {m["id"] for m in oecd} == {"KR.OECD.INTERBANK_3M_MONTHLY", "KR.OECD.LONG_TERM_10Y_MONTHLY"}
    assert all("National source named in OECD metadata: Bank of Korea." in m["attribution"] for m in oecd)
    assert "stlouisfed" not in json.dumps(served).lower()


def test_rows_stored_by_the_retired_fred_mirror_are_never_served(tmp_path, monkeypatch):
    from fastapi import Response
    from seiche import api, store
    from seiche.markets.materialize import _public_observations
    pack = default_registry().get("AU-AUD")
    current = row("AU-AUD", "AU.OECD.LONG_TERM_10Y_MONTHLY", "2026-09-30", 528)
    retired = replace(current, source="oecd_fred_reference", revision_id="fred-mirror",
                      evidence_hash=evidence_sha256("fred-mirror"))
    node = desk.build("AU", {"AU-AUD": [retired]}, now=NOW)["curves"][0]["nodes"][0]
    assert node["value"] is None and node["availability"] == "UNAVAILABLE"
    assert desk.build("AU", {"AU-AUD": [current]}, now=NOW)["curves"][0]["nodes"][0]["value"] == 5.28
    assert _public_observations(pack, [retired, current]) == [current]
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "retired-mirror.sqlite")
    store.save_observations([retired])
    atlas = api.global_money_markets_v2(Response())
    metric = next(m for market in atlas["markets"] if market["market_id"] == "AU-AUD"
                  for m in market["metrics"] if m["id"] == "AU.OECD.LONG_TERM_10Y_MONTHLY")
    assert metric["value"] is None and metric["availability"] == "UNAVAILABLE"
    store.save_observations([current])
    metric = next(m for market in api.global_money_markets_v2(Response())["markets"]
                  if market["market_id"] == "AU-AUD"
                  for m in market["metrics"] if m["id"] == "AU.OECD.LONG_TERM_10Y_MONTHLY")
    assert metric["value"] == 5.28 and metric["source"] == OECD_ADAPTER
    # The series route redacts each row through this same value policy.
    assert not api._observation_value_is_public(pack, retired)
    assert not api._observation_is_publicly_derivable(pack, retired)
    assert api._observation_value_is_public(pack, current)


def test_country_catalog_is_structural_and_never_touches_repository(monkeypatch):
    monkeypatch.setattr(desk, "get_repository", lambda: pytest.fail("catalog touched database"))
    result = desk.read()
    assert result["status"] == "structural" and len(result["countries"]) == len(COUNTRIES)
    assert {"CN", "JP", "KR", "TW", "AU", "DE", "FR", "GB", "ES"} <= {c["code"] for c in result["countries"]}


def test_reader_is_bounded_and_only_reads_admitted_countries_and_public_series(monkeypatch):
    calls = []
    class Repository:
        def load_observations_as_of(self, *args, **kwargs):
            calls.append((args, kwargs))
            return []
    monkeypatch.setattr(desk, "get_repository", lambda: Repository())
    desk.read("FR", now=NOW)
    assert {args[0] for args, _ in calls} == {"FR-EUR", "DE-EUR", "EA-EUR"}
    assert all(kwargs["event_time_from"] == NOW.replace(hour=0) - timedelta(days=400) for _, kwargs in calls)
    assert all(kwargs["instrument_ids"] for _, kwargs in calls)
    calls.clear()
    result = desk.read("UK", now=NOW)
    assert result["country"] == "GB" and calls[0][0][0] == "UK-GBP"
    assert all("SONIA" not in instrument for instrument in calls[0][1]["instrument_ids"])


def test_rest_mcp_and_assembled_country_data_agree(monkeypatch):
    from fastapi import Request, Response, HTTPException
    from seiche import api, mcp_server
    observations = {"JP-JPY": japan_rows()}
    expected = desk.build("JP", observations, now=NOW)
    monkeypatch.setattr(desk, "read", lambda country=None, **kwargs: expected if country else desk.catalog())
    request = Request({"type": "http", "query_string": b"", "headers": [], "client": ("127.0.0.1", 23456)})
    response = Response()
    assert api.country_funding_v2(request, response, "JP") == expected
    assert mcp_server.tool_money_market({"section": "countries", "country": "JP"}, True)["countries"] == expected
    assert response.headers["Cache-Control"] == "public, max-age=60"
    with pytest.raises(HTTPException) as exc:
        api.country_funding_v2(Request({**request.scope, "query_string": b"refresh=true"}), Response(), "JP")
    assert exc.value.status_code == 422
    with pytest.raises(mcp_server.ToolError):
        mcp_server.tool_money_market({"section": "summary", "country": "JP"}, True)
    with pytest.raises(mcp_server.ToolError):
        mcp_server.tool_money_market({"section": "countries", "country": "ZZ"}, True)


def test_atlas_does_not_count_national_sovereigns_as_separate_clearing_systems():
    from seiche.markets.atlas import build_global_money_market_atlas
    result = build_global_money_market_atlas(default_registry().list(), {}, as_of=NOW)
    ids = {m["market_id"] for m in result["markets"]}
    assert "EA-EUR" in ids and not ids & {c + "-EUR" for c in EURO_COUNTRIES}
    assert len(result["country_funding"]["desks"]) == len(COUNTRIES)
    assert not ids & {item["market_id"] for item in result["expansion_ledger"]}


def test_canonical_read_failure_is_reported_without_private_details(monkeypatch):
    class Repository:
        def load_observations_as_of(self, *args, **kwargs):
            raise RuntimeError("postgresql://secret@private-host")
    monkeypatch.setattr(desk, "get_repository", lambda: Repository())
    result = desk.read("JP", now=NOW)
    assert result["status"] == "unavailable" and result["read_faults"]
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("changes", [
    {"redistribution_status": RedistributionStatus.PROHIBITED},
    {"quality": QualityState.REJECTED},
])
def test_latest_withdrawn_revision_cannot_resurrect_older_public_value(changes):
    previous = replace(japan_rows()[-1], knowledge_time=NOW - timedelta(minutes=1))
    withdrawn = replace(previous, knowledge_time=NOW, revision_id="withdrawn", **changes)
    result = desk.build("JP", {"JP-JPY": [previous, withdrawn]}, now=NOW)
    assert result["coverage"]["available"] == 0
    assert not any(n["value"] is not None for n in result["curves"][0]["nodes"])
    earlier = desk.build("JP", {"JP-JPY": [previous, withdrawn]}, now=NOW - timedelta(seconds=1))
    assert earlier["coverage"]["available"] == 1


def test_date_window_includes_boundary_day_and_matches_canonical_read(monkeypatch):
    start = NOW.replace(hour=0) - timedelta(days=400)
    retained = replace(japan_rows()[-1], event_time=start)
    excluded = replace(retained, event_time=start - timedelta(days=1))
    class Repository:
        def load_observations_as_of(self, market, cutoff, **kwargs):
            assert kwargs["event_time_from"] == start
            return [retained]
    monkeypatch.setattr(desk, "get_repository", lambda: Repository())
    assert desk.build("JP", {"JP-JPY": [excluded, retained]}, now=NOW) == desk.read("JP", now=NOW)


def test_reference_materialization_seals_observations_without_gauge(tmp_path, monkeypatch):
    from seiche import store
    from seiche.markets.calibration import LocalCalibration
    from seiche.markets.funding_reference import additional_reference_packs
    from seiche.markets.materialize import materialize_market, materialize_global_tide
    from seiche.repository import SQLiteMarketRepository
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "references.sqlite")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    repository = SQLiteMarketRepository()
    for pack in additional_reference_packs():
        spec = pack.instruments[0]
        repository.save_observations([row(pack.market_id, spec.instrument_id, "2026-08-31", 250)])
        result = materialize_market(pack.market_id, repository=repository, knowledge_time=NOW)
        assert set(result) == {"overview", "gauge"}
        gauge = repository.load_latest_market_snapshot(pack.market_id, "gauge")["payload"]
        assert gauge["status"] == "UNAVAILABLE" and gauge["reading"]["index"] is None
        assert gauge["calibration_maturity"] == "REFERENCE_ONLY"
        assert gauge["components"] == [] and gauge["evidence_eligibility"]["eligible"] is False
        assert "no local gauge calibration" in gauge["reading"]["publication_reason"]
    materialize_global_tide(repository=repository, knowledge_time=NOW)
    tide = repository.load_latest_market_snapshot("GLOBAL", "tide")["payload"]
    assert len(tide["data_coverage"]) == 19
    assert not {row["market_id"] for row in tide["data_coverage"]} & {code + "-EUR" for code in EURO_COUNTRIES}
    with pytest.raises(ValueError, match="required component"):
        LocalCalibration("bad", "JP-JPY", ())
