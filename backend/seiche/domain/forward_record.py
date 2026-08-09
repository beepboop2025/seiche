"""Shared identity rules for immutable forward-validation records.

The deployed v1 chain uses a pipe-delimited preimage. To preserve every
existing record while making that encoding unambiguous, every textual field is
validated to exclude the delimiter before either writing or verifying a link.
"""

from __future__ import annotations

import hashlib


FORWARD_RECORD_SEPARATOR = "|"


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


__all__ = ["FORWARD_RECORD_SEPARATOR", "forward_record_hash"]
