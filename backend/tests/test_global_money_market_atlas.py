from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from seiche.domain.observation import (
    RATE_ROLES,
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
from seiche.markets.atlas import (
    ATLAS_SCHEMA,
    EXPANSION_LEDGER,
    _validate_expansion_ledger,
    build_global_money_market_atlas,
)
from seiche.markets.registry import default_registry


def _row(
    market_id: str,
    instrument_id: str,
    role: SemanticRole,
    day: datetime,
    value: float,
    *,
    source: str,
    currency: str,
    area: str,
    jurisdictions: tuple[str, ...],
    canonical_unit: CanonicalUnit | None = None,
    connector: ConnectorClassification = ConnectorClassification.OFFICIAL_OPEN,
    redistribution: RedistributionStatus = RedistributionStatus.ALLOWED,
) -> Observation:
    evidence = f"{market_id}:{instrument_id}:{day.date()}:{value}"
    is_rate = role in RATE_ROLES
    return Observation(
        market_id=market_id,
        monetary_area_id=area,
        jurisdiction_codes=jurisdictions,
        currency=currency,
        instrument_id=instrument_id,
        semantic_role=role,
        value=value,
        canonical_unit=(
            CanonicalUnit.BASIS_POINTS
            if is_rate
            else canonical_unit or CanonicalUnit.LOCAL_CURRENCY_MILLIONS
        ),
        rate_compounding=RateCompounding.SIMPLE if is_rate else None,
        day_count=(
            DayCountConvention.ACT_360
            if currency in {"USD", "EUR"}
            else DayCountConvention.ACT_365
        )
        if is_rate
        else None,
        event_time=day,
        source_publication_time=day + timedelta(hours=8),
        knowledge_time=day + timedelta(hours=9),
        revision_id=evidence,
        source=source,
        evidence_hash=evidence_sha256(evidence),
        connector_classification=connector,
        redistribution_status=redistribution,
        quality=QualityState.VERIFIED,
        staleness=StalenessState.FRESH,
    )


def _pbc_calendar_test_pack():
    """Opt a local fixture into PBC values without weakening the real pack."""

    pack = default_registry().get("CN-CNY")
    pbc = pack.adapter_map["pbc_operations"]
    assert pbc.classification is ConnectorClassification.UNAVAILABLE
    assert pbc.redistribution_status is RedistributionStatus.METADATA_ONLY
    allowed_pbc = replace(
        pbc,
        classification=ConnectorClassification.OFFICIAL_OPEN,
        redistribution_status=RedistributionStatus.ALLOWED,
    )
    return replace(
        pack,
        source_adapters=tuple(
            allowed_pbc if adapter.adapter_id == pbc.adapter_id else adapter
            for adapter in pack.source_adapters
        ),
    )


def test_all_declared_and_planned_markets_remain_visible_without_data():
    out = build_global_money_market_atlas(
        default_registry().list(),
        {},
        as_of=datetime(2026, 8, 21, tzinfo=UTC),
    )

    assert out["schema"] == ATLAS_SCHEMA
    assert out["legal_notices"]
    assert out["status"] == "PARTIAL"
    assert len(out["markets"]) == 11
    assert all(market["status"] == "DECLARED_UNAVAILABLE" for market in out["markets"])
    assert {market["market_id"] for market in out["markets"]} >= {
        "US-USD",
        "EA-EUR",
        "UK-GBP",
        "IN-INR",
        "CN-CNY",
        "JP-JPY",
        "HK-HKD",
        "KR-KRW",
        "SG-SGD",
        "AU-AUD",
        "NZ-NZD",
    }
    assert "KR-KRW" not in {item["market_id"] for item in out["expansion_ledger"]}


def test_expansion_ledger_is_broad_source_audited_and_not_live_coverage():
    out = build_global_money_market_atlas(
        default_registry().list(),
        {},
        as_of=datetime(2026, 8, 21, tzinfo=UTC),
    )
    registered_ids = {pack.market_id for pack in default_registry().list()}
    expansion_ids = {item["market_id"] for item in EXPANSION_LEDGER}
    required_fields = {
        "market_id",
        "region",
        "currency",
        "market",
        "benchmark",
        "benchmark_kind",
        "authority",
        "source_url",
        "official_reference_url",
        "methodology_url",
        "data_endpoint",
        "terms_url",
        "access",
        "access_note",
        "confidence",
        "status",
        "verified_on",
    }

    assert len(EXPANSION_LEDGER) >= 50
    assert len(expansion_ids) == len(EXPANSION_LEDGER)
    assert registered_ids.isdisjoint(expansion_ids)
    assert {"CA-CAD", "CZ-CZK", "ID-IDR", "AE-AED", "ZA-ZAR"} <= expansion_ids
    assert all(required_fields <= item.keys() for item in EXPANSION_LEDGER)
    assert all(item["source_url"].startswith("https://") for item in EXPANSION_LEDGER)
    assert all(
        item["confidence"] in {"HIGH", "MEDIUM", "LOW"} for item in EXPANSION_LEDGER
    )
    assert all(item["verified_on"] == "2026-08-21" for item in EXPANSION_LEDGER)
    assert out["coverage"]["global_discovery_universe"] == (
        len(registered_ids) + len(EXPANSION_LEDGER)
    )
    assert out["coverage"]["discovery_candidates"] == len(EXPANSION_LEDGER)
    assert "deprecated alias" in out["coverage"]["legacy_aliases"]["planned_markets"]
    assert out["coverage"]["source_verified_candidates"] > 25
    assert out["coverage"]["compliance_blocked_candidates"] == 1
    assert "not live data coverage" in out["expansion_scope"]["exclusions"]


def test_policy_spread_uses_only_exact_common_event_dates():
    pack = default_registry().get("AU-AUD")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    cash = [
        _row(
            "AU-AUD",
            "AU.RBA.AONIA",
            SemanticRole.UNSECURED_OVERNIGHT,
            start + timedelta(days=index),
            435 + index,
            source="rba_cash",
            currency="AUD",
            area="AU",
            jurisdictions=("AU",),
        )
        for index in range(30)
    ]
    target = [
        _row(
            "AU-AUD",
            "AU.RBA.CASH_TARGET",
            SemanticRole.POLICY_TARGET,
            start + timedelta(days=index),
            430,
            source="rba_policy",
            currency="AUD",
            area="AU",
            jurisdictions=("AU",),
        )
        for index in range(29)
    ]
    cutoff = start + timedelta(days=31)

    out = build_global_money_market_atlas(
        (pack,), {pack.market_id: [*cash, *target]}, as_of=cutoff
    )
    market = out["markets"][0]
    spread = market["policy_relative_spread"]

    assert (
        market["benchmark"]["asof"] == (start + timedelta(days=29)).date().isoformat()
    )
    assert spread["asof"] == (start + timedelta(days=28)).date().isoformat()
    assert spread["value"] == 33.0
    assert spread["alignment"].startswith("exact event_time")
    assert len(spread["history"]) == 29
    assert spread["event_time"] == (start + timedelta(days=28)).isoformat()
    assert {item["instrument_id"] for item in spread["input_lineage"]} == {
        "AU.RBA.AONIA",
        "AU.RBA.CASH_TARGET",
    }
    assert all(item["evidence_hash"] for item in spread["input_lineage"])


def test_sparse_native_series_is_never_upsampled():
    pack = default_registry().get("JP-JPY")
    days = (
        datetime(2026, 1, 31, tzinfo=UTC),
        datetime(2026, 2, 28, tzinfo=UTC),
        datetime(2026, 3, 31, tzinfo=UTC),
    )
    rows = [
        _row(
            "JP-JPY",
            "JP.BOJ.TONA",
            SemanticRole.UNSECURED_OVERNIGHT,
            day,
            50 + index,
            source="boj_rates",
            currency="JPY",
            area="JP",
            jurisdictions=("JP",),
        )
        for index, day in enumerate(days)
    ]

    out = build_global_money_market_atlas(
        (pack,), {pack.market_id: rows}, as_of=datetime(2026, 4, 1, tzinfo=UTC)
    )

    assert out["markets"][0]["benchmark"]["history"] == [
        [day.date().isoformat(), (50 + index) / 100] for index, day in enumerate(days)
    ]
    assert out["markets"][0]["benchmark"]["n_observations"] == 3


def test_restricted_instrument_metadata_is_visible_without_value():
    pack = default_registry().get("UK-GBP")
    out = build_global_money_market_atlas(
        (pack,), {}, as_of=datetime(2026, 8, 21, tzinfo=UTC)
    )
    sonia = next(
        metric
        for metric in out["markets"][0]["metrics"]
        if metric["id"] == "GB.BOE.SONIA"
    )

    assert sonia["availability"] == "RESTRICTED"
    assert sonia["value"] is None
    assert sonia["redistribution_status"] == "derived_only"


def test_derived_only_benchmark_exposes_non_reversible_context_not_raw_values():
    pack = default_registry().get("UK-GBP")
    start = datetime(2026, 5, 1, tzinfo=UTC)
    rows = [
        _row(
            "UK-GBP",
            "GB.BOE.SONIA",
            SemanticRole.UNSECURED_OVERNIGHT,
            start + timedelta(days=index),
            420 + (index % 11) * 2 + index / 10,
            source="boe_sonia",
            currency="GBP",
            area="UK",
            jurisdictions=("GB",),
            redistribution=RedistributionStatus.DERIVED_ONLY,
        )
        for index in range(60)
    ]

    out = build_global_money_market_atlas(
        (pack,), {pack.market_id: rows}, as_of=start + timedelta(days=61)
    )
    market = out["markets"][0]
    sonia = next(
        metric for metric in market["metrics"] if metric["id"] == "GB.BOE.SONIA"
    )

    assert market["status"] == "DERIVED_CONTEXT"
    assert market["benchmark"] is None
    assert market["derived_benchmark"]["id"] == "GB.BOE.SONIA"
    assert market["policy_relative_spread"] is None
    assert sonia["availability"] == "DERIVED_CONTEXT"
    assert sonia["value"] is None
    assert sonia["canonical_value"] is None
    assert sonia["history"] == []
    assert sonia["change_1_observation"] is None
    assert sonia["change_5_observations"] is None
    assert sonia["change_20_observations"] is None
    assert sonia["change_vol_20_annualized"] is None
    assert sonia["robust_z_1y"] is not None
    assert sonia["percentile_3y"] is not None
    assert sonia["n_observations"] == 60
    assert out["coverage"]["derived_context_benchmarks"] == 1
    assert "raw level and history are withheld" in market["plain_language"]


def test_policy_rate_is_not_promoted_into_a_traded_benchmark():
    pack = default_registry().get("UK-GBP")
    observed_at = datetime(2026, 8, 20, tzinfo=UTC)
    bank_rate = _row(
        "UK-GBP",
        "GB.BOE.BANK_RATE",
        SemanticRole.POLICY_TARGET,
        observed_at,
        375,
        source="boe_policy",
        currency="GBP",
        area="UK",
        jurisdictions=("GB",),
    )

    out = build_global_money_market_atlas(
        (pack,), {pack.market_id: (bank_rate,)}, as_of=observed_at + timedelta(days=1)
    )
    market = out["markets"][0]

    assert market["benchmark"] is None
    assert market["policy_anchor"]["value"] == 3.75
    assert market["status"] == "POLICY_ONLY"
    assert "no redistributable traded benchmark" in market["plain_language"]
    assert out["coverage"]["policy_only_markets"] == 1


def test_atlas_is_json_safe_deterministic_and_point_in_time():
    pack = default_registry().get("EA-EUR")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        _row(
            "EA-EUR",
            "EA.ECB.ESTR",
            SemanticRole.UNSECURED_OVERNIGHT,
            start + timedelta(days=index),
            200 + index / 10,
            source="ecb_benchmark",
            currency="EUR",
            area="EA",
            jurisdictions=("DE",),
        )
        for index in range(40)
    ]
    cutoff = start + timedelta(days=20, hours=12)

    full = build_global_money_market_atlas(
        (pack,), {pack.market_id: rows}, as_of=cutoff
    )
    truncated = build_global_money_market_atlas(
        (pack,), {pack.market_id: rows[:21]}, as_of=cutoff
    )

    assert full == truncated
    encoded = json.dumps(full, allow_nan=False, sort_keys=True)
    assert encoded == json.dumps(truncated, allow_nan=False, sort_keys=True)
    assert "NaN" not in encoded and "Infinity" not in encoded


def test_weekly_statistics_use_native_cadence_and_old_rows_age_at_read_time():
    pack = default_registry().get("EA-EUR")
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = [
        _row(
            "EA-EUR",
            "EA.ECB.EXCESS_LIQUIDITY",
            SemanticRole.SYSTEM_LIQUIDITY,
            start + timedelta(weeks=index),
            100_000 + index * 250,
            source="ecb_liquidity",
            currency="EUR",
            area="EA",
            jurisdictions=("DE",),
        )
        for index in range(60)
    ]
    cutoff = rows[-1].event_time + timedelta(days=20)

    out = build_global_money_market_atlas((pack,), {pack.market_id: rows}, as_of=cutoff)
    metric = next(
        item
        for item in out["markets"][0]["metrics"]
        if item["id"] == "EA.ECB.EXCESS_LIQUIDITY"
    )

    assert metric["statistics_window"]["one_year"]["observations"] == 53
    assert metric["statistics_window"]["three_year"]["observations"] == 60
    assert "true elapsed calendar-time" in metric["statistics_window"]["basis"]
    assert metric["robust_z_1y"] is not None
    assert metric["status"] == "AGING"
    assert metric["missed_publication_opportunities"] == 3
    # Observation integrity and source freshness are deliberately separate:
    # the value remains verified even while its publication clock is aging.
    assert metric["confidence"] == "high"


def test_recent_successful_run_does_not_refresh_old_estr_observation():
    pack = default_registry().get("EA-EUR")
    cutoff = datetime(2026, 8, 21, tzinfo=UTC)
    end = cutoff - timedelta(days=120)
    rows = [
        _row(
            "EA-EUR",
            "EA.ECB.ESTR",
            SemanticRole.UNSECURED_OVERNIGHT,
            end - timedelta(days=79 - index),
            190 + index,
            source="ecb_benchmark",
            currency="EUR",
            area="EA",
            jurisdictions=("DE",),
        )
        for index in range(80)
    ]
    run_finished = cutoff - timedelta(hours=1)

    out = build_global_money_market_atlas(
        (pack,),
        {pack.market_id: rows},
        collector_runs=(
            {
                "market_id": pack.market_id,
                "adapter_id": "ecb_benchmark",
                "status": "SUCCESS",
                "finished_at": run_finished.isoformat(),
                "next_due": (cutoff + timedelta(days=1)).isoformat(),
                "fault": None,
            },
        ),
        as_of=cutoff,
    )
    market = out["markets"][0]
    adapter = next(
        item for item in market["adapters"] if item["adapter_id"] == "ecb_benchmark"
    )

    assert market["benchmark"]["asof"] == end.date().isoformat()
    assert market["benchmark"]["status"] == "DEAD"
    assert market["status"] == "STALE_REFERENCE"
    assert out["strongest_divergence"] is None
    assert adapter["last_run_status"] == "SUCCESS"
    assert adapter["last_finished_at"] == run_finished.isoformat()
    assert "elapsed calendar year" in out["methodology"]["robust_z"]
    assert "minimum 20" in out["methodology"]["robust_z"]


def test_prohibited_adapter_and_run_metadata_are_omitted_from_public_atlas():
    pack = default_registry().get("EA-EUR")
    cutoff = datetime(2026, 8, 21, tzinfo=UTC)
    private_instrument = replace(
        pack.instruments[0],
        instrument_id="EA.PRIVATE.TENANT_RATE",
        mnemonic="PRIVATE_TENANT_RATE",
        source_adapter_id="tenant_market_data",
    )
    pack = replace(pack, instruments=(*pack.instruments, private_instrument))
    private_row = _row(
        "EA-EUR",
        private_instrument.instrument_id,
        private_instrument.semantic_role,
        cutoff - timedelta(days=1),
        999,
        source="private_tenant_source",
        currency="EUR",
        area="EA",
        jurisdictions=("DE",),
        connector=ConnectorClassification.TENANT_PROVIDED,
        redistribution=RedistributionStatus.PROHIBITED,
    )
    out = build_global_money_market_atlas(
        (pack,),
        {pack.market_id: (private_row,)},
        collector_runs=(
            {
                "market_id": "EA-EUR",
                "adapter_id": "tenant_market_data",
                "status": "FAILED",
                "finished_at": "2026-08-20T09:00:00+00:00",
                "next_due": "2026-08-21T09:00:00+00:00",
                "fault": "private tenant endpoint and credential detail",
            },
        ),
        as_of=cutoff,
    )
    market = out["markets"][0]
    serialized = json.dumps(out, sort_keys=True)

    assert "tenant_market_data" not in serialized
    assert private_instrument.instrument_id not in serialized
    assert "private_tenant_source" not in serialized
    assert "private tenant endpoint and credential detail" not in serialized
    assert market["coverage"]["declared_instruments"] == len(pack.instruments)
    assert (
        market["coverage"]["public_projected_instruments"] == len(pack.instruments) - 1
    )
    assert market["coverage"]["omitted_by_policy"] == 1
    assert market["coverage"]["declared_adapters"] == len(pack.source_adapters)
    assert market["coverage"]["public_projected_adapters"] == 3
    assert market["coverage"]["omitted_adapters_by_policy"] == 1
    assert not market["faults"]


def test_future_collector_run_cannot_leak_into_point_in_time_atlas():
    pack = default_registry().get("EA-EUR")
    observed_at = datetime(2026, 8, 20, tzinfo=UTC)
    estr = _row(
        "EA-EUR",
        "EA.ECB.ESTR",
        SemanticRole.UNSECURED_OVERNIGHT,
        observed_at,
        190,
        source="ecb_benchmark",
        currency="EUR",
        area="EA",
        jurisdictions=("DE",),
    )
    cutoff = observed_at + timedelta(days=1)

    out = build_global_money_market_atlas(
        (pack,),
        {pack.market_id: (estr,)},
        collector_runs=(
            {
                "market_id": "EA-EUR",
                "adapter_id": "ecb_benchmark",
                "status": "FAILED",
                "finished_at": (cutoff + timedelta(days=1)).isoformat(),
                "next_due": (cutoff + timedelta(days=2)).isoformat(),
                "fault": "not knowable yet",
            },
        ),
        as_of=cutoff,
    )
    market = out["markets"][0]
    adapter = next(
        item for item in market["adapters"] if item["adapter_id"] == "ecb_benchmark"
    )

    assert market["benchmark"]["status"] == "FRESH"
    assert adapter["last_run_status"] == "NO_RUN_RECORDED"
    assert adapter["fault"] is None


def test_stale_benchmark_remains_visible_but_cannot_win_global_ranking():
    pack = default_registry().get("EA-EUR")
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = [
        _row(
            "EA-EUR",
            "EA.ECB.ESTR",
            SemanticRole.UNSECURED_OVERNIGHT,
            start + timedelta(days=index),
            200 + (index % 9) * 3,
            source="ecb_benchmark",
            currency="EUR",
            area="EA",
            jurisdictions=("DE",),
        )
        for index in range(80)
    ]
    cutoff = rows[-1].event_time + timedelta(days=30)

    out = build_global_money_market_atlas(
        (pack,),
        {pack.market_id: rows},
        as_of=cutoff,
    )
    market = out["markets"][0]

    assert market["status"] == "STALE_REFERENCE"
    assert market["benchmark"]["status"] == "DEAD"
    assert market["benchmark"]["value"] is not None
    assert out["strongest_divergence"] is None
    assert out["coverage"]["stale_benchmarks"] == 1
    assert "cannot enter the current global ranking" in market["plain_language"]
    assert "non-stale benchmark" in out["plain_language"]


def test_derived_context_uses_strict_allowlist_and_leaks_no_source_clock_or_lineage():
    pack = default_registry().get("UK-GBP")
    start = datetime(2026, 6, 1, tzinfo=UTC)
    rows = []
    for index in range(25):
        row = _row(
            "UK-GBP",
            "GB.BOE.SONIA",
            SemanticRole.UNSECURED_OVERNIGHT,
            start + timedelta(days=index),
            400 + index,
            source="SOURCE_SECRET",
            currency="GBP",
            area="UK",
            jurisdictions=("GB",),
            connector=ConnectorClassification.LICENSED,
            redistribution=RedistributionStatus.DERIVED_ONLY,
        )
        rows.append(
            replace(
                row,
                revision_id=(
                    "REVISION_SECRET:RAW_VALUE_CANARY"
                    if index == 7
                    else f"REVISION_SECRET:{index}"
                ),
            )
        )
    cutoff = start + timedelta(days=26)
    out = build_global_money_market_atlas(
        (pack,),
        {pack.market_id: rows},
        collector_runs=(
            {
                "market_id": pack.market_id,
                "adapter_id": "boe_sonia",
                "status": "SUCCESS",
                "finished_at": (cutoff - timedelta(hours=1)).isoformat(),
                "next_due": "NEXT_DUE_SECRET",
                "fault": None,
            },
        ),
        as_of=cutoff,
    )
    metric = out["markets"][0]["derived_benchmark"]
    serialized = json.dumps(out, sort_keys=True)

    assert set(metric) == {
        "id",
        "mnemonic",
        "label",
        "semantic_role",
        "availability",
        "value",
        "unit",
        "canonical_value",
        "canonical_unit",
        "cadence",
        "redistribution_status",
        "confidence",
        "status",
        "change_1_observation",
        "change_5_observations",
        "change_20_observations",
        "change_unit",
        "robust_z_1y",
        "percentile_3y",
        "change_vol_20_annualized",
        "change_vol_unit",
        "n_observations",
        "statistics_window",
        "day_count",
        "compounding",
        "formula",
        "formula_version",
        "explanation",
        "history_clock",
        "history",
    }
    assert metric["value"] is None
    assert metric["history"] == []
    for canary in (
        "RAW_VALUE_CANARY",
        "SOURCE_SECRET",
        "NEXT_DUE_SECRET",
        "REVISION_SECRET",
    ):
        assert canary not in serialized


def test_public_history_filters_every_row_by_redistribution_rights():
    pack = default_registry().get("US-USD")
    start = datetime(2026, 7, 1, tzinfo=UTC)
    rows = [
        _row(
            "US-USD",
            "US.NYFED.SOFR",
            SemanticRole.SECURED_OVERNIGHT,
            start + timedelta(days=index),
            400 + index,
            source="fred_daily",
            currency="USD",
            area="US",
            jurisdictions=("US",),
        )
        for index in range(21)
    ]
    rows[5] = replace(
        rows[5],
        value="499",
        redistribution_status=RedistributionStatus.DERIVED_ONLY,
        revision_id="OLDER_RAW_RIGHTS_CANARY",
    )
    rows[-1] = replace(rows[-1], value="500", revision_id="LATEST_ALLOWED")

    out = build_global_money_market_atlas(
        (pack,),
        {pack.market_id: rows},
        as_of=start + timedelta(days=21, hours=12),
    )
    metric = out["markets"][0]["benchmark"]

    assert metric["value"] == 5.0
    assert metric["n_observations"] == 21
    assert len(metric["history"]) == 20
    assert all(value != 4.99 for _, value in metric["history"])
    assert "OLDER_RAW_RIGHTS_CANARY" not in json.dumps(out, sort_keys=True)


def test_fresh_effr_beats_dead_sofr_despite_role_priority():
    pack = default_registry().get("US-USD")
    cutoff = datetime(2026, 8, 21, 12, tzinfo=UTC)
    dead_sofr = _row(
        "US-USD",
        "US.NYFED.SOFR",
        SemanticRole.SECURED_OVERNIGHT,
        cutoff - timedelta(days=60),
        510,
        source="fred_daily",
        currency="USD",
        area="US",
        jurisdictions=("US",),
    )
    fresh_effr = _row(
        "US-USD",
        "US.NYFED.EFFR",
        SemanticRole.UNSECURED_OVERNIGHT,
        cutoff - timedelta(days=1),
        490,
        source="fred_daily",
        currency="USD",
        area="US",
        jurisdictions=("US",),
    )

    out = build_global_money_market_atlas(
        (pack,), {pack.market_id: (dead_sofr, fresh_effr)}, as_of=cutoff
    )
    market = out["markets"][0]

    assert market["benchmark"]["id"] == "US.NYFED.EFFR"
    assert market["benchmark"]["status"] == "FRESH"
    sofr = next(item for item in market["metrics"] if item["id"] == "US.NYFED.SOFR")
    assert sofr["status"] == "DEAD"


def test_old_exact_overlap_spread_is_aged_and_excluded_from_current_prose():
    pack = default_registry().get("AU-AUD")
    cutoff = datetime(2026, 8, 21, 12, tzinfo=UTC)
    overlap = cutoff - timedelta(days=100)
    rows = (
        _row(
            "AU-AUD",
            "AU.RBA.AONIA",
            SemanticRole.UNSECURED_OVERNIGHT,
            overlap,
            435,
            source="rba_cash",
            currency="AUD",
            area="AU",
            jurisdictions=("AU",),
        ),
        _row(
            "AU-AUD",
            "AU.RBA.CASH_TARGET",
            SemanticRole.POLICY_TARGET,
            overlap,
            430,
            source="rba_policy",
            currency="AUD",
            area="AU",
            jurisdictions=("AU",),
        ),
        _row(
            "AU-AUD",
            "AU.RBA.AONIA",
            SemanticRole.UNSECURED_OVERNIGHT,
            cutoff - timedelta(days=1),
            440,
            source="rba_cash",
            currency="AUD",
            area="AU",
            jurisdictions=("AU",),
        ),
        _row(
            "AU-AUD",
            "AU.RBA.CASH_TARGET",
            SemanticRole.POLICY_TARGET,
            cutoff - timedelta(days=1) + timedelta(hours=1),
            430,
            source="rba_policy",
            currency="AUD",
            area="AU",
            jurisdictions=("AU",),
        ),
    )

    out = build_global_money_market_atlas(
        (pack,), {pack.market_id: rows}, as_of=cutoff
    )
    market = out["markets"][0]
    spread = market["policy_relative_spread"]

    assert market["benchmark"]["status"] == "FRESH"
    assert market["policy_anchor"]["status"] == "FRESH"
    assert spread["status"] == "DEAD"
    assert spread["current_for_prose"] is False
    assert spread["observation_age_days"] == 100.0
    assert "versus" not in market["plain_language"]
    assert spread["asof"] not in market["plain_language"]


def test_publication_clock_respects_weekends_holidays_and_china_working_weekends():
    us_pack = default_registry().get("US-USD")
    friday = datetime(2026, 8, 21, 12, tzinfo=UTC)
    friday_row = _row(
        "US-USD",
        "US.NYFED.EFFR",
        SemanticRole.UNSECURED_OVERNIGHT,
        friday,
        490,
        source="fred_daily",
        currency="USD",
        area="US",
        jurisdictions=("US",),
    )
    monday = datetime(2026, 8, 24, 12, tzinfo=UTC)
    us_metric = build_global_money_market_atlas(
        (us_pack,), {us_pack.market_id: (friday_row,)}, as_of=monday
    )["markets"][0]["benchmark"]
    assert us_metric["status"] == "FRESH"
    assert us_metric["missed_publication_opportunities"] <= 1

    euro_pack = default_registry().get("EA-EUR")
    lagged_row = _row(
        "EA-EUR",
        "EA.ECB.ESTR",
        SemanticRole.UNSECURED_OVERNIGHT,
        friday,
        190,
        source="ecb_benchmark",
        currency="EUR",
        area="EA",
        jurisdictions=("DE",),
    )
    lagged_metric = build_global_money_market_atlas(
        (euro_pack,),
        {euro_pack.market_id: (lagged_row,)},
        as_of=monday,
    )["markets"][0]["benchmark"]
    assert lagged_metric["missed_publication_opportunities"] == 0
    assert lagged_metric["expected_next_update"].startswith("2026-08-25")

    before_holiday = datetime(2026, 7, 2, 12, tzinfo=UTC)
    holiday_row = replace(
        friday_row,
        event_time=before_holiday,
        source_publication_time=before_holiday + timedelta(hours=8),
        knowledge_time=before_holiday + timedelta(hours=9),
        revision_id="holiday-clock",
    )
    holiday_metric = build_global_money_market_atlas(
        (us_pack,),
        {us_pack.market_id: (holiday_row,)},
        as_of=datetime(2026, 7, 3, 12, tzinfo=UTC),
    )["markets"][0]["benchmark"]
    assert holiday_metric["missed_publication_opportunities"] == 0
    assert holiday_metric["expected_next_update"].startswith("2026-07-06")

    china_pack = _pbc_calendar_test_pack()
    before_working_weekend = datetime(2026, 2, 13, tzinfo=UTC)
    china_row = _row(
        "CN-CNY",
        "CN.PBC.OMO_7D",
        SemanticRole.POLICY_TARGET,
        before_working_weekend,
        150,
        source="pbc_operations",
        currency="CNY",
        area="CN",
        jurisdictions=("CN",),
    )
    china_metric = next(
        item
        for item in build_global_money_market_atlas(
            (china_pack,),
            {china_pack.market_id: (china_row,)},
            as_of=datetime(2026, 2, 14, 12, tzinfo=UTC),
        )["markets"][0]["metrics"]
        if item["id"] == "CN.PBC.OMO_7D"
    )
    assert china_metric["missed_publication_opportunities"] == 1
    assert china_metric["status"] == "FRESH"


def test_invalid_calendar_fails_loud_unknown_and_stored_staleness_is_lower_bound():
    china_pack = _pbc_calendar_test_pack()
    event_time = datetime(2026, 12, 30, tzinfo=UTC)
    row = _row(
        "CN-CNY",
        "CN.PBC.OMO_7D",
        SemanticRole.POLICY_TARGET,
        event_time,
        150,
        source="pbc_operations",
        currency="CNY",
        area="CN",
        jurisdictions=("CN",),
    )
    invalid_metric = next(
        item
        for item in build_global_money_market_atlas(
            (china_pack,),
            {china_pack.market_id: (row,)},
            as_of=datetime(2027, 1, 2, tzinfo=UTC),
        )["markets"][0]["metrics"]
        if item["id"] == "CN.PBC.OMO_7D"
    )
    assert invalid_metric["status"] == "UNKNOWN"
    assert invalid_metric["missed_publication_opportunities"] is None
    assert "calendar unavailable" in invalid_metric["freshness_basis"]

    us_pack = default_registry().get("US-USD")
    cutoff = datetime(2026, 8, 21, 12, tzinfo=UTC)
    stored_stale = replace(
        _row(
            "US-USD",
            "US.NYFED.EFFR",
            SemanticRole.UNSECURED_OVERNIGHT,
            cutoff - timedelta(days=1),
            490,
            source="fred_daily",
            currency="USD",
            area="US",
            jurisdictions=("US",),
        ),
        staleness=StalenessState.STALE,
    )
    stored_metric = build_global_money_market_atlas(
        (us_pack,), {us_pack.market_id: (stored_stale,)}, as_of=cutoff
    )["markets"][0]["benchmark"]
    assert stored_metric["status"] == "STALE"


def test_latest_collector_run_is_input_order_independent():
    pack = default_registry().get("EA-EUR")
    cutoff = datetime(2026, 8, 21, 12, tzinfo=UTC)
    row = _row(
        "EA-EUR",
        "EA.ECB.ESTR",
        SemanticRole.UNSECURED_OVERNIGHT,
        cutoff - timedelta(days=1),
        190,
        source="ecb_benchmark",
        currency="EUR",
        area="EA",
        jurisdictions=("DE",),
    )
    older = {
        "market_id": "EA-EUR",
        "adapter_id": "ecb_benchmark",
        "status": "FAILED",
        "finished_at": (cutoff - timedelta(hours=3)).isoformat(),
        "next_due": (cutoff - timedelta(hours=2)).isoformat(),
        "fault": "OLDER_RUN_CANARY",
    }
    newer = {
        "market_id": "EA-EUR",
        "adapter_id": "ecb_benchmark",
        "status": "SUCCESS",
        "finished_at": (cutoff - timedelta(hours=1)).isoformat(),
        "next_due": (cutoff + timedelta(days=1)).isoformat(),
        "fault": None,
    }

    forward = build_global_money_market_atlas(
        (pack,),
        {pack.market_id: (row,)},
        collector_runs=(older, newer),
        as_of=cutoff,
    )
    reverse = build_global_money_market_atlas(
        (pack,),
        {pack.market_id: (row,)},
        collector_runs=(newer, older),
        as_of=cutoff,
    )

    assert forward == reverse
    adapter = next(
        item
        for item in forward["markets"][0]["adapters"]
        if item["adapter_id"] == "ecb_benchmark"
    )
    assert adapter["last_run_status"] == "SUCCESS"
    assert "OLDER_RUN_CANARY" not in json.dumps(forward, sort_keys=True)


def test_flat_history_is_midrank_fifty_z_zero_and_future_invariant():
    pack = default_registry().get("EA-EUR")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        _row(
            "EA-EUR",
            "EA.ECB.ESTR",
            SemanticRole.UNSECURED_OVERNIGHT,
            start + timedelta(days=index),
            200,
            source="ecb_benchmark",
            currency="EUR",
            area="EA",
            jurisdictions=("DE",),
        )
        for index in range(30)
    ]
    cutoff = rows[-1].knowledge_time + timedelta(hours=1)
    future = _row(
        "EA-EUR",
        "EA.ECB.ESTR",
        SemanticRole.UNSECURED_OVERNIGHT,
        cutoff + timedelta(days=1),
        999,
        source="ecb_benchmark",
        currency="EUR",
        area="EA",
        jurisdictions=("DE",),
    )

    baseline = build_global_money_market_atlas(
        (pack,), {pack.market_id: rows}, as_of=cutoff
    )
    with_future = build_global_money_market_atlas(
        (pack,), {pack.market_id: (*rows, future)}, as_of=cutoff
    )
    metric = baseline["markets"][0]["benchmark"]

    assert baseline == with_future
    assert metric["robust_z_1y"] == 0.0
    assert metric["percentile_3y"] == 50.0


def test_statistics_use_elapsed_calendar_windows_not_nominal_row_counts():
    pack = default_registry().get("EA-EUR")
    old_start = datetime(2024, 1, 1, tzinfo=UTC)
    recent_start = datetime(2026, 1, 1, tzinfo=UTC)
    old_rows = [
        _row(
            "EA-EUR",
            "EA.ECB.ESTR",
            SemanticRole.UNSECURED_OVERNIGHT,
            old_start + timedelta(weeks=index),
            150 + index,
            source="ecb_benchmark",
            currency="EUR",
            area="EA",
            jurisdictions=("DE",),
        )
        for index in range(30)
    ]
    recent_rows = [
        _row(
            "EA-EUR",
            "EA.ECB.ESTR",
            SemanticRole.UNSECURED_OVERNIGHT,
            recent_start + timedelta(days=index * 8),
            200 + index,
            source="ecb_benchmark",
            currency="EUR",
            area="EA",
            jurisdictions=("DE",),
        )
        for index in range(25)
    ]
    cutoff = recent_rows[-1].knowledge_time + timedelta(hours=1)

    out = build_global_money_market_atlas(
        (pack,), {pack.market_id: (*old_rows, *recent_rows)}, as_of=cutoff
    )
    metric = out["markets"][0]["benchmark"]

    assert metric["statistics_window"]["one_year"]["observations"] == 25
    assert metric["statistics_window"]["three_year"]["observations"] == 55
    assert metric["n_observations"] == 55
    assert "elapsed calendar-year window" in out["quant_read"]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("access", "NOT_AN_ACCESS_ENUM"),
        ("confidence", "CERTAIN"),
        ("status", "LIVE"),
        ("currency", "CADX"),
        ("currency", "ZZZ"),
        ("market_id", "canada-CAD"),
        ("verified_on", "2026-02-30"),
    ),
)
def test_expansion_ledger_validation_rejects_invalid_enums_currency_and_date(
    field, value
):
    row = dict(EXPANSION_LEDGER[0])
    row[field] = value

    with pytest.raises(ValueError):
        _validate_expansion_ledger((row,))


def test_expansion_ledger_validation_rejects_duplicate_ids_and_non_https_urls():
    row = dict(EXPANSION_LEDGER[0])
    with pytest.raises(ValueError, match="duplicate expansion market_id"):
        _validate_expansion_ledger((row, dict(row)))

    row["source_url"] = "http://example.invalid/rate"
    row["official_reference_url"] = row["source_url"]
    with pytest.raises(ValueError, match="HTTPS URL"):
        _validate_expansion_ledger((row,))
