"""Independent integrity and maturity checks for forward paper records.

The verifier deliberately knows nothing about validation evidence artifacts or
model outcomes.  It answers two narrower questions: is each selected
market/product hash chain intact, and has the caller-requested amount of
forward history accrued on the hash-bound knowledge timeline?
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from seiche.domain.forward_record import (
    FORWARD_RECORD_GENESIS_HASH,
    analyze_forward_topology,
    forward_chain_generation,
    forward_record_hash,
)


GENESIS_HASH = FORWARD_RECORD_GENESIS_HASH

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


@dataclass(frozen=True)
class _LegacyJsonbSignedZeroCompatibility:
    """One audited JSONB representation loss in an immutable v1 record."""

    snapshot_id: str
    market_id: str
    product: str
    event_cutoff: str
    knowledge_cutoff: str
    calibration_id: str
    created_at: str
    payload_hash: str
    decoded_payload_hash: str
    previous_record_hash: str
    json_path: tuple[str | int, ...]
    json_pointer: str


# PostgreSQL JSONB normalized ``components[0].kernel.value`` from ``-0.0`` to
# ``0.0`` after this record's canonical preimage had already been hashed.  The
# exact signed-zero candidate reproduces payload_hash, and payload_hash in turn
# reproduces record_id.  Keep this declaration identity-bound: any future
# mismatch remains a hard failure until it is independently audited.
_LEGACY_JSONB_SIGNED_ZERO_COMPATIBILITY = {
    "35baac2858797c1673f23fcab723b8d40ef31945737a1c18f35753b51b23eb94": (
        _LegacyJsonbSignedZeroCompatibility(
            snapshot_id=(
                "975fc5531c444b4123e058d29633fbecca95cd0100c2774e1b3943aeedc0fb7a"
            ),
            market_id="HK-HKD",
            product="gauge",
            event_cutoff="2026-08-11T00:00:00+00:00",
            knowledge_cutoff="2026-08-11T16:41:17+00:00",
            calibration_id="hk-hkd-local-forward-v1",
            created_at="2026-08-11T16:41:18.087325+00:00",
            payload_hash=(
                "8a68b214f624e63406bdc1005dab46fdd86bca740fb98233b2c8aaf1dfbb61ea"
            ),
            decoded_payload_hash=(
                "c18a56ed0f58a2ff22daa829f8894714f1e104180448aaa10199296ee6a45722"
            ),
            previous_record_hash=(
                "38f929072db4c6cc157ccf5466a02f4967450ff9d8c4fa8fbcb9565a3721640e"
            ),
            json_path=("components", 0, "kernel", "value"),
            json_pointer="/components/0/kernel/value",
        )
    )
}


class ForwardRecordReader(Protocol):
    def load_forward_records(
        self,
        market_id: str | None = None,
        product: str | None = None,
        calibration_id: str | None = None,
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


def _legacy_jsonb_signed_zero_match(
    record: Mapping[str, object],
    *,
    event_cutoff: str,
    knowledge_cutoff: str,
    created_at: str,
    decoded_payload_hash: str,
) -> str | None:
    """Return the audited JSON pointer only for the exact known HK record."""

    record_id = str(record.get("record_id", ""))
    compatibility = _LEGACY_JSONB_SIGNED_ZERO_COMPATIBILITY.get(record_id)
    if compatibility is None:
        return None
    expected_fields = (
        compatibility.snapshot_id,
        compatibility.market_id,
        compatibility.product,
        compatibility.event_cutoff,
        compatibility.knowledge_cutoff,
        compatibility.calibration_id,
        compatibility.created_at,
        compatibility.payload_hash,
        compatibility.previous_record_hash,
        record_id,
        compatibility.decoded_payload_hash,
    )
    observed_fields = (
        record.get("snapshot_id"),
        record.get("market_id"),
        record.get("product"),
        event_cutoff,
        knowledge_cutoff,
        record.get("calibration_id"),
        created_at,
        record.get("payload_hash"),
        record.get("previous_record_hash"),
        record.get("record_hash"),
        decoded_payload_hash,
    )
    if observed_fields != expected_fields:
        return None

    try:
        candidate = copy.deepcopy(record.get("payload"))
        parent = candidate
        for part in compatibility.json_path[:-1]:
            parent = parent[part]  # type: ignore[index]
        leaf = compatibility.json_path[-1]
        value = parent[leaf]  # type: ignore[index]
        if not isinstance(value, float) or value != 0.0:
            return None
        parent[leaf] = -0.0  # type: ignore[index]
        candidate_hash = _payload_hash(candidate)
    except (KeyError, IndexError, TypeError, ValueError, OverflowError):
        return None
    if not hmac.compare_digest(candidate_hash, compatibility.payload_hash):
        return None
    return compatibility.json_pointer


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
        "legacy_jsonb_signed_zero_compatibility_records": 0,
        "legacy_jsonb_signed_zero_compatibility": [],
        "link_mismatches": 0,
        "fork_defects": 0,
        "missing_predecessor_defects": 0,
        "cycle_defects": 0,
        "orphaned_records": 0,
        "root_defects": 0,
        "head_defects": 0,
        "duplicate_record_hashes": 0,
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
        calibration_id = record["calibration_id"]
        label = f"{market_id}/{product}/{record_id}"
        if record_id in seen_record_ids:
            base_metrics["malformed_record_defects"] += 1
            issues.append(("DUPLICATE_RECORD_ID", f"{label}: record_id is duplicated"))
        seen_record_ids.add(record_id)
        if market_id != market_id.upper():
            base_metrics["malformed_record_defects"] += 1
            issues.append(
                (
                    "NONCANONICAL_MARKET_ID",
                    f"{label}: market_id is not uppercase canonical form",
                )
            )

        try:
            expected_generation = forward_chain_generation(calibration_id)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            base_metrics["malformed_record_defects"] += 1
            issues.append(
                (
                    "INVALID_CHAIN_GENERATION",
                    f"{label}: calibration_id has no valid chain generation ({exc})",
                )
            )
            continue
        stored_generation = record.get("chain_generation", expected_generation)
        if (
            isinstance(stored_generation, bool)
            or not isinstance(stored_generation, int)
            or stored_generation != expected_generation
        ):
            base_metrics["malformed_record_defects"] += 1
            issues.append(
                (
                    "CHAIN_GENERATION_MISMATCH",
                    f"{label}: stored chain_generation does not match calibration_id",
                )
            )
            continue

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
        signed_zero_pointer = None
        if record["payload_hash"] != computed_payload_hash:
            signed_zero_pointer = _legacy_jsonb_signed_zero_match(
                record,
                event_cutoff=canonical_timestamps["event_cutoff"],
                knowledge_cutoff=canonical_timestamps["knowledge_cutoff"],
                created_at=canonical_timestamps["created_at"],
                decoded_payload_hash=computed_payload_hash,
            )
        if (
            record["payload_hash"] != computed_payload_hash
            and not signed_zero_pointer
        ):
            base_metrics["payload_hash_mismatches"] += 1
            issues.append(
                (
                    "PAYLOAD_HASH_MISMATCH",
                    f"{label}: stored payload_hash does not match the payload",
                )
            )
        elif signed_zero_pointer:
            base_metrics["legacy_jsonb_signed_zero_compatibility_records"] += 1
            base_metrics["legacy_jsonb_signed_zero_compatibility"].append(
                {
                    "record_id": record_id,
                    "json_pointer": signed_zero_pointer,
                    "stored_payload_hash": record["payload_hash"],
                    "decoded_payload_hash": computed_payload_hash,
                }
            )

        try:
            computed_record_hash = _record_hash(
                record,
                event_cutoff=canonical_timestamps["event_cutoff"],
                knowledge_cutoff=canonical_timestamps["knowledge_cutoff"],
                payload_hash=(
                    str(record["payload_hash"])
                    if signed_zero_pointer
                    else computed_payload_hash
                ),
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
                "calibration_id": calibration_id,
                "chain_generation": expected_generation,
                "created_at": parsed_timestamps["created_at"],
                "knowledge_cutoff": parsed_timestamps["knowledge_cutoff"],
            }
        )

    groups: dict[tuple[str, str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for item in prepared:
        groups[
            (
                item["market_id"],
                item["product"],
                item["calibration_id"],
                item["chain_generation"],
            )
        ].append(item)

    chain_metrics: list[dict[str, Any]] = []
    for (market_id, product, calibration_id, generation), chain in sorted(
        groups.items()
    ):
        topology = analyze_forward_topology(item["record"] for item in chain)
        label = f"{market_id}/{product}/{calibration_id}"
        if len(topology.roots) == 0:
            base_metrics["root_defects"] += 1
            issues.append(("MISSING_CHAIN_ROOT", f"{label}: no genesis link exists"))
        elif len(topology.roots) > 1:
            base_metrics["root_defects"] += len(topology.roots) - 1
            issues.append(
                (
                    "MULTIPLE_CHAIN_ROOTS",
                    f"{label}: {len(topology.roots)} genesis links exist",
                )
            )
        if len(topology.heads) == 0:
            base_metrics["head_defects"] += 1
            issues.append(("MISSING_CHAIN_HEAD", f"{label}: no terminal head exists"))
        elif len(topology.heads) > 1:
            base_metrics["head_defects"] += len(topology.heads) - 1
            issues.append(
                (
                    "MULTIPLE_CHAIN_HEADS",
                    f"{label}: {len(topology.heads)} terminal heads exist",
                )
            )
        for parent, children in topology.forks:
            base_metrics["fork_defects"] += len(children) - 1
            base_metrics["link_mismatches"] += len(children) - 1
            issues.append(
                (
                    "CHAIN_FORK",
                    f"{label}: parent {parent} has {len(children)} children",
                )
            )
        for record_hash, previous_hash in topology.missing_predecessors:
            base_metrics["missing_predecessor_defects"] += 1
            base_metrics["link_mismatches"] += 1
            issues.append(
                (
                    "MISSING_PREDECESSOR",
                    f"{label}/{record_hash}: predecessor {previous_hash} is absent",
                )
            )
        for cycle in topology.cycles:
            base_metrics["cycle_defects"] += 1
            issues.append(
                (
                    "CHAIN_CYCLE",
                    f"{label}: parent cycle contains {','.join(cycle)}",
                )
            )
        if topology.orphans:
            base_metrics["orphaned_records"] += len(topology.orphans)
            issues.append(
                (
                    "ORPHANED_FORWARD_RECORD",
                    f"{label}: {len(topology.orphans)} records are unreachable from genesis",
                )
            )
        if topology.duplicate_record_hashes:
            base_metrics["duplicate_record_hashes"] += len(
                topology.duplicate_record_hashes
            )
            issues.append(
                (
                    "DUPLICATE_RECORD_HASH",
                    f"{label}: duplicate record hashes are present",
                )
            )
        knowledge_times = [item["knowledge_cutoff"] for item in chain]
        first_knowledge = min(knowledge_times)
        last_knowledge = max(knowledge_times)
        span_days = max(0, (last_knowledge - first_knowledge).days)
        chain_metrics.append(
            {
                "market_id": market_id,
                "product": product,
                "calibration_id": calibration_id,
                "chain_generation": generation,
                "record_count": len(chain),
                "knowledge_span_days": span_days,
                "first_knowledge_cutoff": first_knowledge.isoformat(),
                "last_knowledge_cutoff": last_knowledge.isoformat(),
                "root_count": len(topology.roots),
                "head_count": len(topology.heads),
                "fork_count": sum(
                    len(children) - 1 for _, children in topology.forks
                ),
                "fork_parent_count": len(topology.forks),
                "orphan_count": len(topology.orphans),
                "cycle_count": len(topology.cycles),
                "missing_predecessor_count": len(topology.missing_predecessors),
                "head_record_hash": topology.heads[0]
                if len(topology.heads) == 1
                else None,
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
    calibration_id: str | None = None,
    required_products: Iterable[str] = (),
    minimum_records: int,
    minimum_span_days: int,
) -> dict[str, Any]:
    """Verify an active generation and report excluded history as quarantine.

    Historical generations remain immutable and visible, but they can never
    satisfy the maturity gate for a new active generation.
    """

    rows = repository.load_forward_records(market_id=market_id, product=product)
    active_rows = (
        [row for row in rows if row.get("calibration_id") == calibration_id]
        if calibration_id is not None
        else rows
    )
    result = verify_forward_chain(
        active_rows,
        minimum_records=minimum_records,
        minimum_span_days=minimum_span_days,
    )
    required_product_set = {
        item
        for item in required_products
        if isinstance(item, str) and item.strip()
    }
    present_products = {str(row.get("product", "")) for row in active_rows}
    missing_products = sorted(required_product_set - present_products)
    if missing_products:
        result = {
            **result,
            "status": "FAIL" if result["status"] == "FAIL" else "PENDING",
            "reason_codes": sorted(
                {*result["reason_codes"], "MISSING_FORWARD_PRODUCT_CHAIN"}
            ),
            "reasons": sorted(
                {
                    *result["reasons"],
                    "active generation is missing product chains: "
                    + ",".join(missing_products),
                }
            ),
        }
    try:
        active_generation = (
            forward_chain_generation(calibration_id)
            if calibration_id is not None
            else None
        )
    except (TypeError, ValueError):
        active_generation = None
    metrics = dict(result["metrics"])
    metrics["active_calibration_id"] = calibration_id
    metrics["active_chain_generation"] = active_generation
    metrics["required_products"] = sorted(required_product_set)
    metrics["missing_product_chains"] = missing_products

    quarantined: list[dict[str, Any]] = []
    if calibration_id is not None:
        historical: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
        for row in rows:
            historical_calibration = str(row.get("calibration_id", ""))
            if historical_calibration != calibration_id:
                historical[
                    (
                        str(row.get("market_id", "")),
                        historical_calibration,
                        str(row.get("product", "")),
                    )
                ].append(row)
        for (
            historical_market,
            historical_calibration,
            historical_product,
        ), generation_rows in sorted(historical.items()):
            audit = verify_forward_chain(
                generation_rows,
                minimum_records=0,
                minimum_span_days=0,
            )
            try:
                historical_generation = forward_chain_generation(
                    historical_calibration
                )
            except (TypeError, ValueError):
                historical_generation = None
            audited_chains = audit["metrics"]["chains"]
            topology_metrics = audited_chains[0] if len(audited_chains) == 1 else {}
            quarantined.append(
                {
                    "market_id": historical_market,
                    "calibration_id": historical_calibration,
                    "chain_generation": historical_generation,
                    "product": historical_product,
                    "record_count": len(generation_rows),
                    "integrity_status": audit["status"],
                    "reason_codes": audit["reason_codes"],
                    "topology": {
                        key: topology_metrics.get(key, 0)
                        for key in (
                            "root_count",
                            "head_count",
                            "fork_count",
                            "fork_parent_count",
                            "orphan_count",
                            "cycle_count",
                            "missing_predecessor_count",
                        )
                    },
                    "disposition": (
                        "QUARANTINED_INTEGRITY_INCIDENT"
                        if audit["status"] == "FAIL"
                        else "HISTORICAL_READ_ONLY"
                    ),
                }
            )
    metrics["historical_generation_count"] = len(
        {(item["market_id"], item["calibration_id"]) for item in quarantined}
    )
    metrics["quarantined_chain_count"] = len(quarantined)
    metrics["quarantined_generations"] = quarantined
    metrics["historical_quarantine_status"] = (
        "INCIDENT_EVIDENCE_QUARANTINED"
        if any(
            item["disposition"] == "QUARANTINED_INTEGRITY_INCIDENT"
            for item in quarantined
        )
        else "HISTORICAL_READ_ONLY"
        if quarantined
        else "NONE"
    )
    return {**result, "metrics": metrics}


__all__ = [
    "GENESIS_HASH",
    "ForwardRecordReader",
    "verify_forward_chain",
    "verify_repository_forward_chain",
]
