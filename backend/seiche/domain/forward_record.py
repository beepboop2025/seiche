"""Shared identity rules for immutable forward-validation records.

The deployed v1 chain uses a pipe-delimited preimage. To preserve every
existing record while making that encoding unambiguous, every textual field is
validated to exclude the delimiter before either writing or verifying a link.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime


FORWARD_RECORD_SEPARATOR = "|"
FORWARD_RECORD_GENESIS_HASH = "0" * 64
SNAPSHOT_HANDOFF_SCHEMA = "seiche.snapshot-handoff.v1"
MARKET_SNAPSHOT_ROW_FIELDS = (
    "snapshot_id",
    "market_id",
    "product",
    "event_cutoff",
    "knowledge_cutoff",
    "sealed_at",
    "calibration_id",
    "evidence_eligible",
    "payload_hash",
    "payload",
)
_GENERATION_SUFFIX = re.compile(r"-v([1-9][0-9]*)$")


def forward_chain_generation(calibration_id: str) -> int:
    """Return the explicit chain generation bound into ``calibration_id``.

    Forward-record identities already commit to the calibration id.  Requiring
    a terminal ``-vN`` therefore gives chain generations an authenticated,
    migration-free identity while leaving every deployed v1 hash unchanged.
    """

    value = _identity_field("calibration_id", calibration_id)
    match = _GENERATION_SUFFIX.search(value)
    if match is None:
        raise ValueError("forward-record calibration_id must end in -vN")
    return int(match.group(1))


@dataclass(frozen=True)
class ForwardTopology:
    """Structural facts for a parent-hash graph, independent of timestamps."""

    record_count: int
    roots: tuple[str, ...]
    heads: tuple[str, ...]
    forks: tuple[tuple[str, tuple[str, ...]], ...]
    missing_predecessors: tuple[tuple[str, str], ...]
    cycles: tuple[tuple[str, ...], ...]
    orphans: tuple[str, ...]
    duplicate_record_hashes: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return (
            self.record_count > 0
            and len(self.roots) == 1
            and len(self.heads) == 1
            and not self.forks
            and not self.missing_predecessors
            and not self.cycles
            and not self.orphans
            and not self.duplicate_record_hashes
        )


def _canonical_cycle(nodes: list[str]) -> tuple[str, ...]:
    """Rotate a cycle to a deterministic representation for reporting."""

    if not nodes:
        return ()
    # Each graph node is a unique record hash. Rotating once from the smallest
    # hash is therefore the same canonical choice as comparing every rotation,
    # without the quadratic memory cost on a large corrupted cycle.
    offset = min(range(len(nodes)), key=nodes.__getitem__)
    return tuple(nodes[offset:] + nodes[:offset])


def analyze_forward_topology(
    records: Iterable[Mapping[str, object]],
) -> ForwardTopology:
    """Inspect parent links as a graph without inferring order from timestamps."""

    rows = tuple(records)
    hashes = [str(row["record_hash"]) for row in rows]
    counts: dict[str, int] = defaultdict(int)
    for record_hash in hashes:
        counts[record_hash] += 1
    duplicates = tuple(sorted(key for key, count in counts.items() if count > 1))

    # Keep one representative for topology reporting. Duplicate identities are
    # already a hard defect and therefore can never make the result valid.
    nodes: dict[str, str] = {}
    for row in rows:
        nodes.setdefault(str(row["record_hash"]), str(row["previous_record_hash"]))

    children: dict[str, list[str]] = defaultdict(list)
    missing: list[tuple[str, str]] = []
    roots: list[str] = []
    for record_hash, previous_hash in nodes.items():
        children[previous_hash].append(record_hash)
        if previous_hash == FORWARD_RECORD_GENESIS_HASH:
            roots.append(record_hash)
        elif previous_hash not in nodes:
            missing.append((record_hash, previous_hash))

    forks = tuple(
        sorted(
            (parent, tuple(sorted(child_hashes)))
            for parent, child_hashes in children.items()
            if len(child_hashes) > 1
        )
    )
    heads = tuple(
        sorted(record_hash for record_hash in nodes if record_hash not in children)
    )

    cycles_seen: set[tuple[str, ...]] = set()
    complete: set[str] = set()
    for start in sorted(nodes):
        if start in complete:
            continue
        path: list[str] = []
        positions: dict[str, int] = {}
        current = start
        while current in nodes and current not in complete:
            if current in positions:
                cycles_seen.add(_canonical_cycle(path[positions[current] :]))
                break
            positions[current] = len(path)
            path.append(current)
            previous = nodes[current]
            if previous == FORWARD_RECORD_GENESIS_HASH or previous not in nodes:
                break
            current = previous
        complete.update(path)

    reachable: set[str] = set()
    pending = list(roots)
    while pending:
        current = pending.pop()
        if current in reachable:
            continue
        reachable.add(current)
        pending.extend(children.get(current, ()))
    orphans = tuple(sorted(set(nodes) - reachable))

    return ForwardTopology(
        record_count=len(rows),
        roots=tuple(sorted(roots)),
        heads=heads,
        forks=forks,
        missing_predecessors=tuple(sorted(missing)),
        cycles=tuple(sorted(cycles_seen)),
        orphans=orphans,
        duplicate_record_hashes=duplicates,
    )


def _identity_field(name: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"forward-record {name} must be a non-empty string")
    if FORWARD_RECORD_SEPARATOR in value:
        raise ValueError(
            f"forward-record {name} contains reserved delimiter "
            f"{FORWARD_RECORD_SEPARATOR!r}"
        )
    return value


def forward_record_hash(
    *,
    snapshot_id: str,
    market_id: str,
    product: str,
    event_cutoff: str,
    knowledge_cutoff: str,
    calibration_id: str,
    payload_hash: str,
    previous_record_hash: str,
) -> str:
    """Hash one unambiguous v1 chain identity without invalidating history."""

    fields = (
        _identity_field("snapshot_id", snapshot_id),
        _identity_field("market_id", market_id).upper(),
        _identity_field("product", product),
        _identity_field("event_cutoff", event_cutoff),
        _identity_field("knowledge_cutoff", knowledge_cutoff),
        _identity_field("calibration_id", calibration_id),
        _identity_field("payload_hash", payload_hash),
        _identity_field("previous_record_hash", previous_record_hash),
    )
    identity = FORWARD_RECORD_SEPARATOR.join(fields)
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def validate_snapshot_forward_binding(
    snapshot: Mapping[str, object],
    forward_record: Mapping[str, object],
    expected_record_hash: str,
    expected_snapshot_row_hash: str,
) -> None:
    """Prove one staged snapshot is the exact payload sealed by its receipt."""

    if any(
        re.fullmatch(r"[0-9a-f]{64}", value) is None
        for value in (expected_record_hash, expected_snapshot_row_hash)
    ):
        raise ValueError("release receipt record hash is invalid")

    def canonical_time(
        value: object, *, field: str, timespec: str
    ) -> tuple[str, datetime]:
        if not isinstance(value, str):
            raise ValueError("release snapshot timestamp is invalid")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("release snapshot timestamp must be timezone-aware")
        parsed = parsed.astimezone(UTC)
        canonical = parsed.isoformat(timespec=timespec)
        if value != canonical:
            raise ValueError(f"release snapshot {field} is not canonical UTC")
        return canonical, parsed

    try:
        snapshot_payload = json.dumps(
            snapshot["payload"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        forward_payload = json.dumps(
            forward_record["payload"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("release snapshot payload is not canonical JSON") from exc
    payload_hash = hashlib.sha256(snapshot_payload.encode("utf-8")).hexdigest()
    if snapshot_payload != forward_payload:
        raise ValueError("staged snapshot payload differs from its forward record")

    snapshot_id = _identity_field("snapshot_id", snapshot.get("snapshot_id"))
    market_id = _identity_field("market_id", snapshot.get("market_id"))
    if market_id != market_id.upper():
        raise ValueError("staged snapshot market_id is not canonical uppercase")
    product = _identity_field("product", snapshot.get("product"))
    event_cutoff, _ = canonical_time(
        snapshot.get("event_cutoff"), field="event_cutoff", timespec="seconds"
    )
    knowledge_cutoff, _ = canonical_time(
        snapshot.get("knowledge_cutoff"),
        field="knowledge_cutoff",
        timespec="seconds",
    )
    _, sealed_at = canonical_time(
        snapshot.get("sealed_at"), field="sealed_at", timespec="microseconds"
    )
    calibration_id = _identity_field(
        "calibration_id", snapshot.get("calibration_id")
    )
    identity = FORWARD_RECORD_SEPARATOR.join(
        (
            market_id,
            product,
            event_cutoff,
            knowledge_cutoff,
            calibration_id,
            payload_hash,
        )
    )
    expected_snapshot_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    if snapshot_id != expected_snapshot_id:
        raise ValueError("staged snapshot ID does not match its stored content")

    forward_market = _identity_field(
        "market_id", forward_record.get("market_id")
    )
    if forward_market != forward_market.upper():
        raise ValueError("forward-record market_id is not canonical uppercase")
    forward_event, _ = canonical_time(
        forward_record.get("event_cutoff"),
        field="forward event_cutoff",
        timespec="seconds",
    )
    forward_knowledge, _ = canonical_time(
        forward_record.get("knowledge_cutoff"),
        field="forward knowledge_cutoff",
        timespec="seconds",
    )
    _, forward_created_at = canonical_time(
        forward_record.get("created_at"),
        field="forward created_at",
        timespec="microseconds",
    )
    if sealed_at > forward_created_at:
        raise ValueError("staged snapshot was sealed after its forward record")
    forward_identity = (
        forward_record.get("snapshot_id"),
        forward_market,
        forward_record.get("product"),
        forward_event,
        forward_knowledge,
        forward_record.get("calibration_id"),
        forward_record.get("payload_hash"),
    )
    snapshot_identity = (
        snapshot_id,
        market_id,
        product,
        event_cutoff,
        knowledge_cutoff,
        calibration_id,
        payload_hash,
    )
    if forward_identity != snapshot_identity:
        raise ValueError("staged snapshot identity differs from its forward record")
    if snapshot.get("payload_hash") != payload_hash:
        raise ValueError("staged snapshot payload hash is invalid")
    if forward_record.get("payload_hash") != payload_hash:
        raise ValueError("forward-record payload hash is invalid")
    actual_snapshot_row_hash = market_snapshot_row_hash(snapshot)
    if not hmac.compare_digest(
        actual_snapshot_row_hash, expected_snapshot_row_hash
    ):
        raise ValueError("staged snapshot row differs from the release receipt")
    payload = snapshot.get("payload")
    evidence = payload.get("evidence_eligibility") if isinstance(payload, dict) else None
    if (
        not isinstance(evidence, dict)
        or not isinstance(evidence.get("eligible"), bool)
        or snapshot.get("evidence_eligible") is not evidence["eligible"]
    ):
        raise ValueError(
            "staged snapshot evidence eligibility differs from its payload"
        )
    if forward_record.get("chain_generation") != forward_chain_generation(
        calibration_id
    ):
        raise ValueError("forward-record chain generation is invalid")

    expected_forward_hash = forward_record_hash(
        snapshot_id=snapshot_id,
        market_id=market_id,
        product=product,
        event_cutoff=event_cutoff,
        knowledge_cutoff=knowledge_cutoff,
        calibration_id=calibration_id,
        payload_hash=payload_hash,
        previous_record_hash=forward_record.get("previous_record_hash"),
    )
    if (
        forward_record.get("record_id") != expected_forward_hash
        or forward_record.get("record_hash") != expected_forward_hash
        or not hmac.compare_digest(expected_forward_hash, expected_record_hash)
    ):
        raise ValueError("forward record does not match the release receipt")


def market_snapshot_row_hash(snapshot: Mapping[str, object]) -> str:
    """Hash the exact storage row copied during controller activation."""

    if set(snapshot) != set(MARKET_SNAPSHOT_ROW_FIELDS):
        raise ValueError("market snapshot row contract is invalid")
    try:
        canonical = json.dumps(
            {field: snapshot[field] for field in MARKET_SNAPSHOT_ROW_FIELDS},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("market snapshot row is not canonical JSON") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def release_handoff_snapshot_bindings(
    envelope: Mapping[str, object],
) -> tuple[tuple[str, str, str, str], ...]:
    """Extract exact product/snapshot/forward/row bindings from a handoff."""

    receipt = envelope.get("release_receipt")
    products = receipt.get("products") if isinstance(receipt, Mapping) else None
    if not isinstance(products, Mapping) or not products:
        raise ValueError("release handoff does not contain product bindings")
    bindings = []
    for product, binding in products.items():
        if (
            not isinstance(product, str)
            or not product
            or not isinstance(binding, Mapping)
            or set(binding)
            != {"snapshot_id", "forward_record_id", "snapshot_row_sha256"}
        ):
            raise ValueError("release handoff product binding is invalid")
        snapshot_id = binding.get("snapshot_id")
        record_hash = binding.get("forward_record_id")
        snapshot_row_hash = binding.get("snapshot_row_sha256")
        if (
            not isinstance(snapshot_id, str)
            or re.fullmatch(r"[0-9a-f]{64}", snapshot_id) is None
            or not isinstance(record_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", record_hash) is None
            or not isinstance(snapshot_row_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", snapshot_row_hash) is None
        ):
            raise ValueError("release handoff product hashes are invalid")
        bindings.append((product, snapshot_id, record_hash, snapshot_row_hash))
    if len({binding[1] for binding in bindings}) != len(bindings):
        raise ValueError("release handoff snapshot bindings are not unique")
    return tuple(sorted(bindings))


def release_handoff_generated_at(envelope: Mapping[str, object]) -> datetime:
    """Return the receipt-bound build time used for same-release monotonicity."""

    payload = envelope.get("payload")
    receipt = envelope.get("release_receipt")
    payload_generated_at = (
        payload.get("generated_at") if isinstance(payload, Mapping) else None
    )
    receipt_generated_at = (
        receipt.get("generated_at") if isinstance(receipt, Mapping) else None
    )
    if (
        not isinstance(payload_generated_at, str)
        or payload_generated_at != receipt_generated_at
    ):
        raise ValueError("release handoff generated_at binding is invalid")
    try:
        parsed = datetime.fromisoformat(payload_generated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("release handoff generated_at is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("release handoff generated_at must be timezone-aware")
    return parsed.astimezone(UTC)


def validate_release_handoff_envelope(
    envelope: Mapping[str, object],
    *,
    expected_handoff_id: str | None = None,
    expected_producer_sha: str | None = None,
) -> tuple[tuple[str, str, str, str], ...]:
    """Recompute the protocol token and payload digest for one locked handoff."""

    if set(envelope) != {
        "schema",
        "producer_sha",
        "payload_sha256",
        "release_receipt",
        "payload",
        "handoff_id",
    } or envelope.get("schema") != SNAPSHOT_HANDOFF_SCHEMA:
        raise ValueError("release handoff envelope contract is invalid")
    producer_sha = envelope.get("producer_sha")
    handoff_id = envelope.get("handoff_id")
    if (
        not isinstance(producer_sha, str)
        or re.fullmatch(r"[0-9a-f]{40}", producer_sha) is None
        or not isinstance(handoff_id, str)
        or re.fullmatch(r"[0-9a-f]{64}", handoff_id) is None
    ):
        raise ValueError("release handoff identity is invalid")
    if expected_producer_sha is not None and not hmac.compare_digest(
        producer_sha, expected_producer_sha
    ):
        raise ValueError("release handoff producer differs from the expected SHA")
    if expected_handoff_id is not None and not hmac.compare_digest(
        handoff_id, expected_handoff_id
    ):
        raise ValueError("release handoff token differs from the expected token")

    try:
        payload_json = json.dumps(
            envelope["payload"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        body = {key: value for key, value in envelope.items() if key != "handoff_id"}
        body_json = json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("release handoff is not canonical JSON") from exc
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    body_hash = hashlib.sha256(body_json.encode("utf-8")).hexdigest()
    if (
        not isinstance(envelope.get("payload_sha256"), str)
        or not hmac.compare_digest(envelope["payload_sha256"], payload_hash)
        or not hmac.compare_digest(handoff_id, body_hash)
    ):
        raise ValueError("release handoff payload or protocol digest is invalid")
    return release_handoff_snapshot_bindings(envelope)


__all__ = [
    "FORWARD_RECORD_GENESIS_HASH",
    "FORWARD_RECORD_SEPARATOR",
    "MARKET_SNAPSHOT_ROW_FIELDS",
    "SNAPSHOT_HANDOFF_SCHEMA",
    "ForwardTopology",
    "analyze_forward_topology",
    "forward_chain_generation",
    "forward_record_hash",
    "market_snapshot_row_hash",
    "release_handoff_generated_at",
    "release_handoff_snapshot_bindings",
    "validate_release_handoff_envelope",
    "validate_snapshot_forward_binding",
]
