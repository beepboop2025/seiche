"""Independent integrity and maturity checks for forward paper records.

The verifier deliberately knows nothing about validation evidence artifacts or
model outcomes.  It answers two narrower questions: is each selected
market/product hash chain intact, and has the caller-requested amount of
forward history accrued on the hash-bound knowledge timeline?
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from seiche.domain.forward_record import forward_record_hash


GENESIS_HASH = "0" * 64

_FORWARD_RECORD_FIELDS = (
    "record_id",
    "snapshot_id",
    "market_id",
    "product",
    "event_cutoff",
    "knowledge_cutoff",
    "calibration_id",
    "created_at",
    "payload_hash",
    "previous_record_hash",
    "record_hash",
    "payload",
)


class ForwardRecordReader(Protocol):
    def load_forward_records(
        self,
        market_id: str | None = None,
        product: str | None = None,
    ) -> list[dict]: ...


def _minimum(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _timestamp(
    value: object,
    *,
    second_precision: bool = True,
) -> tuple[datetime, str]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise TypeError("timestamp must be an ISO string or datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    normalized = parsed.astimezone(UTC)
    if second_precision:
        normalized = normalized.replace(microsecond=0)
    return normalized, normalized.isoformat()


def _payload_hash(payload: object) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _record_hash(
    record: Mapping[str, object],
    *,
    event_cutoff: str,
    knowledge_cutoff: str,
    payload_hash: str,
) -> str:
    return forward_record_hash(
        snapshot_id=record["snapshot_id"],  # type: ignore[arg-type]
        market_id=record["market_id"],  # type: ignore[arg-type]
        product=record["product"],  # type: ignore[arg-type]
        event_cutoff=event_cutoff,
        knowledge_cutoff=knowledge_cutoff,
        calibration_id=record["calibration_id"],  # type: ignore[arg-type]
        payload_hash=payload_hash,
        previous_record_hash=record["previous_record_hash"],  # type: ignore[arg-type]
    )


def _result(
    status: str,
    metrics: dict[str, Any],
    issues: Iterable[tuple[str, str]],
) -> dict[str, Any]:
    ordered = sorted(set(issues))
    return {
        "status": status,
        "metrics": metrics,
        "reason_codes": sorted({code for code, _ in ordered}),
        "reasons": [message for _, message in ordered],
    }


def verify_forward_chain(
    records: Iterable[Mapping[str, object]],
    *,
    minimum_records: int,
    minimum_span_days: int,
) -> dict[str, Any]:
    """Verify forward records and apply caller-owned history thresholds.

    Integrity defects are ``FAIL``.  An intact chain with no records or with
    less than either requested threshold is ``PENDING``.  Only an intact,
    sufficiently mature chain is ``PASS``.  Passing says nothing about the
    predictive quality of the payloads.
    """

    required_records = _minimum("minimum_records", minimum_records)
    required_span = _minimum("minimum_span_days", minimum_span_days)
    rows = tuple(records)
    base_metrics: dict[str, Any] = {
        "record_count": len(rows),
        "chain_count": 0,
        "minimum_records": required_records,
        "minimum_span_days": required_span,
        "minimum_chain_record_count": 0,
        "minimum_chain_span_days": 0,
        "payload_hash_mismatches": 0,
        "record_hash_mismatches": 0,
        "record_id_mismatches": 0,
        "link_mismatches": 0,
        "malformed_record_defects": 0,
        "chains": [],
    }
    if not rows:
        return _result(
            "PENDING",
            base_metrics,
            (("NO_FORWARD_RECORDS", "no forward-validation records are available"),),
        )

    issues: list[tuple[str, str]] = []
    prepared: list[dict[str, Any]] = []
    seen_record_ids: set[str] = set()
    for position, record in enumerate(rows):
        if not isinstance(record, Mapping):
            base_metrics["malformed_record_defects"] += 1
            issues.append(
                (
                    "MALFORMED_FORWARD_RECORD",
                    f"record at position {position} is not a mapping",
                )
            )
            continue
        missing = [field for field in _FORWARD_RECORD_FIELDS if field not in record]
        if missing:
            base_metrics["malformed_record_defects"] += 1
            issues.append(
                (
                    "MALFORMED_FORWARD_RECORD",
                    f"record at position {position} is missing {','.join(missing)}",
                )
            )
            continue

        record_id = str(record["record_id"])
        market_id = str(record["market_id"])
        product = str(record["product"])
        label = f"{market_id}/{product}/{record_id}"
        if record_id in seen_record_ids:
            base_metrics["malformed_record_defects"] += 1
            issues.append(
                ("DUPLICATE_RECORD_ID", f"{label}: record_id is duplicated")
            )
        seen_record_ids.add(record_id)
        if market_id != market_id.upper():
            base_metrics["malformed_record_defects"] += 1
            issues.append(
                (
                    "NONCANONICAL_MARKET_ID",
                    f"{label}: market_id is not uppercase canonical form",
                )
            )

        parsed_timestamps: dict[str, datetime] = {}
        canonical_timestamps: dict[str, str] = {}
        timestamp_error = False
        for field in ("event_cutoff", "knowledge_cutoff", "created_at"):
            try:
                parsed, canonical = _timestamp(
                    record[field],
                    second_precision=field != "created_at",
                )
            except (TypeError, ValueError, OverflowError) as exc:
                timestamp_error = True
                base_metrics["malformed_record_defects"] += 1
                issues.append(
                    (
                        "INVALID_FORWARD_TIMESTAMP",
                        f"{label}: {field} is invalid ({exc})",
                    )
                )
            else:
                parsed_timestamps[field] = parsed
                canonical_timestamps[field] = canonical
        if timestamp_error:
            continue
        if parsed_timestamps["event_cutoff"] > parsed_timestamps["knowledge_cutoff"]:
            base_metrics["malformed_record_defects"] += 1
            issues.append(
                (
                    "EVENT_AFTER_KNOWLEDGE_CUTOFF",
                    f"{label}: event_cutoff follows knowledge_cutoff",
                )
            )

        try:
            computed_payload_hash = _payload_hash(record["payload"])
        except (TypeError, ValueError, OverflowError) as exc:
            base_metrics["malformed_record_defects"] += 1
            issues.append(
                (
                    "INVALID_FORWARD_PAYLOAD",
                    f"{label}: payload cannot be canonically hashed ({exc})",
                )
            )
            continue
        if record["payload_hash"] != computed_payload_hash:
            base_metrics["payload_hash_mismatches"] += 1
            issues.append(
                (
                    "PAYLOAD_HASH_MISMATCH",
                    f"{label}: stored payload_hash does not match the payload",
                )
            )

        try:
            computed_record_hash = _record_hash(
                record,
                event_cutoff=canonical_timestamps["event_cutoff"],
                knowledge_cutoff=canonical_timestamps["knowledge_cutoff"],
                payload_hash=computed_payload_hash,
            )
        except (TypeError, ValueError) as exc:
            base_metrics["malformed_record_defects"] += 1
            issues.append(
                (
                    "UNSAFE_FORWARD_IDENTITY_FIELD",
                    f"{label}: identity fields are invalid ({exc})",
                )
            )
            continue
        if record["record_hash"] != computed_record_hash:
            base_metrics["record_hash_mismatches"] += 1
            issues.append(
                (
                    "RECORD_HASH_MISMATCH",
                    f"{label}: stored record_hash does not recompute",
                )
            )
        if record_id != record["record_hash"]:
            base_metrics["record_id_mismatches"] += 1
            issues.append(
                (
                    "RECORD_ID_MISMATCH",
                    f"{label}: record_id does not equal record_hash",
                )
            )
        prepared.append(
            {
                "record": record,
                "record_id": record_id,
                "market_id": market_id,
                "product": product,
                "created_at": parsed_timestamps["created_at"],
                "knowledge_cutoff": parsed_timestamps["knowledge_cutoff"],
            }
        )

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in prepared:
        groups[(item["market_id"], item["product"])].append(item)

    chain_metrics: list[dict[str, Any]] = []
    for (market_id, product), chain in sorted(groups.items()):
        chain.sort(key=lambda item: (item["created_at"], item["record_id"]))
        expected_previous = GENESIS_HASH
        for offset, item in enumerate(chain):
            record = item["record"]
            if record["previous_record_hash"] != expected_previous:
                base_metrics["link_mismatches"] += 1
                code = "CHAIN_START_MISMATCH" if offset == 0 else "CHAIN_LINK_MISMATCH"
                issues.append(
                    (
                        code,
                        f"{market_id}/{product}/{item['record_id']}: "
                        "previous_record_hash does not identify the prior link",
                    )
                )
            expected_previous = str(record["record_hash"])
        knowledge_times = [item["knowledge_cutoff"] for item in chain]
        first_knowledge = min(knowledge_times)
        last_knowledge = max(knowledge_times)
        span_days = max(0, (last_knowledge - first_knowledge).days)
        chain_metrics.append(
            {
                "market_id": market_id,
                "product": product,
                "record_count": len(chain),
                "knowledge_span_days": span_days,
                "first_knowledge_cutoff": first_knowledge.isoformat(),
                "last_knowledge_cutoff": last_knowledge.isoformat(),
                "head_record_hash": expected_previous,
            }
        )

    base_metrics["chain_count"] = len(chain_metrics)
    base_metrics["chains"] = chain_metrics
    if chain_metrics:
        base_metrics["minimum_chain_record_count"] = min(
            item["record_count"] for item in chain_metrics
        )
        base_metrics["minimum_chain_span_days"] = min(
            item["knowledge_span_days"] for item in chain_metrics
        )

    if issues:
        return _result("FAIL", base_metrics, issues)

    pending: list[tuple[str, str]] = []
    for chain in chain_metrics:
        label = f"{chain['market_id']}/{chain['product']}"
        if chain["record_count"] < required_records:
            pending.append(
                (
                    "INSUFFICIENT_FORWARD_RECORDS",
                    f"{label}: {chain['record_count']} records; {required_records} required",
                )
            )
        if chain["knowledge_span_days"] < required_span:
            pending.append(
                (
                    "INSUFFICIENT_FORWARD_SPAN",
                    f"{label}: {chain['knowledge_span_days']} knowledge-span days; "
                    f"{required_span} required",
                )
            )
    return _result("PENDING" if pending else "PASS", base_metrics, pending)


def verify_repository_forward_chain(
    repository: ForwardRecordReader,
    *,
    market_id: str | None = None,
    product: str | None = None,
    minimum_records: int,
    minimum_span_days: int,
) -> dict[str, Any]:
    """Load and verify a repository selection without evidence-layer coupling."""

    return verify_forward_chain(
        repository.load_forward_records(market_id=market_id, product=product),
        minimum_records=minimum_records,
        minimum_span_days=minimum_span_days,
    )


__all__ = [
    "GENESIS_HASH",
    "ForwardRecordReader",
    "verify_forward_chain",
    "verify_repository_forward_chain",
]
