"""Export a full engine snapshot as static JSON for serverless hosting.

Usage: python backend/scripts/export_snapshot.py <output-path>

This is the palimpsest-style publish path: a CI cron runs the engines and
publishes the payload as a static file; the dashboard fetches it when no live
API is present. Exits non-zero if every engine failed (a bad upstream day
should fail the pipeline loudly, not publish an empty dashboard).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seiche import assemble  # noqa: E402


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("overview.json")
    snap = asyncio.run(assemble.snapshot(force=True))

    engines = snap.get("engines", {})
    ok_count = sum(1 for k, v in engines.items() if isinstance(v, dict) and v.get("ok"))
    if ok_count == 0:
        print("FATAL: zero engines produced output; refusing to publish", file=sys.stderr)
        print(json.dumps(snap.get("faults", []), indent=2), file=sys.stderr)
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, default=str))
    comp = engines.get("composite", {})
    print(
        f"wrote {out} ({out.stat().st_size // 1024} KB) — "
        f"index {comp.get('value')} {comp.get('regime')} · "
        f"{ok_count} engines ok · {len(snap.get('faults', []))} faults"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
