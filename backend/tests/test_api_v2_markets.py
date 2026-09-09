from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
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
from seiche.markets.us_usd import materialize as us_materialize
from seiche.markets.us_usd.materialize import (
    seal_legacy_snapshot,
    verify_release_receipt,
)
from seiche.repository import (
    PostgresMarketRepository,
    SQLiteMarketRepository,
    _OBSERVATION_COLUMNS,
)


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
                "decomposition": [{"component": "repo", "score": 45.0, "status": "OK"}],
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
            "modelcourt": {"ok": True, "ensemble": {"p": 0.98}},
        },
        "navigator": {"ok": True, "p_event_5bd": 0.99},
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

    assert payload["count"] == 11
    assert {item["market_id"] for item in payload["markets"]} >= {
        "US-USD",
        "IN-INR",
        "EA-EUR",
    }


def test_catalog_batches_snapshots_and_keeps_faults_with_their_market(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "catalog-batch.sqlite")
    base = SQLiteMarketRepository()
    cutoff = datetime(2026, 8, 9, tzinfo=UTC)
    store.seal_market_snapshot(
        market_id="IN-INR", product="gauge", event_cutoff=cutoff,
        knowledge_cutoff=cutoff, calibration_id="private", evidence_eligible=False,
        payload={"schema": "seiche.local-gauge.v2", "source": "private-not-public"},
    )
    runs = [
        {"market_id": market, "adapter_id": "nyfed_rates", "status": "FAILED",
         "fault": "timeout", "finished_at": cutoff.isoformat(), "next_due": cutoff.isoformat()}
        for market in ("US-USD", "EA-EUR")
    ]
    monkeypatch.setattr(base, "latest_collector_runs", lambda market=None: [r for r in runs if market is None or r["market_id"] == market])
    monkeypatch.setattr(api, "get_repository", lambda: base)
    expected = api.markets_v2(Response())
    calls = []

    class Batched:
        def load_latest_market_snapshots(self, markets, product):
            markets = tuple(markets)
            calls.append(("snapshots", markets))
            return {market: base.load_latest_market_snapshot(market, product) for market in markets}

        def latest_collector_runs(self):
            calls.append(("runs",))
            return runs

    monkeypatch.setattr(api, "get_repository", Batched)
    actual = api.markets_v2(Response())
    assert actual == expected
    assert [call[0] for call in calls] == ["snapshots", "runs"]
    assert "private-not-public" not in json.dumps(actual)
    assert len(next(m for m in actual["markets"] if m["market_id"] == "US-USD")["faults"]) == 1


def test_series_status_batch_retains_selection_cutoffs_and_rights(monkeypatch):
    cutoff = datetime(2026, 8, 9, tzinfo=UTC)
    pack = api._market_pack("US-USD")
    selected = api._public_instrument_ids(pack)
    observations = {selected[0]: object()}
    calls = []

    class Batched:
        def load_latest_observations_by_instrument(self, market, known, **kwargs):
            calls.append((market, known, kwargs))
            return observations

        def load_observation_page(self, *args, **kwargs):
            raise AssertionError("Status must not open a connection for each instrument")

    assert api._latest_public_series_observations(Batched(), pack, cutoff) == observations
    assert calls == [("US-USD", cutoff, {
        "event_time": cutoff, "instrument_ids": selected,
        "redistribution_statuses": (RedistributionStatus.ALLOWED,
                                   RedistributionStatus.DERIVED_ONLY,
                                   RedistributionStatus.METADATA_ONLY),
    })]


def test_market_without_snapshot_is_explicitly_unavailable(
    tmp_path, monkeypatch
) -> None:
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
    assert store.load_latest_market_snapshot("US-USD", "overview") is None
    assert store.load_latest_market_snapshot("US-USD", "gauge") is None
    store.promote_market_snapshots(binding["snapshot_id"] for binding in ids.values())

    gauge = api.market_gauge_v2("US-USD", Response())
    assert gauge["schema"] == "seiche.local-gauge.v2"
    assert gauge["reading"]["index"] == 42.0
    assert gauge["reading"]["p_event_5bd_members"] == {"model": 0.2}
    assert gauge["evidence_eligibility"]["eligible"] is False
    assert [fault["source"] for fault in gauge["faults"]] == ["fred"]
    assert (
        ids["gauge"]["snapshot_id"]
        == store.load_latest_market_snapshot("US-USD", "gauge")["snapshot_id"]
    )
    records = store.load_forward_records("US-USD", "gauge", "us-usd-legacy-parity-v1")
    assert ids["gauge"]["forward_record_id"] == records[-1]["record_hash"]


def test_us_materializer_partial_append_retries_idempotently(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "partial-us-v2.sqlite")
    previous = _legacy_snapshot()
    previous_receipt = seal_legacy_snapshot(previous)
    store.promote_market_snapshots(
        binding["snapshot_id"] for binding in previous_receipt.values()
    )
    candidate = _legacy_snapshot()
    candidate["generated_at"] = "2026-08-09T11:00:00+00:00"
    delegate = SQLiteMarketRepository()

    class FailGaugeOnce:
        failed = False

        def __getattr__(self, name):
            return getattr(delegate, name)

        def append_forward_record(self, **kwargs):
            if kwargs["product"] == "gauge" and not self.failed:
                self.failed = True
                raise RuntimeError("injected gauge append failure")
            return delegate.append_forward_record(**kwargs)

    repository = FailGaugeOnce()
    monkeypatch.setattr(us_materialize, "get_repository", lambda: repository)

    with pytest.raises(RuntimeError, match="injected gauge append failure"):
        seal_legacy_snapshot(candidate)
    # Both candidate snapshots were staged, but public readers retain the
    # previously promoted pair until the full forward bundle verifies.
    assert (
        store.load_latest_market_snapshot("US-USD", "overview")["snapshot_id"]
        == previous_receipt["overview"]["snapshot_id"]
    )
    assert (
        store.load_latest_market_snapshot("US-USD", "gauge")["snapshot_id"]
        == previous_receipt["gauge"]["snapshot_id"]
    )
    assert len(store.load_forward_records("US-USD", "overview")) == 2
    assert len(store.load_forward_records("US-USD", "gauge")) == 1

    receipt = seal_legacy_snapshot(candidate)
    assert set(receipt) == {"overview", "gauge"}
    assert (
        store.load_latest_market_snapshot("US-USD", "overview")["snapshot_id"]
        == previous_receipt["overview"]["snapshot_id"]
    )
    assert (
        store.load_latest_market_snapshot("US-USD", "gauge")["snapshot_id"]
        == previous_receipt["gauge"]["snapshot_id"]
    )
    store.promote_market_snapshots(
        binding["snapshot_id"] for binding in receipt.values()
    )
    assert (
        store.load_latest_market_snapshot("US-USD", "overview")["snapshot_id"]
        == receipt["overview"]["snapshot_id"]
    )
    assert (
        store.load_latest_market_snapshot("US-USD", "gauge")["snapshot_id"]
        == receipt["gauge"]["snapshot_id"]
    )
    assert len(store.load_forward_records("US-USD", "overview")) == 2
    assert len(store.load_forward_records("US-USD", "gauge")) == 2


def test_corrupt_us_forward_record_cannot_issue_a_release_receipt(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "corrupt-us-v2.sqlite")
    receipt = seal_legacy_snapshot(_legacy_snapshot())

    with store._conn() as connection:
        connection.execute(
            "UPDATE forward_validation_records SET payload=? WHERE record_id=?",
            ('{"tampered":true}', receipt["gauge"]["forward_record_id"]),
        )

    release_receipt = {
        "generated_at": _legacy_snapshot()["generated_at"],
        "producer": "seiche.markets.us_usd.materialize.seal_legacy_snapshot",
        "products": receipt,
    }
    with pytest.raises(RuntimeError, match="post-append verification"):
        verify_release_receipt(SQLiteMarketRepository(), release_receipt)
    assert assemble._seal_release_evidence(_legacy_snapshot()) is None


def test_release_receipt_requires_a_canonical_utc_generation_time(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "receipt-time.sqlite")
    snapshot = _legacy_snapshot()
    products = seal_legacy_snapshot(snapshot)
    receipt = {
        "generated_at": snapshot["generated_at"],
        "producer": "seiche.markets.us_usd.materialize.seal_legacy_snapshot",
        "products": products,
    }

    assert verify_release_receipt(SQLiteMarketRepository(), receipt)
    for invalid in (
        None,
        "2026-08-09T10:00:00",
        "2026-08-09T15:30:00+05:30",
        "2026-08-09T10:00:00Z",
        "2026-08-09T10:00:00.000000+00:00",
    ):
        with pytest.raises(ValueError, match="generated_at must be canonical UTC"):
            verify_release_receipt(
                SQLiteMarketRepository(),
                {**receipt, "generated_at": invalid},
            )


def test_market_asof_reads_sealed_history_and_global_tide_stays_separate(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "asof-v2.sqlite")
    receipt = seal_legacy_snapshot(_legacy_snapshot())
    store.promote_market_snapshots(
        binding["snapshot_id"] for binding in receipt.values()
    )

    historical = api.market_asof_v2("US-USD", "2026-08-09T10:00:00+00:00", Response())
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
        "/api/v2/money-markets",
        "/api/v2/world-markets",
        "/api/v2/markets/{market_id}/overview",
        "/api/v2/markets/{market_id}/gauge",
        "/api/v2/markets/{market_id}/asof/{timestamp}",
        "/api/v2/markets/{market_id}/series",
        "/api/v2/global/tide",
        "/api/v2/coverage",
    } <= set(paths)


def test_global_money_market_atlas_reads_canonical_rows_without_collecting(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "money-market-atlas.sqlite")

    async def forbidden_collection(*args, **kwargs):
        raise AssertionError("money-market atlas attempted request-time collection")

    monkeypatch.setattr(assemble, "snapshot", forbidden_collection)
    observed_at = datetime.now(UTC).replace(microsecond=0) - timedelta(days=1)
    store.save_observations(
        [
            _rate_observation(
                event_time=observed_at,
                instrument_id="US.NYFED.SOFR",
                role=SemanticRole.SECURED_OVERNIGHT,
                value="531",
                source="nyfed_rates",
            ),
            _rate_observation(
                event_time=observed_at,
                instrument_id="US.FED.IORB",
                role=SemanticRole.POLICY_TARGET,
                value="540",
                source="fred_daily",
            ),
        ]
    )

    response = Response()
    payload = api.global_money_markets_v2(response)
    us = next(item for item in payload["markets"] if item["market_id"] == "US-USD")

    assert payload["schema"] == "seiche.global-money-markets.v1"
    assert payload["coverage"]["declared_markets"] == 11
    assert us["benchmark"]["value"] == 5.31
    assert us["policy_relative_spread"]["value"] == -9.0
    assert not any(
        item["market_id"] == "KR-KRW" for item in payload["expansion_ledger"]
    )
    assert payload["coverage"]["expansion_markets"] >= 50
    assert payload["coverage"]["global_discovery_universe"] >= 60
    canada = next(
        item for item in payload["expansion_ledger"] if item["market_id"] == "CA-CAD"
    )
    assert canada["benchmark"] == "CORRA"
    assert canada["benchmark_kind"] == "secured overnight transaction rate"
    assert canada["source_url"].startswith("https://www.bankofcanada.ca/")
    assert canada["verified_on"] == "2026-08-21"
    assert response.headers["Cache-Control"].startswith("public, max-age=60")


def test_global_money_market_atlas_omits_prohibited_source_metadata(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "money-market-policy.sqlite")
    finished_at = datetime.now(UTC).replace(microsecond=0) - timedelta(days=1)
    secret = "private tenant endpoint and credential detail"
    store.save_collector_run(
        {
            "market_id": "EA-EUR",
            "adapter_id": "tenant_market_data",
            "status": "FAILED",
            "started_at": finished_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "observations_written": 0,
            "attempts": 1,
            "next_due": (finished_at + timedelta(days=1)).isoformat(),
            "fault": secret,
        }
    )

    payload = api.global_money_markets_v2(Response())
    euro = next(item for item in payload["markets"] if item["market_id"] == "EA-EUR")
    serialized = json.dumps(payload, sort_keys=True)

    assert "tenant_market_data" not in serialized
    assert secret not in serialized
    assert euro["coverage"]["declared_adapters"] == 4
    assert euro["coverage"]["public_projected_adapters"] == 3
    assert euro["coverage"]["omitted_adapters_by_policy"] == 1


def test_global_money_market_atlas_derives_context_without_redistributing_level(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "money-market-derived.sqlite")
    start = datetime.now(UTC).replace(microsecond=0) - timedelta(days=70)
    store.save_observations(
        [
            _rate_observation(
                event_time=start + timedelta(days=index),
                market_id="UK-GBP",
                monetary_area_id="UK",
                jurisdiction="GB",
                currency="GBP",
                instrument_id="GB.BOE.SONIA",
                role=SemanticRole.UNSECURED_OVERNIGHT,
                value=str(420 + (index % 11) * 2 + index / 10),
                source="boe_sonia",
                redistribution=RedistributionStatus.DERIVED_ONLY,
            )
            for index in range(60)
        ]
    )

    payload = api.global_money_markets_v2(Response())
    uk = next(item for item in payload["markets"] if item["market_id"] == "UK-GBP")
    sonia = next(metric for metric in uk["metrics"] if metric["id"] == "GB.BOE.SONIA")

    assert uk["status"] == "DERIVED_CONTEXT"
    assert uk["benchmark"] is None
    assert uk["derived_benchmark"]["id"] == "GB.BOE.SONIA"
    assert sonia["value"] is None
    assert sonia["canonical_value"] is None
    assert sonia["history"] == []
    assert sonia["robust_z_1y"] is not None
    assert sonia["percentile_3y"] is not None


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


def test_korea_series_distinguishes_public_values_derived_context_and_restriction(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "korea-rights-v2.sqlite")
    observed_at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)
    store.save_observations(
        [
            _rate_observation(
                event_time=observed_at,
                market_id="KR-KRW",
                monetary_area_id="KR",
                jurisdiction="KR",
                currency="KRW",
                instrument_id="KR.BOK.BASE_RATE",
                role=SemanticRole.POLICY_TARGET,
                value="250",
                revision_id="bok-public-v1",
                source="bok_ecos_policy",
            ),
            _rate_observation(
                event_time=observed_at,
                market_id="KR-KRW",
                monetary_area_id="KR",
                jurisdiction="KR",
                currency="KRW",
                instrument_id="KR.KOFIA.CD_91D",
                role=SemanticRole.CD_3M,
                value="271",
                revision_id="licensed-derived-v1",
                source="licensed_krw_market",
                connector=ConnectorClassification.LICENSED,
                redistribution=RedistributionStatus.DERIVED_ONLY,
            ),
        ]
    )

    payload = api.market_series_v2("KR-KRW", _request(), Response())
    availability = {
        item["instrument_id"]: item["availability"]
        for item in payload["instruments"]
    }
    cd = next(
        item
        for item in payload["observations"]
        if item["instrument_id"] == "KR.KOFIA.CD_91D"
    )
    bok = next(
        item
        for item in payload["observations"]
        if item["instrument_id"] == "KR.BOK.BASE_RATE"
    )

    assert availability["KR.BOK.BASE_RATE"] == "RESTRICTED"
    assert availability["KR.KOFIA.CD_91D"] == "DERIVED_CONTEXT"
    assert availability["KR.KSD.KOFR"] == "RESTRICTED"
    assert bok["value"] is None
    assert bok["value_status"] == "REDACTED_BY_LICENCE"
    assert cd["value"] is None
    assert cd["value_status"] == "REDACTED_BY_LICENCE"
    assert payload["status"] == "PARTIAL"


def test_market_series_applies_adapter_policy_when_row_policy_is_stale(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "effective-policy-v2.sqlite")
    stale_policy_row = _rate_observation(
        event_time=datetime(2026, 8, 8, tzinfo=UTC),
        market_id="CN-CNY",
        monetary_area_id="CN",
        jurisdiction="CN",
        currency="CNY",
        instrument_id="CN.CFETS.SHIBOR_ON",
        role=SemanticRole.UNSECURED_OVERNIGHT,
        value="187",
        revision_id="stale-row-policy",
        source="cfets-test",
        connector=ConnectorClassification.OFFICIAL_OPEN,
        redistribution=RedistributionStatus.ALLOWED,
    )
    store.save_observations([stale_policy_row])

    payload = api.market_series_v2("CN-CNY", _request(), Response())
    record = payload["observations"][0]

    assert record["value"] is None
    assert record["value_status"] == "REDACTED_BY_LICENCE"
    assert record["evidence_hash"] == stale_policy_row.evidence_hash
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
        "one or more latest observations are stale or unavailable",
    ]


def test_market_series_instruments_publish_honest_source_references(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "source-reference-v2.sqlite")

    us = api.market_series_v2("US-USD", _request(), Response())
    sofr = next(
        item
        for item in us["instruments"]
        if item["instrument_id"] == "US.NYFED.SOFR_MEDIAN"
    )
    assert sofr["publisher"] == "Federal Reserve Bank of New York"
    assert sofr["source_url"] == (
        "https://markets.newyorkfed.org/static/docs/markets-api.html"
    )

    india = api.market_series_v2("IN-INR", _request(), Response())
    treps = next(
        item for item in india["instruments"] if item["instrument_id"] == "IN.CCIL.TREPS"
    )
    assert treps["publisher"] == "Clearing Corporation of India Limited"
    assert treps["source_url"] is None


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

    eligibility = api.market_series_v2("US-USD", _request(), Response())[
        "evidence_eligibility"
    ]

    assert eligibility["eligible"] is False
    assert (
        "observation quality is not evidence-eligible: provisional"
        in eligibility["reasons"]
    )


def test_market_series_readiness_is_page_independent_and_ages_at_read_time(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "current-series-state.sqlite")
    cutoff = datetime(2026, 8, 21, 12, tzinfo=UTC)

    class FrozenDateTime(datetime):
        current = cutoff

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr(api, "datetime", FrozenDateTime)
    store.save_observations(
        [
            _rate_observation(
                event_time=cutoff - timedelta(days=1),
                instrument_id="US.NYFED.SOFR",
                role=SemanticRole.SECURED_OVERNIGHT,
                source="nyfed_rates",
                revision_id="current-sofr",
            ),
            _rate_observation(
                event_time=cutoff - timedelta(days=2),
                instrument_id="US.FED.IORB",
                role=SemanticRole.POLICY_TARGET,
                source="fred_daily",
                revision_id="current-iorb",
            ),
        ]
    )

    first = api.market_series_v2("US-USD", _request(), Response(), n=1)
    second = api.market_series_v2(
        "US-USD", _request(), Response(), n=1, cursor=first["next_cursor"]
    )

    assert first["observations"][0]["instrument_id"] == "US.NYFED.SOFR"
    assert second["observations"][0]["instrument_id"] == "US.FED.IORB"
    for payload in (first, second):
        availability = {
            item["instrument_id"]: item["availability"]
            for item in payload["instruments"]
        }
        assert availability["US.NYFED.SOFR"] == "READY"
        assert availability["US.FED.IORB"] == "READY"
        # Other declared public instruments have no current row, so a mixed
        # pack is honest PARTIAL rather than READY.
        assert payload["status"] == "PARTIAL"
        assert payload["readiness_scope"] == (
            "latest_public_observation_per_instrument"
        )
        assert payload["stale_inputs"] == []
    assert first["evidence_eligibility"] == second["evidence_eligibility"]

    FrozenDateTime.current = cutoff + timedelta(days=20)
    aged = api.market_series_v2("US-USD", _request(), Response(), n=1)
    aged_availability = {
        item["instrument_id"]: item["availability"] for item in aged["instruments"]
    }

    assert aged["observations"][0]["staleness"] == "dead"
    assert aged_availability["US.NYFED.SOFR"] == "STALE"
    assert aged_availability["US.FED.IORB"] == "STALE"
    assert aged["status"] == "PARTIAL"
    assert {item["instrument_id"] for item in aged["stale_inputs"]} == {
        "US.NYFED.SOFR",
        "US.FED.IORB",
    }
    assert (
        "one or more latest observations are stale or unavailable"
        in aged["evidence_eligibility"]["reasons"]
    )


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


@pytest.fixture
def postgres_reader(monkeypatch):
    """Execute the actual portable SELECT on isolated SQLite, never a server.

    Only DB-API parameter spelling and timestamp binding differ here. Rows use
    PostgreSQL's JSON jurisdiction representation so production hydration also
    runs. This is a SQL semantics test, not a PostgreSQL planner benchmark.
    """

    database = sqlite3.connect(":memory:")
    database.execute(
        "CREATE TABLE canonical_observations ("
        + ",".join(f"{column} TEXT" for column in _OBSERVATION_COLUMNS)
        + ")"
    )
    opened = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, params):
            return database.execute(
                query.replace("%s", "?"),
                [
                    value.isoformat() if isinstance(value, datetime) else value
                    for value in params
                ],
            )

    def connect():
        opened.append(True)
        return Connection()

    repository = PostgresMarketRepository("postgresql://unused.invalid/test")
    repository._initialized = True
    monkeypatch.setattr(repository, "_connect", connect)
    yield repository, database, opened
    database.close()


def _observation(**changes):
    event = datetime(2026, 8, 8, tzinfo=UTC)
    row = Observation(
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
        event_time=event,
        source_publication_time=event,
        knowledge_time=event,
        revision_id="v1",
        source="nyfed_rates",
        evidence_hash=evidence_sha256("synthetic batch test"),
        connector_classification=ConnectorClassification.OFFICIAL_OPEN,
        redistribution_status=RedistributionStatus.ALLOWED,
        quality=QualityState.VERIFIED,
        staleness=StalenessState.FRESH,
    )
    return replace(row, **changes)


def _seed(database, observations):
    records = []
    for observation in observations:
        record = observation.to_record()
        record["jurisdiction_codes"] = json.dumps(record["jurisdiction_codes"])
        records.append(tuple(record[column] for column in _OBSERVATION_COLUMNS))
    database.executemany(
        "INSERT INTO canonical_observations VALUES ("
        + ",".join("?" for _ in _OBSERVATION_COLUMNS)
        + ")",
        records,
    )


def test_batch_preserves_vintages_cutoffs_market_instrument_pairs_and_order(
    postgres_reader,
):
    repository, database, opened = postgres_reader
    base = _observation(instrument_id="SHARED")
    cutoff = base.event_time + timedelta(days=2)
    floor = base.event_time - timedelta(days=1)
    revision_time = base.event_time + timedelta(hours=1)
    revised = replace(
        base,
        knowledge_time=revision_time,
        source_publication_time=revision_time,
        revision_id="v2",
        value="532",
        redistribution_status=RedistributionStatus.PROHIBITED,
    )
    euro = replace(
        base,
        market_id="EA-EUR",
        monetary_area_id="EA",
        jurisdiction_codes=("DE",),
        currency="EUR",
        instrument_id="EU.ONLY",
        value="301",
    )
    earlier = replace(base, event_time=floor)
    later = replace(base, event_time=cutoff)
    _seed(
        database,
        [
            base,
            revised,
            earlier,
            later,
            euro,
            replace(revised, source_publication_time=base.event_time, revision_id="z9"),
            replace(revised, revision_id="v1", value="533"),
            # A future vintage cannot replace the known prohibited revision.
            replace(
                revised, knowledge_time=cutoff + timedelta(seconds=1), revision_id="v3"
            ),
            replace(base, event_time=floor - timedelta(seconds=1)),
            replace(base, event_time=cutoff + timedelta(seconds=1)),
            # A cross-product of market and instrument filters would leak these.
            replace(euro, instrument_id="SHARED"),
            replace(base, instrument_id="EU.ONLY"),
            replace(base, market_id="UK-GBP", monetary_area_id="UK", currency="GBP"),
        ],
    )
    selections = {"us-usd": ("SHARED", "SHARED"), "EA-EUR": ("EU.ONLY",), "UK-GBP": ()}
    batch = repository.load_observations_batch_as_of(
        selections,
        cutoff,
        event_time=cutoff,
        event_time_from=floor,
    )
    assert len(opened) == 1
    assert batch == {
        "US-USD": [earlier, revised, later],
        "EA-EUR": [euro],
        "UK-GBP": [],
    }
    assert batch == {
        market.upper(): repository.load_observations_as_of(
            market,
            cutoff,
            event_time=cutoff,
            event_time_from=floor,
            instrument_ids=instruments,
        )
        for market, instruments in selections.items()
    }


@pytest.mark.parametrize("selections", [{}, {"US-USD": ()}])
def test_empty_batch_never_opens_an_unbounded_read(postgres_reader, selections):
    repository, _, opened = postgres_reader
    cutoff = datetime(2026, 8, 10, tzinfo=UTC)
    assert repository.load_observations_batch_as_of(
        selections,
        cutoff,
        event_time=cutoff,
        event_time_from=cutoff - timedelta(days=1),
    ) == {market: [] for market in selections}
    assert opened == []


def test_batch_rejects_ambiguous_market_keys_and_reversed_bounds(postgres_reader):
    repository, _, opened = postgres_reader
    cutoff = datetime(2026, 8, 10, tzinfo=UTC)
    with pytest.raises(ValueError, match="duplicate normalized market"):
        repository.load_observations_batch_as_of(
            {"US-USD": (), "us-usd": ()},
            cutoff,
            event_time=cutoff,
            event_time_from=cutoff,
        )
    with pytest.raises(ValueError, match="event_time_from"):
        repository.load_observations_batch_as_of(
            {"US-USD": ("SOFR",)},
            cutoff,
            event_time=cutoff,
            event_time_from=cutoff + timedelta(seconds=1),
        )
    assert opened == []


def _freeze_api(monkeypatch, repository):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 10, 12, tzinfo=UTC)

    def forbidden_build(*_args, **_kwargs):
        raise AssertionError("atlas must not restore or rebuild a board")

    monkeypatch.setattr(api, "datetime", Clock)
    monkeypatch.setattr(api, "get_repository", lambda: repository)
    monkeypatch.setattr(assemble, "snapshot", forbidden_build)
    monkeypatch.setattr(assemble, "restore_cached_snapshot", forbidden_build)


def test_atlas_batch_is_exactly_equal_to_legacy_projection_with_rights_gates(
    postgres_reader,
    monkeypatch,
):
    repository, database, opened = postgres_reader
    base = _observation()
    protected = _observation(
        instrument_id="US.FED.IORB",
        source="fred_daily",
        value="540",
        semantic_role=SemanticRole.POLICY_TARGET,
    )
    _seed(
        database,
        [
            base,
            protected,
            replace(
                protected,
                revision_id="v2",
                knowledge_time=protected.knowledge_time + timedelta(hours=1),
                redistribution_status=RedistributionStatus.PROHIBITED,
            ),
            # Adapter/pack policy must still exclude an otherwise allowed row.
            replace(
                base,
                market_id="CN-CNY",
                monetary_area_id="CN",
                currency="CNY",
                instrument_id="CN.CFETS.SHIBOR.ON",
                source="chinamoney",
            ),
            *[
                replace(
                    base,
                    market_id="UK-GBP",
                    monetary_area_id="UK",
                    currency="GBP",
                    instrument_id="GB.BOE.SONIA",
                    source="boe_sonia",
                    value=str(400 + index),
                    event_time=base.event_time - timedelta(days=index),
                    redistribution_status=RedistributionStatus.DERIVED_ONLY,
                )
                for index in range(30)
            ],
        ],
    )
    monkeypatch.setattr(repository, "latest_collector_runs", lambda: [])
    _freeze_api(monkeypatch, repository)
    response = Response()
    batched = api.global_money_markets_v2(response)
    assert len(opened) == 1
    monkeypatch.setattr(repository, "load_observations_batch_as_of", None)
    legacy_response = Response()
    legacy = api.global_money_markets_v2(legacy_response)
    assert batched == legacy
    assert response.headers == legacy_response.headers
    assert batched["coverage"]["declared_markets"] == 11
    assert batched["read_faults"] == []
    us = next(
        market for market in batched["markets"] if market["market_id"] == "US-USD"
    )
    assert us["benchmark"]["value"] == 5.31
    iorb = next(
        metric for metric in us["metrics"] if metric["id"] == protected.instrument_id
    )
    assert iorb["value"] is None  # Do not fall back to its older allowed vintage.
    uk = next(
        market for market in batched["markets"] if market["market_id"] == "UK-GBP"
    )
    assert uk["derived_benchmark"]["value"] is None
    assert uk["derived_benchmark"]["history"] == []
    assert "CN.CFETS.SHIBOR.ON" not in json.dumps(batched)


def test_batch_failure_preserves_individual_faults_cutoff_and_sanitization(
    monkeypatch, caplog
):
    secret = "postgresql://private.invalid/credential-do-not-log"
    calls = []
    bounds = []

    class Repository:
        def load_observations_batch_as_of(self, selections, cutoff, **kwargs):
            assert len(selections) == 11
            bounds.append((cutoff, kwargs["event_time"], kwargs["event_time_from"]))
            raise ValueError(secret)

        def load_observations_as_of(self, market_id, cutoff, **kwargs):
            calls.append(market_id)
            assert (cutoff, kwargs["event_time"], kwargs["event_time_from"]) == bounds[
                0
            ]
            if market_id == "EA-EUR":
                raise ValueError(secret)
            return [_observation()] if market_id == "US-USD" else []

        def latest_collector_runs(self):
            return []

    repository = Repository()
    _freeze_api(monkeypatch, repository)
    result = api.global_money_markets_v2(Response())
    assert len(calls) == len(set(calls)) == 11
    assert len(result["read_faults"]) == 1
    assert result["read_faults"][0]["market_id"] == "EA-EUR"
    assert result["read_faults"][0]["source"] == "canonical_repository"
    assert result["status"] == "PARTIAL"
    us = next(market for market in result["markets"] if market["market_id"] == "US-USD")
    assert us["benchmark"]["value"] == 5.31
    assert secret not in json.dumps(result)
    assert secret not in caplog.text
    monkeypatch.setattr(repository, "load_observations_batch_as_of", None)
    assert result == api.global_money_markets_v2(Response())


def test_bad_row_in_one_market_does_not_erase_other_batch_markets(
    postgres_reader, monkeypatch
):
    repository, database, opened = postgres_reader
    _seed(
        database,
        [
            _observation(),
            _observation(
                market_id="EA-EUR",
                monetary_area_id="EA",
                currency="EUR",
                instrument_id="EA.ECB.ESTR",
                source="ecb_benchmark",
            ),
        ],
    )
    database.execute(
        "UPDATE canonical_observations SET quality='CORRUPT' WHERE market_id='EA-EUR'"
    )
    monkeypatch.setattr(repository, "latest_collector_runs", lambda: [])
    _freeze_api(monkeypatch, repository)
    result = api.global_money_markets_v2(Response())
    assert len(opened) > 1  # Failed hydration recovers through isolated reads.
    assert [fault["market_id"] for fault in result["read_faults"]] == ["EA-EUR"]
    us = next(market for market in result["markets"] if market["market_id"] == "US-USD")
    assert us["benchmark"]["value"] == 5.31
