"""Append today's as-published Book record to the hash-chained history.

Usage: python backend/scripts/append_book_record.py <prev_history.json|-> <out.json>

CI is stateless, so the published artifact itself is the ledger: the previous
history is pulled from the live site before the build, verified (fail-loud on
any tamper), today's record is chained on, and the result ships inside the
static deploy — the site repo's git history is the append-only backbone.

Idempotent per date: re-running on a day that already has a record leaves the
chain untouched (exit 0, "no-op") — a record, once published, is never
replaced.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seiche import assemble, publisher  # noqa: E402


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    prev_path, out_path = sys.argv[1], Path(sys.argv[2])

    history: list[dict] = []
    if prev_path != "-" and Path(prev_path).exists():
        try:
            history = json.loads(Path(prev_path).read_text()) or []
        except json.JSONDecodeError:
            print("FATAL: previous history is not valid JSON — refusing to overwrite a ledger", file=sys.stderr)
            return 1
    ok, msg = publisher.verify_chain(history)
    if not ok:
        print(f"FATAL: {msg} — refusing to append to a tampered chain", file=sys.stderr)
        return 1

    snap = asyncio.run(assemble.snapshot(force=True))
    deep = snap.get("deep", {})
    book = deep.get("book", {})
    comp = snap.get("engines", {}).get("composite", {})
    if not book.get("ok"):
        reason = book.get("reason") or deep.get("reason") or "deep layer unavailable"
        print(f"book unavailable ({reason}); chain left as-is", file=sys.stderr)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(history))
        return 0

    today = book["today"]
    day = (snap.get("generated_at") or "")[:10]
    if history and history[-1].get("date") == day:
        print(f"record for {day} already published — no-op (a ledger is never rewritten)")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(history))
        return 0

    record = {
        "date": day,
        "stance": today.get("stance"),
        "positions": [[p.get("sleeve"), p.get("weight")] for p in today.get("positions", [])],
        "p_ensemble": today.get("p_ensemble"),
        "dispersion": today.get("dispersion"),
        "index": comp.get("value"),
        "regime": comp.get("regime"),
    }
    record = publisher.get_publisher().publish(record, history)
    history.append(record)
    ok, msg = publisher.verify_chain(history)
    if not ok:
        print(f"FATAL: {msg} after append — bug, refusing to publish", file=sys.stderr)
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(history))
    print(f"appended {day}: {record['stance']} · {msg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
