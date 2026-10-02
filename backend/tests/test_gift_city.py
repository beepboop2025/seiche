from datetime import UTC, datetime
from decimal import Decimal

import pytest

from seiche import gift_city as desk


def inputs(**overrides):
    return dict(quantity_kg="0.0311034768", fineness="1", price_usd_per_oz="4000",
                annual_rate_pct="6", fx_inr_per_usd="90", fees_usd="10", days=30, **overrides)


def test_gold_inventory_units_carry_and_fees():
    result = desk.gold_carry(inputs())
    values = result["outputs"]
    assert values["fine_troy_oz"] == "1.00000000"
    assert values["metal_value_usd"] == "4000.00000000"
    assert values["funding_cost_usd"] == "20.00000000"
    assert values["total_cost_usd"] == "4030.00000000"
    assert values["total_cost_inr"] == "362700.00000000"
    assert result["status"] == "scenario" and result["persisted"] is False
    assert result["eligibility"] == {"execution": False, "scoring": False}


def test_day_count_fineness_and_no_implicit_zero_fees():
    args = inputs(day_count=365)
    args.update(fineness="0.995", days=365)
    values = desk.gold_carry(args)["outputs"]
    assert values["fine_troy_oz"] == "0.99500000"
    assert values["funding_cost_usd"] == "238.80000000"
    del args["fees_usd"]
    with pytest.raises(ValueError):
        desk.gold_carry(args)


@pytest.mark.parametrize("key,value", [("days", True), ("days", 3661), ("day_count", True),
    ("day_count", 366), ("fineness", "1.01"), ("quantity_kg", "0"),
    ("price_usd_per_oz", "NaN"), ("price_usd_per_oz", "Infinity"),
    ("price_usd_per_oz", 4000), ("fx_inr_per_usd", "-1"), ("annual_rate_pct", "1001"),
    ("fees_usd", "1e999"), ("fees_usd", "0.0000000000001"), ("unknown", "1")])
def test_reject_ambiguous_or_unbounded_inputs(key, value):
    args = inputs()
    args[key] = value
    with pytest.raises(ValueError):
        desk.gold_carry(args)


def gold_blob(day="2026-09-22", fetched="2026-10-02T18:00:00Z"):
    return {"fetched_at": fetched, "rows": [{"cftc_contract_market_code": "088691",
        "report_date_as_yyyy_mm_dd": day + "T00:00:00.000", "open_interest_all": "100",
        "m_money_positions_long_all": "30", "m_money_positions_short_all": "10",
        "prod_merc_positions_long": "5", "prod_merc_positions_short": "45"}]}


def test_cftc_clock_does_not_refresh_old_positions():
    before = datetime(2026, 10, 2, 19, 29, tzinfo=UTC)
    after = datetime(2026, 10, 2, 19, 31, tzinfo=UTC)
    assert desk.gold_positioning(gold_blob(), before)["status"] == "fresh"
    result = desk.gold_positioning(gold_blob(fetched=after.isoformat()), after)
    assert result["status"] == "stale"
    assert result["as_of"] == "2026-09-22"
    assert result["managed_money_net_contracts"] == 20
    assert result["producer_net_contracts"] == -40
    assert desk.gold_positioning(gold_blob(day="2026-10-06"), before)["status"] == "unavailable"
    assert desk.gold_positioning(gold_blob(fetched="2026-10-03T00:00:00Z"), before)["status"] == "unavailable"


def test_gold_position_identity_and_integer_contracts():
    blob = gold_blob()
    blob["rows"][0]["cftc_contract_market_code"] = "067651"
    assert desk.gold_positioning(blob, datetime.now(UTC))["status"] == "unavailable"
    blob = gold_blob()
    blob["rows"][0]["open_interest_all"] = "1.5"
    assert desk.gold_positioning(blob, datetime.now(UTC))["status"] == "unavailable"


def test_mcp_and_http_share_scenario_without_persistence(monkeypatch):
    from fastapi.testclient import TestClient
    from seiche import api, mcp_server
    monkeypatch.setattr(api._market_series_limiter, "allow", lambda *_: True)
    monkeypatch.setattr(desk.store, "save_blob", lambda *_: pytest.fail("scenario wrote a blob"))
    result = mcp_server.tool_gold_carry(inputs(), True)
    with TestClient(api.app) as client:
        response = client.post("/api/v2/gift-city/gold-carry", json=inputs())
        assert response.status_code == 200
        assert response.json() == result
        assert response.headers["cache-control"] == "no-store"
        assert client.post("/api/v2/gift-city/gold-carry", json={"days": 30}).status_code == 422
    assert "gift_city_context" in mcp_server.TOOLS
    assert "gold_inventory_carry" in mcp_server.OUTPUT_SCHEMAS
    assert "/api/v2/gift-city/gold-carry" in api._public_openapi_document()["paths"]


def test_funding_row_rights_are_checked_before_projection(monkeypatch):
    from types import SimpleNamespace
    from seiche.domain.observation import RedistributionStatus
    class Repo:
        def load_observations_as_of(self, market, *args, **kwargs):
            return [SimpleNamespace(instrument_id="CALL_WAR" if market == "IN-INR" else "SOFR",
                                    redistribution_status=RedistributionStatus.PROHIBITED)]
    monkeypatch.setattr(desk, "get_repository", lambda: Repo())
    rows = desk._funding(datetime.now(UTC))
    assert all(row["value"] is None for row in rows)


def test_fx_reuse_does_not_require_unrelated_china_acceptance(monkeypatch):
    from seiche import market_workbench as wb
    monkeypatch.setattr(wb.store, "load_series_window_snapshot", lambda *a, **kw: ({}, {}))
    monkeypatch.setattr(wb.context_views, "public_china_economic_context", lambda: pytest.fail("unrelated intake queried"))
    result = wb.read({"provider": "ecb", "base": "USD", "quote": "INR"}, include_china=False)
    assert result["forex"]["coverage"]["available_pairs"] == 0


def test_maximum_decimal_precision_stays_finite():
    args = inputs()
    args.update(quantity_kg="999999999999.999999999999", price_usd_per_oz="999999999999.999999999999",
                fx_inr_per_usd="999999999999.999999999999", annual_rate_pct="1000", days=3660)
    assert Decimal(desk.gold_carry(args)["outputs"]["total_cost_inr"]).is_finite()


def test_funding_source_failure_does_not_hide_another_currency(monkeypatch):
    from seiche.domain.observation import (
        CanonicalUnit, ConnectorClassification, DayCountConvention, Observation,
        QualityState, RateCompounding, RedistributionStatus, SemanticRole,
        StalenessState, evidence_sha256,
    )

    now = datetime(2026, 10, 2, 19, tzinfo=UTC)
    row = Observation(
        market_id="IN-INR", monetary_area_id="IN", jurisdiction_codes=("IN",),
        currency="INR", instrument_id="IN.MARKET.CALL_WAR",
        semantic_role=SemanticRole.UNSECURED_OVERNIGHT, value=Decimal("522"),
        canonical_unit=CanonicalUnit.BASIS_POINTS, rate_compounding=RateCompounding.SIMPLE,
        day_count=DayCountConvention.ACT_365,
        event_time=datetime(2026, 9, 30, tzinfo=UTC),
        source_publication_time=datetime(2026, 9, 30, 18, 29, 59, tzinfo=UTC),
        knowledge_time=datetime(2026, 10, 1, 19, tzinfo=UTC),
        revision_id="fixture-sep30", source="rbi_official",
        evidence_hash=evidence_sha256("fixture-inr-5.22"),
        connector_classification=ConnectorClassification.OFFICIAL_OPEN,
        redistribution_status=RedistributionStatus.ALLOWED,
        quality=QualityState.ESTIMATED, staleness=StalenessState.FRESH,
    )
    calls = []

    class Repo:
        def load_observations_as_of(self, market, *args, **kwargs):
            calls.append(market)
            if market == "US-USD":
                raise ValueError("USD source temporarily unreadable")
            assert row.instrument_id in kwargs["instrument_ids"]
            return [row]

    monkeypatch.setattr(desk, "get_repository", lambda: Repo())
    rows = {r["currency"]: r for r in desk._funding(now)}
    assert calls == ["US-USD", "IN-INR"]
    assert rows["USD"]["value"] is None
    assert rows["USD"]["reason"] == "The canonical funding source could not be read."
    assert rows["INR"]["value"] == 5.22
    assert rows["INR"]["status"] == "FRESH"
    assert rows["INR"]["as_of"] == "2026-09-30"
    assert rows["INR"]["evidence"]["knowledge_time"] == row.knowledge_time.isoformat()
    assert rows["INR"]["reason"] is None
