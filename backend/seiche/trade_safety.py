"""Cache-only Seiche context for external trade-safety evaluators.

This module deliberately projects a completed board; it never obtains one.
Callers must pass the value returned by ``mcp_server._get_in_memory_completed_snapshot``
so an order-path read cannot collect data, fit a model, touch an attestation
ledger, call a broker, or otherwise turn context retrieval into a side effect.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import Any


RISK_CONTEXT_SCHEMA = "seiche.risk-context.v1"
RISK_CONTEXT_URL = "https://api.seiche.info/api/trade-safety/risk-context"
REGIMES = frozenset({"CALM", "EROSION", "STRAIN", "STRESS"})
STALENESS_STATES = ("fresh", "aging", "stale", "dead", "unknown")

_ATTESTATION_LIMITATION = "attestation_ledger_not_evaluated_by_this_projection"
_ATTESTATION_DISCLOSURE = (
    "This cache-only projection does not read or evaluate Seiche's attestation "
    "ledger. Verify stream attestations separately; even a verified stream "
    "attestation is not per-order execution authority."
)
_LIMITATIONS = (
    "public_metadata_context_only_not_licensed_for_real_money_execution",
    "not_order_bound_and_cannot_authorize_or_route_an_order",
    "evidence_as_of_is_the_oldest_valid_observation_clock_in_public_provenance",
    "bounded_next_day_effective_values_use_their_prior_collection_clock",
    "rows_without_observation_clocks_remain_unknown_and_are_not_treated_as_current",
    _ATTESTATION_LIMITATION,
    "stream_attestation_is_not_per_order_execution_authority",
    "projection_sha256_is_a_server_internal_change_detector_not_authentication",
)


def _utc(value: object) -> datetime | None:
    """Parse an aware ISO timestamp and normalize it to whole-second UTC."""

    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if len(text) == 10:
        try:
            parsed_date = date.fromisoformat(text)
        except ValueError:
            return None
        return datetime.combine(parsed_date, time.min, tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC).replace(microsecond=0)


def _utc_text(value: datetime) -> str:
    return (
        value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _evidence_clock(
    row: Mapping[str, Any], *, snapshot_at: datetime
) -> datetime | None:
    """Return the clock a provenance row can contribute to evidence age.

    A date-only value exactly one UTC day after the completed snapshot can be
    a pre-announced effective value, such as IORB. It is admissible only when
    the row also carries an aware collection timestamp no later than the
    snapshot. The collection timestamp, not the future effective date, then
    contributes to the public evidence clock.
    """

    raw_asof = row.get("asof")
    observation_at = _utc(raw_asof)
    if observation_at is None:
        return None
    if observation_at <= snapshot_at:
        return observation_at

    if not isinstance(raw_asof, str) or len(raw_asof.strip()) != 10:
        return None
    try:
        effective_date = date.fromisoformat(raw_asof.strip())
    except ValueError:
        return None
    if effective_date != snapshot_at.date() + timedelta(days=1):
        return None

    raw_fetched_at = row.get("fetched_at")
    if not isinstance(raw_fetched_at, str) or len(raw_fetched_at.strip()) == 10:
        return None
    fetched_at = _utc(raw_fetched_at)
    if fetched_at is None or fetched_at > snapshot_at:
        return None
    return fetched_at


def _finite_percentage(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 100.0:
        return None
    return number


def _provenance_rows(value: object) -> list[Mapping[str, Any]] | None:
    if isinstance(value, Mapping):
        rows = list(value.values())
    elif isinstance(value, list):
        rows = value
    else:
        return None
    if not rows or not all(isinstance(row, Mapping) for row in rows):
        return None
    return rows


def _staleness(
    rows: list[Mapping[str, Any]], *, evaluation_at: datetime
) -> dict[str, int]:
    counts = Counter()
    for row in rows:
        if row.get("asof") is None:
            counts["unknown"] += 1
            continue
        observation_at = _utc(row.get("asof"))
        grace = row.get("freshness_grace_days")
        if (
            observation_at is not None
            and not isinstance(grace, bool)
            and isinstance(grace, (int, float))
            and math.isfinite(float(grace))
            and float(grace) >= 0
        ):
            age_days = max(0, (evaluation_at.date() - observation_at.date()).days)
            if age_days <= grace:
                state = "fresh"
            elif age_days <= grace * 2:
                state = "aging"
            elif age_days <= grace * 6:
                state = "stale"
            else:
                state = "dead"
        else:
            # A cached label has no safe meaning at a later evaluation time
            # unless its cadence/grace rule is available to recompute it.
            state = "unknown"
        counts[state if state in STALENESS_STATES[:-1] else "unknown"] += 1
    return {
        **{state: counts[state] for state in STALENESS_STATES},
        "total": len(rows),
    }


def _attestation_boundary() -> dict[str, Any]:
    return {
        "status": "not_evaluated",
        "ed25519_status": "not_evaluated",
        "ots_status": "not_evaluated",
        "bitcoin_anchor_claimed": False,
        "ledger_read": False,
        "reason": _ATTESTATION_LIMITATION,
        "disclosure": _ATTESTATION_DISCLOSURE,
    }


def _base(*, ok: bool, status: str, reason: str | None) -> dict[str, Any]:
    return {
        "ok": ok,
        "schema": RISK_CONTEXT_SCHEMA,
        "status": status,
        "reason": reason,
        "state": "context_only" if ok else "unavailable",
        "evidence_class": "derived" if ok else "unavailable",
        "rights_status": "metadata_only",
        "context_only": True,
        "executable": False,
        "executable_quote": False,
        "real_money_eligible": False,
        "can_authorize_order": False,
        "projection_mode": "cache_only",
        "request_time_collection": False,
        "request_time_model_fitting": False,
        "request_time_network": False,
        "request_time_notary": False,
        "request_time_broker": False,
        "attestation_state": "not_evaluated",
        "source_url": RISK_CONTEXT_URL,
        "source_snapshot_version": None,
        "regime": None,
        "stress_index": None,
        "coverage_pct": None,
        "fault_count": None,
        "staleness": {**{state: 0 for state in STALENESS_STATES}, "total": 0},
        "clocks": {
            "snapshot_generated_at": None,
            "evidence_as_of": None,
            "evaluated_at": None,
            "snapshot_age_seconds": None,
            "evidence_age_seconds": None,
            "basis": (
                "oldest valid public provenance evidence clock; bounded next-day "
                "date-only effective values use their prior collection time"
            ),
        },
        "attestation": _attestation_boundary(),
        "limitations": list(_LIMITATIONS),
        "disclaimer": "Research context only; not investment advice.",
    }


def _seal(payload: dict[str, Any]) -> dict[str, Any]:
    """Bind the deterministic projection without implying signed authority."""

    sealed = {
        **payload,
        "canonicalization": ("python-json-sort-keys-utf8-no-nan-server-internal-v1"),
    }
    canonical = json.dumps(
        sealed,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        **sealed,
        "projection_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def unavailable(reason: str) -> dict[str, Any]:
    """Return the stable fail-closed envelope without snapshot-derived facts."""

    return _seal(_base(ok=False, status="unavailable", reason=reason))


def project(
    snapshot: object,
    *,
    evaluation_at: datetime,
) -> dict[str, Any]:
    """Project one completed Seiche snapshot into non-executable risk context.

    ``evaluation_at`` is injected so clock validation is deterministic in tests
    and callers cannot accidentally validate a future snapshot against its own
    publication timestamp.
    """

    if evaluation_at.tzinfo is None or evaluation_at.utcoffset() is None:
        raise ValueError("evaluation_at must be timezone-aware")
    evaluated = evaluation_at.astimezone(UTC).replace(microsecond=0)
    if not isinstance(snapshot, dict):
        return unavailable("no_completed_snapshot")

    # The publication rights walker is deliberately defined over JSON-native
    # dict/list trees. Normalize the completed cache before invoking it so a
    # custom Mapping implementation cannot conceal a restricted identity from
    # the recursive check. This is an in-memory copy only: no cache or ledger is
    # read or written, and non-serializable/infinite cache state fails closed.
    try:
        snapshot = json.loads(
            json.dumps(snapshot, allow_nan=False, ensure_ascii=False, sort_keys=True)
        )
    except (TypeError, ValueError, OverflowError):
        return unavailable("invalid_completed_snapshot")

    # Repeat the publication boundary at the adapter edge. A cache that was
    # valid when restored can still be poisoned in memory by a caller or old
    # process; a trade-safety consumer must not inherit that trust implicitly.
    from seiche import assemble

    try:
        assemble._assert_snapshot_rights(snapshot)
    except (TypeError, ValueError):
        return unavailable("snapshot_rights_validation_failed")

    snapshot_at = _utc(snapshot.get("generated_at"))
    if snapshot_at is None or snapshot_at > evaluated:
        return unavailable("invalid_snapshot_clock")

    version = snapshot.get("version")
    engines = snapshot.get("engines")
    composite = engines.get("composite") if isinstance(engines, Mapping) else None
    if (
        not isinstance(version, str)
        or not version.strip()
        or not isinstance(composite, Mapping)
        or composite.get("ok") is not True
    ):
        return unavailable("invalid_completed_snapshot")

    regime = composite.get("regime")
    stress_index = _finite_percentage(composite.get("value"))
    coverage_pct = _finite_percentage(composite.get("coverage_pct"))
    if regime not in REGIMES or stress_index is None or coverage_pct is None:
        return unavailable("invalid_composite_reading")

    faults = snapshot.get("faults")
    rows = _provenance_rows(snapshot.get("provenance"))
    if (
        not isinstance(faults, list)
        or not all(isinstance(fault, Mapping) for fault in faults)
        or rows is None
    ):
        return unavailable("invalid_completed_snapshot")

    evidence_dates: list[datetime] = []
    for row in rows:
        raw_asof = row.get("asof")
        if raw_asof is None:
            continue
        evidence_clock = _evidence_clock(row, snapshot_at=snapshot_at)
        if evidence_clock is None:
            return unavailable("invalid_evidence_clock")
        evidence_dates.append(evidence_clock)
    if not evidence_dates:
        return unavailable("evidence_clock_unavailable")

    evidence_at = min(evidence_dates)
    payload = _base(ok=True, status="available", reason=None)
    payload.update(
        source_snapshot_version=version.strip(),
        regime=regime,
        stress_index=stress_index,
        coverage_pct=coverage_pct,
        fault_count=len(faults),
        staleness=_staleness(rows, evaluation_at=evaluated),
        clocks={
            "snapshot_generated_at": _utc_text(snapshot_at),
            "evidence_as_of": _utc_text(evidence_at),
            "evaluated_at": _utc_text(evaluated),
            "snapshot_age_seconds": int((evaluated - snapshot_at).total_seconds()),
            "evidence_age_seconds": int((evaluated - evidence_at).total_seconds()),
            "basis": (
                "oldest valid public provenance evidence clock; bounded next-day "
                "date-only effective values use their prior collection time; rows "
                "without observation clocks remain unknown"
            ),
        },
    )

    # Validate the deliberately small output too. This is inexpensive and
    # prevents a future field addition from re-opening a restricted identity.
    try:
        assemble._assert_snapshot_rights(payload)
    except (TypeError, ValueError):
        return unavailable("projection_rights_validation_failed")
    return _seal(payload)
