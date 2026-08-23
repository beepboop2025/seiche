"""Source-only entry point for the Railway snapshot prebuilder.

The process deliberately receives no production database or signing material.
It computes a complete public board and writes one canonical JSON value to
stdout; the Railway wrapper binds those bytes to the exact source identity.
"""

from __future__ import annotations

import asyncio
import json
import sys

from seiche import assemble


def canonical_payload(payload: dict) -> bytes:
    return (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


async def build_payload() -> dict:
    payload = await assemble.prebuild_snapshot_payload()
    assemble._assert_snapshot_rights(payload)
    if not assemble._servable_snapshot(payload):
        raise ValueError("prebuilt snapshot is not safely servable")
    return payload


def main() -> int:
    payload = asyncio.run(build_payload())
    sys.stdout.buffer.write(canonical_payload(payload))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
