"""Pinned USD funding-core research profile.

``us-usd-funding-core-v1`` is an opinionated projection of the generic
world-model input pack.  It exports only the NY Fed SOFR median, P99 and repo
volume instruments named below.  The event grid is their exact intersection;
partial dates are dropped and values are never filled or imputed.  Every
canonical revision for an included event remains in the output.

This module serializes research/training inputs only.  It does not fit
``us-usd-funding-core-var1-v1``, serve an API, publish a forecast, or establish
forward evidence.  Historical captures are retrospective until a later live
collection establishes its own knowledge-time record.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from seiche.domain.observation import (
    CanonicalUnit,
    DayCountConvention,
    Observation,
    RateCompounding,
    SemanticRole,
)
from seiche.markets.world_model import (
    RequiredWorldModelState,
    WorldModelInputError,
    build_world_model_input_pack,
    world_model_input_pack_json,
)
from seiche.repository import MarketRepository

FUNDING_CORE_PROFILE_ID = "us-usd-funding-core-v1"
FUNDING_CORE_MODEL_ID = "us-usd-funding-core-var1-v1"
MINIMUM_COMPLETE_DATES = 504
EXPORT_DIRECTORY_ENV = "SEICHE_USD_FUNDING_CORE_EXPORT_DIR"
EXPORT_FILENAME = f"{FUNDING_CORE_PROFILE_ID}.json"

_MARKET_ID = "US-USD"


class FundingCoreProfileError(WorldModelInputError):
    """Canonical USD rows cannot satisfy the pinned funding-core profile."""


@dataclass(frozen=True, slots=True)
class FundingCoreStateSpec:
    state_name: str
    instrument_id: str
    semantic_role: SemanticRole
    canonical_unit: CanonicalUnit
    rate_compounding: RateCompounding | None
    day_count: DayCountConvention | None


FUNDING_CORE_STATES = (
    FundingCoreStateSpec(
        "sofr_median_bp",
        "US.NYFED.SOFR_MEDIAN",
        SemanticRole.RATE_MEDIAN,
        CanonicalUnit.BASIS_POINTS,
        RateCompounding.SIMPLE,
        DayCountConvention.ACT_360,
    ),
    FundingCoreStateSpec(
        "sofr_p99_bp",
        "US.NYFED.SOFR_P99",
        SemanticRole.RATE_P99,
        CanonicalUnit.BASIS_POINTS,
        RateCompounding.SIMPLE,
        DayCountConvention.ACT_360,
    ),
    FundingCoreStateSpec(
        "sofr_volume_usd_m",
        "US.NYFED.SOFR_VOLUME",
        SemanticRole.REPO_VOLUME,
        CanonicalUnit.LOCAL_CURRENCY_MILLIONS,
        None,
        None,
    ),
)

_SPEC_BY_INSTRUMENT = {item.instrument_id: item for item in FUNDING_CORE_STATES}
_INSTRUMENT_IDS = frozenset(_SPEC_BY_INSTRUMENT)
_REQUIRED_STATES = tuple(
    RequiredWorldModelState(item.state_name, _MARKET_ID, item.semantic_role)
    for item in FUNDING_CORE_STATES
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FundingCoreProfileError("as_of must be a timezone-aware datetime")
    return value.astimezone(UTC).replace(microsecond=0)


def _latest_by_event(
    rows: Iterable[Observation],
) -> dict[str, dict[datetime, Observation]]:
    latest: dict[str, dict[datetime, Observation]] = {
        instrument_id: {} for instrument_id in _INSTRUMENT_IDS
    }
    for observation in rows:
        by_event = latest[observation.instrument_id]
        current = by_event.get(observation.event_time)
        if current is None or (
            observation.knowledge_time,
            observation.source_publication_time,
            observation.revision_id,
            observation.evidence_hash,
        ) > (
            current.knowledge_time,
            current.source_publication_time,
            current.revision_id,
            current.evidence_hash,
        ):
            by_event[observation.event_time] = observation
    return latest


def _has_corrected_median_lineage(observation: Observation) -> bool:
    # Official date-labeled rows use UTC midnight as their canonical event key;
    # converting that sentinel to New York time would shift it to the prior day.
    event_time = observation.event_time
    if event_time.utcoffset() != timedelta(0) or event_time.time() != time.min:
        return False
    event_day = event_time.date().isoformat()
    prefix = f"nyfed:percentRate:{event_day}:"
    return observation.revision_id.startswith(prefix) and bool(
        observation.revision_id[len(prefix) :].strip()
    )


def _validate_pinned_semantics(rows: Iterable[Observation]) -> None:
    for observation in rows:
        spec = _SPEC_BY_INSTRUMENT[observation.instrument_id]
        actual = (
            observation.market_id,
            observation.currency,
            observation.semantic_role,
            observation.canonical_unit,
            observation.rate_compounding,
            observation.day_count,
        )
        expected = (
            _MARKET_ID,
            "USD",
            spec.semantic_role,
            spec.canonical_unit,
            spec.rate_compounding,
            spec.day_count,
        )
        if actual != expected:
            raise FundingCoreProfileError(
                f"{observation.instrument_id} does not match the profile's pinned semantics"
            )


def _profile_rows(
    observations: Iterable[Observation],
    *,
    as_of: datetime,
) -> tuple[Observation, ...]:
    captured = tuple(observations)
    if not all(isinstance(item, Observation) for item in captured):
        raise FundingCoreProfileError("funding-core inputs must be Observation values")
    # Filter before invoking the role-oriented generic builder.  A future
    # instrument assigned RATE_MEDIAN/RATE_P99/REPO_VOLUME cannot silently
    # replace the exact identities this version was trained against.
    selected = tuple(
        item
        for item in captured
        if item.market_id == _MARKET_ID
        and item.instrument_id in _INSTRUMENT_IDS
        and item.event_time <= as_of
        and item.source_publication_time <= as_of
        and item.knowledge_time <= as_of
    )
    if not selected:
        raise FundingCoreProfileError(
            "no declared funding-core instruments are available"
        )
    _validate_pinned_semantics(selected)
    return selected


def build_funding_core_input_pack(
    observations: Iterable[Observation],
    *,
    as_of: datetime,
) -> dict:
    """Build the complete pinned profile without fitting, filling, or writing."""

    cutoff = _utc(as_of)
    selected = _profile_rows(observations, as_of=cutoff)
    latest = _latest_by_event(selected)

    for instrument_id, rows in latest.items():
        if not rows:
            raise FundingCoreProfileError(
                f"missing declared funding-core instrument {instrument_id}"
            )

    # Old P25-derived rows intentionally remain earlier revisions, but a pack
    # cannot leave Seiche until every latest median row proves the corrected
    # NY Fed percentRate field and its exact source event date.
    bad_medians = [
        item
        for item in latest["US.NYFED.SOFR_MEDIAN"].values()
        if not _has_corrected_median_lineage(item)
    ]
    if bad_medians:
        first = min(bad_medians, key=lambda item: item.event_time)
        raise FundingCoreProfileError(
            "latest SOFR median rows require corrected percentRate lineage; "
            f"first failure is {first.event_time.isoformat()} ({first.revision_id})"
        )

    eligible_events = [
        {
            event_time
            for event_time, observation in latest[instrument_id].items()
            if observation.usable
        }
        for instrument_id in sorted(_INSTRUMENT_IDS)
    ]
    complete_events = set.intersection(*eligible_events)
    if len(complete_events) < MINIMUM_COMPLETE_DATES:
        raise FundingCoreProfileError(
            f"funding-core profile has {len(complete_events)} complete dates; "
            f"{MINIMUM_COMPLETE_DATES} required"
        )

    included = tuple(item for item in selected if item.event_time in complete_events)
    try:
        pack = build_world_model_input_pack(
            included,
            required_states=_REQUIRED_STATES,
            as_of=cutoff,
        )
    except WorldModelInputError as exc:
        raise FundingCoreProfileError(str(exc)) from exc

    expected_definitions = [
        (
            item.state_name,
            item.instrument_id,
            item.semantic_role.value,
            item.canonical_unit.value,
        )
        for item in FUNDING_CORE_STATES
    ]
    actual_definitions = [
        (
            item["state_name"],
            item["instrument_id"],
            item["semantic_role"],
            item["canonical_unit"],
        )
        for item in pack["state_definitions"]
    ]
    if actual_definitions != expected_definitions:
        raise FundingCoreProfileError(
            "generic builder changed pinned profile identities"
        )
    if len(pack["event_grid"]) != len(complete_events):
        raise FundingCoreProfileError(
            "generic builder changed the exact event intersection"
        )
    return pack


def build_funding_core_input_pack_from_repository(
    repository: MarketRepository,
    *,
    as_of: datetime,
) -> dict:
    """Load all revisions for only the three exact profile instruments."""

    cutoff = _utc(as_of)
    rows = repository.load_observation_revisions_as_of(
        _MARKET_ID,
        cutoff,
        event_time=cutoff,
        roles=tuple(item.semantic_role for item in FUNDING_CORE_STATES),
    )
    # The repository protocol filters roles, not instrument IDs.  Apply the
    # versioned identity allow-list here before the generic builder sees rows.
    exact_rows = [item for item in rows if item.instrument_id in _INSTRUMENT_IDS]
    return build_funding_core_input_pack(exact_rows, as_of=cutoff)


def export_funding_core_input_pack(
    repository: MarketRepository,
    *,
    as_of: datetime,
    directory: str | os.PathLike[str] | None = None,
) -> Path:
    """Atomically replace the deterministic latest research-pack JSON file."""

    configured = directory
    if configured is None:
        configured = os.getenv(EXPORT_DIRECTORY_ENV, "").strip()
    if not configured:
        raise FundingCoreProfileError(
            f"{EXPORT_DIRECTORY_ENV} is not configured for funding-core export"
        )
    root = Path(configured).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / EXPORT_FILENAME
    pack = build_funding_core_input_pack_from_repository(repository, as_of=as_of)
    payload = (world_model_input_pack_json(pack) + "\n").encode("utf-8")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{EXPORT_FILENAME}.",
        suffix=".tmp",
        dir=root,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, target)
        directory_fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
    return target


__all__ = [
    "EXPORT_DIRECTORY_ENV",
    "EXPORT_FILENAME",
    "FUNDING_CORE_MODEL_ID",
    "FUNDING_CORE_PROFILE_ID",
    "FUNDING_CORE_STATES",
    "FundingCoreProfileError",
    "FundingCoreStateSpec",
    "MINIMUM_COMPLETE_DATES",
    "build_funding_core_input_pack",
    "build_funding_core_input_pack_from_repository",
    "export_funding_core_input_pack",
]
