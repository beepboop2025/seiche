"""Export the FREE public surface (argument + quality + conclusion + PROOF).

Usage: python backend/scripts/export_public.py [--snapshot FILE] <public-path> [<overview-path>]

The slim derived slice is always written. It includes the thesis, evidence,
countercase and data-quality contract, but never the underlying engine
payloads. When a second path is given, the FULL board snapshot is baked next
to it too — the terminal is fully open (no gate) and the static site uses that
file as its offline fallback: if api.seiche.info is unreachable, the board
still renders from the last CI-baked snapshot instead of dying on an error
screen.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seiche import assemble, public_view  # noqa: E402
from seiche.publish_snapshot import PublishSnapshotError, load_publish_snapshot  # noqa: E402


def _json_safe(o):
    """NaN/Inf → null: strict JSON parsers (every browser) reject them."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_json_safe(v) for v in o]
    return o


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", help="reuse this already-built full-board JSON")
    parser.add_argument("public_path", nargs="?", default="public.json")
    parser.add_argument("overview_path", nargs="?")
    args = parser.parse_args(argv)

    out = Path(args.public_path)
    overview_out = Path(args.overview_path) if args.overview_path else None
    if args.snapshot:
        try:
            snap = load_publish_snapshot(args.snapshot)
        except PublishSnapshotError as exc:
            print(f"FATAL: {exc}", file=sys.stderr)
            return 1
        print(f"board read from {args.snapshot} (generated {snap.get('generated_at')})")
    else:
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
