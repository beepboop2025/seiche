import hashlib

from fastapi.testclient import TestClient

from seiche_agent_network.api import create_app
from seiche_agent_network.crypto import generate_keys
from seiche_agent_network.enrollment import POLICY
from seiche_agent_network.store import Store


def solve(value):
    for nonce in range(2000000):
        if hashlib.sha256(f"{value['challenge']['id']}:{nonce}".encode()).digest()[:2] == b"\0\0":
            return {"challenge": value["challenge"], "signature": value["signature"], "proof": nonce,
                    "organization": "SYNTHETIC team", "agent": "SYNTHETIC agent",
                    "policy_version": POLICY, "data_notice_acknowledged": True}
    raise AssertionError("could not solve synthetic challenge")


def test_self_service_identity_is_free_private_unverified_and_not_learning():
    store = Store(":memory:", **generate_keys())
    with TestClient(create_app(store)) as client:
        body = solve(client.get("/v1/enrollment/challenge").json())
        response = client.post("/v1/enrollment", json=body)
        assert response.status_code == 201
        value = response.json()
        assert value["organization"]["verification"] == "self_declared"
        assert value["directory_visibility"] == "private"
        assert value["shared_training"] is False and value["data_resale_permission"] is False
        assert client.get("/v1/me", headers={"Authorization": "Bearer " + value["agent_token"]}).json()["role"] == "agent"
        assert client.post("/v1/enrollment", json=body).status_code == 409
        assert client.get("/v1/integrations").json()["account_required_for_public_tools"] is False
        assert set(client.get("/mcp-config.json").json()["mcpServers"]) == {
            "seiche", "liquilens", "undertow", "palimpsest", "noisefloor", "narcoscope", "wake",
            "riptide", "myquant-editorial", "myquant-app", "trade-safety", "financial-evidence"}
        assert client.delete("/v1/me", headers={"Authorization": "Bearer " + value["agent_token"]}).status_code == 403
        assert client.delete("/v1/me", headers={"Authorization": "Bearer " + value["owner_token"]}).status_code == 200
        assert client.get("/v1/me", headers={"Authorization": "Bearer " + value["agent_token"]}).status_code == 401
        assert client.post("/v1/enrollment", json=body).status_code == 409


def test_challenge_forgery_expiry_notice_and_pause(monkeypatch):
    now = [10000]
    store = Store(":memory:", **generate_keys(), clock=lambda: now[0])
    with TestClient(create_app(store)) as client:
        body = solve(client.get("/v1/enrollment/challenge").json())
        assert client.post("/v1/enrollment", json=body | {"data_notice_acknowledged": False}).status_code == 400
        assert client.post("/v1/enrollment", json=body | {"proof": True}).status_code == 422
        bad = body | {"challenge": body["challenge"] | {"difficulty_bits": 0}}
        assert client.post("/v1/enrollment", json=bad).status_code == 400
        now[0] += 301
        assert client.post("/v1/enrollment", json=body).status_code == 400
        monkeypatch.setenv("SEICHE_SELF_SERVICE", "0")
        assert client.get("/v1/enrollment/challenge").status_code == 503


def test_account_capacity_is_shared_between_workers():
    from seiche_agent_network.enrollment import EnrollmentRequest, challenge, enroll, meta
    from seiche_agent_network.models import NetworkError
    import pytest
    store = Store(":memory:", **generate_keys())
    meta.create_all(store.engine)
    enroll(store, EnrollmentRequest(**solve(challenge(store))), daily_limit=1)
    with pytest.raises(NetworkError) as failure:
        enroll(store, EnrollmentRequest(**solve(challenge(store))), daily_limit=1)
    assert failure.value.status == 429
