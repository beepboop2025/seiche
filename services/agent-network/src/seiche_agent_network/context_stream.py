"""Precomputed public source-status snapshots, not a market quote/event feed.

Only the scheduled first-party freshness report is read, off the request path.
Every stream message replaces the complete snapshot; no replay log is implied.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import suppress
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import time
import uuid

from fastapi import Request
from starlette.responses import JSONResponse, Response

from .gateway import PRODUCTS
from .upstream_auth import read_private_json

SCHEMA = "seiche-agent-network.context.v1"
PATHS = frozenset({"/v1/context/contract", "/v1/context/snapshot", "/v1/context/events", "/v1/context/metrics"})
MAX_SNAPSHOT_BYTES = 16384
MAX_FRAME_BYTES = 17408
MAX_REPORT_BYTES = 1048576
MONITOR_MAX_AGE_SECONDS = 900
STATES = frozenset({"fresh", "stale", "unknown", "restricted"})
CORE_SOURCES = frozenset({"fred:SOFR", "fred:EFFR", "fred:IORB"})


def _json(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()


def _stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def _clock(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def _label(value, maximum=160):
    # Only contract identifiers and human-readable scope metadata, never blobs.
    return value if isinstance(value, str) and len(value) <= maximum and all(32 <= ord(c) < 127 for c in value) else None


def _number(value):
    return value if type(value) in {int, float} and 0 <= value <= 100 * 366 * 86400 else None


def _clock_row(row, evaluated_at):
    if not isinstance(row, dict) or not _label(row.get("source_id")):
        raise ValueError("Invalid source clock")
    observed = _clock(row.get("observed_at"))
    state = row.get("freshness") if row.get("freshness") in STATES else "unknown"
    reason = _label(row.get("reason"))
    if observed is None or observed > evaluated_at:
        state = "unknown"
        reason = "future_observation_clock" if observed is not None else "missing_observation_clock"
    result = {"source_id": row["source_id"], "observed_at": _stamp(observed) if observed is not None else None,
              "cadence": _label(row.get("cadence"), 32), "clock_precision": _label(row.get("clock_precision"), 32),
              "max_age_seconds": _number(row.get("max_age_seconds")), "freshness_at_evaluation": state,
              "reason": reason}
    for key in ("clock_role", "policy", "effective_at", "available_at", "latest_expected_release_at", "expected_observation_date"):
        if _label(row.get(key)) is not None:
            result[key] = row[key]
    for key in ("current_observation_available", "schedule_is_publication_receipt"):
        if type(row.get(key)) is bool:
            result[key] = row[key]
    return result


def _product(row, evaluated_at):
    if not isinstance(row, dict) or row.get("product") not in PRODUCTS:
        raise ValueError("Invalid product")
    if row.get("freshness") not in STATES or row.get("transport") not in {"available", "unavailable", "tool_error"}:
        raise ValueError("Invalid product status")
    clocks = row.get("clocks", [])
    if not isinstance(clocks, list) or len(clocks) > 2000:
        raise ValueError("Invalid clocks")
    # Preserve funding clocks and research venue clocks; full source coverage
    # remains available at /v1/data-status, not silently claimed by this subset.
    selected = [item for item in clocks if isinstance(item, dict) and (
        row["product"] == "undertow" or row["product"] == "seiche" and item.get("source_id") in CORE_SOURCES)][:6]
    coverage = row.get("coverage", {})
    coverage = coverage if isinstance(coverage, dict) else {}
    rights = row.get("rights", {})
    rights = rights if isinstance(rights, dict) else {}
    result = {"product": row["product"], "scope": _label(row.get("scope"), 240),
              "transport": row["transport"], "freshness_at_evaluation": row["freshness"],
              "coverage": {key: coverage[key] for key in ("inspected_rows", "observation_clock_rows", "fresh", "stale", "unknown", "restricted")
                           if type(coverage.get(key)) is int and 0 <= coverage[key] <= 2000},
              "rights": {"status": _label(rights.get("status")) or "not_verified_by_this_stream"},
              "clocks": [_clock_row(item, evaluated_at) for item in selected],
              "omitted_clock_count": len(clocks) - len(selected),
              "reasons": [_label(item) for item in row.get("reasons", [])[:6] if _label(item)] if isinstance(row.get("reasons", []), list) else []}
    for key in ("use_boundary", "source_policy", "native_availability"):
        if _label(row.get(key)) is not None:
            result[key] = row[key]
    for key in ("scope", "valid_until", "policy", "use_boundary"):
        if _label(rights.get(key)) is not None:
            result["rights"][key] = rights[key]
    for key in ("publication_allowed", "scoring_allowed", "context_only"):
        value = rights.get(key, row.get(key))
        if type(value) is bool:
            result["rights"][key] = value
    if row.get("freshness") == "restricted":
        result["rights"]["restricted"] = True
    if row["product"] == "seiche":
        critical = row.get("critical_subset", {})
        critical = critical if isinstance(critical, dict) else {}
        present = {item["source_id"] for item in result["clocks"]}
        critical_state = critical.get("freshness") if critical.get("freshness") in STATES else "unknown"
        if critical_state == "fresh" and (present != CORE_SOURCES or any(item["freshness_at_evaluation"] != "fresh" for item in result["clocks"])):
            critical_state = "unknown"
        result["critical_subset"] = {"definition": "seiche-us-funding-core-v1",
            "freshness_at_evaluation": critical_state,
            "source_ids": sorted(CORE_SOURCES), "missing_source_ids": sorted(CORE_SOURCES - present),
            "scope": "funding_clock_check_not_input_admission"}
    return result


def project_report(report, *, now):
    """Allowlist the existing metadata producer; never preserve arbitrary keys."""
    if not isinstance(report, dict) or report.get("schema") != "seiche-agent-network.freshness.v1":
        raise ValueError("Unknown source report schema")
    evaluated = _clock(report.get("evaluated_at"))
    rows = report.get("products")
    if evaluated is None or not isinstance(rows, list) or len(rows) > len(PRODUCTS):
        raise ValueError("Invalid report clock or rows")
    products = [_product(row, evaluated) for row in rows]
    if len({row["product"] for row in products}) != len(products):
        raise ValueError("Duplicate product")
    status = "future_clock" if evaluated > now else "stale" if now >= evaluated + MONITOR_MAX_AGE_SECONDS else "current"
    return {"schema": SCHEMA, "scope": "source_status_metadata",
            "source_monitor": {"status": status, "current": status == "current", "evaluated_at": _stamp(evaluated),
                               "valid_until": _stamp(evaluated + MONITOR_MAX_AGE_SECONDS), "max_age_seconds": MONITOR_MAX_AGE_SECONDS},
            "products": products, "missing_products": sorted(set(PRODUCTS) - {row["product"] for row in products}),
            "coverage_complete": False, "input_eligibility": "not_evaluated" if status == "current" else "blocked_monitor",
            "execution_authority": "none", "raw_payloads_retained": False}


def unavailable(status):
    return {"schema": SCHEMA, "scope": "source_status_metadata",
            "source_monitor": {"status": status, "current": False, "evaluated_at": None, "valid_until": None,
                               "max_age_seconds": MONITOR_MAX_AGE_SECONDS}, "products": [], "missing_products": sorted(PRODUCTS),
            "coverage_complete": False, "input_eligibility": "blocked_monitor", "execution_authority": "none",
            "raw_payloads_retained": False}


class ContextStream:
    def __init__(self, *, path=None, clock=time.time, poll_seconds=1, heartbeat_seconds=15,
                 max_subscribers=64, session_seconds=300, write_seconds=5):
        self.path = path
        self.clock = clock
        self.poll_seconds, self.heartbeat_seconds = poll_seconds, heartbeat_seconds
        self.max_subscribers, self.session_seconds, self.write_seconds = max_subscribers, session_seconds, write_seconds
        self.stream_id, self.sequence = uuid.uuid4().hex, 0
        self.started_at = _stamp(clock())
        self.changed = asyncio.Event()
        self.closing = False
        self.worker = None
        self.report = None
        self.read_status = "not_configured" if not path else "not_started"
        self.counters = {traffic: Counter() for traffic in ("synthetic", "unknown")}
        self.active = {traffic: 0 for traffic in self.counters}
        self._revision = None
        self._publish()

    def _read(self):
        if not self.path:
            return None, "not_configured"
        try:
            return read_private_json(self.path, limit=MAX_REPORT_BYTES), "ok"
        except FileNotFoundError:
            return None, "missing_report"
        except (ValueError, TypeError, OSError, RecursionError, OverflowError):
            return None, "invalid_report"

    def _publish(self):
        try:
            value = project_report(self.report, now=self.clock()) if self.read_status == "ok" else unavailable(self.read_status)
        except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
            value = unavailable("invalid_report")
        # Leave room for the fixed revision/sequence/publication envelope.
        if len(_json(value)) > MAX_SNAPSHOT_BYTES - 384:
            value = unavailable("projection_too_large")
        revision = hashlib.sha256(_json(value)).hexdigest()
        if revision == self._revision:
            return
        self.sequence += 1
        value.update(stream_id=self.stream_id, sequence=self.sequence, revision=revision, published_at=_stamp(self.clock()))
        body = _json(value)
        self._revision, self.snapshot, self.body = revision, value, body
        previous, self.changed = self.changed, asyncio.Event()
        previous.set()

    async def refresh(self):
        self.report, self.read_status = await asyncio.to_thread(self._read)
        self._publish()

    def current(self):
        # Ordinary requests return precomputed bytes. The only request-time
        # projection is a clock transition, without I/O, so expiry fails closed
        # even if the background task has been delayed.
        monitor = self.snapshot["source_monitor"]
        evaluated = _clock(monitor["evaluated_at"])
        if evaluated is not None:
            now = self.clock()
            status = "future_clock" if evaluated > now else "stale" if now >= evaluated + MONITOR_MAX_AGE_SECONDS else "current"
            if status != monitor["status"]:
                self._publish()
        return self.snapshot

    async def start(self):
        if self.worker is not None or self.closing:
            raise RuntimeError("Context stream lifecycle is already started or closed")
        await self.refresh()
        async def run():
            while True:
                await asyncio.sleep(self.poll_seconds)
                await self.refresh()
        self.worker = asyncio.create_task(run())

    async def stop(self):
        self.closing = True
        self.changed.set()
        if self.worker:
            self.worker.cancel()
            with suppress(asyncio.CancelledError):
                await self.worker

    def metrics(self):
        return {"schema": "seiche-agent-network.context-metrics.v1", "stream_id": self.stream_id,
                "since": self.started_at, "scope": "process_local_resets_on_restart",
                "active_subscribers": dict(self.active), "counters": {key: dict(value) for key, value in self.counters.items()},
                "independent_users": "not_established", "source_freshness_proof": False}


def traffic_class(request):
    return "synthetic" if request.headers.get("x-financial-evidence-traffic-class") == "synthetic" else "unknown"


class ContextEvents(Response):
    media_type = "text/event-stream"

    def __init__(self, hub, *, traffic, reconnect=False):
        super().__init__(content=None, headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
        self.raw_headers = [(key, value) for key, value in self.raw_headers if key != b"content-length"]
        self.hub, self.traffic, self.reconnect = hub, traffic, reconnect

    async def __call__(self, scope, receive, send):
        hub, counters = self.hub, self.hub.counters[self.traffic]
        # No await between capacity check and acquisition on this event loop.
        if hub.closing or sum(hub.active.values()) >= hub.max_subscribers:
            counters["capacity_rejections"] += 1
            response = JSONResponse({"error": {"code": "context_capacity", "message": "Reconnect after the retry interval"}},
                                    status_code=503, headers={"Retry-After": "5"})
            try:
                await asyncio.wait_for(response(scope, receive, send), hub.write_seconds)
            except (asyncio.TimeoutError, OSError):
                counters["write_failures"] += 1
            return
        hub.active[self.traffic] += 1
        counters["stream_connections"] += 1

        async def write(message):
            await asyncio.wait_for(send(message), hub.write_seconds)

        async def events():
            await write({"type": "http.response.start", "status": 200, "headers": self.raw_headers})
            last_sequence = None
            deadline = asyncio.get_running_loop().time() + hub.session_seconds
            while not hub.closing:
                snapshot = hub.current()
                changed = hub.changed
                if snapshot["sequence"] != last_sequence:
                    coalesced = last_sequence is not None and snapshot["sequence"] != last_sequence + 1
                    reset = last_sequence is None or coalesced
                    reason = ("reconnect_latest" if self.reconnect else "initial") if last_sequence is None else "updates_coalesced" if coalesced else "update"
                    data = {**snapshot, "delivery": {"reason": reason, "replay_complete": False}}
                    frame = (f"id: {hub.stream_id}:{snapshot['sequence']}\nevent: {'reset' if reset else 'snapshot'}\nretry: 3000\ndata: ".encode()
                             + _json(data) + b"\n\n")
                    if len(frame) > MAX_FRAME_BYTES:
                        raise ValueError("Context frame exceeds bound")
                    await write({"type": "http.response.body", "body": frame, "more_body": True})
                    counters["snapshot_frames"] += 1
                    last_sequence = snapshot["sequence"]
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    counters["session_expirations"] += 1
                    break
                try:
                    await asyncio.wait_for(changed.wait(), min(hub.heartbeat_seconds, remaining))
                except asyncio.TimeoutError:
                    if asyncio.get_running_loop().time() >= deadline:
                        counters["session_expirations"] += 1
                        break
                    frame = b'event: keepalive\ndata: {"transport_only":true,"source_freshness_proof":false}\n\n'
                    await write({"type": "http.response.body", "body": frame, "more_body": True})
                    counters["keepalive_frames"] += 1
            await write({"type": "http.response.body", "body": b"", "more_body": False})

        async def disconnected():
            while (await receive())["type"] != "http.disconnect":
                pass

        producer = asyncio.create_task(events())
        listener = asyncio.create_task(disconnected())
        try:
            done, _ = await asyncio.wait({producer, listener}, return_when=asyncio.FIRST_COMPLETED)
            if producer in done:
                await producer
            else:
                counters["disconnects"] += 1
        except (asyncio.TimeoutError, OSError):
            counters["write_failures"] += 1
        finally:
            for task in (producer, listener):
                task.cancel()
            await asyncio.gather(producer, listener, return_exceptions=True)
            hub.active[self.traffic] -= 1


def mount_context(app):
    hub = ContextStream(path=os.environ.get("SEICHE_FRESHNESS_REPORT"))
    app.state.context_stream = hub

    @app.get("/v1/context/contract")
    async def contract():
        return {"schema": "seiche-agent-network.context-contract.v1", "snapshot_schema": SCHEMA,
                "snapshot": "/v1/context/snapshot", "events": "/v1/context/events", "metrics": "/v1/context/metrics",
                "content": "source_status_metadata_only", "market_quotes": False, "execution_authority": "none",
                "source": "scheduled_first_party_freshness_report", "upstream_requests_per_client_request": 0,
                "source_poll_seconds": hub.poll_seconds, "source_monitor_max_age_seconds": MONITOR_MAX_AGE_SECONDS,
                "snapshot_max_bytes": MAX_SNAPSHOT_BYTES, "frame_max_bytes": MAX_FRAME_BYTES,
                "selected_clocks": "seiche_funding_core_and_undertow_research_venue_clocks",
                "complete_source_status": "/v1/data-status", "freshness": "at_evaluation_not_live_input_admission",
                "reconnection": "always_reset_to_latest_complete_snapshot", "replay_available": False,
                "event_id": "stream_id:sequence", "sequence_scope": "process_lifetime_content_changes_and_monitor_transitions",
                "slow_consumers": "coalesced_latest_snapshot_with_reset_on_sequence_gap",
                "keepalive": "transport_only_not_source_freshness", "heartbeat_seconds": hub.heartbeat_seconds,
                "max_subscribers_per_process": hub.max_subscribers, "session_seconds": hub.session_seconds,
                "write_deadline_seconds": hub.write_seconds, "latency_slo": "not_established",
                "client_requirement": "Expire source_monitor at valid_until locally; fetch a new snapshot after a reset or sequence gap"}

    @app.get("/v1/context/snapshot")
    async def snapshot(request: Request):
        hub.current()
        hub.counters[traffic_class(request)]["snapshot_requests"] += 1
        return Response(hub.body, media_type="application/json")

    @app.get("/v1/context/events")
    async def events(request: Request):
        resume = request.headers.get("last-event-id", "")
        if resume and not re.fullmatch(r"[a-f0-9]{32}:[1-9][0-9]{0,19}", resume):
            return JSONResponse({"error": {"code": "invalid_event_id", "message": "Last-Event-ID is invalid"}}, status_code=400)
        return ContextEvents(hub, traffic=traffic_class(request), reconnect=bool(resume))

    @app.get("/v1/context/metrics")
    async def metrics():
        return hub.metrics()
