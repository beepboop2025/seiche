from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import Response
from fastapi.responses import JSONResponse

from seiche import api, assemble, store
from seiche.domain.observation import (
    CanonicalUnit,
    ConnectorClassification,
    DayCountConvention,
    Observation,
    QualityState,
    RateCompounding,
    RedistributionStatus,
    SemanticRole,
    StalenessState,
    evidence_sha256,
)
from seiche.markets.us_usd.materialize import seal_legacy_snapshot


def _legacy_snapshot() -> dict:
    return {
        "generated_at": "2026-08-09T10:00:00+00:00",
        "headline": {"sofr_pct": {"value": 5.31, "asof": "2026-08-08"}},
        "engines": {
            "composite": {
                "value": 42.0,
                "regime": "EROSION",
                "coverage_pct": 90.0,
                "decomposition": [
                    {"component": "repo", "score": 45.0, "status": "OK"}
                ],
            }
        },
        "deep": {
            "tell": {"ok": True, "tell": 12.0},
            "stacker": {
                "ok": True,
                "p_now": 0.2,
                "dispersion_now": 0.03,
                "members_now": {"model": 0.2},
            },
            "modelcourt": {"ok": False},
        },
        "navigator": {"ok": False},
        "calendar": {"next_turn": None, "crunch_windows": []},
        "faults": [
            {"source": "fred", "detail": "rate source timeout"},
            {"source": "boj", "detail": "unrelated collector timeout"},
            {"source": "gdelt", "detail": "unrelated context timeout"},
        ],
        "provenance": [
            {
                "mnemonic": "SOFR",
                "asof": "2026-08-08",
                "fetched_at": "2026-08-09T09:00:00+00:00",
                "staleness": "fresh",
            }
        ],
        "data_quality": {"status": "partial"},
    }


def test_v2_catalog_does_not_collect_at_request_time(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "api-v2.sqlite")

    async def forbidden_collection(*args, **kwargs):
        raise AssertionError("v2 route attempted request-time collection")

    monkeypatch.setattr(assemble, "snapshot", forbidden_collection)
    payload = api.markets_v2(Response())

    assert payload["count"] == 10
    assert {item["market_id"] for item in payload["markets"]} >= {"US-USD", "IN-INR", "EA-EUR"}


def test_market_without_snapshot_is_explicitly_unavailable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "empty-v2.sqlite")
    response = api.market_gauge_v2("IN-INR", Response())

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    payload = json.loads(response.body)
    assert payload["status"] == "UNAVAILABLE"
    assert payload.get("reading") is None
    assert payload["faults"]


def test_us_materializer_filters_unrelated_market_faults(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "sealed-v2.sqlite")
    ids = seal_legacy_snapshot(_legacy_snapshot())

    gauge = api.market_gauge_v2("US-USD", Response())
    assert gauge["schema"] == "seiche.local-gauge.v2"
    assert gauge["reading"]["index"] == 42.0
    assert gauge["evidence_eligibility"]["eligible"] is False
    assert [fault["source"] for fault in gauge["faults"]] == ["fred"]
    assert ids["gauge"] == store.load_latest_market_snapshot("US-USD", "gauge")["snapshot_id"]


def test_market_asof_reads_sealed_history_and_global_tide_stays_separate(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "asof-v2.sqlite")
    seal_legacy_snapshot(_legacy_snapshot())

    historical = api.market_asof_v2(
        "US-USD", "2026-08-09T10:00:00+00:00", Response()
    )
    tide = api.global_tide_v2(Response())

    assert historical["market_id"] == "US-USD"
    assert historical["sealed_snapshot_id"]
    assert tide["product"] == "GLOBAL_SEICHE_TIDE"
    assert tide["status"] == "UNAVAILABLE"
    assert tide["reading"]["value"] is None


def test_public_openapi_advertises_all_v2_contracts() -> None:
    paths = api._public_openapi_document()["paths"]
    assert {
        "/api/v2/markets",
        "/api/v2/markets/{market_id}/overview",
        "/api/v2/markets/{market_id}/gauge",
        "/api/v2/markets/{market_id}/asof/{timestamp}",
        "/api/v2/markets/{market_id}/series",
        "/api/v2/global/tide",
        "/api/v2/coverage",
    } <= set(paths)


def test_market_series_redacts_licensed_values_but_keeps_evidence_metadata(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "licensed-v2.sqlite")
    observed_at = datetime(2026, 8, 8, tzinfo=UTC)
    store.save_observations(
        [
            Observation(
                market_id="US-USD",
                monetary_area_id="US",
                jurisdiction_codes=("US",),
                currency="USD",
                instrument_id="US.NYFED.SOFR",
                semantic_role=SemanticRole.SECURED_OVERNIGHT,
                value="531",
                canonical_unit=CanonicalUnit.BASIS_POINTS,
                rate_compounding=RateCompounding.SIMPLE,
                day_count=DayCountConvention.ACT_360,
                event_time=observed_at,
                source_publication_time=observed_at,
                knowledge_time=observed_at,
                revision_id="vendor-v1",
                source="licensed-test",
                evidence_hash=evidence_sha256("licensed row"),
                connector_classification=ConnectorClassification.LICENSED,
                redistribution_status=RedistributionStatus.DERIVED_ONLY,
                quality=QualityState.VERIFIED,
                staleness=StalenessState.FRESH,
            )
        ]
    )

    payload = api.market_series_v2("US-USD", Response())
    record = payload["observations"][0]

    assert record["value"] is None
    assert record["value_status"] == "REDACTED_BY_LICENCE"
    assert record["evidence_hash"] == evidence_sha256("licensed row")
