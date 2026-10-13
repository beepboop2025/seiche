#!/usr/bin/env python3
"""Synthetic Store acceptance benchmark against an explicitly empty database.

This exercises the persistence/domain layer, not HTTP, TLS, MCP or model latency.
It creates only synthetic identities and never sends traffic to product servers.
Credentials and encryption keys remain in memory; the retained database is a
disposable synthetic fixture, not a deployment or recoverable production store.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import sys
import time

from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.engine import make_url

from seiche_agent_network.crypto import canonical, generate_keys
from seiche_agent_network.models import NetworkError
from seiche_agent_network.store import Store, agents as agents_table, messages


def database_url(value):
    if "://" not in value:
        if value == ":memory:":
            raise ValueError("A persistent, empty qualification database is required")
        path = Path(value).resolve()
        if path.exists() and path.stat().st_size:
            raise ValueError("Qualification requires an empty database")
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        return "sqlite:///" + str(path)
    url = make_url(value)
    if url.get_backend_name() not in {"postgresql", "sqlite"}:
        raise ValueError("Only isolated SQLite or PostgreSQL databases are supported")
    if url.get_backend_name() == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    if url.get_backend_name() == "sqlite":
        if not url.database or url.database == ":memory:":
            raise ValueError("Persistent database required for restart proof")
        # Reserve a new local file privately before SQLAlchemy inspects it.
        path = Path(url.database)
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
    return url.render_as_string(hide_password=False)


def require_empty(url):
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as db:
            if inspect(db).get_table_names():
                raise ValueError("Refusing a database/schema containing existing tables")
            version = db.dialect.server_version_info
            return {"backend": db.dialect.name, "server_version": list(version) if version else None}
    finally:
        engine.dispose()


def latency_summary(values):
    ordered = sorted(values)
    def at(fraction):
        return round(ordered[max(0, math.ceil(len(ordered) * fraction) - 1)], 3) if ordered else None
    return {"observations": len(ordered), "p50_ms": at(.5), "p95_ms": at(.95),
            "max_ms": round(ordered[-1], 3) if ordered else None,
            "mean_ms": round(sum(ordered) / len(ordered), 3) if ordered else None,
            "percentile_definition": "nearest-rank of measured synthetic observations"}


def run(args):
    if not args.allow_synthetic_database:
        raise ValueError("Explicit --allow-synthetic-database is required")
    if not 1 <= args.workers <= 16 or not 1 <= args.rate <= 20 or not 1 <= args.seconds <= 20 or not 1 <= args.burst <= 200:
        raise ValueError("Use workers1–16, rate1–20, seconds1–20 and burst1–200")
    if args.rate * args.seconds + args.burst > 550:
        raise ValueError("Total messages must remain below the default per-organization operating quota")
    # Reserve the report before touching a database; never overwrite prior proof.
    output = Path(args.output)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    stores = []
    started = datetime.now(timezone.utc)
    report = {"schema": "seiche-agent-network.store-qualification.v1", "synthetic": True,
              "status": "running", "started_at": started.isoformat(), "tokens_or_keys_in_report": False}
    try:
        url = database_url(args.database)
        database = require_empty(url)
        keys = generate_keys()
        store = Store(url, **keys)
        stores.append(store)
        tokens, owners, people = [], [], []
        for organization_index in range(2):
            owner = store.authenticate(store.bootstrap_org(f"SYNTHETIC qualification organization {organization_index + 1}")["owner_token"])
            owners.append(owner)
            organization_agents = []
            for agent_index in range(50):
                registration = store.register_agent(owner, f"SYNTHETIC {organization_index + 1}-{agent_index:02d}", capabilities=["synthetic-qualification"], public=False)
                token = registration["token"]
                tokens.append(token)
                organization_agents.append(store.authenticate(token))
            people.append(organization_agents)
        pairs = []
        for sender, recipient in zip(*people):
            invitation = store.invite_contact(sender, recipient.agent_id)
            store.accept_contact(recipient, invitation["id"])
            room = store.create_room(sender, "SYNTHETIC qualification room", [recipient.agent_id])
            store.accept_room(recipient, room["id"])
            pairs.append((sender, recipient, room["id"]))
        for _ in range(args.workers - 1):
            stores.append(Store(url, **keys))
        payload = {"classification": "synthetic", "purpose": "store-qualification", "text": "x" * 128}
        payload_bytes = len(canonical(payload))

        def accept(index, phase, submitted_at):
            caller, _, room_id = pairs[index % len(pairs)]
            operation_started = time.monotonic()
            receipt = stores[index % len(stores)].send_message(caller, room_id, "question", payload, f"qualification:{phase}:{index:04d}")
            accepted_at = time.monotonic()
            return {"message_id": receipt["id"], "room_id": room_id, "duplicate": receipt["duplicate"],
                    "acceptance_ms": (accepted_at - submitted_at) * 1000,
                    "operation_ms": (accepted_at - operation_started) * 1000}

        phases, results = {}, []
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            offered = args.rate * args.seconds
            phase_start = time.monotonic()
            futures = []
            for index in range(offered):
                scheduled_at = phase_start + index / args.rate
                delay = scheduled_at - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                futures.append(pool.submit(accept, index, "sustained", scheduled_at))
            sustained = [future.result() for future in futures]
            elapsed = time.monotonic() - phase_start
            phases["sustained"] = {"offered_rate_per_second": args.rate, "schedule_seconds": args.seconds,
                "observed_elapsed_seconds": round(elapsed, 3), "accepted": len(sustained),
                "submission_to_acceptance": latency_summary([item["acceptance_ms"] for item in sustained]),
                "store_call": latency_summary([item["operation_ms"] for item in sustained])}
            results.extend(sustained)
            phase_start = time.monotonic()
            futures = [pool.submit(accept, index, "burst", phase_start) for index in range(args.burst)]
            burst = [future.result() for future in futures]
            phases["burst"] = {"simultaneously_offered": args.burst, "worker_limit": args.workers,
                "observed_elapsed_seconds": round(time.monotonic() - phase_start, 3), "accepted": len(burst),
                "submission_to_acceptance": latency_summary([item["acceptance_ms"] for item in burst]),
                "store_call": latency_summary([item["operation_ms"] for item in burst])}
            results.extend(burst)
            sender, _, room_id = pairs[0]
            def retry(index):
                return stores[index % len(stores)].send_message(sender, room_id, "question", payload, "qualification:duplicate:shared")
            retries = list(pool.map(retry, range(args.workers * 2)))
        duplicate_safe = len({item["id"] for item in retries}) == 1 and sum(not item["duplicate"] for item in retries) == 1
        bound_conflict = False
        try:
            store.send_message(sender, room_id, "question", {"changed": "synthetic"}, "qualification:duplicate:shared")
        except NetworkError as exc:
            bound_conflict = exc.code == "idempotency_conflict"
        expected_messages = len(results) + 1
        for connection_store in stores:
            connection_store.close()
        stores.clear()
        reopened = Store(url, **keys)
        stores.append(reopened)
        fresh_principals = [reopened.authenticate(token) for token in tokens]
        chain_results = [reopened.verify_room(fresh_principals[index], room_id)["valid"] for index, (_, _, room_id) in enumerate(pairs)]
        deliveries_count = sum(len(reopened.poll_deliveries(fresh_principals[50 + index])["items"]) for index in range(50))
        with reopened.engine.connect() as db:
            actual_messages = db.scalar(select(func.count()).select_from(messages))
            actual_agents = db.scalar(select(func.count()).select_from(agents_table))
        isolated = False
        try:
            reopened.list_messages(fresh_principals[51], pairs[0][2])
        except NetworkError as exc:
            isolated = exc.status in {403, 404}
        reopened.set_agent_status(owners[1], pairs[0][1].agent_id, "revoked")
        revoked = False
        try:
            reopened.poll_deliveries(fresh_principals[50])
        except NetworkError as exc:
            revoked = exc.status == 401
        checks = {"stable_100_identities": actual_agents == 100,
                  "all_unique_requests_durable": actual_messages == expected_messages,
                  "single_acceptance_for_concurrent_duplicate": duplicate_safe,
                  "changed_duplicate_payload_rejected": bound_conflict,
                  "all_50_chains_valid_after_reopen": all(chain_results),
                  "offline_inbox_count_matches_accepted": deliveries_count == expected_messages,
                  "cross_room_access_denied": isolated, "revoked_recipient_access_denied": revoked}
        performance = {"target_p95_submission_to_acceptance_ms": 1000,
                       "sustained_target_met": phases["sustained"]["submission_to_acceptance"]["p95_ms"] < 1000,
                       "burst_target_met": phases["burst"]["submission_to_acceptance"]["p95_ms"] < 1000,
                       "meaning": "Initial engineering target from the design; not a production SLA"}
        target_met = performance["sustained_target_met"] and performance["burst_target_met"]
        status = "fail" if not all(checks.values()) else ("pass" if target_met else "partial")
        report.update({"status": status, "correctness_status": "pass" if all(checks.values()) else "fail",
            "performance_targets": performance, "finished_at": datetime.now(timezone.utc).isoformat(),
            "database": database, "environment": {"python": platform.python_version(), "platform": platform.platform(), "cpu_count": os.cpu_count()},
            "fixture": {"organizations": 2, "delegated_agents": 100, "accepted_private_rooms": 50, "independent_store_instances": args.workers,
                        "payload_bytes": payload_bytes, "expected_durable_messages": expected_messages,
                        "http_connections": 0, "independent_external_organizations": 0},
            "limits": {"workers": args.workers, "max_body_bytes": reopened.max_body_bytes, "max_turns_per_room": reopened.max_turns,
                       "agent_messages_per_minute": reopened.rate_limit, "organization_messages_per_minute": reopened.rate_limit * 10},
            "phases": phases, "correctness": checks,
            "command_receipt": {"command": "python scripts/qualify.py --database <isolated-database> --output <new-private-report> --allow-synthetic-database",
                                "rate": args.rate, "seconds": args.seconds, "burst": args.burst, "workers": args.workers,
                                "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
            "interpretation": "Synthetic persistence-layer qualification only. Excludes HTTP/TLS/MCP/model/source latency, production workloads and adoption. Reopen proves restart recovery, not backup restoration or an SLA.",
            "retained_database": "Disposable synthetic fixture; encryption and bearer credentials were held only in memory. No automatic database deletion."})
    except Exception as exc:
        # Exception strings can contain database URLs or SQL parameters.
        report.update(status="fail", finished_at=datetime.now(timezone.utc).isoformat(), error_class=type(exc).__name__,
                      error="Qualification refused or failed; no credentials, SQL parameters or exception text retained")
    finally:
        for store in stores:
            store.close()
        with os.fdopen(fd, "w") as handle:
            json.dump(report, handle, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--allow-synthetic-database", action="store_true")
    parser.add_argument("--rate", type=int, default=10)
    parser.add_argument("--seconds", type=int, default=10)
    parser.add_argument("--burst", type=int, default=100)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    try:
        report = run(args)
    except (OSError, ValueError):
        print("Qualification refused: use an explicitly isolated empty database and an unused private output path.", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "synthetic": True, "layer": "store_only"}))
    return 0 if report["status"] == "pass" else (3 if report["status"] == "partial" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
