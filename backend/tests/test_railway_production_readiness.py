"""Startup waits cannot turn unhealthy or stale production into accepted proof."""

import importlib.util
import io
from pathlib import Path
from urllib.error import HTTPError

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "wait_production_ready", ROOT / "ops/railway/wait_production_ready.py"
)
assert spec and spec.loader
readiness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(readiness)
ORIGIN = "https://seiche-stateful-core-production.up.railway.app"


def exercise(responses, *, timeout=30, io_seconds=0):
    now = 0.0
    calls = []

    def fetch(url, budget):
        nonlocal now
        calls.append((url, budget))
        now += io_seconds
        return next(responses)

    def pause(seconds):
        nonlocal now
        assert 0 <= seconds <= 10
        now += seconds

    return readiness.wait_until_ready(
        ORIGIN, timeout, fetch=fetch, clock=lambda: now, pause=pause
    ), calls


def test_startup_can_become_ready_without_accepting_preserved_snapshot():
    ready = {"status": "ready", "mode": "production"}
    result, calls = exercise(
        iter(
            [
                (503, {"status": "warming_or_unavailable"}),
                (503, {"status": "rebuilding_from_last_known_good"}),
                (200, ready),
            ]
        )
    )
    assert result == ready
    assert calls == [
        (ORIGIN + "/healthz", 30),
        (ORIGIN + "/healthz", 20),
        (ORIGIN + "/healthz", 10),
    ]


@pytest.mark.parametrize(
    "response",
    [
        (403, {"status": "warming_or_unavailable"}),
        (503, {"status": "agent_room_not_ready"}),
        (503, {"status": "rebuilt_without_market_evidence"}),
        (200, {"status": "ready", "mode": "cutover_candidate"}),
        (200, {"status": "rebuilding_from_last_known_good", "mode": "production"}),
    ],
)
def test_real_failures_are_not_retried(response):
    with pytest.raises(ValueError):
        exercise(iter([response]))


def test_startup_wait_has_a_deadline():
    with pytest.raises(TimeoutError):
        exercise(iter([(503, {"status": "warming_or_unavailable"})]), timeout=4)


def test_request_duration_counts_toward_deadline():
    with pytest.raises(TimeoutError):
        exercise(
            iter([(200, {"status": "ready", "mode": "production"})]),
            timeout=4,
            io_seconds=5,
        )


def test_http_503_payload_is_available_for_startup_classification(monkeypatch):
    class StartupResponse:
        def open(self, request, timeout):
            raise HTTPError(
                request.full_url,
                503,
                "Unavailable",
                {},
                io.BytesIO(b'{"status":"rebuilding_from_last_known_good"}'),
            )

    monkeypatch.setattr(readiness, "build_opener", lambda *args: StartupResponse())
    assert readiness.read_health(ORIGIN + "/healthz", 1) == (
        503,
        {"status": "rebuilding_from_last_known_good"},
    )


def test_transport_failures_are_not_retried():
    def failed_fetch(url, timeout):
        raise OSError("connection refused")

    with pytest.raises(OSError, match="connection refused"):
        readiness.wait_until_ready(ORIGIN, 30, fetch=failed_fetch)


def test_workflow_takes_fresh_edge_samples_after_runtime_ready():
    text = (ROOT / ".github/workflows/railway-stateful-recovery.yml").read_text()
    step = text.split(
        "- name: Prove native backups, PITR coverage, volume headroom, and both edges",
        1,
    )[1]
    step = step.split("- name: Remove the private PostgreSQL probe transport", 1)[0]
    assert step.index('wait_production_ready.py"') < step.index("origin_status=$(curl")
    assert "timedelta(minutes=15)" in step
    assert 'body.get("faults") != []' in step
    assert 'runtime.get("mode") != "production"' in step
