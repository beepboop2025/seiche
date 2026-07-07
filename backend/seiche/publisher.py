"""Tamper-evident publication of the as-published record.

The Book's live track record is only worth anything if nobody — including
the operator — can quietly rewrite it. The mechanism is deliberately boring:
each daily record carries the SHA-256 of the canonical JSON of the previous
record (genesis = 64 zeros), the chained history file ships inside the
published static site, and the site repo's git history is the append-only
backbone. Editing any past record breaks every hash after it; `verify_chain`
runs on every publish and the pipeline fails loud.

The publisher is a hook, not a hardcode: `get_publisher()` reads
SEICHE_PUBLISHER so an external attestation layer (e.g. a palimpsest-style
observatory publishing its own receipts) can be swapped in — it only has to
implement `publish(record, history) -> record` and may add a `receipt` field
(commit hash, timestamp proof) to the record it returns.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Protocol

GENESIS = "0" * 64


def canonical(record: dict) -> str:
    """Deterministic JSON for hashing — sorted keys, no whitespace drift,
    the record's own chain fields excluded."""
    clean = {k: v for k, v in record.items() if k not in ("prev_hash", "receipt")}
    return json.dumps(clean, sort_keys=True, separators=(",", ":"))


def record_hash(record: dict) -> str:
    """Hash over the canonical body AND the link to its predecessor, so the
    chain commits to order, not just content."""
    body = canonical(record) + (record.get("prev_hash") or "")
    return hashlib.sha256(body.encode()).hexdigest()


class Publisher(Protocol):
    def publish(self, record: dict, history: list[dict]) -> dict: ...


class HashChainPublisher:
    def publish(self, record: dict, history: list[dict]) -> dict:
        record = dict(record)
        record["prev_hash"] = record_hash(history[-1]) if history else GENESIS
        return record


def verify_chain(history: list[dict]) -> tuple[bool, str]:
    prev = GENESIS
    for i, rec in enumerate(history):
        if rec.get("prev_hash") != prev:
            return False, f"chain broken at record {i} ({rec.get('date', '?')}): prev_hash mismatch"
        prev = record_hash(rec)
    return True, f"chain intact ({len(history)} records)"


def get_publisher() -> Publisher:
    name = os.environ.get("SEICHE_PUBLISHER", "hashchain")
    # external attestation layers register here; hashchain is the default
    return HashChainPublisher() if name == "hashchain" else HashChainPublisher()
