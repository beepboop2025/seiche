"""Transport-level isolation, lifecycle and bounded A2A contract tests."""
import json
import os
import stat
import time

from fastapi.testclient import TestClient
import pytest

from seiche_agent_network.api import create_app
from seiche_agent_network.cli import main, read_private_json
from seiche_agent_network.crypto import generate_keys
from seiche_agent_network.store import Store


def auth(token):
    return {"Authorization": "Bearer " + token}


@pytest.fixture
def network(tmp_path):
    now = [1800000000.0]
    store = Store(tmp_path / "network.db", **generate_keys(), clock=lambda: now[0])
    people = []
    for label in ("alpha", "beta", "outsider"):
        owner = store.bootstrap_org(label)
        owner_p = store.authenticate(owner["owner_token"])
        agent = store.register_agent(owner_p, label + " agent", capabilities=["funding"], public=True)
        people.append({"owner": owner, "owner_p": owner_p, "agent": agent,
                       "p": store.authenticate(agent["token"]), "h": auth(agent["token"]), "oh": auth(owner["owner_token"])})
    a, b, outsider = people
    contact = store.invite_contact(a["p"], b["agent"]["agent"]["id"])
    store.accept_contact(b["p"], contact["id"])
    room = store.create_room(a["p"], "Funding evidence", [b["agent"]["agent"]["id"]])
    store.accept_room(b["p"], room["id"])
    with TestClient(create_app(store), raise_server_exceptions=False) as client:
        yield store, client, a, b, outsider, room["id"], contact["id"], now
    store.close()


def send(client, headers, room, key="request-1", **extra):
    return client.post(f"/v1/rooms/{room}/messages", headers=headers,
                       json={"kind": "question", "payload": {"text": "Compare the dated sources"}, "idempotency_key": key, **extra})


def test_auth_role_and_tenant_isolation(network):
    _, client, a, b, outsider, room, _, _ = network
    assert client.get("/healthz").status_code == 200
    unauth = client.get("/v1/agents")
    assert unauth.status_code == 401 and unauth.headers["www-authenticate"] == "Bearer"
    assert client.get("/v1/agents", headers=a["h"]).status_code == 403
    assert len(client.get("/v1/agents", headers=a["oh"]).json()) == 1
    assert client.post("/v1/agents", headers=a["h"], json={"label": "Escalated"}).status_code == 403
    assert send(client, a["oh"], room).status_code == 403
    assert client.get(f"/v1/rooms/{room}/messages", headers=outsider["h"]).status_code in {403, 404}
    target = b["agent"]["agent"]["id"]
    assert client.post(f"/v1/agents/{target}/rotate", headers=a["oh"]).status_code == 404


def test_offline_delivery_idempotency_task_and_usefulness_are_distinct(network):
    _, client, a, b, outsider, room, _, _ = network
    first = send(client, a["h"], room)
    assert first.status_code == 201
    message_id = first.json()["id"]
    duplicate = send(client, a["h"], room)
    assert duplicate.json()["id"] == message_id and duplicate.json()["duplicate"] is True
    collision = send(client, a["h"], room, payload={"text": "Changed input"})
    assert collision.status_code == 409
    inbox = client.get("/v1/inbox", headers=b["h"]).json()
    assert len(inbox["items"]) == 1 and inbox["items"][0]["message"]["id"] == message_id
    assert client.get("/v1/inbox", headers=outsider["h"]).json()["items"] == []
    assert client.post(f"/v1/messages/{message_id}/ack", headers=b["h"]).json()["meaning"] == "receipt_only"
    assert client.get(f"/v1/tasks/{message_id}", headers=a["h"]).json()["status"] == "submitted"
    assert client.post(f"/v1/tasks/{message_id}/complete", headers=a["h"], json={"useful": True}).status_code == 409
    assert client.post(f"/v1/tasks/{message_id}/status", headers=a["h"], json={"status": "completed"}).status_code == 403
    assert client.post(f"/v1/tasks/{message_id}/status", headers=b["h"], json={"status": "completed"}).status_code == 200
    assert client.post(f"/v1/tasks/{message_id}/complete", headers=b["h"], json={"useful": True}).status_code == 403
    assert client.post(f"/v1/tasks/{message_id}/complete", headers=a["h"], json={"useful": True}).status_code == 200
    metrics = client.get("/v1/metrics", headers=a["oh"]).json()
    assert metrics["accepted_messages"] == 1
    assert metrics["requester_confirmed_useful_tasks"] == 1
    assert metrics["shared_training"] is False
    assert client.get(f"/v1/rooms/{room}/verify", headers=a["h"]).json()["valid"] is True


def test_rotation_pause_and_contact_revocation_take_effect_on_next_request(network):
    _, client, a, b, _, room, contact, _ = network
    message_id = send(client, a["h"], room).json()["id"]
    agent_id = b["agent"]["agent"]["id"]
    replacement = client.post(f"/v1/agents/{agent_id}/rotate", headers=b["oh"]).json()["token"]
    assert client.get("/v1/me", headers=b["h"]).status_code == 401
    current = auth(replacement)
    assert client.get("/v1/me", headers=current).status_code == 200
    client.post(f"/v1/agents/{agent_id}/status", headers=b["oh"], json={"status": "paused"})
    assert client.get("/v1/inbox", headers=current).status_code in {401, 403}
    client.post(f"/v1/agents/{agent_id}/status", headers=b["oh"], json={"status": "active"})
    assert client.get("/v1/inbox", headers=current).status_code == 200
    assert client.post(f"/v1/contacts/{contact}/revoke", headers=a["h"]).status_code == 200
    assert client.get("/v1/inbox", headers=current).json()["items"] == []
    assert client.get(f"/v1/tasks/{message_id}", headers=current).status_code == 404


def test_strict_validation_does_not_echo_private_inputs(network):
    _, client, a, _, _, room, _, _ = network
    secret = "private-strategy-should-not-be-echoed"
    response = client.post("/v1/agents", headers=a["oh"], json={"label": "Research", "secret": secret})
    assert response.status_code == 422 and secret not in response.text
    response = client.post("/v1/agents", headers=a["oh"], json={"label": "Research", "public": "false"})
    assert response.status_code == 422
    response = client.post(f"/v1/rooms/{room}/messages", headers={**a["h"], "Content-Type": "application/json"}, content='{ "secret": "' + secret)
    assert response.status_code == 422 and secret not in response.text
    response = send(client, a["h"], room, expires_in=True)
    assert response.status_code == 422
    assert client.get("/v1/inbox?limit=101", headers=a["h"]).status_code == 422
    assert client.get("/v1/inbox?after=-1", headers=a["h"]).status_code == 422


def test_body_origin_host_and_security_headers(network):
    _, client, a, _, _, _, _, _ = network
    blocked = client.get("/v1/me", headers={**a["h"], "Origin": "https://unrelated.example"})
    assert blocked.status_code == 403
    assert "access-control-allow-origin" not in blocked.headers
    allowed = client.get("/v1/me", headers={**a["h"], "Origin": "http://127.0.0.1:8765"})
    assert allowed.status_code == 200
    assert allowed.headers["cache-control"] == "no-store"
    assert allowed.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in allowed.headers["content-security-policy"]
    assert client.get("/healthz", headers={"Host": "attacker.example"}).status_code == 400
    response = client.post("/v1/agents", headers=a["oh"], content=iter([b"x" * 40000, b"y" * 40000]))
    assert response.status_code == 413
    assert client.post("/v1/agents", headers={**a["oh"], "Content-Encoding": "gzip"}, content=b"bad").status_code == 415


def test_directory_card_respects_private_visibility(network):
    store, client, a, b, outsider, _, _, _ = network
    private = store.register_agent(a["owner_p"], "Private execution research", public=False)
    private_id = private["agent"]["id"]
    path = f"/a2a/{private_id}/.well-known/agent-card.json"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=outsider["h"]).status_code == 404
    card = client.get(path, headers=a["oh"]).json()
    assert card["name"] == "Private execution research"
    assert card["supportedInterfaces"][0]["protocolVersion"] == "1.0"
    assert card["capabilities"]["pushNotifications"] is False
    assert private["token"] not in json.dumps(card)


def test_a2a_10_send_poll_followup_cancel_and_rejections(network):
    _, client, a, b, outsider, room, _, _ = network
    base = "/a2a/" + b["agent"]["agent"]["id"]
    headers = {**a["h"], "A2A-Version": "1.0"}
    body = {"message": {"messageId": "a2a-first", "role": "ROLE_USER", "contextId": room, "parts": [{"text": "Compare funding releases"}]},
            "configuration": {"returnImmediately": True}}
    assert client.post(base + "/message:send", headers=a["h"], json=body).status_code == 400
    blocking = {**body, "configuration": {"returnImmediately": False}}
    assert client.post(base + "/message:send", headers=headers, json=blocking).status_code == 400
    result = client.post(base + "/message:send", headers=headers, json=body)
    assert result.status_code == 200, result.text
    task = result.json()["task"]
    assert task["status"]["state"] == "TASK_STATE_SUBMITTED"
    assert result.headers["content-type"] == "application/a2a+json"
    assert client.post(base + "/message:send", headers=headers, json=body).json()["task"]["id"] == task["id"]
    get = base + "/tasks/" + task["id"]
    assert client.get(get, headers=headers).json()["contextId"] == room
    assert client.get(get, headers={**outsider["h"], "A2A-Version": "1.0"}).status_code == 404
    followup = {"message": {"messageId": "a2a-more", "role": "ROLE_USER", "taskId": task["id"], "parts": [{"data": None}]},
                "configuration": {"returnImmediately": True}}
    assert client.post(base + "/message:send", headers=headers, json=followup).json()["task"]["id"] == task["id"]
    cancelled = client.post(get + ":cancel", headers=headers)
    assert cancelled.json()["status"]["state"] == "TASK_STATE_CANCELED"
    assert client.post(base + "/message:send", headers=headers, json=followup).status_code == 409
    invalid = {**body, "configuration": {"returnImmediately": True, "taskPushNotificationConfig": {"url": "http://127.0.0.1/private"}}}
    assert client.post(base + "/message:send", headers=headers, json=invalid).status_code == 422


def test_operator_cli_credentials_are_private_and_not_printed(tmp_path, capsys):
    keys, owner, agent, rotated = [tmp_path / name for name in ("keys.json", "owner.json", "agent.json", "rotated.json")]
    db = tmp_path / "relay.db"
    assert main(["init-keys", "--output", str(keys)]) == 0
    common = ["--keys-file", str(keys), "--database", str(db)]
    assert main([*common, "init-org", "--label", "Operator", "--output", str(owner)]) == 0
    assert main([*common, "provision-agent", "--owner-credential", str(owner), "--label", "Agent", "--output", str(agent)]) == 0
    owner_record = read_private_json(owner)
    agent_record = read_private_json(agent)
    assert stat.S_IMODE(agent.stat().st_mode) == 0o600
    output = capsys.readouterr()
    assert owner_record["owner_token"] not in output.out + output.err
    assert agent_record["token"] not in output.out + output.err
    assert main([*common, "rotate-owner", "--owner-credential", str(owner), "--output", str(rotated)]) == 0
    assert main([*common, "rotate-owner", "--owner-credential", str(rotated), "--output", str(rotated)]) == 2
    agent.chmod(0o644)
    with pytest.raises(ValueError):
        read_private_json(agent)
    link = tmp_path / "link.json"
    link.symlink_to(owner)
    with pytest.raises(OSError):
        read_private_json(link)


def test_subpath_proxy_root_and_agent_card_urls(network):
    store, _, a, b, _, _, _, _ = network
    app = create_app(store, public_url="https://api.seiche.info/agents")
    assert app.root_path == "/agents"
    with TestClient(app, base_url="https://api.seiche.info") as client:
        assert client.get("/healthz").status_code == 200
        card = client.get(f"/a2a/{b['agent']['agent']['id']}/.well-known/agent-card.json", headers=a["h"]).json()
        assert card["supportedInterfaces"][0]["url"].startswith("https://api.seiche.info/agents/a2a/")


def test_retention_failure_is_visible_retried_and_does_not_echo_error(tmp_path, caplog):
    store = Store(tmp_path / "retention.db", **generate_keys())
    original = store.purge_expired
    state = {"fail": False}
    def controlled():
        if state["fail"]:
            raise RuntimeError("private-error-sentinel")
        return original()
    store.purge_expired = controlled
    with TestClient(create_app(store, purge_interval=0.01)) as client:
        assert client.get("/healthz").status_code == 200
        state["fail"] = True
        for _ in range(100):
            if client.get("/healthz").status_code == 503:
                break
            time.sleep(0.01)
        assert client.get("/healthz").json()["retention"] == "failed"
        state["fail"] = False
        for _ in range(100):
            if client.get("/healthz").status_code == 200:
                break
            time.sleep(0.01)
        assert client.get("/healthz").status_code == 200
    assert "private-error-sentinel" not in caplog.text
    store.close()


def test_internal_exceptions_have_no_private_details(network, monkeypatch):
    store, client, a, _, _, _, _, _ = network
    def broken(_):
        raise RuntimeError("private-query-secret")
    monkeypatch.setattr(store, "me", broken)
    response = client.get("/v1/me", headers=a["h"])
    assert response.status_code == 500
    assert "private-query-secret" not in response.text
    assert response.headers["cache-control"] == "no-store"
