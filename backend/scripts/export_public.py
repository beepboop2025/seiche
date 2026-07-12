"""Export the FREE public surface (conclusion + PROOF) as static JSON.

Usage: python backend/scripts/export_public.py <public-path> [<overview-path>]

The slim public slice (conclusion + PROOF) is always written. When a second
path is given, the FULL board snapshot is baked next to it too — the terminal
is fully open (no gate) and the static site uses that file as its offline
fallback: if api.seiche.info is unreachable, the board still renders from the
last CI-baked snapshot instead of dying on an error screen.
"""
from __future__ import annotations

import asyncio
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seiche import assemble, public_view  # noqa: E402


def _json_safe(o):
    """NaN/Inf → null: strict JSON parsers (every browser) reject them."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_json_safe(v) for v in o]
    return o


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("public.json")
    overview_out = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    snap = asyncio.run(assemble.snapshot(force=True))
    engines = snap.get("engines", {})
    if sum(1 for v in engines.values() if isinstance(v, dict) and v.get("ok")) == 0:
        print("FATAL: zero engines produced output; refusing to publish", file=sys.stderr)
        return 1
    payload = public_view.public_payload(snap)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"wrote public surface -> {out} (regime {payload['conclusion']['regime']})")
    if overview_out is not None:
        overview_out.parent.mkdir(parents=True, exist_ok=True)
        overview_out.write_text(
            json.dumps(_json_safe(snap), separators=(",", ":"), allow_nan=False))
        print(f"wrote full-board fallback -> {overview_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
