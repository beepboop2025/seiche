"""Export the FREE public surface (conclusion + PROOF) as static JSON.

Usage: python backend/scripts/export_public.py <output-path>

Replaces the old full-snapshot publish on the public site: the board data is
now subscriber-gated (served live from the box behind a token), so only this
slim, deliberately-free slice is baked into the public build.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seiche import assemble, public_view  # noqa: E402


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("public.json")
    snap = asyncio.run(assemble.snapshot(force=True))
    engines = snap.get("engines", {})
    if sum(1 for v in engines.values() if isinstance(v, dict) and v.get("ok")) == 0:
        print("FATAL: zero engines produced output; refusing to publish", file=sys.stderr)
        return 1
    payload = public_view.public_payload(snap)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"wrote public surface -> {out} (regime {payload['conclusion']['regime']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
