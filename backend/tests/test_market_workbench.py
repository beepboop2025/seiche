"""Research invariants: quote orientation, dates, rights, gaps and bounded IO."""

from datetime import UTC, datetime
import json
import sqlite3

import pandas as pd
import pytest

from seiche import market_workbench as wb, store
from seiche.config import ALL_SERIES
from seiche.sources.base import Series

NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)


def series(mnemonic, values, dates=None):
    spec = ALL_SERIES[mnemonic]
    dates = dates or ["2026-09-07", "2026-09-08"]
    return Series(
        mnemonic,
        spec.source,
        spec.remote_id,
        spec.label,
        spec.unit,
        spec.freq,
        "2026-09-09T08:00:00Z",
        pd.Series(values, index=pd.to_datetime(dates)),
    )


def test_usd_cny_and_eur_cross_preserve_units_and_dated_inputs():
    data = {"CNY": series("CNY", [7.0, 7.2]), "EURUSD": series("EURUSD", [1.1, 1.2])}
    result = wb.project(data, {"base": "EUR", "quote": "CNY"}, evaluated_at=NOW)
    row = next(r for r in result["forex"]["rows"] if r["quote_currency"] == "CNY")
    assert row["value"] == pytest.approx(8.64)
    assert row["unit"] == "CNY per EUR" and row["evidence_status"] == "derived"
    assert len(row["sources"]) == 2
    assert result["china"]["fx"]["value"] == 7.2
    assert result["china"]["fx"]["evidence_status"] == "observed"
    assert result["forex"]["history"][0]["value"] == pytest.approx(7.7)
    assert result["forex"]["history"][-1]["date"] == "2026-09-08"
    assert result["selection"]["base"] == "EUR"


def test_ecb_cross_uses_eur_anchor_and_ignores_h10_cache():
    data = {
        "ECBFX_CNY": series("ECBFX_CNY", [7.7, 8.64]),
        "ECBFX_USD": series("ECBFX_USD", [1.1, 1.2]),
        "CNY": series("CNY", [100, 100]),
    }
    result = wb.project(data, {"provider": "ecb"}, evaluated_at=NOW)
    assert result["forex"]["history"][-1]["value"] == pytest.approx(7.2)
    assert result["forex"]["coverage"]["declared_pairs"] == 29
    assert result["china"]["fx"]["evidence_status"] == "derived"
    assert all(
        s["source_id"].startswith("EXR/") for s in result["china"]["fx"]["sources"]
    )
    assert "European Central Bank" in result["forex"]["methodology"][0]


def test_ecb_eur_base_is_observed_and_does_not_need_usd_leg():
    result = wb.project(
        {"ECBFX_CNY": series("ECBFX_CNY", [7.7, 8.64])},
        {"provider": "ecb", "base": "EUR"},
        evaluated_at=NOW,
    )
    row = next(r for r in result["forex"]["rows"] if r["quote_currency"] == "CNY")
    assert row["evidence_status"] == "observed" and row["value"] == 8.64
    assert result["china"]["fx"]["value"] is None


def test_ecb_export_rights_do_not_enable_unrelated_ecb_series():
    assert wb.methodology.csv_restriction("ECBFX_CNY") is None
    assert wb.methodology.csv_restriction("ESTR") is not None


def capture():
    return {
        "schema": "seiche.ecb-fx-capture.v1",
        "source": "ecb_fx",
        "base_currency": "EUR",
        "mode": "history90d",
        "source_url": "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml",
        "fetched_at": "2026-09-09T00:00:00Z",
        "evidence_sha256": "a" * 64,
        "first_observation_date": "2026-06-11",
        "last_observation_date": "2026-09-08",
        "raw_path": "/private/provider-archive.xml",
    }


def test_capture_metadata_preserves_document_scope_and_hides_local_paths():
    result = wb._public_capture(capture(), NOW)
    assert result["source_url"].endswith("hist-90d.xml")
    assert result["first_observation_date"] == "2026-06-11"
    assert "raw_path" not in result and "/private/" not in json.dumps(result)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_url", "https://bad.test/data.xml"),
        ("evidence_sha256", "invalid"),
        ("fetched_at", "2026-10-01T00:00:00Z"),
        ("last_observation_date", "2026-09-10"),
    ],
)
def test_invalid_capture_metadata_cannot_establish_provenance(field, value):
    row = capture()
    row[field] = value
    assert wb._public_capture(row, NOW) is None


def test_ecb_read_requires_capture_generation_and_labels_partial_document(monkeypatch):
    rows = {
        name: series(name, values)
        for name, values in (("ECBFX_CNY", [7.7, 8.64]), ("ECBFX_USD", [1.1, 1.2]))
    }
    for row in rows.values():
        row.fetched_at = "2026-09-09T00:00:00Z"
    manifest = capture()
    monkeypatch.setattr(
        wb.store,
        "load_series_window_snapshot",
        lambda *a, **k: (rows, {"ecb_fx:latest": manifest}),
    )
    monkeypatch.setattr(wb.context_views, "public_china_economic_context", lambda: None)
    result = wb.read({"provider": "ecb"})
    assert result["china"]["fx"]["value"] == pytest.approx(7.2)
    assert (
        "not the entire merged history"
        in result["forex"]["capture_documents"]["boundary"]
    )
    assert "/private/" not in json.dumps(result)
    rows["ECBFX_USD"].fetched_at = "2026-09-08T00:00:00Z"
    assert wb.read({"provider": "ecb"})["china"]["fx"]["value"] is None


def test_cross_never_forward_fills_missing_dates_or_uses_future_observations():
    data = {
        "CNY": series("CNY", [7.0, 7.2], ["2026-09-07", "2026-09-08"]),
        "EURUSD": series("EURUSD", [1.1, 1.2], ["2026-09-07", "2026-09-10"]),
    }
    result = wb.project(data, {"base": "EUR", "quote": "CNY"}, evaluated_at=NOW)
    assert [r["date"] for r in result["forex"]["history"]] == ["2026-09-07"]
    row = next(r for r in result["forex"]["rows"] if r["quote_currency"] == "CNY")
    assert row["change_1obs_pct"] is None


@pytest.mark.parametrize("values", [[0, -1], [float("nan"), float("inf")]])
def test_invalid_reference_values_remain_unavailable(values):
    result = wb.project({"CNY": series("CNY", values)}, evaluated_at=NOW)
    assert result["forex"]["history"] == []
    assert result["china"]["fx"]["value"] is None
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("remote_id", "LICENSED"),
        ("source", "chinamoney"),
        ("unit", "wrong"),
        ("fetched_at", "2026-09-10T00:00:00Z"),
        ("fetched_at", "bad"),
    ],
)
def test_cache_metadata_cannot_replace_reviewed_fx_source(attribute, value):
    row = series("CNY", [7.0, 7.2])
    setattr(row, attribute, value)
    result = wb.project({"CNY": row}, evaluated_at=NOW)
    assert result["china"]["fx"]["value"] is None


def test_policy_revocation_hides_existing_cache(monkeypatch):
    monkeypatch.setattr(wb.methodology, "csv_restriction", lambda _: "rights revoked")
    assert (
        wb.project({"CNY": series("CNY", [7.0, 7.2])}, evaluated_at=NOW)["forex"][
            "history"
        ]
        == []
    )


def test_stale_clock_does_not_advance_on_response_generation():
    row = series("CNY", [7, 7.2], ["2026-07-01", "2026-07-02"])
    result = wb.project({"CNY": row}, evaluated_at=NOW)
    assert result["china"]["fx"]["as_of"] == "2026-07-02"
    assert result["china"]["fx"]["status"] == "dead"
    assert result["forex"]["coverage"]["fresh_pairs"] == 0


def test_observation_cannot_postdate_its_capture():
    row = series("CNY", [7, 7.2])
    row.fetched_at = "2026-09-07T23:00:00Z"
    result = wb.project({"CNY": row}, evaluated_at=NOW)
    assert result["china"]["fx"]["as_of"] == "2026-09-07"


def test_extreme_finite_prices_cannot_produce_nonfinite_json():
    result = wb.project({"CNY": series("CNY", [1e-300, 1e300])}, evaluated_at=NOW)
    assert result["china"]["fx"]["change_1obs_pct"] is None
    json.dumps(result, allow_nan=False)


def test_native_usd_per_euro_remains_observed():
    result = wb.project(
        {"EURUSD": series("EURUSD", [1.1, 1.2])},
        {"base": "EUR", "quote": "USD"},
        evaluated_at=NOW,
    )
    row = next(r for r in result["forex"]["rows"] if r["quote_currency"] == "USD")
    assert row["evidence_status"] == "observed"
    assert row["value"] == pytest.approx(1.2)


def test_untrusted_china_mapping_cannot_publish_numbers():
    result = wb.project(
        {},
        china_context={"owner_attested": True, "observations": [{"value": 999999}]},
        evaluated_at=NOW,
    )
    assert result["china"]["series"] == []
    assert result["china"]["history"] == []
    assert "999999" not in json.dumps(result)
    assert result["china"]["gaps"]


@pytest.mark.parametrize(
    "args",
    [
        {"days": True},
        {"days": 3651},
        {"days": 1},
        {"base": []},
        {"base": "CNH"},
        {"base": "USD", "quote": "USD"},
        {"china_series": "../secrets"},
        {"url": "https://bad.test"},
    ],
)
def test_query_rejects_ambiguous_or_unbounded_requests(args):
    with pytest.raises(ValueError):
        wb.project({}, args, evaluated_at=NOW)


def test_bounded_reader_does_not_create_database(tmp_path, monkeypatch):
    path = tmp_path / "absent.sqlite"
    monkeypatch.setattr(store, "DB_PATH", path)
    assert (
        store.load_series_window(("CNY",), start="2026-09-01", end="2026-09-09") == {}
    )
    assert not path.exists()


def test_bounded_reader_selects_only_window_and_never_mutates(tmp_path, monkeypatch):
    path = tmp_path / "cache.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE observations(mnemonic TEXT,obs_date TEXT,value REAL); CREATE TABLE fetches(mnemonic TEXT,source TEXT,remote_id TEXT,label TEXT,unit TEXT,freq TEXT,fetched_at TEXT);"
    )
    spec = ALL_SERIES["CNY"]
    conn.execute(
        "INSERT INTO fetches VALUES(?,?,?,?,?,?,?)",
        (
            "CNY",
            spec.source,
            spec.remote_id,
            spec.label,
            spec.unit,
            spec.freq,
            "2026-09-09T08:00:00Z",
        ),
    )
    conn.executemany(
        "INSERT INTO observations VALUES(?,?,?)",
        [
            ("CNY", "1990-01-01", 100),
            ("CNY", "2026-09-08", 7.2),
            ("CNY", "2026-09-10", 200),
        ],
    )
    conn.commit()
    conn.close()
    prior = path.read_bytes()
    monkeypatch.setattr(store, "DB_PATH", path)
    result = store.load_series_window(("CNY",), start="2026-09-01", end="2026-09-09")
    assert result["CNY"].tail_records() == [["2026-09-08", 7.2]]
    assert path.read_bytes() == prior


def test_rest_mcp_and_openapi_share_one_contract(monkeypatch):
    from fastapi.testclient import TestClient
    from seiche import api, mcp_server as mcp
    from seiche import context_views

    monkeypatch.setattr(
        wb.store, "load_series_window", lambda *a, **k: {"CNY": series("CNY", [7, 7.2])}
    )
    monkeypatch.setattr(context_views, "public_china_economic_context", lambda: None)
    args = {"base": "USD", "quote": "CNY", "days": 365, "china_series": ""}
    direct = mcp.tool_market_workbench(args, True)
    with TestClient(api.app, raise_server_exceptions=False) as client:
        response = client.get("/api/v2/market-workbench", params=args)
    assert response.status_code == 200
    rest = response.json()
    assert rest["selection"] == direct["selection"] and rest["forex"] == direct["forex"]
    assert "market_workbench" in mcp.STRUCTURED_OUTPUT_TOOLS
    assert "/api/v2/market-workbench" in api._public_openapi_document()["paths"]
