"""Bounded source-status delivery, with synthetic traffic and no upstream I/O."""
import asyncio
import json
import os

from fastapi.testclient import TestClient
import pytest

from seiche_agent_network.api import create_app
from seiche_agent_network.context_stream import (
    ContextEvents, ContextStream, MAX_FRAME_BYTES, MAX_SNAPSHOT_BYTES, PATHS, SCHEMA,
    _stamp, project_report,
)
from seiche_agent_network.crypto import generate_keys
from seiche_agent_network.freshness import save_report
from seiche_agent_network.store import Store

NOW = 1792065600.0


def report(now=NOW):
    return {"schema": "seiche-agent-network.freshness.v1", "evaluated_at": _stamp(now),
            "products": [{"product": "seiche", "transport": "available", "freshness": "fresh",
                "scope": "inspected_source_rows_only_not_product_wide_readiness",
                "coverage": {"fresh": 3, "unknown": 0, "inspected_rows": 3, "observation_clock_rows": 3},
                "critical_subset": {"freshness": "fresh"},
                "clocks": [{"source_id": "fred:" + name, "observed_at": _stamp(now - 86400),
                    "cadence": "D", "clock_precision": "date", "max_age_seconds": 345600,
                    "freshness": "fresh", "reason": None} for name in ("SOFR", "EFFR", "IORB")]}]}


def loaded_hub(*, now=None, **kwargs):
    now = now or [NOW]
    hub = ContextStream(clock=lambda: now[0], heartbeat_seconds=.01, session_seconds=.04, **kwargs)
    hub.report, hub.read_status = report(), "ok"
    hub._publish()
    return hub


def decode_frames(messages):
    frames = []
    for message in messages:
        if message["type"] != "http.response.body" or not message.get("body"):
            continue
        fields = dict(line.split(": ", 1) for line in message["body"].decode().strip().splitlines())
        fields["data"] = json.loads(fields["data"])
        frames.append(fields)
    return frames


async def run_events(hub, *, traffic="synthetic", reconnect=False, on_send=None):
    messages = []
    disconnected = asyncio.Event()
    async def receive():
        await disconnected.wait()
        return {"type": "http.disconnect"}
    async def send(message):
        messages.append(message)
        if on_send:
            await on_send(message, disconnected)
    await ContextEvents(hub, traffic=traffic, reconnect=reconnect)({"type": "http"}, receive, send)
    return messages


def test_projection_keeps_clock_cadence_scope_coverage_and_missing_sources():
    source = report()
    source["products"][0]["clocks"].append({"source_id": "fred:HISTORICAL", "observed_at": "2021-01-01"})
    result = project_report(source, now=NOW)
    assert result["schema"] == SCHEMA
    assert result["source_monitor"] == {"status": "current", "current": True, "evaluated_at": _stamp(NOW),
        "valid_until": _stamp(NOW + 900), "max_age_seconds": 900}
    item = result["products"][0]
    assert item["omitted_clock_count"] == 1 and len(item["clocks"]) == 3
    assert item["clocks"][0]["observed_at"] == _stamp(NOW - 86400)
    assert item["clocks"][0]["cadence"] == "D" and item["coverage"]["inspected_rows"] == 3
    assert item["rights"]["status"] == "not_verified_by_this_stream"
    assert "undertow" in result["missing_products"]
    assert result["coverage_complete"] is False and result["input_eligibility"] == "not_evaluated"
    assert result["execution_authority"] == "none"


@pytest.mark.parametrize("age,status", [(899.999, "current"), (900, "stale"), (10000, "stale"), (-.001, "future_clock")])
def test_monitor_age_exact_boundary_and_future_clock_fail_closed(age, status):
    result = project_report(report(), now=NOW + age)
    assert result["source_monitor"]["status"] == status
    assert result["source_monitor"]["current"] is (status == "current")
    assert result["input_eligibility"] == ("not_evaluated" if status == "current" else "blocked_monitor")


@pytest.mark.parametrize("stamp", [None, "2026-10-15", "2026-10-15T12:00:00", "not a clock"])
def test_unqualified_monitor_clock_is_invalid(stamp):
    source = report()
    source["evaluated_at"] = stamp
    with pytest.raises(ValueError):
        project_report(source, now=NOW)


def test_future_announced_and_missing_observations_do_not_become_current():
    source = report()
    clock = source["products"][0]["clocks"][0]
    clock.update(observed_at=_stamp(NOW + 86400), effective_at="2026-10-16", clock_role="announced_forward_effective",
                 current_observation_available=False)
    result = project_report(source, now=NOW)
    output = result["products"][0]["clocks"][0]
    assert output["freshness_at_evaluation"] == "unknown" and output["current_observation_available"] is False
    assert output["clock_role"] == "announced_forward_effective" and output["effective_at"] == "2026-10-16"
    source["products"][0]["clocks"] = []
    result = project_report(source, now=NOW)
    assert len(result["products"][0]["critical_subset"]["missing_source_ids"]) == 3


def test_rights_restrictions_and_expiry_are_preserved_without_payloads():
    source = report()
    source["products"].append({"product": "undertow", "transport": "available", "freshness": "restricted",
        "rights": {"status": "pending_owner_review", "valid_until": _stamp(NOW - 1), "scope": "derived_metadata_only",
                   "publication_allowed": False, "secret": "customer-key"},
        "raw_payload": {"trade": "private-order"}, "clocks": [], "private_strategy": "secret-strategy"})
    output = project_report(source, now=NOW)
    rights = output["products"][1]["rights"]
    assert rights["status"] == "pending_owner_review" and rights["restricted"] is True
    assert rights["valid_until"] == _stamp(NOW - 1) and rights["publication_allowed"] is False
    assert all(value not in json.dumps(output) for value in ("customer-key", "private-order", "secret-strategy"))


def test_no_sequence_churn_from_age_but_expiry_and_clock_rollback_publish():
    now = [NOW]
    hub = loaded_hub(now=now)
    initial = hub.current()
    now[0] += 899
    assert hub.current() is initial
    assert hub.current()["sequence"] == initial["sequence"]
    now[0] += 1
    stale = hub.current()
    assert stale["sequence"] == initial["sequence"] + 1
    assert stale["source_monitor"]["status"] == "stale"
    now[0] = NOW - 1
    assert hub.current()["source_monitor"]["status"] == "future_clock"


async def test_request_path_never_reads_files_or_calls_upstream(monkeypatch):
    hub = loaded_hub()
    monkeypatch.setattr(hub, "_read", lambda: pytest.fail("request performed I/O"))
    assert hub.current()["source_monitor"]["current"]
    messages = await run_events(hub)
    assert messages[0]["status"] == 200


async def test_atomic_private_file_refresh_missing_invalid_and_recovery(tmp_path):
    path = tmp_path / "freshness.json"
    hub = ContextStream(path=str(path), clock=lambda: NOW)
    await hub.refresh()
    assert hub.current()["source_monitor"]["status"] == "missing_report"
    save_report(path, report())
    await hub.refresh()
    assert hub.current()["source_monitor"]["current"]
    os.chmod(path, 0o644)
    await hub.refresh()
    assert hub.current()["source_monitor"]["status"] == "invalid_report"
    save_report(path, report())
    await hub.refresh()
    assert hub.current()["source_monitor"]["current"]
    path.write_text("x" * 1048577)
    await hub.refresh()
    assert hub.current()["source_monitor"]["status"] == "invalid_report"


async def test_symlink_source_is_rejected(tmp_path):
    path, alias = tmp_path / "report.json", tmp_path / "alias.json"
    save_report(path, report())
    alias.symlink_to(path)
    hub = ContextStream(path=str(alias), clock=lambda: NOW)
    await hub.refresh()
    assert hub.current()["source_monitor"]["status"] == "invalid_report"


def test_snapshot_and_frame_size_bounds_and_stable_oversize_failure():
    hub = loaded_hub()
    assert len(hub.body) <= MAX_SNAPSHOT_BYTES
    source = report()
    source["products"] = []
    from seiche_agent_network.gateway import PRODUCTS
    for product in PRODUCTS:
        source["products"].append({"product": product, "transport": "available", "freshness": "unknown",
            "scope": "s" * 240, "reasons": ["r" * 160] * 6, "rights": {"status": "r" * 160}, "clocks": []})
    hub.report = source
    hub._publish()
    sequence = hub.sequence
    assert hub.current()["source_monitor"]["status"] == "projection_too_large"
    for _ in range(5):
        hub._publish()
    assert hub.sequence == sequence and len(hub.body) <= MAX_SNAPSHOT_BYTES


@pytest.mark.parametrize("reconnect,reason", [(False, "initial"), (True, "reconnect_latest")])
async def test_stream_starts_full_reset_and_keepalive_does_not_claim_freshness(reconnect, reason):
    hub = loaded_hub()
    frames = decode_frames(await run_events(hub, reconnect=reconnect))
    assert frames[0]["event"] == "reset"
    assert frames[0]["id"] == f"{hub.stream_id}:{hub.sequence}"
    assert frames[0]["data"]["delivery"] == {"reason": reason, "replay_complete": False}
    assert frames[0]["data"]["schema"] == SCHEMA and frames[0]["data"]["products"]
    assert frames[1]["event"] == "keepalive"
    assert frames[1]["data"] == {"transport_only": True, "source_freshness_proof": False}
    assert "id" not in frames[1]
    assert sum(hub.active.values()) == 0 and hub.counters["synthetic"]["session_expirations"] == 1
    assert not hub.counters["unknown"]


async def test_slow_consumer_gets_explicit_latest_reset_instead_of_fake_replay():
    hub = loaded_hub()
    sent = 0
    async def publish_two(message, disconnected):
        nonlocal sent
        if message["type"] == "http.response.body" and message.get("body"):
            sent += 1
            if sent == 1:
                for offset in (1, 2):
                    hub.report = report(NOW - offset)
                    hub._publish()
    frames = decode_frames(await run_events(hub, on_send=publish_two))
    assert frames[1]["event"] == "reset"
    assert frames[1]["data"]["delivery"]["reason"] == "updates_coalesced"
    assert frames[1]["data"]["sequence"] == frames[0]["data"]["sequence"] + 2


async def test_single_update_is_snapshot_and_monitor_expiry_is_an_update():
    now = [NOW]
    hub = loaded_hub(now=now)
    sent = 0
    async def expire(message, disconnected):
        nonlocal sent
        if message["type"] == "http.response.body" and message.get("body"):
            sent += 1
            if sent == 1:
                now[0] += 900
                hub.current()
    messages = await run_events(hub, on_send=expire)
    frames = decode_frames(messages)
    assert frames[1]["event"] == "snapshot" and frames[1]["data"]["input_eligibility"] == "blocked_monitor"
    assert all(len(item.get("body", b"")) <= MAX_FRAME_BYTES for item in messages)


async def test_capacity_rejection_and_disconnect_return_slots():
    hub = loaded_hub(max_subscribers=1)
    admitted = asyncio.Event()
    disconnect = asyncio.Event()
    async def receive():
        await disconnect.wait()
        return {"type": "http.disconnect"}
    async def send(message):
        if message["type"] == "http.response.body":
            admitted.set()
    task = asyncio.create_task(ContextEvents(hub, traffic="unknown")({}, receive, send))
    await admitted.wait()
    assert hub.active["unknown"] == 1
    blocked = await run_events(hub)
    assert blocked[0]["status"] == 503 and (b"retry-after", b"5") in blocked[0]["headers"]
    disconnect.set()
    await task
    assert sum(hub.active.values()) == 0
    assert hub.counters["unknown"]["disconnects"] == 1
    assert hub.counters["synthetic"]["capacity_rejections"] == 1


async def test_slow_socket_deadline_and_outer_cancellation_release_slot():
    hub = loaded_hub(write_seconds=.01)
    async def never_send(message, disconnected):
        await asyncio.Event().wait()
    await run_events(hub, on_send=never_send)
    assert hub.counters["synthetic"]["write_failures"] == 1 and sum(hub.active.values()) == 0
    task = asyncio.create_task(run_events(hub))
    await asyncio.sleep(.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sum(hub.active.values()) == 0


async def test_worker_lifecycle_and_shutdown_wakes_streams(tmp_path):
    path = tmp_path / "report.json"
    save_report(path, report())
    hub = ContextStream(path=str(path), clock=lambda: NOW, poll_seconds=.005)
    await hub.start()
    assert hub.current()["source_monitor"]["current"]
    save_report(path, report(NOW - 901))
    for _ in range(100):
        if not hub.current()["source_monitor"]["current"]:
            break
        await asyncio.sleep(.002)
    assert hub.current()["input_eligibility"] == "blocked_monitor"
    task = asyncio.create_task(run_events(hub))
    await asyncio.sleep(.005)
    await hub.stop()
    await asyncio.wait_for(task, .1)
    assert hub.worker.done() and sum(hub.active.values()) == 0


def test_public_routes_cors_no_auth_and_synthetic_counter(tmp_path, monkeypatch):
    path = tmp_path / "report.json"
    import time
    save_report(path, report(time.time()))
    monkeypatch.setenv("SEICHE_FRESHNESS_REPORT", str(path))
    store = Store(tmp_path / "network.db", **generate_keys())
    app = create_app(store, gateway=True)
    app.state.context_stream.session_seconds = .01
    with TestClient(app) as client:
        headers = {"Origin": "https://liquilens.in", "X-Financial-Evidence-Traffic-Class": "synthetic"}
        for path in PATHS - {"/v1/context/events"}:
            response = client.get(path, headers=headers)
            assert response.status_code == 200 and response.headers["access-control-allow-origin"] == "https://liquilens.in"
            assert "no-store" in response.headers["cache-control"]
        stream = client.get("/v1/context/events", headers=headers)
        assert stream.status_code == 200 and stream.text.startswith("id: ")
        assert "event: reset" in stream.text
        assert "content-length" not in stream.headers
        assert client.get("/v1/context/snapshot", headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.get("/v1/context/events", headers={"Last-Event-ID": "private arbitrary data"}).status_code == 400
        good = client.options("/v1/context/events", headers={"Origin": "https://liquilens.in",
            "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "Last-Event-ID"})
        assert good.status_code == 200
        denied = client.options("/v1/context/events", headers={"Origin": "https://liquilens.in", "Access-Control-Request-Method": "POST"})
        assert denied.status_code == 403
        assert client.post("/v1/context/events", headers=headers).status_code == 403
        metrics = client.get("/v1/context/metrics").json()
        assert metrics["counters"]["synthetic"]["snapshot_requests"] == 1
        assert metrics["counters"]["synthetic"]["stream_connections"] == 1
        assert metrics["independent_users"] == "not_established"
    assert app.state.context_stream.worker.done()
    store.close()
