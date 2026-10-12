"""Permission, erasure and semantic bounds for optional task reporting."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from seiche_agent_network.api import create_app
from seiche_agent_network.crypto import generate_keys
from seiche_agent_network.models import NetworkError
from seiche_agent_network.participation import (
    POLICY, Participation, ParticipationRequest, TaskSignal, grants, limits, signals,
)
from seiche_agent_network.store import Store


@pytest.fixture
def participation():
    now = [1800000000.0]
    store = Store(":memory:", **generate_keys(), clock=lambda: now[0])
    owner_token = store.bootstrap_org("Synthetic retail workspace")["owner_token"]
    owner = store.authenticate(owner_token)
    agent_token = store.register_agent(owner, "Synthetic agent")["token"]
    agent = store.authenticate(agent_token)
    other_token = store.register_agent(owner, "Synthetic second agent")["token"]
    foreign_token = store.bootstrap_org("Synthetic outsider")["owner_token"]
    foreign_owner = store.authenticate(foreign_token)
    foreign_agent_token = store.register_agent(foreign_owner, "Synthetic outsider agent")["token"]
    with TestClient(create_app(store, purge_interval=0)) as client:
        yield {"store": store, "now": now, "client": client, "service": Participation(store),
               "owner": owner, "agent": agent, "other_agent": store.authenticate(other_token),
               "foreign_owner": foreign_owner, "foreign_agent": store.authenticate(foreign_agent_token),
               "owner_headers": {"Authorization": "Bearer " + owner_token},
               "agent_headers": {"Authorization": "Bearer " + agent_token},
               "foreign_headers": {"Authorization": "Bearer " + foreign_token}}
    store.close()


def enable(fixture, owner=None):
    return fixture["service"].configure(owner or fixture["owner"], ParticipationRequest(
        enabled=True, policy_version=POLICY, data_notice_acknowledged=True))["grant_id"]


def event(grant_id, **changes):
    return TaskSignal(event_id=str(uuid4()), grant_id=grant_id, goal="research",
                      topic="ai_economy", stage="exploring", traffic_class="synthetic").model_copy(update=changes)


def assert_rows(fixture, table, expected):
    with fixture["store"].engine.connect() as db:
        assert db.scalar(select(func.count()).select_from(table)) == expected


def test_anonymous_contract_but_private_status_summary_and_mutations(participation):
    client = participation["client"]
    value = client.get("/v1/participation/contract").json()
    assert value["policy_version"] == POLICY
    assert value["taxonomy"]["goal"] == ["research", "compare", "monitor", "buy", "arbitrage"]
    assert value["evidence_kind"] == "self_reported"
    assert value["free_tools_require_sharing"] is False
    assert value["schemas"]["event"]["additionalProperties"] is False
    for method, path in (("get", "/v1/participation"), ("get", "/v1/participation/summary"),
                         ("delete", "/v1/participation")):
        assert getattr(client, method)(path).status_code == 401


def test_only_owner_can_enable_with_explicit_current_notice(participation):
    f, client = participation, participation["client"]
    assert client.get("/v1/participation", headers=f["agent_headers"]).json()["enabled"] is False
    body = {"enabled": True, "policy_version": POLICY, "data_notice_acknowledged": True}
    assert client.patch("/v1/participation", json=body, headers=f["agent_headers"]).status_code == 403
    assert client.patch("/v1/participation", json=body | {"data_notice_acknowledged": False},
                        headers=f["owner_headers"]).status_code == 400
    assert client.patch("/v1/participation", json=body | {"policy_version": "old"},
                        headers=f["owner_headers"]).status_code == 422
    assert client.patch("/v1/participation", json=body | {"enabled": "true"},
                        headers=f["owner_headers"]).status_code == 422
    enabled = client.patch("/v1/participation", json=body, headers=f["owner_headers"]).json()
    assert enabled["enabled"] is True
    assert enabled["shared_training"] is False and enabled["data_resale_permission"] is False
    assert client.patch("/v1/participation", json=body, headers=f["owner_headers"]).json()["grant_id"] == enabled["grant_id"]
    assert client.get("/v1/participation", headers=f["agent_headers"]).json()["grant_id"] == enabled["grant_id"]


def test_report_receipts_idempotency_and_conflicts(participation):
    f = participation
    body = event(enable(f)).model_dump()
    response = f["client"].post("/v1/participation/events", json=body, headers=f["agent_headers"])
    assert response.status_code == 201
    assert response.json() == {"event_id": body["event_id"], "status": "accepted",
                               "received_at": f["now"][0], "evidence_kind": "self_reported"}
    retry = f["client"].post("/v1/participation/events", json=body, headers=f["agent_headers"])
    assert retry.status_code == 200 and retry.json()["status"] == "duplicate"
    conflict = f["client"].post("/v1/participation/events", json=body | {"stage": "completed"}, headers=f["agent_headers"])
    assert conflict.status_code == 409
    assert_rows(f, signals, 1)
    with f["store"].engine.connect() as db:
        assert db.scalar(select(limits.c.count)) == 1
        row = db.execute(select(signals)).mappings().one()
        assert row["org_id"] == f["owner"].org_id and row["agent_id"] == f["agent"].agent_id


@pytest.mark.parametrize("changes", [
    {"prompt": "private prompt canary"}, {"org_id": "private prompt canary"},
    {"agent_id": "private prompt canary"}, {"evidence_kind": "verified"},
    {"goal": "private prompt canary"}, {"topic": "crypto_wallet"}, {"stage": "settled"},
    {"outcome": "profitable"}, {"traffic_class": "verified_real"},
    {"event_id": "123"}, {"event_id": "123E4567-E89B-12D3-A456-426614174000"},
    {"event_id": True}, {"grant_id": ""}, {"received_at": 1},
])
def test_unsupported_fields_rejected_without_echoing_secrets(participation, changes):
    f = participation
    body = event(enable(f)).model_dump() | changes
    response = f["client"].post("/v1/participation/events", json=body, headers=f["agent_headers"])
    assert response.status_code == 422
    assert "private prompt canary" not in response.text
    assert_rows(f, signals, 0)


def test_agent_only_current_tenant_grant_and_revocation(participation):
    f = participation
    signal = event(enable(f))
    for principal in (f["owner"], f["foreign_agent"]):
        with pytest.raises(NetworkError) as failure:
            f["service"].record(principal, signal)
        assert failure.value.status == 403
    f["store"].set_agent_status(f["owner"], f["agent"].agent_id, "paused")
    with pytest.raises(NetworkError) as failure:
        f["service"].record(f["agent"], signal)
    assert failure.value.status == 401
    assert_rows(f, signals, 0)


def test_summary_separates_self_reported_synthetic_and_agent_scope(participation):
    f = participation
    grant = enable(f)
    f["service"].record(f["agent"], event(grant, goal="buy", stage="completed", outcome="useful"))
    f["service"].record(f["other_agent"], event(grant, traffic_class="real", goal="arbitrage"))
    summary = f["service"].summary(f["owner"])
    assert summary["total_events"] == 2 and summary["scope"] == "workspace"
    assert summary["traffic_classes"]["synthetic"]["goals"] == {"buy": 1}
    assert summary["traffic_classes"]["synthetic"]["stages"] == {"completed": 1}
    assert summary["traffic_classes"]["real"]["goals"] == {"arbitrage": 1}
    assert summary["evidence_kind"] == "self_reported"
    own = f["service"].summary(f["agent"])
    assert own["total_events"] == 1 and own["traffic_classes"]["real"]["total_events"] == 0
    assert own["scope"] == "agent"
    assert f["service"].summary(f["foreign_owner"])["total_events"] == 0
    assert f["agent"].agent_id not in str(summary) and f["owner"].org_id not in str(summary)


def test_withdraw_erases_immediately_and_reenable_invalidates_old_grant(participation):
    f = participation
    grant = enable(f)
    signal = event(grant)
    f["service"].record(f["agent"], signal)
    assert f["client"].delete("/v1/participation", headers=f["agent_headers"]).status_code == 403
    withdrawn = f["client"].delete("/v1/participation", headers=f["owner_headers"])
    assert withdrawn.status_code == 200 and withdrawn.json()["enabled"] is False
    assert f["service"].summary(f["owner"])["total_events"] == 0
    assert_rows(f, signals, 0)
    assert_rows(f, grants, 0)
    # Minimal daily admission counters survive withdrawal, never task labels.
    assert_rows(f, limits, 1)
    assert enable(f) != grant
    with pytest.raises(NetworkError) as failure:
        f["service"].record(f["agent"], signal)
    assert failure.value.code == "participation_disabled"


def test_daily_agent_limit_excludes_retries_survives_consent_toggle_and_resets(participation):
    f = participation
    grant = enable(f)
    receipts = [event(grant) for _ in range(100)]
    for signal in receipts:
        f["service"].record(f["agent"], signal)
    assert f["service"].record(f["agent"], receipts[0])["status"] == "duplicate"
    with pytest.raises(NetworkError) as failure:
        f["service"].record(f["agent"], event(grant))
    assert failure.value.status == 429
    f["service"].withdraw(f["owner"])
    fresh = enable(f)
    with pytest.raises(NetworkError) as failure:
        f["service"].record(f["agent"], event(fresh))
    assert failure.value.status == 429
    f["now"][0] += 86400
    assert f["service"].record(f["agent"], event(fresh))["status"] == "accepted"
    assert_rows(f, limits, 1)


def test_workspace_limit_shared_between_agents_atomically(participation, monkeypatch):
    import seiche_agent_network.participation as module
    monkeypatch.setattr(module, "AGENT_DAILY_LIMIT", 4)
    monkeypatch.setattr(module, "WORKSPACE_DAILY_LIMIT", 3)
    f = participation
    grant = enable(f)
    def submit(index):
        principal = f["agent"] if index % 2 == 0 else f["other_agent"]
        try:
            return f["service"].record(principal, event(grant))["status"]
        except NetworkError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(pool.map(submit, range(8)))
    assert result.count("accepted") == 3
    assert result.count("participation_capacity") == 5
    assert_rows(f, signals, 3)
    with f["store"].engine.connect() as db:
        assert db.scalar(select(func.sum(limits.c.count))) == 3


def test_concurrent_duplicates_accept_once(participation):
    f = participation
    signal = event(enable(f))
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: f["service"].record(f["agent"], signal), range(12)))
    assert sum(r["status"] == "accepted" for r in receipts) == 1
    assert_rows(f, signals, 1)


def test_retention_boundary_and_idle_maintenance(participation):
    f = participation
    f["service"].record(f["agent"], event(enable(f)))
    f["now"][0] += 30 * 86400 - 1
    assert f["service"].summary(f["owner"])["total_events"] == 1
    f["now"][0] += 1
    assert f["service"].summary(f["owner"])["total_events"] == 0
    assert f["store"].purge_expired()["purged_participation_signals"] == 1
    assert_rows(f, signals, 0)
    assert_rows(f, limits, 0)


def test_workspace_deletion_removes_all_own_rows_and_preserves_other_workspace(participation):
    f = participation
    f["service"].record(f["agent"], event(enable(f)))
    f["service"].record(f["foreign_agent"], event(enable(f, f["foreign_owner"])))
    f["store"].delete_organization(f["owner"])
    for table in (signals, grants, limits):
        with f["store"].engine.connect() as db:
            assert db.scalar(select(func.count()).select_from(table).where(table.c.org_id == f["owner"].org_id)) == 0
            assert db.scalar(select(func.count()).select_from(table)) == 1
    with pytest.raises(NetworkError):
        f["service"].summary(f["owner"])


def test_schema_initialized_for_cli_without_http_and_reopen(tmp_path):
    path = tmp_path / "private.sqlite"
    keys = generate_keys()
    store = Store(path, **keys)
    owner_token = store.bootstrap_org("Synthetic CLI owner")["owner_token"]
    owner = store.authenticate(owner_token)
    Participation(store).configure(owner, ParticipationRequest(enabled=True, policy_version=POLICY,
                                                              data_notice_acknowledged=True))
    store.close()
    reopened = Store(path, **keys)
    assert Participation(reopened).status(reopened.authenticate(owner_token))["enabled"] is True
    reopened.purge_expired()
    reopened.delete_organization(reopened.authenticate(owner_token))
    reopened.close()
