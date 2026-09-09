"""Health validation must not stall unrelated work on the API request loop."""

import asyncio
import threading

from fastapi.responses import Response
import pytest

from seiche import api, mcp_server, stateful_cutover
from seiche.repository import PostgresMarketRepository


@pytest.mark.parametrize("handler_name", ["health", "release_health", "railway_stateful_health"])
def test_slow_health_dependency_allows_other_coroutines_to_progress(monkeypatch, handler_name):
    entered = threading.Event()
    release = threading.Event()
    peer_progressed = threading.Event()
    observed = []

    def slow_health(*args, **kwargs):
        entered.set()
        assert release.wait(3), "The independent watchdog did not release the fixture"
        observed.append(peer_progressed.is_set())
        return {"version": "fixture", "generated_at": "2026-09-09T00:00:00Z"}

    def watchdog():
        if entered.wait(3):
            # Release even a blocked event loop, so the broken implementation
            # fails the progress assertion rather than hanging the test runner.
            release.wait(0.25)
        release.set()

    monkeypatch.setattr(api, "_health_response", slow_health)
    monkeypatch.setenv("SEICHE_RAILWAY_STATEFUL_MODE", "production")
    monkeypatch.setattr(mcp_server, "agent_room_release_ready", lambda: True)
    monkeypatch.setattr(stateful_cutover, "validate_activation_runtime", lambda environment: {
        "authority": {"source": "railway", "hetzner_writers_frozen": True,
                      "railway_writers_started": True, "public_traffic_enabled": True},
    })

    async def exercise():
        task = asyncio.create_task(getattr(api, handler_name)(Response()))
        await asyncio.sleep(0)
        peer_progressed.set()
        return await task

    worker = threading.Thread(target=watchdog)
    worker.start()
    try:
        result = asyncio.run(exercise())
    finally:
        release.set()
        worker.join(timeout=4)
    assert not worker.is_alive()
    assert result["version"] == "fixture"
    assert observed == [True]


def test_heartbeat_read_deadlines_preserve_unknown_fault_and_redact_details(monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    calls = []
    statements = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, statement, *parameters):
            statements.append(statement)
            if statement.startswith("SET LOCAL"):
                return None
            raise TimeoutError("private database transport detail")

    def connect(dsn, **options):
        calls.append(options)
        return Connection()

    monkeypatch.setattr(psycopg, "connect", connect)
    repository = PostgresMarketRepository("postgresql://example.invalid/health-test")
    repository._initialized = True
    fault = api._collector_worker_fault(repository=repository)

    assert calls == [{"connect_timeout": 2}]
    assert statements[0] == "SET LOCAL statement_timeout = '2000ms'"
    assert "FROM worker_heartbeats" in statements[1]
    assert fault["status"] == "UNKNOWN"
    assert fault["detail"] == "official collector worker health is unavailable"
    assert "private" not in str(fault)
