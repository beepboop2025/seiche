import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


spec = importlib.util.spec_from_file_location("context_latency_monitor", Path(__file__).parents[1] / "ops/context_latency_monitor.py")
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


def payload(**changes):
    source = {"current": True, "status": "current", "max_age_seconds": 900,
              "evaluated_at": (NOW-timedelta(seconds=60)).isoformat(),
              "valid_until": (NOW+timedelta(seconds=840)).isoformat()}
    source.update(changes)
    return {"schema": "seiche-agent-network.context.v1", "scope": "source_status_metadata",
            "execution_authority": "none", "input_eligibility": "not_evaluated", "source_monitor": source}


class Response:
    status = 200
    def __init__(self, value):
        self.body = json.dumps(value).encode()
    def __enter__(self):
        return self
    def __exit__(self, *_):
        pass
    def read(self, limit):
        return self.body[:limit]


class Opener:
    def __init__(self, value):
        self.value, self.calls = value, 0
    def open(self, request, timeout):
        self.calls += 1
        assert request.get_header("X-financial-evidence-traffic-class") == "synthetic"
        assert request.get_header("Authorization") is None
        return Response(self.value)


def test_transport_and_source_monitor_are_independent():
    current = monitor.measure(opener=Opener(payload()), now=NOW)
    assert current["status"] == "passed"
    assert current["source_monitor_status"] == "current"
    assert current["p95_ms"] is None and current["sla_verified"] is False
    for changes in ({"current": False}, {"status": "stale"}, {"max_age_seconds": 3600},
                    {"evaluated_at": (NOW+timedelta(seconds=1)).isoformat()},
                    {"valid_until": NOW.isoformat()},
                    {"evaluated_at": "2026-10-15T11:59:00"}):
        result = monitor.measure(opener=Opener(payload(**changes)), now=NOW)
        assert result["status"] == "passed"
        assert result["source_monitor_status"] == "stale_or_unavailable"


def test_invalid_contract_stops_probing_and_never_retains_payload():
    opener = Opener({"credential": "must-not-be-retained"})
    result = monitor.measure(opener=opener, now=NOW)
    assert result["status"] == "partial" and opener.calls == 1
    assert "must-not-be-retained" not in json.dumps(result)


def test_atomic_history_replaces_symlink_without_touching_target(tmp_path):
    result = monitor.measure(opener=Opener(payload()), now=NOW)
    history = tmp_path / "history"
    history.mkdir()
    target = tmp_path / "unrelated"
    target.write_text("preserved")
    receipt = history / (result["observed_at"].replace(":", "-") + ".json")
    receipt.symlink_to(target)
    monitor.persist(result, tmp_path / "latest.json")
    assert target.read_text() == "preserved"
    assert not receipt.is_symlink()
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert json.loads(receipt.read_text()) == result


def test_redirect_is_never_followed():
    assert monitor.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid") is None
