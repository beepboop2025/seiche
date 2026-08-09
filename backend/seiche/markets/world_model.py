"""Deterministic research input packs for a money-market world model.

The pack is a portable, long-form projection of Seiche's canonical
``Observation`` and ``MarketPanel`` semantics.  It is not a fitted model, a
forecast, or a serving surface.  Callers must name every required market/role
state explicitly; this module resolves each role to exactly one instrument and
requires a complete common event grid without forward filling or imputation.

The repository seam preserves every revision knowable at ``as_of`` and assigns
an ID stable for the exact market/instrument/event-time slot plus a chronological
revision ordinal.  Because Seiche has no upstream source-event ID, correcting an
event timestamp creates a new slot.  The common event grid is defined by the
latest-as-of selection, while older revisions remain in the pack so
rolling-origin consumers can reconstruct earlier prefixes.  These are model
inputs, not first-release evaluation targets.  Every accepted pack is a
retrospective research export, is not forward-evidence eligible, and carries no
publication or execution authority.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from seiche.domain.observation import (
    CanonicalUnit,
    Observation,
    RedistributionStatus,
    SemanticRole,
)
from seiche.kernel.engines import MarketPanel
from seiche.repository import MarketRepository


WORLD_MODEL_INPUT_SCHEMA = "seiche.world-model-input-pack.v1"

PACK_FIELDS = frozenset(
    {
        "schema",
        "pack_digest",
        "as_of",
        "policy",
        "state_definitions",
        "event_grid",
        "observations",
        "coverage",
    }
)
POLICY_FIELDS = frozenset(
    {
        "maturity",
        "validation_mode",
        "imputation",
        "capture_kind",
        "forward_evidence_eligible",
        "can_publish",
        "can_execute",
    }
)
STATE_FIELDS = frozenset(
    {
        "state_name",
        "market_id",
        "currency",
        "instrument_id",
        "semantic_role",
        "canonical_unit",
    }
)
OBSERVATION_FIELDS = frozenset(
    {
        "state_name",
        "observation_id",
        "revision_ordinal",
        "market_id",
        "monetary_area_id",
        "jurisdiction_codes",
        "currency",
        "instrument_id",
        "semantic_role",
        "value",
        "canonical_unit",
        "rate_compounding",
        "day_count",
        "event_time",
        "knowledge_time",
        "source_publication_time",
        "revision_id",
        "source",
        "evidence_hash",
        "connector_classification",
        "redistribution_status",
        "quality",
        "staleness",
    }
)
COVERAGE_FIELDS = frozenset(
    {
        "complete",
        "required_state_count",
        "observed_state_count",
        "event_time_count",
        "expected_state_event_count",
        "observed_state_event_count",
        "missing_state_event_count",
        "revision_row_count",
        "missing_states",
        "per_state",
    }
)
PER_STATE_COVERAGE_FIELDS = frozenset(
    {
        "state_name",
        "event_time_count",
        "revision_row_count",
        "event_start",
        "event_end",
        "latest_knowledge_time",
    }
)

_POLICY = {
    "maturity": "research",
    "validation_mode": "rolling_origin_research",
    "imputation": "forbidden",
    "capture_kind": "retrospective_export",
    "forward_evidence_eligible": False,
    "can_publish": False,
    "can_execute": False,
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_OBSERVATION_ID_RE = re.compile(r"^obs_[0-9a-f]{32}$")
_MARKET_ID_RE = re.compile(r"^[A-Z0-9]+-[A-Z]{3}$")
_STATE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class WorldModelInputError(ValueError):
    """Input rows or a decoded pack violate the pinned research contract."""


@dataclass(frozen=True, slots=True)
class RequiredWorldModelState:
    """One explicitly required state, resolved by market and semantic role."""

    state_name: str
    market_id: str
    semantic_role: SemanticRole

    def __post_init__(self) -> None:
        name = self.state_name
        market = self.market_id.upper()
        try:
            role = SemanticRole(self.semantic_role)
        except (TypeError, ValueError) as exc:
            raise ValueError("semantic_role must be a canonical Seiche role") from exc
        if not isinstance(name, str) or _STATE_NAME_RE.fullmatch(name) is None:
            raise ValueError("state_name must be a stable ASCII identifier")
        if _MARKET_ID_RE.fullmatch(market) is None:
            raise ValueError("market_id must look like 'US-USD' or 'EA-EUR'")
        object.__setattr__(self, "market_id", market)
        object.__setattr__(self, "semantic_role", role)


def _exact_object(value: Any, fields: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorldModelInputError(f"{label} must be an object")
    actual = set(value)
    if actual != fields:
        details = []
        missing = sorted(fields - actual)
        unknown = sorted(actual - fields)
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise WorldModelInputError(f"{label} has {'; '.join(details)}")
    return value


def _utc(value: datetime, label: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise WorldModelInputError(f"{label} must be a timezone-aware datetime")
    return value.astimezone(UTC).replace(microsecond=0)


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise WorldModelInputError(f"{label} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WorldModelInputError(f"{label} must be an ISO-8601 timestamp") from exc
    normalized = _utc(parsed, label)
    if value != normalized.isoformat():
        raise WorldModelInputError(
            f"{label} must use second-precision UTC with an explicit +00:00 offset"
        )
    return normalized


def _canonical_decimal(value: Decimal | int | float | str) -> str:
    try:
        decimal = value if isinstance(value, Decimal) else Decimal(str(value))
    except (ArithmeticError, ValueError) as exc:
        raise WorldModelInputError(
            "observation value must be a finite decimal"
        ) from exc
    if not decimal.is_finite():
        raise WorldModelInputError("observation value must be finite")
    if not decimal:
        return "0"
    rendered = format(decimal, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _nonblank(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise WorldModelInputError(f"{label} must be a non-blank trimmed string")
    return value


def _strict_integer(value: Any, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "positive" if minimum == 1 else "nonnegative"
        raise WorldModelInputError(f"{label} must be a {qualifier} integer")
    return value


def canonical_world_model_input_digest(pack: Mapping[str, Any]) -> str:
    """Hash canonical UTF-8 JSON, omitting only top-level ``pack_digest``."""

    if not isinstance(pack, Mapping):
        raise WorldModelInputError("world-model input pack must be an object")
    body = dict(pack)
    body.pop("pack_digest", None)
    try:
        encoded = json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise WorldModelInputError(
            f"world-model input pack is not canonical finite JSON: {exc}"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _normalize_required_states(
    states: Iterable[RequiredWorldModelState],
) -> tuple[RequiredWorldModelState, ...]:
    captured = tuple(states)
    if not captured:
        raise WorldModelInputError("at least one required state must be declared")
    if not all(isinstance(item, RequiredWorldModelState) for item in captured):
        raise WorldModelInputError(
            "required_states must contain RequiredWorldModelState values"
        )
    names = [item.state_name for item in captured]
    if len(names) != len(set(names)):
        raise WorldModelInputError("required state names must be unique")
    role_keys = [(item.market_id, item.semantic_role) for item in captured]
    if len(role_keys) != len(set(role_keys)):
        raise WorldModelInputError(
            "a market/role pair may map to only one required state"
        )
    return tuple(sorted(captured, key=lambda item: item.state_name))


def _validate_input_rows(
    observations: tuple[Observation, ...],
    states: tuple[RequiredWorldModelState, ...],
    as_of: datetime,
) -> None:
    if not observations:
        raise WorldModelInputError("world-model input needs canonical observations")
    if not all(isinstance(item, Observation) for item in observations):
        raise WorldModelInputError("all world-model inputs must be Observation values")

    required_pairs = {(item.market_id, item.semantic_role) for item in states}
    identities: set[tuple[Any, ...]] = set()
    revision_groups: dict[tuple[str, str, datetime], list[Observation]] = {}
    for observation in observations:
        pair = (observation.market_id, observation.semantic_role)
        if pair not in required_pairs:
            raise WorldModelInputError(
                f"observation {pair!r} does not belong to a required state"
            )
        if observation.identity in identities:
            raise WorldModelInputError("duplicate canonical observation identity")
        identities.add(observation.identity)
        revision_slot = (
            observation.market_id,
            observation.instrument_id,
            observation.event_time,
        )
        revision_groups.setdefault(revision_slot, []).append(observation)

        if observation.redistribution_status is not RedistributionStatus.ALLOWED:
            raise WorldModelInputError(
                "raw world-model observations require redistribution_status='allowed'"
            )
        if not observation.usable:
            raise WorldModelInputError(
                f"required observation {observation.identity!r} is unusable"
            )
        if observation.event_time > as_of:
            raise WorldModelInputError("observation event_time cannot follow as_of")
        if observation.source_publication_time > as_of:
            raise WorldModelInputError(
                "observation source_publication_time cannot follow as_of"
            )
        if observation.knowledge_time > as_of:
            raise WorldModelInputError("observation knowledge_time cannot follow as_of")
        if observation.knowledge_time < observation.event_time:
            raise WorldModelInputError(
                "observation knowledge_time cannot precede event_time"
            )
        if observation.knowledge_time < observation.source_publication_time:
            raise WorldModelInputError(
                "observation knowledge_time cannot precede source_publication_time"
            )
        _canonical_decimal(observation.value)  # type: ignore[arg-type]

    for revision_slot, revisions in revision_groups.items():
        sources = {item.source for item in revisions}
        if len(sources) != 1:
            raise WorldModelInputError(
                f"source conflict across revisions for {revision_slot!r}"
            )
        knowledge_times = [item.knowledge_time for item in revisions]
        if len(knowledge_times) != len(set(knowledge_times)):
            raise WorldModelInputError(
                f"ambiguous same-knowledge revision tie for {revision_slot!r}"
            )
        revision_ids = [item.revision_id for item in revisions]
        if len(revision_ids) != len(set(revision_ids)):
            raise WorldModelInputError(
                f"duplicate revision_id values for {revision_slot!r}"
            )
        semantic_shapes = {
            (
                item.monetary_area_id,
                item.currency,
                item.semantic_role,
                item.canonical_unit,
                item.rate_compounding,
                item.day_count,
            )
            for item in revisions
        }
        if len(semantic_shapes) != 1:
            raise WorldModelInputError(
                f"mixed role/unit semantics across revisions for {revision_slot!r}"
            )


def world_model_observation_id(observation: Observation) -> str:
    """Identify one exact market/instrument/event-time slot across revisions.

    Seiche has no upstream source-event identifier, so an event-time correction
    intentionally produces a different observation ID and therefore a new slot.
    """

    if not isinstance(observation, Observation):
        raise TypeError("observation must be an Observation")
    identity = {
        "market_id": observation.market_id,
        "instrument_id": observation.instrument_id,
        "event_time": observation.event_time.isoformat(),
    }
    encoded = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "obs_" + hashlib.sha256(encoded).hexdigest()[:32]


def _ordered_revisions(revisions: Iterable[Observation]) -> tuple[Observation, ...]:
    return tuple(
        sorted(
            revisions,
            key=lambda item: (
                item.knowledge_time,
                item.source_publication_time,
                item.revision_id,
            ),
        )
    )


def _observation_record(
    state_name: str,
    observation: Observation,
    revision_ordinal: int,
) -> dict[str, Any]:
    record = observation.to_record()
    record["value"] = _canonical_decimal(observation.value)  # type: ignore[arg-type]
    return {
        "state_name": state_name,
        "observation_id": world_model_observation_id(observation),
        "revision_ordinal": revision_ordinal,
        **record,
    }


def _coverage(
    state_definitions: list[dict[str, Any]],
    event_grid: list[str],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    rows_by_state: dict[str, list[dict[str, Any]]] = {
        item["state_name"]: [] for item in state_definitions
    }
    for observation in observations:
        rows_by_state[observation["state_name"]].append(observation)
    per_state = []
    for definition in state_definitions:
        rows = rows_by_state[definition["state_name"]]
        event_times = sorted({item["event_time"] for item in rows})
        per_state.append(
            {
                "state_name": definition["state_name"],
                "event_time_count": len(event_times),
                "revision_row_count": len(rows),
                "event_start": event_times[0] if event_times else None,
                "event_end": event_times[-1] if event_times else None,
                "latest_knowledge_time": (
                    max(item["knowledge_time"] for item in rows) if rows else None
                ),
            }
        )
    expected_cells = len(state_definitions) * len(event_grid)
    observed_cells = sum(item["event_time_count"] for item in per_state)
    return {
        "complete": observed_cells == expected_cells,
        "required_state_count": len(state_definitions),
        "observed_state_count": sum(
            bool(item["event_time_count"]) for item in per_state
        ),
        "event_time_count": len(event_grid),
        "expected_state_event_count": expected_cells,
        "observed_state_event_count": observed_cells,
        "missing_state_event_count": expected_cells - observed_cells,
        "revision_row_count": len(observations),
        "missing_states": [
            item["state_name"] for item in per_state if not item["event_time_count"]
        ],
        "per_state": per_state,
    }


def _validated_coverage(
    value: Any,
    definitions: list[dict[str, Any]],
    as_of: datetime,
) -> dict[str, Any]:
    coverage = _exact_object(value, COVERAGE_FIELDS, "coverage")
    if type(coverage["complete"]) is not bool:
        raise WorldModelInputError("coverage.complete must be a boolean")
    for field in (
        "required_state_count",
        "observed_state_count",
        "event_time_count",
        "expected_state_event_count",
        "observed_state_event_count",
        "revision_row_count",
    ):
        _strict_integer(coverage[field], f"coverage.{field}", minimum=1)
    _strict_integer(
        coverage["missing_state_event_count"],
        "coverage.missing_state_event_count",
        minimum=0,
    )

    missing_states = coverage["missing_states"]
    if not isinstance(missing_states, list) or not all(
        isinstance(item, str) for item in missing_states
    ):
        raise WorldModelInputError(
            "coverage.missing_states must be an array of strings"
        )
    if missing_states:
        raise WorldModelInputError("coverage.missing_states must be empty")

    per_state = coverage["per_state"]
    if not isinstance(per_state, list):
        raise WorldModelInputError("coverage.per_state must be an array")
    if len(per_state) != len(definitions):
        raise WorldModelInputError(
            "coverage.per_state must contain one row per state definition"
        )
    expected_names = [definition["state_name"] for definition in definitions]
    actual_names = []
    for index, item in enumerate(per_state):
        row = _exact_object(
            item, PER_STATE_COVERAGE_FIELDS, f"coverage.per_state[{index}]"
        )
        name = row["state_name"]
        if not isinstance(name, str):
            raise WorldModelInputError(
                f"coverage.per_state[{index}].state_name must be a string"
            )
        actual_names.append(name)
        _strict_integer(
            row["event_time_count"],
            f"coverage.per_state[{index}].event_time_count",
            minimum=1,
        )
        _strict_integer(
            row["revision_row_count"],
            f"coverage.per_state[{index}].revision_row_count",
            minimum=1,
        )
        for field in ("event_start", "event_end", "latest_knowledge_time"):
            parsed = _timestamp(row[field], f"coverage.per_state[{index}].{field}")
            if parsed > as_of:
                raise WorldModelInputError(
                    f"coverage.per_state[{index}].{field} follows as_of"
                )
    if actual_names != expected_names:
        raise WorldModelInputError(
            "coverage.per_state rows must follow state_definitions order"
        )
    return coverage


def build_world_model_input_pack(
    observations: Iterable[Observation],
    *,
    required_states: Iterable[RequiredWorldModelState],
    as_of: datetime,
) -> dict[str, Any]:
    """Build one complete as-of pack without fitting, filling, or writing."""

    cutoff = _utc(as_of, "as_of")
    states = _normalize_required_states(required_states)
    captured = tuple(observations)
    _validate_input_rows(captured, states, cutoff)

    rows_by_market: dict[str, list[Observation]] = {}
    for observation in captured:
        rows_by_market.setdefault(observation.market_id, []).append(observation)

    state_series: list[
        tuple[RequiredWorldModelState, Any, tuple[Observation, ...]]
    ] = []
    missing = []
    for state in states:
        market_rows = rows_by_market.get(state.market_id)
        if not market_rows:
            missing.append(state.state_name)
            continue
        try:
            panel = MarketPanel.from_observations(market_rows)
        except ValueError as exc:
            raise WorldModelInputError(
                f"cannot construct market panel for {state.market_id}: {exc}"
            ) from exc
        lookup = panel.lookup(state.semantic_role)
        if lookup.series is None:
            reason = lookup.reason or "required role is unavailable"
            if "ambiguous" in reason:
                raise WorldModelInputError(reason)
            missing.append(state.state_name)
            continue
        revisions = tuple(
            observation
            for observation in market_rows
            if observation.semantic_role is state.semantic_role
            and observation.instrument_id == lookup.series.instrument_id
        )
        state_series.append((state, lookup.series, revisions))
    if missing:
        raise WorldModelInputError(
            "missing required world-model states: " + ", ".join(sorted(missing))
        )

    reference_grid = tuple(item.event_time for item in state_series[0][1].observations)
    if not reference_grid:
        raise WorldModelInputError("required states have no usable event times")
    for state, series, _ in state_series[1:]:
        grid = tuple(item.event_time for item in series.observations)
        if grid != reference_grid:
            raise WorldModelInputError(
                f"required state {state.state_name!r} has a ragged event grid; "
                "imputation is forbidden"
            )

    state_definitions = [
        {
            "state_name": state.state_name,
            "market_id": state.market_id,
            "currency": series.observations[0].currency,
            "instrument_id": series.instrument_id,
            "semantic_role": state.semantic_role.value,
            "canonical_unit": series.unit.value,
        }
        for state, series, _ in state_series
    ]
    event_grid = [item.isoformat() for item in reference_grid]
    output_rows = []
    for state, _, revisions in state_series:
        revisions_by_event: dict[datetime, list[Observation]] = {}
        for observation in revisions:
            revisions_by_event.setdefault(observation.event_time, []).append(
                observation
            )
        for event_time in reference_grid:
            ordered = _ordered_revisions(revisions_by_event[event_time])
            output_rows.extend(
                _observation_record(state.state_name, observation, ordinal)
                for ordinal, observation in enumerate(ordered, start=1)
            )
    output_rows.sort(
        key=lambda item: (
            item["event_time"],
            item["state_name"],
            item["revision_ordinal"],
        )
    )
    body = {
        "schema": WORLD_MODEL_INPUT_SCHEMA,
        "as_of": cutoff.isoformat(),
        "policy": dict(_POLICY),
        "state_definitions": state_definitions,
        "event_grid": event_grid,
        "observations": output_rows,
        "coverage": _coverage(state_definitions, event_grid, output_rows),
    }
    pack = {**body, "pack_digest": canonical_world_model_input_digest(body)}
    return verify_world_model_input_pack(pack)


def build_world_model_input_pack_from_repository(
    repository: MarketRepository,
    *,
    required_states: Iterable[RequiredWorldModelState],
    as_of: datetime,
) -> dict[str, Any]:
    """Load every eligible as-of revision, then call the pure builder."""

    cutoff = _utc(as_of, "as_of")
    states = _normalize_required_states(required_states)
    roles_by_market: dict[str, set[SemanticRole]] = {}
    for state in states:
        roles_by_market.setdefault(state.market_id, set()).add(state.semantic_role)
    observations = []
    for market_id in sorted(roles_by_market):
        observations.extend(
            repository.load_observation_revisions_as_of(
                market_id,
                cutoff,
                event_time=cutoff,
                roles=tuple(
                    sorted(roles_by_market[market_id], key=lambda item: item.value)
                ),
            )
        )
    return build_world_model_input_pack(
        observations,
        required_states=states,
        as_of=cutoff,
    )


def _validated_state_definitions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise WorldModelInputError("state_definitions must be a non-empty array")
    definitions = []
    names = set()
    role_keys = set()
    for index, raw in enumerate(value):
        definition = _exact_object(raw, STATE_FIELDS, f"state_definitions[{index}]")
        name = definition["state_name"]
        if not isinstance(name, str) or _STATE_NAME_RE.fullmatch(name) is None:
            raise WorldModelInputError(
                f"state_definitions[{index}].state_name is invalid"
            )
        market_id = definition["market_id"]
        if not isinstance(market_id, str) or _MARKET_ID_RE.fullmatch(market_id) is None:
            raise WorldModelInputError(
                f"state_definitions[{index}].market_id is invalid"
            )
        _nonblank(definition["currency"], f"state_definitions[{index}].currency")
        _nonblank(
            definition["instrument_id"], f"state_definitions[{index}].instrument_id"
        )
        try:
            role = SemanticRole(definition["semantic_role"])
            CanonicalUnit(definition["canonical_unit"])
        except (TypeError, ValueError) as exc:
            raise WorldModelInputError(
                f"state_definitions[{index}] has an unknown role or unit"
            ) from exc
        if name in names:
            raise WorldModelInputError("state definition names must be unique")
        role_key = (market_id, role)
        if role_key in role_keys:
            raise WorldModelInputError(
                "state definitions cannot repeat a market/role pair"
            )
        names.add(name)
        role_keys.add(role_key)
        definitions.append(definition)
    if [item["state_name"] for item in definitions] != sorted(names):
        raise WorldModelInputError("state_definitions must be sorted by state_name")
    return definitions


def _validated_event_grid(value: Any, as_of: datetime) -> list[str]:
    if not isinstance(value, list) or not value:
        raise WorldModelInputError("event_grid must be a non-empty array")
    parsed = [
        _timestamp(item, f"event_grid[{index}]") for index, item in enumerate(value)
    ]
    if any(right <= left for left, right in zip(parsed, parsed[1:], strict=False)):
        raise WorldModelInputError("event_grid must be strictly increasing")
    if parsed[-1] > as_of:
        raise WorldModelInputError("event_grid cannot extend beyond as_of")
    return value


def _validated_observations(
    value: Any,
    definitions: list[dict[str, Any]],
    event_grid: list[str],
    as_of: datetime,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise WorldModelInputError("observations must be a non-empty array")
    by_name = {item["state_name"]: item for item in definitions}
    state_events: dict[str, set[str]] = {name: set() for name in by_name}
    identities = set()
    revision_groups: dict[str, list[tuple[dict[str, Any], Observation]]] = {}
    order = []
    validated = []
    for index, raw in enumerate(value):
        row = _exact_object(raw, OBSERVATION_FIELDS, f"observations[{index}]")
        name = row["state_name"]
        if not isinstance(name, str) or _STATE_NAME_RE.fullmatch(name) is None:
            raise WorldModelInputError(f"observations[{index}].state_name is invalid")
        definition = by_name.get(name)
        if definition is None:
            raise WorldModelInputError(
                f"observations[{index}] names an undeclared state"
            )
        observation_id = row["observation_id"]
        if (
            not isinstance(observation_id, str)
            or _OBSERVATION_ID_RE.fullmatch(observation_id) is None
        ):
            raise WorldModelInputError(
                f"observations[{index}].observation_id is invalid"
            )
        ordinal = row["revision_ordinal"]
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal <= 0:
            raise WorldModelInputError(
                f"observations[{index}].revision_ordinal must be a positive integer"
            )
        if not isinstance(row["jurisdiction_codes"], list):
            raise WorldModelInputError(
                f"observations[{index}].jurisdiction_codes must be an array"
            )
        for field in (
            "market_id",
            "monetary_area_id",
            "currency",
            "instrument_id",
            "semantic_role",
            "canonical_unit",
            "revision_id",
            "source",
            "evidence_hash",
            "connector_classification",
            "redistribution_status",
            "quality",
            "staleness",
        ):
            _nonblank(row[field], f"observations[{index}].{field}")
        for field in ("event_time", "source_publication_time", "knowledge_time"):
            _timestamp(row[field], f"observations[{index}].{field}")
        if not isinstance(row["value"], str) or row["value"] != _canonical_decimal(
            row["value"]
        ):
            raise WorldModelInputError(
                f"observations[{index}].value must be a canonical decimal string"
            )
        if (row["rate_compounding"] is None) != (row["day_count"] is None):
            raise WorldModelInputError(
                f"observations[{index}] must carry both rate conventions or neither"
            )
        raw_observation = {
            key: item
            for key, item in row.items()
            if key not in {"state_name", "observation_id", "revision_ordinal"}
        }
        try:
            observation = Observation.from_record(raw_observation)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise WorldModelInputError(
                f"observations[{index}] is not a canonical Observation: {exc}"
            ) from exc
        canonical_record = observation.to_record()
        canonical_record["value"] = _canonical_decimal(observation.value)  # type: ignore[arg-type]
        if raw_observation != canonical_record:
            raise WorldModelInputError(
                f"observations[{index}] does not use canonical Observation encoding"
            )
        if observation.redistribution_status is not RedistributionStatus.ALLOWED:
            raise WorldModelInputError(
                f"observations[{index}].redistribution_status must be 'allowed' "
                "before raw values can leave Seiche"
            )
        if not observation.usable:
            raise WorldModelInputError(f"observations[{index}] is unusable")
        if observation.event_time > as_of:
            raise WorldModelInputError(
                f"observations[{index}].event_time follows as_of"
            )
        if observation.source_publication_time > as_of:
            raise WorldModelInputError(
                f"observations[{index}].source_publication_time follows as_of"
            )
        if observation.knowledge_time > as_of:
            raise WorldModelInputError(
                f"observations[{index}].knowledge_time follows as_of"
            )
        if observation.knowledge_time < observation.event_time:
            raise WorldModelInputError(
                f"observations[{index}].knowledge_time precedes event_time"
            )
        expected_identity = {
            "market_id": definition["market_id"],
            "currency": definition["currency"],
            "instrument_id": definition["instrument_id"],
            "semantic_role": definition["semantic_role"],
            "canonical_unit": definition["canonical_unit"],
        }
        for field, expected in expected_identity.items():
            if row[field] != expected:
                raise WorldModelInputError(
                    f"observations[{index}].{field} does not match its state definition"
                )
        expected_observation_id = world_model_observation_id(observation)
        if observation_id != expected_observation_id:
            raise WorldModelInputError(
                f"observations[{index}].observation_id does not match its event slot"
            )
        identity = observation.identity
        if identity in identities:
            raise WorldModelInputError("observations repeat a canonical identity")
        identities.add(identity)
        revision_groups.setdefault(observation_id, []).append((row, observation))
        state_events[name].add(row["event_time"])
        order.append((row["event_time"], name, ordinal))
        validated.append(row)
    if order != sorted(order):
        raise WorldModelInputError(
            "observations must be sorted by event_time, state_name, then revision_ordinal"
        )
    for observation_id, revisions in revision_groups.items():
        sources = {observation.source for _, observation in revisions}
        if len(sources) != 1:
            raise WorldModelInputError(
                f"source conflict across revisions for {observation_id}"
            )
        knowledge_times = [observation.knowledge_time for _, observation in revisions]
        if len(knowledge_times) != len(set(knowledge_times)):
            raise WorldModelInputError(
                f"ambiguous same-knowledge revision tie for {observation_id}"
            )
        revision_ids = [observation.revision_id for _, observation in revisions]
        if len(revision_ids) != len(set(revision_ids)):
            raise WorldModelInputError(
                f"duplicate revision_id values for {observation_id}"
            )
        ordered = sorted(
            revisions,
            key=lambda item: (
                item[1].knowledge_time,
                item[1].source_publication_time,
                item[1].revision_id,
            ),
        )
        expected_ordinals = list(range(1, len(ordered) + 1))
        actual_ordinals = [row["revision_ordinal"] for row, _ in ordered]
        if actual_ordinals != expected_ordinals:
            raise WorldModelInputError(
                f"revision_ordinal values are not contiguous for {observation_id}"
            )
        shapes = {
            (
                observation.monetary_area_id,
                observation.currency,
                observation.semantic_role,
                observation.canonical_unit,
                observation.rate_compounding,
                observation.day_count,
            )
            for _, observation in revisions
        }
        if len(shapes) != 1:
            raise WorldModelInputError(
                f"mixed role/unit semantics across revisions for {observation_id}"
            )
    for name, events in state_events.items():
        if sorted(events) != event_grid:
            raise WorldModelInputError(
                f"required state {name!r} has a ragged event grid; imputation is forbidden"
            )
    return validated


def verify_world_model_input_pack(pack: Mapping[str, Any]) -> dict[str, Any]:
    """Strictly verify a decoded v1 pack and return the same plain object."""

    top = _exact_object(pack, PACK_FIELDS, "world-model input pack")
    if top["schema"] != WORLD_MODEL_INPUT_SCHEMA:
        raise WorldModelInputError(
            f"schema must be exactly {WORLD_MODEL_INPUT_SCHEMA!r}"
        )
    digest = top["pack_digest"]
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        raise WorldModelInputError("pack_digest must be a lowercase SHA-256 value")
    expected_digest = canonical_world_model_input_digest(top)
    if not hmac.compare_digest(digest, expected_digest):
        raise WorldModelInputError("pack_digest does not match canonical pack content")

    policy = _exact_object(top["policy"], POLICY_FIELDS, "policy")
    for field in ("forward_evidence_eligible", "can_publish", "can_execute"):
        if type(policy[field]) is not bool:
            raise WorldModelInputError(f"policy.{field} must be a boolean")
    if policy != _POLICY:
        raise WorldModelInputError(
            "policy must remain a retrospective research export, ineligible "
            "as forward evidence, with no imputation, publication, or execution authority"
        )
    as_of = _timestamp(top["as_of"], "as_of")
    definitions = _validated_state_definitions(top["state_definitions"])
    event_grid = _validated_event_grid(top["event_grid"], as_of)
    observations = _validated_observations(
        top["observations"], definitions, event_grid, as_of
    )
    coverage = _validated_coverage(top["coverage"], definitions, as_of)
    expected_coverage = _coverage(definitions, event_grid, observations)
    if coverage != expected_coverage:
        raise WorldModelInputError("coverage does not reproduce from pack observations")
    if not coverage["complete"] or coverage["missing_state_event_count"] != 0:
        raise WorldModelInputError("required state coverage must be complete")
    return top


def world_model_input_pack_json(pack: Mapping[str, Any]) -> str:
    """Verify and serialize the complete pack as deterministic UTF-8 JSON text."""

    verified = verify_world_model_input_pack(pack)
    return json.dumps(
        verified,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
