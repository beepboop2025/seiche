from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

import seiche.domain.investigative_outcome as outcome_module
from seiche.domain.investigative_outcome import (
    DatasetEligibility,
    OUTCOME_GENESIS_HASH,
    OutcomeExportPurpose,
    OutcomeIntegrityError,
    OutcomeLedgerEntry,
    OutcomeLedgerError,
    OutcomeRecordKind,
    ResolutionDisposition,
    build_investigative_outcome_export,
    verify_outcome_chain,
)

BASE = datetime(2026, 1, 5, 12, tzinfo=UTC)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _ts(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _head(rows: list[OutcomeLedgerEntry]) -> str:
    return rows[-1].record_hash if rows else OUTCOME_GENESIS_HASH


def _rehash_export(document: dict[str, object]) -> None:
    content = {key: value for key, value in document.items() if key != "export_hash"}
    document["export_hash"] = outcome_module._sha256(
        content, max_nodes=outcome_module.MAX_EXPORT_JSON_NODES
    )


def _append(
    rows: list[OutcomeLedgerEntry],
    *,
    kind: OutcomeRecordKind,
    at: datetime,
    payload: dict[str, object],
    knowledge_time: datetime | None = None,
    market_id: str = "US-USD",
    entity_group_id: str = "funding-market",
) -> OutcomeLedgerEntry:
    row = OutcomeLedgerEntry(
        sequence=len(rows),
        previous_record_hash=rows[-1].record_hash if rows else OUTCOME_GENESIS_HASH,
        kind=kind,
        recorded_at=at,
        knowledge_time=knowledge_time or at,
        market_id=market_id,
        entity_group_id=entity_group_id,
        payload=payload,
    )
    rows.append(row)
    return row


def _forecast(
    rows: list[OutcomeLedgerEntry],
    *,
    at: datetime = BASE,
    model_id: str = "swell-v1",
    forecast_run_id: str = "daily-close-20260105",
    probability_ppm: int = 125_000,
    observation_dates: tuple[str, ...] = ("2026-01-06", "2026-01-07"),
    window_closed_at: datetime | None = None,
    evidence_ids: tuple[str, ...] | None = None,
    evidence_cut_digest: str | None = None,
    calendar_id: str = "US-FEDWIRE",
    calendar_version: str = "2026.1",
    calendar_digest: str | None = None,
    calendar_timezone: str = "America/New_York",
) -> OutcomeLedgerEntry:
    return _append(
        rows,
        kind=OutcomeRecordKind.FORECAST,
        at=at,
        payload={
            "model_id": model_id,
            "forecast_run_id": forecast_run_id,
            "target_rule_id": "sofr-iorb-pop-v1",
            "prediction_time": _ts(at),
            "probability_ppm": probability_ppm,
            "evidence_ids": list(evidence_ids or (_digest("forecast-evidence"),)),
            "evidence_cut_digest": evidence_cut_digest or _digest("forecast-cut"),
            "window_opened_at": _ts(at),
            "window_closed_at": _ts(window_closed_at or BASE + timedelta(days=2)),
            "observation_dates": list(observation_dates),
            "calendar_id": calendar_id,
            "calendar_version": calendar_version,
            "calendar_digest": calendar_digest or _digest("us-fedwire-2026.1"),
            "calendar_timezone": calendar_timezone,
        },
    )


def _decision(
    rows: list[OutcomeLedgerEntry],
    *,
    forecast: OutcomeLedgerEntry,
    eligibility: DatasetEligibility,
    at: datetime | None = None,
    supersedes: OutcomeLedgerEntry | None = None,
    rights_label: str = "rights-review",
) -> OutcomeLedgerEntry:
    return _append(
        rows,
        kind=OutcomeRecordKind.ELIGIBILITY_DECISION,
        at=at or forecast.recorded_at,
        market_id=forecast.market_id,
        entity_group_id=forecast.entity_group_id,
        payload={
            "forecast_record_hash": forecast.record_hash,
            "dataset_eligibility": eligibility.value,
            "rights_decision_hash": _digest(rights_label),
            "reviewer_id": "data-rights-reviewer",
            "policy_id": "outcome-rights-v1",
            "supersedes_decision_record_hash": (
                supersedes.record_hash if supersedes is not None else None
            ),
        },
    )


def _observation(
    rows: list[OutcomeLedgerEntry],
    *,
    at: datetime,
    event_time: datetime,
    occurred: bool | None,
    suffix: str,
    observation_date: str | None = None,
) -> OutcomeLedgerEntry:
    return _append(
        rows,
        kind=OutcomeRecordKind.OBSERVATION,
        at=at,
        payload={
            "target_rule_id": "sofr-iorb-pop-v1",
            "observation_date": observation_date or event_time.date().isoformat(),
            "event_time": _ts(event_time),
            "event_occurred": occurred,
            "source_record_hash": _digest(f"source-{suffix}"),
            "evidence_ids": [_digest(f"observation-{suffix}")],
        },
    )


def _resolution(
    rows: list[OutcomeLedgerEntry],
    *,
    at: datetime,
    forecast: OutcomeLedgerEntry,
    observations: list[OutcomeLedgerEntry],
    disposition: ResolutionDisposition,
    outcome: bool | None,
    censor_reason: str | None = None,
) -> OutcomeLedgerEntry:
    return _append(
        rows,
        kind=OutcomeRecordKind.RESOLUTION,
        at=at,
        market_id=forecast.market_id,
        entity_group_id=forecast.entity_group_id,
        payload={
            "forecast_record_hash": forecast.record_hash,
            "observation_record_hashes": [row.record_hash for row in observations],
            "disposition": disposition.value,
            "outcome": outcome,
            "censor_reason": censor_reason,
        },
    )


def _resolved_chain(
    *,
    eligibility: DatasetEligibility = DatasetEligibility.TRAINING_ELIGIBLE,
    occurred: tuple[bool | None, bool | None] = (False, True),
) -> tuple[list[OutcomeLedgerEntry], OutcomeLedgerEntry, OutcomeLedgerEntry]:
    rows: list[OutcomeLedgerEntry] = []
    forecast = _forecast(rows)
    decision = _decision(rows, forecast=forecast, eligibility=eligibility)
    first = _observation(
        rows,
        at=BASE + timedelta(days=1, hours=1),
        event_time=BASE + timedelta(days=1),
        occurred=occurred[0],
        suffix="d1",
    )
    second = _observation(
        rows,
        at=BASE + timedelta(days=2, hours=1),
        event_time=BASE + timedelta(days=2),
        occurred=occurred[1],
        suffix="d2",
    )
    if any(value is True for value in occurred):
        disposition = ResolutionDisposition.RESOLVED
        outcome: bool | None = True
        reason = None
    elif all(value is False for value in occurred):
        disposition = ResolutionDisposition.RESOLVED
        outcome = False
        reason = None
    else:
        disposition = ResolutionDisposition.CENSORED
        outcome = None
        reason = "incomplete-source-coverage"
    _resolution(
        rows,
        at=BASE + timedelta(days=2, hours=2),
        forecast=forecast,
        observations=[first, second],
        disposition=disposition,
        outcome=outcome,
        censor_reason=reason,
    )
    return rows, forecast, decision


def test_record_is_canonical_content_addressed_and_deeply_immutable() -> None:
    rows: list[OutcomeLedgerEntry] = []
    first = _forecast(rows)
    same = OutcomeLedgerEntry.from_dict(first.to_dict())

    assert same == first
    assert OutcomeLedgerEntry.from_json(first.to_json()) == first
    assert same.to_json() == first.to_json()
    assert json.loads(first.to_json())["record_hash"] == first.record_hash
    with pytest.raises(FrozenInstanceError):
        first.market_id = "CN-CNY"  # type: ignore[misc]
    with pytest.raises(TypeError):
        first.payload["model_id"] = "forged"  # type: ignore[index]
    with pytest.raises(AttributeError):
        first.payload["evidence_ids"].append(_digest("forged"))  # type: ignore[union-attr]


def test_record_json_rejects_duplicate_noncanonical_and_pathological_json() -> None:
    rows: list[OutcomeLedgerEntry] = []
    first = _forecast(rows)
    duplicate = first.to_json().replace(
        '"schema":',
        '"schema":"seiche.investigative-outcome-ledger.v1","schema":',
        1,
    )
    with pytest.raises(OutcomeIntegrityError, match="duplicate JSON key"):
        OutcomeLedgerEntry.from_json(duplicate)
    with pytest.raises(OutcomeIntegrityError, match="not canonical"):
        OutcomeLedgerEntry.from_json(first.to_json() + "\n")
    with pytest.raises(OutcomeIntegrityError, match="bounded JSON"):
        OutcomeLedgerEntry.from_json(b"[" * 10_000 + b"0" + b"]" * 10_000)
    with pytest.raises(OutcomeIntegrityError, match="20 digits"):
        OutcomeLedgerEntry.from_json('{"sequence":' + "9" * 5_000 + "}")


def test_chain_verification_and_training_export() -> None:
    rows, forecast, _ = _resolved_chain()
    assert verify_outcome_chain(rows, expected_head_hash=_head(rows)) == tuple(rows)

    export = build_investigative_outcome_export(
        rows,
        as_of=BASE + timedelta(days=3),
        purpose=OutcomeExportPurpose.TRAINING,
        trusted_head_hash=_head(rows),
    )

    assert len(export.rows) == 1
    assert export.rows[0]["forecast_record_hash"] == forecast.record_hash
    assert export.rows[0]["status"] == "resolved"
    assert export.rows[0]["label_eligible"] is True
    assert export.rows[0]["outcome"] is True
    assert export.rows[0]["probability_ppm"] == 125_000
    assert "event_occurred" not in export.to_json()
    assert export.policy["null_outcome_is_negative"] is False
    assert json.loads(export.to_json())["export_hash"] == export.export_hash


def test_standalone_export_rejects_self_hashed_row_and_policy_forgery() -> None:
    rows, _, _ = _resolved_chain()
    export = build_investigative_outcome_export(
        rows,
        as_of=BASE + timedelta(days=3),
        purpose="training",
        trusted_head_hash=_head(rows),
    )

    assert (
        outcome_module.InvestigativeOutcomeExport.from_json(export.to_json()) == export
    )
    with pytest.raises(OutcomeIntegrityError, match="not canonical"):
        outcome_module.InvestigativeOutcomeExport.from_json(export.to_json() + "\n")

    forged_row = export.to_dict()
    row = forged_row["rows"][0]  # type: ignore[index]
    assert isinstance(row, dict)
    row["outcome"] = None
    _rehash_export(forged_row)
    with pytest.raises(OutcomeLedgerError, match="resolved status fields"):
        outcome_module.InvestigativeOutcomeExport.from_dict(forged_row)

    unknown_field = export.to_dict()
    row = unknown_field["rows"][0]  # type: ignore[index]
    assert isinstance(row, dict)
    row["attacker_controlled"] = True
    _rehash_export(unknown_field)
    with pytest.raises(OutcomeLedgerError, match="fields do not match v1"):
        outcome_module.InvestigativeOutcomeExport.from_dict(unknown_field)

    forged_policy = export.to_dict()
    policy = forged_policy["policy"]
    assert isinstance(policy, dict)
    policy["trusted_full_chain_head_required"] = False
    _rehash_export(forged_policy)
    with pytest.raises(OutcomeLedgerError, match="does not match v1"):
        outcome_module.InvestigativeOutcomeExport.from_dict(forged_policy)


def test_standalone_export_enforces_canonical_byte_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, _, _ = _resolved_chain()
    export = build_investigative_outcome_export(
        rows,
        as_of=BASE + timedelta(days=3),
        purpose="training",
        trusted_head_hash=_head(rows),
    )
    raw = export.to_json()
    monkeypatch.setattr(outcome_module, "MAX_OUTCOME_EXPORT_BYTES", len(raw) - 1)

    with pytest.raises(OutcomeIntegrityError, match="exceeds"):
        outcome_module.InvestigativeOutcomeExport.from_json(raw)
    with pytest.raises(OutcomeLedgerError, match="canonical outcome export exceeds"):
        outcome_module.InvestigativeOutcomeExport.from_dict(export.to_dict())


def test_pre_close_pending_forecast_is_an_explicit_denominator_row() -> None:
    rows: list[OutcomeLedgerEntry] = []
    forecast = _forecast(rows)
    _decision(
        rows,
        forecast=forecast,
        eligibility=DatasetEligibility.TRAINING_ELIGIBLE,
    )
    _observation(
        rows,
        at=BASE + timedelta(days=1, hours=1),
        event_time=BASE + timedelta(days=1),
        occurred=False,
        suffix="d1",
    )

    export = build_investigative_outcome_export(
        rows,
        as_of=BASE + timedelta(days=1, hours=2),
        purpose="training",
        trusted_head_hash=_head(rows),
    )

    assert len(export.rows) == 1
    assert export.rows[0]["status"] == "pending"
    assert export.rows[0]["outcome"] is None
    assert export.rows[0]["label_eligible"] is False
    assert export.rows[0]["resolution_record_hash"] is None


def test_matured_missing_resolution_is_null_unresolved_not_pending_or_false() -> None:
    rows: list[OutcomeLedgerEntry] = []
    forecast = _forecast(rows)
    _decision(
        rows,
        forecast=forecast,
        eligibility=DatasetEligibility.TRAINING_ELIGIBLE,
    )

    export = build_investigative_outcome_export(
        rows,
        as_of=BASE + timedelta(days=10),
        purpose="training",
        trusted_head_hash=_head(rows),
    )

    assert export.rows[0]["status"] == "matured_unresolved"
    assert export.rows[0]["censor_reason"] == "missing_resolution_record"
    assert export.rows[0]["outcome"] is None
    assert export.rows[0]["label_eligible"] is False


def test_resolution_after_cutoff_leaves_matured_unresolved_visible_row() -> None:
    rows, _, _ = _resolved_chain(occurred=(False, False))

    export = build_investigative_outcome_export(
        rows,
        as_of=BASE + timedelta(days=2, hours=1, minutes=30),
        purpose="training",
        trusted_head_hash=_head(rows),
    )

    assert len(export.rows) == 1
    assert export.rows[0]["status"] == "matured_unresolved"
    assert export.rows[0]["censor_reason"] == "missing_resolution_record"
    assert export.source_head_hash == rows[-2].record_hash


def test_training_evaluation_and_prohibited_exports_are_isolated() -> None:
    training_rows, _, _ = _resolved_chain()
    evaluation_rows, _, _ = _resolved_chain(
        eligibility=DatasetEligibility.EVALUATION_ONLY
    )
    prohibited_rows, _, _ = _resolved_chain(eligibility=DatasetEligibility.PROHIBITED)

    assert (
        len(
            build_investigative_outcome_export(
                training_rows,
                as_of=BASE + timedelta(days=3),
                purpose="training",
                trusted_head_hash=_head(training_rows),
            ).rows
        )
        == 1
    )
    assert not build_investigative_outcome_export(
        training_rows,
        as_of=BASE + timedelta(days=3),
        purpose="evaluation",
        trusted_head_hash=_head(training_rows),
    ).rows
    assert (
        len(
            build_investigative_outcome_export(
                evaluation_rows,
                as_of=BASE + timedelta(days=3),
                purpose="evaluation",
                trusted_head_hash=_head(evaluation_rows),
            ).rows
        )
        == 1
    )
    assert not build_investigative_outcome_export(
        evaluation_rows,
        as_of=BASE + timedelta(days=3),
        purpose="training",
        trusted_head_hash=_head(evaluation_rows),
    ).rows
    assert not build_investigative_outcome_export(
        prohibited_rows,
        as_of=BASE + timedelta(days=3),
        purpose="training",
        trusted_head_hash=_head(prohibited_rows),
    ).rows


def test_semantic_duplicate_forecasts_are_rejected_before_split_assignment() -> None:
    rows: list[OutcomeLedgerEntry] = []
    first = _forecast(rows)
    _decision(
        rows,
        forecast=first,
        eligibility=DatasetEligibility.TRAINING_ELIGIBLE,
    )
    second = _forecast(
        rows,
        probability_ppm=900_000,
        evidence_ids=(_digest("different-evidence"),),
        evidence_cut_digest=_digest("different-cut"),
        calendar_id="US-NY-FED",
        calendar_version="2026.2",
        calendar_digest=_digest("different-calendar-artifact"),
        calendar_timezone="UTC",
    )
    _decision(
        rows,
        forecast=second,
        eligibility=DatasetEligibility.EVALUATION_ONLY,
    )

    with pytest.raises(OutcomeIntegrityError, match="semantic duplicate forecast"):
        verify_outcome_chain(rows, expected_head_hash=_head(rows))


def test_case_group_cannot_cross_splits_even_for_distinct_models_and_runs() -> None:
    rows: list[OutcomeLedgerEntry] = []
    first = _forecast(rows, model_id="swell-v1", forecast_run_id="swell-run")
    _decision(
        rows,
        forecast=first,
        eligibility=DatasetEligibility.TRAINING_ELIGIBLE,
    )
    second = _forecast(
        rows,
        model_id="bathymetry-v2",
        forecast_run_id="bathymetry-run",
        probability_ppm=900_000,
        evidence_ids=(_digest("bathymetry-evidence"),),
        evidence_cut_digest=_digest("bathymetry-cut"),
    )
    _decision(
        rows,
        forecast=second,
        eligibility=DatasetEligibility.EVALUATION_ONLY,
    )

    with pytest.raises(OutcomeIntegrityError, match="case group cannot cross"):
        verify_outcome_chain(rows, expected_head_hash=_head(rows))


def test_calendar_provenance_cannot_bypass_cross_model_split_isolation() -> None:
    rows: list[OutcomeLedgerEntry] = []
    first = _forecast(rows, model_id="swell-v1", forecast_run_id="swell-run")
    _decision(
        rows,
        forecast=first,
        eligibility=DatasetEligibility.TRAINING_ELIGIBLE,
    )
    second = _forecast(
        rows,
        model_id="bathymetry-v2",
        forecast_run_id="bathymetry-run",
        calendar_id="US-NY-FED",
        calendar_version="emergency-revision",
        calendar_digest=_digest("replacement-calendar-artifact"),
        calendar_timezone="UTC",
    )
    _decision(
        rows,
        forecast=second,
        eligibility=DatasetEligibility.EVALUATION_ONLY,
    )

    with pytest.raises(OutcomeIntegrityError, match="case group cannot cross"):
        verify_outcome_chain(rows, expected_head_hash=_head(rows))


def test_case_group_revocation_does_not_erase_prior_split_exposure() -> None:
    rows: list[OutcomeLedgerEntry] = []
    first = _forecast(rows, model_id="swell-v1", forecast_run_id="swell-run")
    first_decision = _decision(
        rows,
        forecast=first,
        eligibility=DatasetEligibility.TRAINING_ELIGIBLE,
    )
    _decision(
        rows,
        forecast=first,
        eligibility=DatasetEligibility.PROHIBITED,
        supersedes=first_decision,
        rights_label="revoked",
    )
    second = _forecast(
        rows,
        model_id="bathymetry-v2",
        forecast_run_id="bathymetry-run",
    )
    _decision(
        rows,
        forecast=second,
        eligibility=DatasetEligibility.EVALUATION_ONLY,
    )

    with pytest.raises(OutcomeIntegrityError, match="case group cannot cross"):
        verify_outcome_chain(rows, expected_head_hash=_head(rows))


def test_distinct_models_may_share_one_case_group_with_one_split() -> None:
    rows: list[OutcomeLedgerEntry] = []
    first = _forecast(rows, model_id="swell-v1", forecast_run_id="swell-run")
    _decision(
        rows,
        forecast=first,
        eligibility=DatasetEligibility.EVALUATION_ONLY,
    )
    second = _forecast(
        rows,
        model_id="bathymetry-v2",
        forecast_run_id="bathymetry-run",
        probability_ppm=900_000,
        evidence_ids=(_digest("bathymetry-evidence"),),
        evidence_cut_digest=_digest("bathymetry-cut"),
        calendar_id="US-NY-FED",
        calendar_version="2026.2",
        calendar_digest=_digest("bathymetry-calendar"),
        calendar_timezone="UTC",
    )
    _decision(
        rows,
        forecast=second,
        eligibility=DatasetEligibility.EVALUATION_ONLY,
    )

    verify_outcome_chain(rows, expected_head_hash=_head(rows))
    export = build_investigative_outcome_export(
        rows,
        as_of=BASE + timedelta(hours=1),
        purpose="evaluation",
        trusted_head_hash=_head(rows),
    )
    assert len(export.rows) == 2
    assert len({row["case_group_digest"] for row in export.rows}) == 1
    assert len({row["forecast_identity_digest"] for row in export.rows}) == 2


def test_eligibility_cannot_move_between_evaluation_and_training() -> None:
    rows: list[OutcomeLedgerEntry] = []
    forecast = _forecast(rows)
    first = _decision(
        rows,
        forecast=forecast,
        eligibility=DatasetEligibility.EVALUATION_ONLY,
    )
    _decision(
        rows,
        forecast=forecast,
        eligibility=DatasetEligibility.TRAINING_ELIGIBLE,
        at=BASE + timedelta(hours=1),
        supersedes=first,
        rights_label="unsafe-promotion",
    )

    with pytest.raises(OutcomeIntegrityError, match="cannot move"):
        verify_outcome_chain(rows, expected_head_hash=_head(rows))


def test_later_prohibition_revokes_future_exports_without_rewriting_history() -> None:
    rows, forecast, first = _resolved_chain()
    _decision(
        rows,
        forecast=forecast,
        eligibility=DatasetEligibility.PROHIBITED,
        at=BASE + timedelta(days=3),
        supersedes=first,
        rights_label="rights-revoked",
    )
    verify_outcome_chain(rows, expected_head_hash=_head(rows))

    before = build_investigative_outcome_export(
        rows,
        as_of=BASE + timedelta(days=2, hours=3),
        purpose="training",
        trusted_head_hash=_head(rows),
    )
    after = build_investigative_outcome_export(
        rows,
        as_of=BASE + timedelta(days=4),
        purpose="training",
        trusted_head_hash=_head(rows),
    )
    assert len(before.rows) == 1
    assert after.rows == ()


def test_first_eligibility_decision_must_be_reviewed_at_issuance() -> None:
    rows: list[OutcomeLedgerEntry] = []
    forecast = _forecast(rows)
    _decision(
        rows,
        forecast=forecast,
        eligibility=DatasetEligibility.TRAINING_ELIGIBLE,
        at=BASE + timedelta(seconds=1),
    )

    with pytest.raises(OutcomeIntegrityError, match="at forecast issuance"):
        verify_outcome_chain(rows, expected_head_hash=_head(rows))


def test_resolution_must_match_exact_forecast_local_dates() -> None:
    rows: list[OutcomeLedgerEntry] = []
    forecast = _forecast(rows)
    _decision(
        rows,
        forecast=forecast,
        eligibility=DatasetEligibility.TRAINING_ELIGIBLE,
    )
    first = _observation(
        rows,
        at=BASE + timedelta(days=1, hours=1),
        event_time=BASE + timedelta(days=1),
        occurred=False,
        suffix="d1",
    )
    wrong = _observation(
        rows,
        at=BASE + timedelta(days=2, hours=1),
        event_time=BASE + timedelta(days=2),
        observation_date="2026-01-08",
        occurred=False,
        suffix="wrong-date",
    )
    _resolution(
        rows,
        at=BASE + timedelta(days=2, hours=2),
        forecast=forecast,
        observations=[first, wrong],
        disposition=ResolutionDisposition.RESOLVED,
        outcome=False,
    )

    with pytest.raises(OutcomeIntegrityError, match="exact local dates"):
        verify_outcome_chain(rows, expected_head_hash=_head(rows))

    with pytest.raises(OutcomeLedgerError, match="strictly increasing"):
        _forecast([], observation_dates=("2026-01-07", "2026-01-06"))


def test_calendar_dates_are_exact_without_unverifiable_business_day_claim() -> None:
    rows: list[OutcomeLedgerEntry] = []
    forecast = _forecast(
        rows,
        observation_dates=("2026-01-10", "2026-01-11"),
        window_closed_at=BASE + timedelta(days=6),
    )
    _decision(
        rows,
        forecast=forecast,
        eligibility=DatasetEligibility.EVALUATION_ONLY,
    )
    first = _observation(
        rows,
        at=BASE + timedelta(days=5, hours=1),
        event_time=BASE + timedelta(days=5),
        occurred=False,
        suffix="sat",
    )
    second = _observation(
        rows,
        at=BASE + timedelta(days=6, hours=1),
        event_time=BASE + timedelta(days=6),
        occurred=False,
        suffix="sun",
    )
    _resolution(
        rows,
        at=BASE + timedelta(days=6, hours=2),
        forecast=forecast,
        observations=[first, second],
        disposition=ResolutionDisposition.RESOLVED,
        outcome=False,
    )

    verify_outcome_chain(rows, expected_head_hash=_head(rows))
    export = build_investigative_outcome_export(
        rows,
        as_of=BASE + timedelta(days=7),
        purpose="evaluation",
        trusted_head_hash=_head(rows),
    )
    assert export.rows[0]["observation_dates"] == ("2026-01-10", "2026-01-11")
    assert export.policy["calendar_business_day_status_requires_external_evidence_join"]


def test_event_instant_is_bound_to_claimed_market_local_date_across_midnight() -> None:
    prediction = datetime(2026, 1, 5, 23, tzinfo=UTC)
    window_close = datetime(2026, 1, 6, 4, tzinfo=UTC)
    rows: list[OutcomeLedgerEntry] = []
    forecast = _forecast(
        rows,
        at=prediction,
        observation_dates=("2026-01-05",),
        window_closed_at=window_close,
    )
    _decision(
        rows,
        forecast=forecast,
        eligibility=DatasetEligibility.EVALUATION_ONLY,
    )
    observation = _observation(
        rows,
        at=datetime(2026, 1, 6, 2, tzinfo=UTC),
        event_time=datetime(2026, 1, 6, 1, tzinfo=UTC),
        observation_date="2026-01-05",
        occurred=False,
        suffix="cross-midnight",
    )
    _resolution(
        rows,
        at=window_close,
        forecast=forecast,
        observations=[observation],
        disposition=ResolutionDisposition.RESOLVED,
        outcome=False,
    )

    verify_outcome_chain(rows, expected_head_hash=_head(rows))
    export = build_investigative_outcome_export(
        rows,
        as_of=window_close + timedelta(hours=1),
        purpose="evaluation",
        trusted_head_hash=_head(rows),
    )
    assert export.rows[0]["calendar_timezone"] == "America/New_York"
    assert export.rows[0]["observation_dates"] == ("2026-01-05",)


def test_event_instant_rejects_false_claimed_market_local_date() -> None:
    rows: list[OutcomeLedgerEntry] = []
    forecast = _forecast(
        rows,
        observation_dates=("2026-01-06",),
        window_closed_at=BASE + timedelta(days=3),
    )
    _decision(
        rows,
        forecast=forecast,
        eligibility=DatasetEligibility.TRAINING_ELIGIBLE,
    )
    observation = _observation(
        rows,
        at=BASE + timedelta(days=2, hours=1),
        event_time=BASE + timedelta(days=2),
        observation_date="2026-01-06",
        occurred=False,
        suffix="false-local-date",
    )
    _resolution(
        rows,
        at=BASE + timedelta(days=3),
        forecast=forecast,
        observations=[observation],
        disposition=ResolutionDisposition.RESOLVED,
        outcome=False,
    )

    with pytest.raises(OutcomeIntegrityError, match="claimed market-local date"):
        verify_outcome_chain(rows, expected_head_hash=_head(rows))


def test_event_date_conversion_is_dst_safe() -> None:
    prediction = datetime(2026, 3, 7, 12, tzinfo=UTC)
    window_close = datetime(2026, 3, 9, 12, tzinfo=UTC)
    rows: list[OutcomeLedgerEntry] = []
    forecast = _forecast(
        rows,
        at=prediction,
        observation_dates=("2026-03-08",),
        window_closed_at=window_close,
    )
    _decision(
        rows,
        forecast=forecast,
        eligibility=DatasetEligibility.EVALUATION_ONLY,
    )
    observation = _observation(
        rows,
        at=datetime(2026, 3, 8, 8, tzinfo=UTC),
        event_time=datetime(2026, 3, 8, 7, 30, tzinfo=UTC),
        observation_date="2026-03-08",
        occurred=True,
        suffix="dst-transition",
    )
    _resolution(
        rows,
        at=window_close,
        forecast=forecast,
        observations=[observation],
        disposition=ResolutionDisposition.RESOLVED,
        outcome=True,
    )

    verify_outcome_chain(rows, expected_head_hash=_head(rows))


def test_forecast_rejects_non_iana_calendar_timezone() -> None:
    with pytest.raises(OutcomeLedgerError, match="canonical IANA timezone"):
        _forecast([], calendar_timezone="UTC-05:00")


def test_multiple_resolutions_for_one_forecast_fail_chain_verification() -> None:
    rows, forecast, _ = _resolved_chain(occurred=(False, False))
    _resolution(
        rows,
        at=BASE + timedelta(days=2, hours=3),
        forecast=forecast,
        observations=[rows[2], rows[3]],
        disposition=ResolutionDisposition.RESOLVED,
        outcome=False,
    )

    with pytest.raises(OutcomeIntegrityError, match="multiple resolutions"):
        verify_outcome_chain(rows, expected_head_hash=_head(rows))


def test_positive_or_resolves_despite_an_unmeasurable_day() -> None:
    rows, _, _ = _resolved_chain(occurred=(None, True))
    verify_outcome_chain(rows, expected_head_hash=_head(rows))
    export = build_investigative_outcome_export(
        rows,
        as_of=BASE + timedelta(days=3),
        purpose="training",
        trusted_head_hash=_head(rows),
    )
    assert export.rows[0]["status"] == "resolved"
    assert export.rows[0]["outcome"] is True
    assert export.rows[0]["label_eligible"] is True


def test_unknown_without_positive_is_censored_and_never_coerced_false() -> None:
    rows, _, _ = _resolved_chain(occurred=(None, False))
    verify_outcome_chain(rows, expected_head_hash=_head(rows))
    export = build_investigative_outcome_export(
        rows,
        as_of=BASE + timedelta(days=3),
        purpose="training",
        trusted_head_hash=_head(rows),
    )
    assert export.rows[0]["status"] == "censored"
    assert export.rows[0]["outcome"] is None
    assert export.rows[0]["label_eligible"] is False

    forged = list(rows)
    forged.pop()
    _resolution(
        forged,
        at=BASE + timedelta(days=2, hours=2),
        forecast=forged[0],
        observations=[forged[2], forged[3]],
        disposition=ResolutionDisposition.RESOLVED,
        outcome=False,
    )
    with pytest.raises(OutcomeIntegrityError, match="differs"):
        verify_outcome_chain(forged, expected_head_hash=_head(forged))


def test_invalid_future_semantics_do_not_change_prior_cut() -> None:
    rows, forecast, _ = _resolved_chain()
    _resolution(
        rows,
        at=BASE + timedelta(days=10),
        forecast=forecast,
        observations=[rows[2], rows[3]],
        disposition=ResolutionDisposition.RESOLVED,
        outcome=True,
    )

    historical = build_investigative_outcome_export(
        rows,
        as_of=BASE + timedelta(days=3),
        purpose="training",
        trusted_head_hash=_head(rows),
    )
    assert len(historical.rows) == 1
    with pytest.raises(OutcomeIntegrityError, match="multiple resolutions"):
        verify_outcome_chain(rows, expected_head_hash=_head(rows))


def test_truncated_or_cherry_picked_chain_fails_trusted_head() -> None:
    rows, _, _ = _resolved_chain()
    trusted_head = _head(rows)

    with pytest.raises(OutcomeIntegrityError, match="trusted head"):
        build_investigative_outcome_export(
            rows[:-1],
            as_of=BASE + timedelta(days=3),
            purpose="training",
            trusted_head_hash=trusted_head,
        )
    with pytest.raises(OutcomeIntegrityError, match="trusted head"):
        verify_outcome_chain(rows[:-1], expected_head_hash=trusted_head)


def test_forecast_append_receipt_clocks_must_be_exactly_equal() -> None:
    rows: list[OutcomeLedgerEntry] = []
    forecast = _forecast(rows)
    payload = forecast.to_dict()["payload"]
    assert isinstance(payload, dict)

    with pytest.raises(OutcomeLedgerError, match="must be equal"):
        OutcomeLedgerEntry(
            sequence=0,
            previous_record_hash=OUTCOME_GENESIS_HASH,
            kind=OutcomeRecordKind.FORECAST,
            recorded_at=BASE + timedelta(days=10),
            knowledge_time=BASE,
            market_id="US-USD",
            entity_group_id="funding-market",
            payload=payload,
        )


def test_forecast_requires_sha256_evidence_ids_and_cut_digest() -> None:
    with pytest.raises(OutcomeLedgerError, match="forecast evidence_ids"):
        _forecast([], evidence_ids=("seiche:snapshot:not-a-digest",))
    with pytest.raises(OutcomeLedgerError, match="evidence_cut_digest"):
        _forecast([], evidence_cut_digest="0" * 64)


def test_resolution_rejects_event_outside_bound_target_window() -> None:
    rows: list[OutcomeLedgerEntry] = []
    forecast = _forecast(rows)
    _decision(
        rows,
        forecast=forecast,
        eligibility=DatasetEligibility.TRAINING_ELIGIBLE,
    )
    first = _observation(
        rows,
        at=BASE + timedelta(days=1, hours=1),
        event_time=BASE + timedelta(days=1),
        occurred=False,
        suffix="d1",
    )
    outside = _observation(
        rows,
        at=BASE + timedelta(days=3, hours=1),
        event_time=BASE + timedelta(days=3),
        observation_date="2026-01-07",
        occurred=False,
        suffix="outside",
    )
    _resolution(
        rows,
        at=BASE + timedelta(days=3, hours=2),
        forecast=forecast,
        observations=[first, outside],
        disposition=ResolutionDisposition.RESOLVED,
        outcome=False,
    )

    with pytest.raises(OutcomeIntegrityError, match="outside"):
        verify_outcome_chain(rows, expected_head_hash=_head(rows))


def test_payload_semantics_and_export_purpose_fail_closed() -> None:
    with pytest.raises(OutcomeLedgerError, match="probability_ppm"):
        rows: list[OutcomeLedgerEntry] = []
        forecast = _forecast(rows)
        payload = forecast.to_dict()["payload"]
        assert isinstance(payload, dict)
        payload["probability_ppm"] = 1_000_001
        OutcomeLedgerEntry(
            sequence=0,
            previous_record_hash=OUTCOME_GENESIS_HASH,
            kind=OutcomeRecordKind.FORECAST,
            recorded_at=BASE,
            knowledge_time=BASE,
            market_id="US-USD",
            entity_group_id="funding-market",
            payload=payload,
        )

    with pytest.raises(OutcomeLedgerError, match="unsupported export purpose"):
        build_investigative_outcome_export(
            [],
            as_of=BASE,
            purpose="audit",
            trusted_head_hash=OUTCOME_GENESIS_HASH,
        )


def test_chain_entry_bound_is_enforced_before_materializing_unbounded_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, _, _ = _resolved_chain()
    monkeypatch.setattr(outcome_module, "MAX_OUTCOME_CHAIN_ENTRIES", 2)

    with pytest.raises(OutcomeLedgerError, match="exceeds 2 entries"):
        verify_outcome_chain(rows, expected_head_hash=_head(rows))
