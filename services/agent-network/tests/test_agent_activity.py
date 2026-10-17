from datetime import datetime, timezone, timedelta
import json

from fastapi.testclient import TestClient

from seiche_agent_network.agent_activity import read_report
from seiche_agent_network.api import create_app
from seiche_agent_network.crypto import generate_keys
from seiche_agent_network.store import Store


def test_operator_boundary_and_missing_report(monkeypatch, tmp_path):
    token = "operator-activity-report-fixture-123456789"
    monkeypatch.setenv("SEICHE_OPERATOR_REPORT_TOKEN", token)
    monkeypatch.delenv("SEICHE_AGENT_ACTIVITY_REPORT", raising=False)
    store = Store(tmp_path / "network.db", **generate_keys())
    owner = store.bootstrap_org("Fixture")
    app = create_app(store, public_url="https://agents.seiche.info", gateway=True, purge_interval=0)
    with TestClient(app, base_url="https://agents.seiche.info") as client:
        path = "/v1/operator/agent-activity"
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer " + owner["owner_token"]}).status_code == 401
        response = client.get(path, headers={"Authorization": "Bearer " + token})
        assert response.status_code == 200 and response.json()["status"] == "not_configured"
        assert "no-store" in response.headers["cache-control"]
    store.close()


def test_stale_future_oversized_and_malformed_projections_fail_closed(monkeypatch, tmp_path):
    path = tmp_path / "activity.json"
    monkeypatch.setenv("SEICHE_AGENT_ACTIVITY_REPORT", str(path))
    assert read_report()["status"] == "missing_report"
    value = {"schema": "wake.agent-activity.v1", "as_of": datetime.now(timezone.utc).isoformat(),
             "sources": [{"source": "huggingface_datasets", "fresh": True}],
             "observations": [{"source": "huggingface_datasets", "collection_fresh": True}]}
    path.write_text(json.dumps(value))
    assert read_report()["report_current"]
    value["as_of"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    path.write_text(json.dumps(value))
    report = read_report()
    assert report["status"] == "stale_report" and report["observations"] == []
    assert report["withheld_observations"] == 1
    value["as_of"] = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    path.write_text(json.dumps(value))
    assert read_report()["status"] == "invalid_report"
    path.write_bytes(b"x" * (4 * 1024**2 + 1))
    assert read_report()["status"] == "invalid_report"
    path.write_text('{"secret": "never echo"}')
    assert "never echo" not in str(read_report())


def test_expired_radar_rights_hide_retained_rows_even_in_a_current_report(monkeypatch, tmp_path):
    path = tmp_path / "activity.json"
    monkeypatch.setenv("SEICHE_AGENT_ACTIVITY_REPORT", str(path))
    value = {"schema": "wake.agent-activity.v1", "as_of": datetime.now(timezone.utc).isoformat(),
             "sources": [{"source": "cloudflare_radar", "fresh": True,
                          "rights_expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}],
             "observations": [{"source": "cloudflare_radar", "value": "100"}]}
    path.write_text(json.dumps(value))
    report = read_report()
    assert report["report_current"] and report["observations"] == []
    assert report["sources"][0]["status"] == "not_admitted"


def test_report_regeneration_cannot_refresh_old_source_rows(monkeypatch, tmp_path):
    path = tmp_path / "activity.json"
    monkeypatch.setenv("SEICHE_AGENT_ACTIVITY_REPORT", str(path))
    now = datetime.now(timezone.utc)
    stamp = (now - timedelta(hours=13)).isoformat()
    value = {"schema": "wake.agent-activity.v1", "as_of": now.isoformat(),
             "sources": [{"source": "huggingface_datasets", "fresh": True, "status": "ok",
                          "knowledge_time": stamp, "receipt_id": "a" * 64}],
             "observations": [{"source": "huggingface_datasets", "collection_fresh": True,
                               "knowledge_time": stamp, "receipt_id": "a" * 64}]}
    path.write_text(json.dumps(value))
    result = read_report()
    assert result["report_current"] and result["observations"] == []
    assert result["sources"][0]["status"] == "stale"
    assert result["withheld_observations"] == 1
    # A real, still-current receipt survives. Report metadata cannot substitute
    # a different receipt or attach an older row to a fresh source.
    stamp = (now - timedelta(minutes=10)).isoformat()
    value["sources"][0]["knowledge_time"] = stamp
    value["observations"][0]["knowledge_time"] = stamp
    path.write_text(json.dumps(value))
    assert len(read_report()["observations"]) == 1
    value["observations"][0]["receipt_id"] = "b" * 64
    path.write_text(json.dumps(value))
    assert read_report()["observations"] == []


def test_unknown_and_future_source_clocks_and_duplicate_sources_are_rejected(monkeypatch, tmp_path):
    path = tmp_path / "activity.json"
    monkeypatch.setenv("SEICHE_AGENT_ACTIVITY_REPORT", str(path))
    now = datetime.now(timezone.utc)
    source = {"source": "mcp_registry", "fresh": True, "status": "ok", "receipt_id": "a" * 64}
    value = {"schema": "wake.agent-activity.v1", "as_of": now.isoformat(), "sources": [source],
             "observations": [{"source": "mcp_registry", "collection_fresh": True}]}
    for stamp in [None, now.replace(tzinfo=None).isoformat(), (now + timedelta(hours=1)).isoformat()]:
        source["knowledge_time"] = stamp
        path.write_text(json.dumps(value))
        assert read_report()["observations"] == []
    value["sources"].append(dict(source))
    path.write_text(json.dumps(value))
    assert read_report()["status"] == "invalid_report"
