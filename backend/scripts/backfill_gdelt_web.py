#!/usr/bin/env python3
"""Seed Seiche's GDELT WEB-NGRAM baseline from historical heartbeats.

The live collector adds one sample every three hours.  A new deployment can
be made useful immediately by sampling the previous week at a conservative
six-hour cadence.  The job is resumable: history is keyed by the upstream
batch timestamp, so rerunning replaces duplicates rather than double-counting.

Run on the production box from the repository root:

    backend/.venv/bin/python backend/scripts/backfill_gdelt_web.py
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import sys

import httpx

from seiche.sources import gdelt


def _targets(days: int, cadence_hours: int) -> list[datetime]:
    now = datetime.now(timezone.utc)
    cursor = (now - timedelta(days=days)).replace(
        minute=10, second=0, microsecond=0)
    remainder = cursor.hour % cadence_hours
    if remainder:
        cursor += timedelta(hours=cadence_hours - remainder)
    out = []
    while cursor <= now - timedelta(minutes=10):
        out.append(cursor)
        cursor += timedelta(hours=cadence_hours)
    return out


async def _run(days: int, cadence_hours: int) -> int:
    targets = _targets(days, cadence_hours)
    if not targets:
        print("no historical targets in requested window", file=sys.stderr)
        return 2
    history = gdelt._load_web_history()
    succeeded = 0
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for number, target in enumerate(targets, 1):
            try:
                sample = await gdelt._fetch_web_sample(client, target)
                history = gdelt._merge_web_sample(history, sample)
            except Exception as exc:  # a missing heartbeat does not erase peers
                print(f"[{number}/{len(targets)}] {target.isoformat()} skipped: "
                      f"{type(exc).__name__}: {exc}", flush=True)
                continue
            succeeded += 1
            counts = sample.get("topic_counts") or {}
            print(f"[{number}/{len(targets)}] {sample['batch_at']} "
                  f"docs={sample['documents']} matches={sum(counts.values())}",
                  flush=True)
    print(f"backfill complete: added/refreshed {succeeded} batches; "
          f"history now has {len(history.get('samples') or [])} samples")
    if succeeded:
        # Replace the short-lived presentation cache too.  Otherwise a backfill
        # that correctly updates durable history can remain invisible to a
        # freshly restarted API until the previous three-hour index expires.
        gdelt.store.save_blob(gdelt.WEB_INDEX_KEY, gdelt._web_blob(history))
    return 0 if succeeded else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--cadence-hours", type=int, default=6)
    args = parser.parse_args()
    if not 1 <= args.days <= 60:
        parser.error("--days must be 1..60")
    if args.cadence_hours not in (1, 2, 3, 4, 6, 8, 12, 24):
        parser.error("--cadence-hours must divide a day")
    return asyncio.run(_run(args.days, args.cadence_hours))


if __name__ == "__main__":
    raise SystemExit(main())
