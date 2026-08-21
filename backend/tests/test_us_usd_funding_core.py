"""Opinionated US funding-core research profile and worker export contract."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from seiche import market_runtime
from seiche.collectors import CollectorRun, CollectorRunStatus
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
from seiche.markets.us_usd.funding_core import (
    EXPORT_FILENAME,
    FUNDING_CORE_MODEL_ID,
    FUNDING_CORE_PROFILE_ID,
    FundingCoreProfileError,
    build_funding_core_input_pack,
    build_funding_core_input_pack_from_repository,
    export_funding_core_input_pack,
)

START = datetime(2023, 1, 3, tzinfo=UTC)
AS_OF = datetime(2026, 8, 9, tzinfo=UTC)


def _observation(
    instrument_id: str,
    role: SemanticRole,
    unit: CanonicalUnit,
    value: str | int | Decimal,
    event_offset: int,
    *,
    revision_id: str | None = None,
    knowledge_delay: timedelta = timedelta(hours=2),
    publication_delay: timedelta = timedelta(hours=1),
) -> Observation:
    event = START + timedelta(days=event_offset)
    event_day = event.date().isoformat()
    field = {
        "US.NYFED.SOFR_MEDIAN": "percentRate",
        "US.NYFED.SOFR_P99": "percentPercentile99",
        "US.NYFED.SOFR_VOLUME": "volumeInBillions",
    }.get(instrument_id, "percentRate")
    revision = revision_id or f"nyfed:{field}:{event_day}:unrevised"
    is_rate = unit is CanonicalUnit.BASIS_POINTS
    return Observation(
        market_id="US-USD",
        monetary_area_id="US",
        jurisdiction_codes=("US",),
        currency="USD",
        instrument_id=instrument_id,
        semantic_role=role,
        value=value,
        canonical_unit=unit,
        rate_compounding=RateCompounding.SIMPLE if is_rate else None,
        day_count=DayCountConvention.ACT_360 if is_rate else None,
        event_time=event,
        source_publication_time=event + publication_delay,
        knowledge_time=event + knowledge_delay,
        revision_id=revision,
        source="nyfed_rates",
        evidence_hash=evidence_sha256(
            f"{instrument_id}:{event.isoformat()}:{revision}:{value}"
        ),
        connector_classification=ConnectorClassification.OFFICIAL_OPEN,
        redistribution_status=RedistributionStatus.ALLOWED,
        quality=QualityState.VERIFIED,
        staleness=StalenessState.FRESH,
    )


def _rows(count: int) -> list[Observation]:
    rows = []
    for offset in range(count):
        rows.extend(
            (
                _observation(
                    "US.NYFED.SOFR_MEDIAN",
                    SemanticRole.RATE_MEDIAN,
                    CanonicalUnit.BASIS_POINTS,
                    500 + offset % 7,
                    offset,
                ),
                _observation(
                    "US.NYFED.SOFR_P99",
                    SemanticRole.RATE_P99,
                    CanonicalUnit.BASIS_POINTS,
                    510 + offset % 11,
                    offset,
                ),
                _observation(
                    "US.NYFED.SOFR_VOLUME",
                    SemanticRole.REPO_VOLUME,
                    CanonicalUnit.LOCAL_CURRENCY_MILLIONS,
                    1_900_000 + offset,
                    offset,
                ),
            )
        )
    return rows


class _Repository:
    def __init__(self, rows: list[Observation]) -> None:
        self.rows = rows
        self.calls = []

    def load_observation_revisions_as_of(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return list(self.rows)


def test_profile_uses_exact_lab_identity_and_intersects_without_fill() -> None:
    rows = _rows(506)
    partial_p99_event = START + timedelta(days=504)
    partial_volume_event = START + timedelta(days=505)
    rows = [
        item
        for item in rows
        if not (
            item.instrument_id == "US.NYFED.SOFR_P99"
            and item.event_time == partial_p99_event
        )
        and not (
            item.instrument_id == "US.NYFED.SOFR_VOLUME"
            and item.event_time == partial_volume_event
        )
    ]
    first_median = next(
        item
        for item in rows
        if item.instrument_id == "US.NYFED.SOFR_MEDIAN" and item.event_time == START
    )
    legacy_p25 = replace(
        first_median,
        value=Decimal("492"),
        source_publication_time=first_median.event_time + timedelta(minutes=30),
        knowledge_time=first_median.event_time + timedelta(hours=1),
        revision_id="sha256:legacy-p25-lineage",
        evidence_hash=evidence_sha256("legacy P25 row retained as ordinal one"),
        quality=QualityState.REVISED,
    )

    pack = build_funding_core_input_pack([*reversed(rows), legacy_p25], as_of=AS_OF)

    assert FUNDING_CORE_PROFILE_ID == "us-usd-funding-core-v1"
    assert FUNDING_CORE_MODEL_ID == "us-usd-funding-core-var1-v1"
    assert [item["state_name"] for item in pack["state_definitions"]] == [
        "sofr_median_bp",
        "sofr_p99_bp",
        "sofr_volume_usd_m",
    ]
    assert [item["instrument_id"] for item in pack["state_definitions"]] == [
        "US.NYFED.SOFR_MEDIAN",
        "US.NYFED.SOFR_P99",
        "US.NYFED.SOFR_VOLUME",
    ]
    assert len(pack["event_grid"]) == 504
    assert partial_p99_event.isoformat() not in pack["event_grid"]
    assert partial_volume_event.isoformat() not in pack["event_grid"]
    first_revisions = [
        item
        for item in pack["observations"]
        if item["state_name"] == "sofr_median_bp"
        and item["event_time"] == START.isoformat()
    ]
    assert [item["revision_ordinal"] for item in first_revisions] == [1, 2]
    assert [item["value"] for item in first_revisions] == ["492", "500"]
    assert pack["coverage"]["revision_row_count"] == 504 * 3 + 1
    assert pack["policy"]["imputation"] == "forbidden"


def test_profile_rejects_fewer_than_504_complete_dates() -> None:
    with pytest.raises(
        FundingCoreProfileError, match="503 complete dates; 504 required"
    ):
        build_funding_core_input_pack(_rows(503), as_of=AS_OF)


def test_profile_rejects_latest_median_without_percent_rate_lineage() -> None:
    rows = _rows(504)
    median = next(item for item in rows if item.instrument_id == "US.NYFED.SOFR_MEDIAN")
    rows[rows.index(median)] = replace(
        median,
        revision_id="nyfed:percentPercentile25:2023-01-03:unrevised",
        evidence_hash=evidence_sha256("wrong latest median lineage"),
    )

    with pytest.raises(FundingCoreProfileError, match="exact source-field lineage"):
        build_funding_core_input_pack(rows, as_of=AS_OF)


@pytest.mark.parametrize(
    ("instrument_id", "wrong_revision", "expected_field"),
    [
        (
            "US.NYFED.SOFR_P99",
            "nyfed:percentPercentile75:2023-01-03:unrevised",
            "percentPercentile99",
        ),
        (
            "US.NYFED.SOFR_VOLUME",
            "nyfed:percentPercentile99:2023-01-03:unrevised",
            "volumeInBillions",
        ),
    ],
)
def test_profile_rejects_wrong_source_field_lineage_for_every_state(
    instrument_id: str, wrong_revision: str, expected_field: str
) -> None:
    rows = _rows(504)
    selected = next(item for item in rows if item.instrument_id == instrument_id)
    rows[rows.index(selected)] = replace(
        selected,
        revision_id=wrong_revision,
        evidence_hash=evidence_sha256(f"wrong {instrument_id} lineage"),
    )

    with pytest.raises(FundingCoreProfileError, match=expected_field):
        build_funding_core_input_pack(rows, as_of=AS_OF)


def test_profile_accepts_correct_lineage_for_utc_midnight_date_labels() -> None:
    rows = _rows(504)

    pack = build_funding_core_input_pack(rows, as_of=AS_OF)

    assert START.isoformat() in pack["event_grid"]


def test_profile_rejects_prior_day_revision_for_utc_midnight_date_label() -> None:
    rows = _rows(504)
    median = next(item for item in rows if item.instrument_id == "US.NYFED.SOFR_MEDIAN")
    rows[rows.index(median)] = replace(
        median,
        revision_id="nyfed:percentRate:2023-01-02:unrevised",
        evidence_hash=evidence_sha256("prior-day median lineage"),
    )

    with pytest.raises(FundingCoreProfileError, match="exact source-field lineage"):
        build_funding_core_input_pack(rows, as_of=AS_OF)


def test_profile_rejects_non_midnight_event_key_for_date_lineage() -> None:
    rows = _rows(504)
    shifted_event = START + timedelta(hours=1)
    rows = [
        replace(item, event_time=shifted_event) if item.event_time == START else item
        for item in rows
    ]

    with pytest.raises(FundingCoreProfileError, match="exact source-field lineage"):
        build_funding_core_input_pack(rows, as_of=AS_OF)


def test_future_percent_rate_revision_cannot_satisfy_asof_lineage_gate() -> None:
    rows = _rows(504)
    corrected = next(
        item for item in rows if item.instrument_id == "US.NYFED.SOFR_MEDIAN"
    )
    legacy_p25 = replace(
        corrected,
        revision_id="sha256:legacy-p25-lineage",
        evidence_hash=evidence_sha256("latest P25 row knowable at cutoff"),
    )
    rows[rows.index(corrected)] = legacy_p25
    future_correction = replace(
        corrected,
        source_publication_time=AS_OF + timedelta(days=1),
        knowledge_time=AS_OF + timedelta(days=1, hours=1),
        evidence_hash=evidence_sha256("percentRate correction captured after cutoff"),
    )

    with pytest.raises(FundingCoreProfileError, match="exact source-field lineage"):
        build_funding_core_input_pack([*rows, future_correction], as_of=AS_OF)


def test_repository_profile_filters_future_same_role_instrument_before_builder() -> (
    None
):
    rows = _rows(504)
    future_same_role = _observation(
        "US.NYFED.SOFR_MEDIAN_V2",
        SemanticRole.RATE_MEDIAN,
        CanonicalUnit.BASIS_POINTS,
        999,
        0,
        revision_id="future-same-role",
    )
    repository = _Repository([*rows, future_same_role])

    pack = build_funding_core_input_pack_from_repository(repository, as_of=AS_OF)

    assert all(
        item["instrument_id"] != "US.NYFED.SOFR_MEDIAN_V2"
        for item in pack["observations"]
    )
    assert repository.calls[0][0][0] == "US-USD"
    assert set(repository.calls[0][1]["roles"]) == {
        SemanticRole.RATE_MEDIAN,
        SemanticRole.RATE_P99,
        SemanticRole.REPO_VOLUME,
    }


def test_profile_rejects_wrong_unit_or_rate_convention_on_pinned_instrument() -> None:
    rows = _rows(504)
    p99 = next(item for item in rows if item.instrument_id == "US.NYFED.SOFR_P99")
    corrupt = replace(p99)
    object.__setattr__(corrupt, "day_count", DayCountConvention.ACT_365)
    rows[rows.index(p99)] = corrupt

    with pytest.raises(FundingCoreProfileError, match="pinned semantics"):
        build_funding_core_input_pack(rows, as_of=AS_OF)


def test_atomic_export_is_deterministic_and_leaves_no_temporary_file(tmp_path) -> None:
    rows = _rows(504)
    first = export_funding_core_input_pack(
        _Repository(rows), as_of=AS_OF, directory=tmp_path
    )
    first_bytes = first.read_bytes()
    second = export_funding_core_input_pack(
        _Repository(list(reversed(rows))), as_of=AS_OF, directory=tmp_path
    )

    assert first == second == tmp_path / EXPORT_FILENAME
    assert second.read_bytes() == first_bytes
    assert second.read_bytes().endswith(b"\n")
    assert [item.name for item in tmp_path.iterdir()] == [EXPORT_FILENAME]


def _run(market_id: str, adapter_id: str) -> CollectorRun:
    return CollectorRun(
        market_id,
        adapter_id,
        CollectorRunStatus.SUCCESS,
        AS_OF.isoformat(),
        AS_OF.isoformat(),
        1,
        1,
        (AS_OF + timedelta(days=1)).isoformat(),
    )


@pytest.mark.asyncio
async def test_worker_exports_once_at_cycle_boundary_not_per_us_adapter(
    monkeypatch,
) -> None:
    runs = [
        _run("US-USD", "nyfed_rates"),
        _run("US-USD", "fred_daily"),
        _run("EA-EUR", "ecb_benchmark"),
    ]

    class _Supervisor:
        async def run_due(self, *, now):
            return runs

    class _StopWorker(Exception):
        pass

    export_calls = []
    monkeypatch.setattr(
        market_runtime, "build_supervisor", lambda **_kwargs: _Supervisor()
    )
    monkeypatch.setattr(
        market_runtime, "_materialize_after_runs", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        market_runtime,
        "_export_usd_funding_core_after_runs",
        lambda cycle_runs, **_kwargs: (
            export_calls.append(tuple(cycle_runs)) or {"status": "SUCCESS"}
        ),
    )

    async def stop_after_cycle(_seconds):
        raise _StopWorker

    monkeypatch.setattr(market_runtime.asyncio, "sleep", stop_after_cycle)

    with pytest.raises(_StopWorker):
        await market_runtime.run_worker(poll_seconds=5, repository=_Repository([]))

    assert export_calls == [tuple(runs)]


def test_export_failure_is_surfaced_but_does_not_raise(
    monkeypatch, caplog, tmp_path
) -> None:
    monkeypatch.setenv("SEICHE_USD_FUNDING_CORE_EXPORT_DIR", str(tmp_path))
    monkeypatch.setattr(
        market_runtime,
        "export_funding_core_input_pack",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FundingCoreProfileError("latest median still has P25 lineage")
        ),
    )

    result = market_runtime._export_usd_funding_core_after_runs(
        [_run("US-USD", "nyfed_rates"), _run("EA-EUR", "ecb_benchmark")],
        repository=_Repository([]),
        cutoff=AS_OF,
    )

    assert result["status"] == "FAILED"
    assert "latest median still has P25 lineage" in result["fault"]
    assert "funding-core export failed" in caplog.text


def test_sofrai_backfill_generation_ignores_legacy_and_v3_nyfed_markers(
    monkeypatch, tmp_path
) -> None:
    state = tmp_path / "backfill"
    monkeypatch.setenv("SEICHE_BACKFILL_STATE_DIR", str(state))
    monkeypatch.setenv("SEICHE_RAW_CAPTURE_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("SEICHE_NORMALIZED_DIR", str(tmp_path / "normalized"))
    state.mkdir()
    (state / "US-USD--nyfed_rates.done").touch()
    v3_marker = state / "US-USD--nyfed_rates--funding-field-lineage-v3.done"
    v3_marker.touch()
    (state / "US-USD--fred_daily.done").touch()

    class _Adapter:
        def __init__(self, adapter_id):
            self.market_id = "US-USD"
            self.adapter_id = adapter_id

    class _BackfillRepository:
        def save_observations(self, _observations):
            return 0

        def save_collector_run(self, _run):
            return "run"

    monkeypatch.setattr(
        market_runtime,
        "build_official_adapters",
        lambda **_kwargs: (_Adapter("nyfed_rates"), _Adapter("fred_daily")),
    )

    before_v4 = market_runtime.build_supervisor(
        repository=_BackfillRepository(),
        backfill=True,
    )
    assert ("US-USD", "nyfed_rates") in before_v4._tasks
    assert ("US-USD", "fred_daily") not in before_v4._tasks

    generation_marker = (
        state / "US-USD--nyfed_rates--nyfed-sofrai-averages-index-v4.done"
    )
    assert market_runtime._backfill_marker("US-USD", "nyfed_rates") == generation_marker
    market_runtime._mark_backfill_complete("US-USD", "nyfed_rates")
    assert generation_marker.exists()
    assert v3_marker.exists()
    after_v4 = market_runtime.build_supervisor(
        repository=_BackfillRepository(),
        backfill=True,
    )
    assert ("US-USD", "nyfed_rates") not in after_v4._tasks
    assert ("US-USD", "fred_daily") not in after_v4._tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("export_status", ["FAILED", "SUCCESS"])
async def test_percent_rate_marker_waits_for_ready_funding_export(
    monkeypatch, tmp_path, export_status
) -> None:
    state = tmp_path / "backfill"
    monkeypatch.setenv("SEICHE_BACKFILL_STATE_DIR", str(state))
    run = _run("US-USD", "nyfed_rates")

    class _BackfillRepository(_Repository):
        def save_collector_run(self, _run_payload):
            return "run-id"

    class _Supervisor:
        def __init__(self, writer):
            self.writer = writer

        async def run_due(self, *, now, force):
            self.writer(run.to_dict())
            return [run]

    monkeypatch.setattr(
        market_runtime,
        "build_supervisor",
        lambda **kwargs: _Supervisor(kwargs["run_writer"]),
    )
    monkeypatch.setattr(
        market_runtime, "_materialize_after_runs", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        market_runtime,
        "_export_usd_funding_core_after_runs",
        lambda *_args, **_kwargs: {"status": export_status},
    )

    await market_runtime.collect_once(
        backfill=True,
        repository=_BackfillRepository([]),
    )

    marker = state / "US-USD--nyfed_rates--nyfed-sofrai-averages-index-v4.done"
    assert marker.exists() is (export_status == "SUCCESS")
