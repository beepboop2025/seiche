from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException, Request, Response
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


def _request(ip: str = "127.0.0.1") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/v2/markets/US-USD/series",
            "raw_path": b"/api/v2/markets/US-USD/series",
            "query_string": b"",
            "headers": [],
            "client": (ip, 12345),
            "server": ("testserver", 80),
        }
    )


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


def _rate_observation(
    *,
    event_time: datetime,
    knowledge_time: datetime | None = None,
    market_id: str = "US-USD",
    monetary_area_id: str = "US",
    jurisdiction: str = "US",
    currency: str = "USD",
    instrument_id: str = "US.NYFED.SOFR",
    role: SemanticRole = SemanticRole.SECURED_OVERNIGHT,
    value: str = "531",
    revision_id: str = "test-v1",
    source: str = "official-test",
    connector: ConnectorClassification = ConnectorClassification.OFFICIAL_OPEN,
    redistribution: RedistributionStatus = RedistributionStatus.ALLOWED,
    quality: QualityState = QualityState.VERIFIED,
) -> Observation:
    known = knowledge_time or event_time
    return Observation(
        market_id=market_id,
        monetary_area_id=monetary_area_id,
        jurisdiction_codes=(jurisdiction,),
        currency=currency,
        instrument_id=instrument_id,
        semantic_role=role,
        value=value,
        canonical_unit=CanonicalUnit.BASIS_POINTS,
        rate_compounding=RateCompounding.SIMPLE,
        day_count=DayCountConvention.ACT_360,
        event_time=event_time,
        source_publication_time=known,
        knowledge_time=known,
        revision_id=revision_id,
        source=source,
        evidence_hash=evidence_sha256(
            f"{market_id}:{instrument_id}:{event_time.isoformat()}:{revision_id}:{source}"
        ),
        connector_classification=connector,
        redistribution_status=redistribution,
        quality=quality,
        staleness=StalenessState.FRESH,
    )


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
    assert payload["data_coverage"] == {"canonical_observations": []}
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

    payload = api.market_series_v2("US-USD", _request(), Response())
    record = payload["observations"][0]

    assert record["value"] is None
    assert record["value_status"] == "REDACTED_BY_LICENCE"
    assert record["evidence_hash"] == evidence_sha256("licensed row")
    assert (
        "no publicly redistributable observation values are available"
        in payload["evidence_eligibility"]["reasons"]
    )


def test_market_series_uses_sql_page_cursor_and_fails_closed_on_evidence(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "paged-v2.sqlite")
    start = datetime(2026, 8, 1, tzinfo=UTC)
    store.save_observations(
        [
            _rate_observation(
                event_time=start + timedelta(days=offset),
                value=str(500 + offset),
                revision_id=f"page-{offset}",
            )
            for offset in range(3)
        ]
    )

    first = api.market_series_v2("US-USD", _request(), Response(), n=2)
    second = api.market_series_v2(
        "US-USD", _request(), Response(), n=2, cursor=first["next_cursor"]
    )

    assert [item["value"] for item in first["observations"]] == ["501", "502"]
    assert first["next_cursor"]
    assert [item["value"] for item in second["observations"]] == ["500"]
    assert second["next_cursor"] is None
    assert first["evidence_eligibility"]["eligible"] is False
    assert first["evidence_eligibility"]["reasons"] == [
        "pack validation status is not SUPPORTED",
        "calibration is forward-only",
    ]


def test_market_series_omits_pack_prohibited_rows_and_all_row_metadata(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "prohibited-v2.sqlite")
    secret_time = datetime(2026, 8, 1, 12, 34, 56, tzinfo=UTC)
    secret = _rate_observation(
        event_time=secret_time,
        market_id="IN-INR",
        monetary_area_id="IN",
        jurisdiction="IN",
        currency="INR",
        instrument_id="IN.FBIL.MIBOR",
        role=SemanticRole.UNSECURED_OVERNIGHT,
        revision_id="secret-prohibited-revision",
        source="tenant-secret-source",
        connector=ConnectorClassification.LICENSED,
        redistribution=RedistributionStatus.PROHIBITED,
    )
    store.save_observations([secret])

    payload = api.market_series_v2("IN-INR", _request(), Response())
    serialized = json.dumps(payload, sort_keys=True)

    assert payload["observations"] == []
    assert secret_time.isoformat() not in serialized
    assert "tenant-secret-source" not in serialized
    assert "secret-prohibited-revision" not in serialized
    assert secret.evidence_hash not in serialized


def test_latest_prohibited_revision_cannot_reveal_old_allowed_vintage(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "policy-revision-v2.sqlite")
    event = datetime(2026, 8, 1, tzinfo=UTC)
    old = _rate_observation(
        event_time=event,
        knowledge_time=event + timedelta(hours=1),
        value="500",
        revision_id="old-allowed",
    )
    prohibited = _rate_observation(
        event_time=event,
        knowledge_time=event + timedelta(hours=2),
        value="999",
        revision_id="new-prohibited",
        redistribution=RedistributionStatus.PROHIBITED,
    )
    store.save_observations([old, prohibited])

    payload = api.market_series_v2("US-USD", _request(), Response())
    serialized = json.dumps(payload, sort_keys=True)

    assert payload["observations"] == []
    assert "old-allowed" not in serialized
    assert "new-prohibited" not in serialized
    assert old.evidence_hash not in serialized
    assert prohibited.evidence_hash not in serialized


def test_market_series_quality_reason_is_explicit(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "quality-v2.sqlite")
    store.save_observations(
        [
            _rate_observation(
                event_time=datetime(2026, 8, 1, tzinfo=UTC),
                quality=QualityState.PROVISIONAL,
            )
        ]
    )

    eligibility = api.market_series_v2(
        "US-USD", _request(), Response()
    )["evidence_eligibility"]

    assert eligibility["eligible"] is False
    assert "observation quality is not evidence-eligible: provisional" in eligibility[
        "reasons"
    ]


def test_market_series_rate_limit_is_per_client_ip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "rate-limit-v2.sqlite")
    monkeypatch.setattr(api, "_market_series_limiter", api._RateLimiter(1))

    api.market_series_v2("US-USD", _request("203.0.113.8"), Response())

    with pytest.raises(HTTPException) as exc_info:
        api.market_series_v2("US-USD", _request("203.0.113.8"), Response())
    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "60"}

    # A separate client remains within its own allowance.
    api.market_series_v2("US-USD", _request("203.0.113.9"), Response())


def test_unmarked_snapshot_is_not_exposed_as_public_projection(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "unmarked-v2.sqlite")
    cutoff = datetime(2026, 8, 9, tzinfo=UTC)
    store.seal_market_snapshot(
        market_id="IN-INR",
        product="gauge",
        event_cutoff=cutoff,
        knowledge_cutoff=cutoff,
        calibration_id="test-unmarked",
        evidence_eligible=False,
        payload={
            "schema": "seiche.local-gauge.v2",
            "status": "READY",
            "source": "tenant-secret-source",
        },
    )

    response = api.market_gauge_v2("IN-INR", Response())
    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    assert "tenant-secret-source" not in response.body.decode()
