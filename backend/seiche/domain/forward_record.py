"""Shared identity rules for immutable forward-validation records.

The deployed v1 chain uses a pipe-delimited preimage. To preserve every
existing record while making that encoding unambiguous, every textual field is
validated to exclude the delimiter before either writing or verifying a link.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass


FORWARD_RECORD_SEPARATOR = "|"
FORWARD_RECORD_GENESIS_HASH = "0" * 64
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


__all__ = [
    "FORWARD_RECORD_GENESIS_HASH",
    "FORWARD_RECORD_SEPARATOR",
    "ForwardTopology",
    "analyze_forward_topology",
    "forward_chain_generation",
    "forward_record_hash",
]
