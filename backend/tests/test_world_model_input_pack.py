"""Strict bitemporal producer contract for world-model research inputs."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from seiche import store
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
from seiche.markets.world_model import (
    RequiredWorldModelState,
    WorldModelInputError,
    build_world_model_input_pack,
    build_world_model_input_pack_from_repository,
    canonical_world_model_input_digest,
    verify_world_model_input_pack,
    world_model_input_pack_json,
    world_model_observation_id,
)
from seiche.repository import SQLiteMarketRepository


START = datetime(2026, 1, 5, tzinfo=UTC)
AS_OF = datetime(2026, 1, 20, tzinfo=UTC)
STATES = (
    RequiredWorldModelState("secured_rate", "ZZ-ZZZ", SemanticRole.SECURED_OVERNIGHT),
    RequiredWorldModelState(
        "unsecured_rate", "ZZ-ZZZ", SemanticRole.UNSECURED_OVERNIGHT
    ),
)


def _rate(
    role: SemanticRole,
    value: Decimal | int | float | str,
    event_offset: int,
    *,
    instrument: str | None = None,
    knowledge_delay: timedelta = timedelta(hours=2),
    publication_delay: timedelta = timedelta(hours=1),
    revision_id: str = "initial",
    source: str = "official-test",
) -> Observation:
    event = START + timedelta(days=event_offset)
    instrument_id = instrument or f"ZZ.TEST.{role.value}"
    evidence = ":".join(
        (
            instrument_id,
            event.isoformat(),
            revision_id,
            str(value),
            source,
        )
    )
    return Observation(
        market_id="ZZ-ZZZ",
        monetary_area_id="ZZ",
        jurisdiction_codes=("ZZ",),
        currency="ZZZ",
        instrument_id=instrument_id,
        semantic_role=role,
        value=value,
        canonical_unit=CanonicalUnit.BASIS_POINTS,
        rate_compounding=RateCompounding.SIMPLE,
        day_count=DayCountConvention.ACT_360,
        event_time=event,
        source_publication_time=event + publication_delay,
        knowledge_time=event + knowledge_delay,
        revision_id=revision_id,
        source=source,
        evidence_hash=evidence_sha256(evidence),
        connector_classification=ConnectorClassification.OFFICIAL_OPEN,
        redistribution_status=RedistributionStatus.ALLOWED,
        quality=QualityState.VERIFIED,
        staleness=StalenessState.FRESH,
    )


def _revision(observation: Observation, value: str = "501.25") -> Observation:
    return replace(
        observation,
        value=value,
        source_publication_time=observation.event_time + timedelta(days=3),
        knowledge_time=observation.event_time + timedelta(days=3, hours=1),
        revision_id="revision-2",
        evidence_hash=evidence_sha256(
            f"{observation.instrument_id}:{observation.event_time}:revision-2:{value}"
        ),
    )


def _complete_rows() -> list[Observation]:
    secured_0 = _rate(SemanticRole.SECURED_OVERNIGHT, Decimal("500.00"), 0)
    return [
        secured_0,
        _revision(secured_0),
        _rate(SemanticRole.UNSECURED_OVERNIGHT, Decimal("503.500"), 0),
        _rate(SemanticRole.SECURED_OVERNIGHT, 502, 1),
        _rate(SemanticRole.UNSECURED_OVERNIGHT, 504, 1),
    ]


def _build(rows=None, states=STATES):
    return build_world_model_input_pack(
        rows or _complete_rows(),
        required_states=states,
        as_of=AS_OF,
    )


def _reseal(pack):
    pack["pack_digest"] = canonical_world_model_input_digest(pack)
    return pack


def test_pack_is_deterministic_complete_and_preserves_every_revision() -> None:
    rows = _complete_rows()
    first = _build(rows, reversed(STATES))
    second = _build(reversed(rows), STATES)

    assert first == second
    assert first["schema"] == "seiche.world-model-input-pack.v1"
    assert first["as_of"] == AS_OF.isoformat()
    assert first["policy"] == {
        "maturity": "research",
        "validation_mode": "rolling_origin_research",
        "imputation": "forbidden",
        "capture_kind": "retrospective_export",
        "forward_evidence_eligible": False,
        "can_publish": False,
        "can_execute": False,
    }
    assert first["event_grid"] == [
        START.isoformat(),
        (START + timedelta(days=1)).isoformat(),
    ]
    assert [item["state_name"] for item in first["state_definitions"]] == [
        "secured_rate",
        "unsecured_rate",
    ]
    secured_first_event = [
        item
        for item in first["observations"]
        if item["state_name"] == "secured_rate"
        and item["event_time"] == START.isoformat()
    ]
    assert [item["revision_ordinal"] for item in secured_first_event] == [1, 2]
    assert len({item["observation_id"] for item in secured_first_event}) == 1
    assert [item["value"] for item in secured_first_event] == ["500", "501.25"]
    assert first["coverage"]["expected_state_event_count"] == 4
    assert first["coverage"]["observed_state_event_count"] == 4
    assert first["coverage"]["revision_row_count"] == 5
    assert first["coverage"]["missing_state_event_count"] == 0
    assert first["coverage"]["complete"] is True


def test_digest_matches_independent_canonical_utf8_json() -> None:
    rows = _complete_rows()
    rows[-1] = replace(rows[-1], source="official-流动性")
    pack = _build(rows)
    body = {key: value for key, value in pack.items() if key != "pack_digest"}
    encoded = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")

    assert "流动性".encode("utf-8") in encoded
    assert pack["pack_digest"] == hashlib.sha256(encoded).hexdigest()
    assert verify_world_model_input_pack(pack) is pack
    assert "流动性" in world_model_input_pack_json(pack)


def test_observation_id_is_scoped_to_exact_event_timestamp() -> None:
    original = _rate(SemanticRole.SECURED_OVERNIGHT, 500, 0)
    timestamp_correction = replace(
        original,
        event_time=original.event_time + timedelta(seconds=1),
        source_publication_time=original.source_publication_time + timedelta(seconds=1),
        knowledge_time=original.knowledge_time + timedelta(seconds=1),
    )

    assert world_model_observation_id(original) != world_model_observation_id(
        timestamp_correction
    )


def test_repository_seam_loads_all_revisions_not_only_latest(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "world-model.sqlite")
    rows = _complete_rows()
    assert store.save_observations(rows) == len(rows)

    pack = build_world_model_input_pack_from_repository(
        SQLiteMarketRepository(),
        required_states=STATES,
        as_of=AS_OF,
    )

    assert pack["coverage"]["revision_row_count"] == 5
    assert (
        sum(
            item["event_time"] == START.isoformat()
            and item["state_name"] == "secured_rate"
            for item in pack["observations"]
        )
        == 2
    )


def test_repository_revision_query_is_bitemporal(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "revision-query.sqlite")
    initial = _rate(SemanticRole.SECURED_OVERNIGHT, 500, 0)
    revised = _revision(initial)
    store.save_observations((initial, revised))
    repository = SQLiteMarketRepository()

    early = repository.load_observation_revisions_as_of(
        "ZZ-ZZZ", initial.knowledge_time, event_time=AS_OF
    )
    late = repository.load_observation_revisions_as_of(
        "ZZ-ZZZ", revised.knowledge_time, event_time=AS_OF
    )

    assert [item.revision_id for item in early] == ["initial"]
    assert [item.revision_id for item in late] == ["initial", "revision-2"]


def test_repository_revision_query_rejects_tampered_stored_content(
    tmp_path, monkeypatch
) -> None:
    database = tmp_path / "revision-integrity.sqlite"
    monkeypatch.setattr(store, "DB_PATH", database)
    observation = _rate(SemanticRole.SECURED_OVERNIGHT, 500, 0)
    store.save_observations((observation,))
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE canonical_observations SET value=? WHERE revision_id=?",
            ("999", observation.revision_id),
        )

    with pytest.raises(ValueError, match="record_hash mismatch"):
        store.load_observation_revisions_as_of(
            observation.market_id,
            observation.knowledge_time,
            event_time=AS_OF,
        )


@pytest.mark.parametrize(
    ("row", "as_of", "message"),
    [
        (
            _rate(SemanticRole.SECURED_OVERNIGHT, 500, 0),
            START - timedelta(seconds=1),
            "event_time",
        ),
        (
            _rate(
                SemanticRole.SECURED_OVERNIGHT,
                500,
                0,
                publication_delay=timedelta(days=2),
                knowledge_delay=timedelta(days=3),
            ),
            START + timedelta(days=1),
            "source_publication_time",
        ),
        (
            _rate(
                SemanticRole.SECURED_OVERNIGHT,
                500,
                0,
                publication_delay=timedelta(hours=1),
                knowledge_delay=timedelta(days=2),
            ),
            START + timedelta(days=1),
            "knowledge_time",
        ),
        (
            _rate(
                SemanticRole.SECURED_OVERNIGHT,
                500,
                0,
                publication_delay=timedelta(hours=-2),
                knowledge_delay=timedelta(hours=-1),
            ),
            AS_OF,
            "precede event_time",
        ),
    ],
)
def test_builder_rejects_temporally_ineligible_rows(row, as_of, message) -> None:
    state = RequiredWorldModelState(
        "secured_rate", "ZZ-ZZZ", SemanticRole.SECURED_OVERNIGHT
    )
    with pytest.raises(WorldModelInputError, match=message):
        build_world_model_input_pack([row], required_states=[state], as_of=as_of)


def test_builder_rejects_unusable_and_nonfinite_rows() -> None:
    row = _rate(SemanticRole.SECURED_OVERNIGHT, 500, 0)
    unavailable = replace(
        row,
        value=None,
        quality=QualityState.UNAVAILABLE,
        staleness=StalenessState.UNAVAILABLE,
    )
    state = RequiredWorldModelState(
        "secured_rate", "ZZ-ZZZ", SemanticRole.SECURED_OVERNIGHT
    )
    with pytest.raises(WorldModelInputError, match="unusable"):
        build_world_model_input_pack(
            [unavailable], required_states=[state], as_of=AS_OF
        )

    corrupt = replace(row)
    object.__setattr__(corrupt, "value", Decimal("NaN"))
    with pytest.raises(WorldModelInputError, match="finite"):
        build_world_model_input_pack([corrupt], required_states=[state], as_of=AS_OF)


@pytest.mark.parametrize(
    "status",
    [
        RedistributionStatus.DERIVED_ONLY,
        RedistributionStatus.METADATA_ONLY,
        RedistributionStatus.PROHIBITED,
    ],
)
def test_builder_rejects_raw_values_without_redistribution_permission(
    status,
) -> None:
    row = replace(
        _rate(SemanticRole.SECURED_OVERNIGHT, 500, 0),
        redistribution_status=status,
    )
    state = RequiredWorldModelState(
        "secured_rate", "ZZ-ZZZ", SemanticRole.SECURED_OVERNIGHT
    )

    with pytest.raises(WorldModelInputError, match="redistribution_status='allowed'"):
        build_world_model_input_pack([row], required_states=[state], as_of=AS_OF)


@pytest.mark.parametrize(
    "status",
    [
        RedistributionStatus.DERIVED_ONLY,
        RedistributionStatus.METADATA_ONLY,
        RedistributionStatus.PROHIBITED,
    ],
)
def test_verifier_rejects_raw_values_without_redistribution_permission(
    status,
) -> None:
    pack = copy.deepcopy(_build())
    pack["observations"][0]["redistribution_status"] = status.value

    with pytest.raises(WorldModelInputError, match="must be 'allowed'"):
        verify_world_model_input_pack(_reseal(pack))


def test_builder_rejects_duplicate_identities_and_same_knowledge_ties() -> None:
    initial = _rate(SemanticRole.SECURED_OVERNIGHT, 500, 0)
    state = RequiredWorldModelState(
        "secured_rate", "ZZ-ZZZ", SemanticRole.SECURED_OVERNIGHT
    )
    with pytest.raises(WorldModelInputError, match="duplicate canonical"):
        build_world_model_input_pack(
            [initial, initial], required_states=[state], as_of=AS_OF
        )

    tied = replace(
        initial,
        value=501,
        revision_id="same-clock-revision",
        evidence_hash=evidence_sha256("same-clock-revision"),
    )
    with pytest.raises(WorldModelInputError, match="same-knowledge"):
        build_world_model_input_pack(
            [initial, tied], required_states=[state], as_of=AS_OF
        )


def test_builder_rejects_source_conflicts_across_revisions() -> None:
    initial = _rate(SemanticRole.SECURED_OVERNIGHT, 500, 0)
    revised = replace(_revision(initial), source="another-source")
    state = RequiredWorldModelState(
        "secured_rate", "ZZ-ZZZ", SemanticRole.SECURED_OVERNIGHT
    )
    with pytest.raises(WorldModelInputError, match="source conflict"):
        build_world_model_input_pack(
            [initial, revised], required_states=[state], as_of=AS_OF
        )


def test_builder_rejects_ambiguous_roles_and_mixed_revision_units() -> None:
    first = _rate(SemanticRole.SECURED_OVERNIGHT, 500, 0, instrument="ZZ.ONE")
    second = _rate(SemanticRole.SECURED_OVERNIGHT, 501, 0, instrument="ZZ.TWO")
    state = RequiredWorldModelState(
        "secured_rate", "ZZ-ZZZ", SemanticRole.SECURED_OVERNIGHT
    )
    with pytest.raises(WorldModelInputError, match="ambiguous"):
        build_world_model_input_pack(
            [first, second], required_states=[state], as_of=AS_OF
        )

    revised = _revision(first)
    object.__setattr__(revised, "canonical_unit", CanonicalUnit.RATIO)
    with pytest.raises(WorldModelInputError, match="mixed role/unit"):
        build_world_model_input_pack(
            [first, revised], required_states=[state], as_of=AS_OF
        )


def test_builder_rejects_ragged_required_state_grids_without_imputing() -> None:
    rows = _complete_rows()
    rows = [
        item
        for item in rows
        if not (
            item.semantic_role is SemanticRole.UNSECURED_OVERNIGHT
            and item.event_time == START + timedelta(days=1)
        )
    ]
    with pytest.raises(WorldModelInputError, match="ragged event grid"):
        _build(rows)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("maturity", "production"),
        ("validation_mode", "validated"),
        ("imputation", "forward_fill"),
        ("capture_kind", "live_anchored"),
        ("forward_evidence_eligible", True),
        ("can_publish", True),
        ("can_execute", True),
    ],
)
def test_verifier_rejects_any_authority_or_evidence_upgrade(field, value) -> None:
    pack = copy.deepcopy(_build())
    pack["policy"][field] = value
    with pytest.raises(WorldModelInputError, match="policy must remain"):
        verify_world_model_input_pack(_reseal(pack))


@pytest.mark.parametrize(
    "field", ["forward_evidence_eligible", "can_publish", "can_execute"]
)
def test_verifier_rejects_numeric_zero_for_boolean_policy_flags(field) -> None:
    pack = copy.deepcopy(_build())
    pack["policy"][field] = 0

    with pytest.raises(
        WorldModelInputError, match=rf"policy.{field} must be a boolean"
    ):
        verify_world_model_input_pack(_reseal(pack))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("required_state_count",), True),
        (("observed_state_count",), True),
        (("event_time_count",), True),
        (("expected_state_event_count",), True),
        (("observed_state_event_count",), True),
        (("revision_row_count",), True),
        (("missing_state_event_count",), False),
        (("per_state", 0, "event_time_count"), True),
        (("per_state", 0, "revision_row_count"), True),
    ],
)
def test_verifier_rejects_boolean_coverage_counts_after_redigest(path, value) -> None:
    state = RequiredWorldModelState(
        "secured_rate", "ZZ-ZZZ", SemanticRole.SECURED_OVERNIGHT
    )
    pack = build_world_model_input_pack(
        [_rate(SemanticRole.SECURED_OVERNIGHT, 500, 0)],
        required_states=[state],
        as_of=AS_OF,
    )
    target = pack["coverage"]
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value

    with pytest.raises(WorldModelInputError, match="integer"):
        verify_world_model_input_pack(_reseal(pack))


def test_verifier_requires_boolean_complete_and_typed_ordered_coverage() -> None:
    pack = copy.deepcopy(_build())
    pack["coverage"]["complete"] = 1
    with pytest.raises(WorldModelInputError, match="complete must be a boolean"):
        verify_world_model_input_pack(_reseal(pack))

    pack = copy.deepcopy(_build())
    pack["coverage"]["missing_states"] = [1]
    with pytest.raises(WorldModelInputError, match="array of strings"):
        verify_world_model_input_pack(_reseal(pack))

    pack = copy.deepcopy(_build())
    pack["coverage"]["per_state"].reverse()
    with pytest.raises(WorldModelInputError, match="state_definitions order"):
        verify_world_model_input_pack(_reseal(pack))

    pack = copy.deepcopy(_build())
    pack["coverage"]["per_state"][0]["event_start"] = None
    with pytest.raises(WorldModelInputError, match="canonical UTC timestamp"):
        verify_world_model_input_pack(_reseal(pack))


def test_verifier_rejects_tampering_and_noncontiguous_revision_history() -> None:
    pack = _build()
    pack["observations"][0]["value"] = "999"
    with pytest.raises(WorldModelInputError, match="pack_digest"):
        verify_world_model_input_pack(pack)

    pack = copy.deepcopy(_build())
    revised = next(
        item
        for item in pack["observations"]
        if item["state_name"] == "secured_rate"
        and item["event_time"] == START.isoformat()
        and item["revision_ordinal"] == 2
    )
    revised["revision_ordinal"] = 3
    pack["observations"].sort(
        key=lambda item: (
            item["event_time"],
            item["state_name"],
            item["revision_ordinal"],
        )
    )
    with pytest.raises(WorldModelInputError, match="not contiguous"):
        verify_world_model_input_pack(_reseal(pack))
