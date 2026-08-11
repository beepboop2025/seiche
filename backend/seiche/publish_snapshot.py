"""Strict loading for an already-built board used by publish tooling.

The production API owns continuous board assembly.  Static publishing and the
dispatch generators may consume one captured board, but only after this seam
has proved that the file is strict JSON and carries the minimum identity needed
to publish it.  Callers retain their surface-specific checks (for example, the
Book publisher still decides whether ``deep.book`` is available).
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import NoReturn


class PublishSnapshotError(ValueError):
    """An on-disk publish snapshot is missing, malformed, or incomplete."""


def _reject_nonfinite(token: str) -> NoReturn:
    raise PublishSnapshotError(f"snapshot contains non-finite JSON value {token}")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PublishSnapshotError(f"snapshot contains duplicate JSON key {key!r}")
        result[key] = value
    return result


def load_publish_snapshot(path: str | Path) -> dict:
    """Load a strict full-board snapshot and validate its publish identity."""

    source = Path(path)
    try:
        raw = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PublishSnapshotError(f"cannot read snapshot {source}: {exc}") from exc
    try:
        payload = json.loads(
            raw,
            parse_constant=_reject_nonfinite,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except json.JSONDecodeError as exc:
        raise PublishSnapshotError(f"snapshot {source} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PublishSnapshotError(f"snapshot {source} must be a JSON object")
    generated_at = payload.get("generated_at")
    if not isinstance(generated_at, str):
        raise PublishSnapshotError(f"snapshot {source} has no valid generated_at")
    try:
        generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PublishSnapshotError(
            f"snapshot {source} has no valid generated_at"
        ) from exc
    if generated.tzinfo is None or generated.utcoffset() is None:
        raise PublishSnapshotError(f"snapshot {source} generated_at has no timezone")
    if not isinstance(payload.get("engines"), dict):
        raise PublishSnapshotError(f"snapshot {source} has no engines object")
    return payload
