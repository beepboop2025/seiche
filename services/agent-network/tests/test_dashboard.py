"""Dashboard shell, private report boundaries and deployment-prefix checks."""
from datetime import datetime, timezone
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from seiche_agent_network.api import create_app
from seiche_agent_network.crypto import generate_keys
from seiche_agent_network.store import Store


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    token = "operator-report-test-credential-0123456789"
    monkeypatch.setenv("SEICHE_OPERATOR_REPORT_TOKEN", token)
    monkeypatch.delenv("SEICHE_FRESHNESS_REPORT", raising=False)
    store = Store(tmp_path / "dashboard.db", **generate_keys())
    owner = store.bootstrap_org("Dashboard tenant")
    app = create_app(store, public_url="https://api.seiche.info/agents", gateway=True, purge_interval=0)
    with TestClient(app, base_url="https://api.seiche.info") as client:
        yield client, app, store, owner, token
    store.close()


def test_public_shell_contains_no_report_or_credential_and_strict_csp(dashboard):
    client, _, _, owner, token = dashboard
    response = client.get("/agents/dashboard")
    assert response.status_code == 200
    assert "Platform intelligence" in response.text
    assert token not in response.text and owner["owner_token"] not in response.text
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "no-store" in response.headers["cache-control"]
    for path in ("/agents/static/dashboard.js", "/agents/static/dashboard.css"):
        asset = client.get(path)
        assert asset.status_code == 200
    assert 'href="./static/dashboard.css"' in response.text
    assert 'src="./static/dashboard.js"' in response.text


def test_operator_report_token_never_accepts_tenant_or_anonymous(dashboard):
    client, app, store, owner, token = dashboard
    principal = store.authenticate(owner["owner_token"])
    app.state.gateway.analytics.record(principal, product="seiche", tool="none", method="initialize", http_status=200, duration_ms=15, country="IN", traffic_class="synthetic")
    app.state.gateway.analytics.record(None, product="liquilens", tool="none", method="initialize", http_status=200, duration_ms=900, country="unknown")
    for path in ("/v1/operator/usage", "/v1/operator/freshness"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer " + owner["owner_token"]}).status_code == 401
    report = client.get("/v1/operator/usage", headers={"Authorization": "Bearer " + token}).json()
    assert report["scope"] == "operator_global"
    assert report["totals"]["requests"] == 2
    assert report["activity"]["observed_org_ids"] == 1
    assert report["activity"]["verified_trading_adoption"] is None
    assert report["latency"]["p95"]["exact"] is False
    assert {row["country"] for row in report["countries"]} == {"IN", "unknown"}
    own = client.get("/v1/product-usage", headers={"Authorization": "Bearer " + owner["owner_token"]}).json()
    assert own["scope"] == "own_organization" and own["totals"]["requests"] == 1


def test_monitor_not_configured_and_valid_report_are_distinct(dashboard, tmp_path, monkeypatch):
    client, _, _, _, token = dashboard
    headers = {"Authorization": "Bearer " + token}
    result = client.get("/v1/operator/freshness", headers=headers).json()
    assert result["status"] == "not_configured" and result["monitor_current"] is False
    report = {"schema": "seiche-agent-network.freshness.v1", "evaluated_at": datetime.now(timezone.utc).isoformat(),
              "products": [{"product": "seiche", "transport": "available", "freshness": "unknown", "reasons": ["future_observation_clock"]}]}
    path = tmp_path / "freshness.json"
    path.write_text(json.dumps(report))
    monkeypatch.setenv("SEICHE_FRESHNESS_REPORT", str(path))
    result = client.get("/v1/operator/freshness", headers=headers).json()
    assert result["monitor_current"] is True
    assert result["products"][0]["freshness"] == "unknown"


def test_dashboard_script_keeps_secrets_out_of_storage_and_uses_safe_dom():
    directory = Path(__file__).parents[1] / "src" / "seiche_agent_network" / "static"
    script = (directory / "dashboard.js").read_text()
    assert "innerHTML" not in script and "insertAdjacentHTML" not in script
    assert "localStorage" not in script and "sessionStorage" not in script and "document.cookie" not in script
    assert 'credentials: "omit"' in script and 'cache: "no-store"' in script
    assert 'method: "GET"' in script and 'method: "POST"' not in script
    assert 'session.token = ""' in script and 'session.controller?.abort()' in script
    assert "lower_bound_ms" in script and "upper_bound_ms" in script
    assert 'usage.value.scope !== "operator_global"' in script
    assert 'node("progress")' in script
    assert "location.pathname.replace" in script
