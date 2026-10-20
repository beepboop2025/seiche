#!/usr/bin/env python3
"""Observe the public metadata path without retaining payloads or credentials."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics
import tempfile
import time
import urllib.request

URL = "https://agents.seiche.info/v1/context/snapshot"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


def measure(*, opener=None, clock=time.perf_counter, now=None):
    opener = opener or urllib.request.build_opener(NoRedirect())
    fixed_now = now
    now = fixed_now or datetime.now(timezone.utc)
    request = urllib.request.Request(URL, headers={
        "X-Financial-Evidence-Traffic-Class": "synthetic",
        "User-Agent": "Financial-Evidence-context-monitor/1",
    })
    results = []
    for index in range(3):
        start = clock()
        try:
            with opener.open(request, timeout=15) as response:
                body = response.read(16385)
                if response.status != 200 or len(body) > 16384:
                    raise ValueError("invalid_response")
            duration = round((clock() - start) * 1000, 3)
            value = json.loads(body)
            monitor = value.get("source_monitor", {})
            if (value.get("schema") != "seiche-agent-network.context.v1"
                    or value.get("scope") != "source_status_metadata"
                    or value.get("execution_authority") != "none"
                    or not isinstance(monitor, dict)):
                raise ValueError("invalid_contract")
            current = False
            try:
                checked_at = fixed_now or datetime.now(timezone.utc)
                evaluated = datetime.fromisoformat(monitor["evaluated_at"].replace("Z", "+00:00"))
                expires = datetime.fromisoformat(monitor["valid_until"].replace("Z", "+00:00"))
                current = (monitor.get("current") is True and monitor.get("status") == "current"
                           and monitor.get("max_age_seconds") == 900
                           and value.get("input_eligibility") == "not_evaluated"
                           and evaluated.tzinfo is not None and expires.tzinfo is not None
                           and 0 <= (checked_at-evaluated).total_seconds() < 900
                           and evaluated <= checked_at < expires <= evaluated + timedelta(seconds=900))
            except (ValueError, TypeError, KeyError, AttributeError):
                pass
            results.append({"index": index + 1, "status": "passed", "duration_ms": duration,
                            "bytes": len(body), "source_monitor_current": current,
                            "payload_sha256": hashlib.sha256(body).hexdigest()})
        except Exception as exc:
            results.append({"index": index + 1, "status": "failed", "reason": type(exc).__name__,
                            "duration_ms": round((clock() - start) * 1000, 3)})
            break
    values = [row["duration_ms"] for row in results if row["status"] == "passed"]
    return {"schema": "seiche-agent-network.context-latency.v1", "observed_at": now.isoformat(),
            "traffic_class": "synthetic", "vantage": "gateway_host_to_public_edge",
            "url": URL, "scope": "source_metadata_transport_not_market_feed_latency",
            "status": "passed" if len(values) == 3 else "partial", "samples": results,
            "source_monitor_status": "current" if len(values) == 3 and all(row.get("source_monitor_current") for row in results) else "stale_or_unavailable",
            "median_ms": statistics.median(values) if values else None,
            "p95_ms": None, "p99_ms": None, "sla_verified": False,
            "source_latency": "not_measured", "adoption": "not_measured"}


def atomic_write(report, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode="w", dir=destination.parent, delete=False) as stream:
        temp = Path(stream.name)
        try:
            json.dump(report, stream, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    os.chmod(temp, 0o600)
    os.replace(temp, destination)


def persist(report, destination):
    destination = Path(destination)
    atomic_write(report, destination)
    # Bounded rolling receipts, one per observation; no raw source data.
    history = destination.parent / "history"
    history.mkdir(exist_ok=True, mode=0o700)
    receipt = history / (report["observed_at"].replace(":", "-") + ".json")
    atomic_write(report, receipt)
    for old in sorted(history.glob("*.json"))[:-2016]:
        old.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = measure()
    persist(report, args.output)
    print(json.dumps({key: report[key] for key in ("status", "observed_at", "median_ms", "sla_verified")}))
    if report["status"] != "passed":
        return 1
    return 0 if report["source_monitor_status"] == "current" else 2


if __name__ == "__main__":
    raise SystemExit(main())
