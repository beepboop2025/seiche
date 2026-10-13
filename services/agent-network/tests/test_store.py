from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select, update

from seiche_agent_network.crypto import generate_keys
from seiche_agent_network.models import NetworkError
from seiche_agent_network.store import Store, messages, deliveries


@pytest.fixture
def network(tmp_path):
    now = [1_800_000_000.0]
    keys = generate_keys()
    store = Store(tmp_path / "network.db", **keys, clock=lambda: now[0])
    owners, agents = [], []
    for label in ("Institution One", "Institution Two", "Unrelated Institution"):
        bootstrap = store.bootstrap_org(label)
        owner = store.authenticate(bootstrap["owner_token"])
        registration = store.register_agent(owner, label + " research", ["funding"], public=True)
        owners.append(owner)
        agents.append(store.authenticate(registration["token"]))
    yield store, owners, agents, now, keys, tmp_path / "network.db"
    store.close()


def room_for(store, agents):
    a, b = agents[:2]
    invite = store.invite_contact(a, b.agent_id)
    store.accept_contact(b, invite["id"])
    room = store.create_room(a, "Funding evidence", [b.agent_id])
    store.accept_room(b, room["id"])
    return room["id"], invite["id"]


def test_consent_receipt_completion_and_verified_chain(network):
    store, owners, (a, b, outsider), *_ = network
    invite = store.invite_contact(a, b.agent_id)
    with pytest.raises(NetworkError, match="accepted contact"):
        store.create_room(a, "question", [b.agent_id])
    with pytest.raises(NetworkError):
        store.accept_contact(a, invite["id"])
    store.accept_contact(b, invite["id"])
    room = store.create_room(a, "question", [b.agent_id])
    with pytest.raises(NetworkError, match="No active"):
        store.send_message(a, room["id"], "question", {"text": "Private funding request"}, "one")
    store.accept_room(b, room["id"])
    result = store.send_message(a, room["id"], "question", {"text": "Private funding request"}, "one")
    assert result["recipient_count"] == 1
    assert store.acknowledge(b, result["id"])["meaning"] == "receipt_only"
    assert store.get_task(a, result["id"])["state"] == "submitted"
    with pytest.raises(NetworkError):
        store.complete_task(a, result["id"], True)
    with pytest.raises(NetworkError):
        store.set_task_status(a, result["id"], "completed")
    store.set_task_status(b, result["id"], "completed")
    with pytest.raises(NetworkError):
        store.complete_task(b, result["id"], True)
    store.complete_task(a, result["id"], True)
    assert store.metrics(owners[0])["requester_confirmed_useful_tasks"] == 1
    assert store.verify_room(a, room["id"])["valid"]
    with pytest.raises(NetworkError):
        store.list_messages(outsider, room["id"])
    with pytest.raises(NetworkError):
        store.get_task(outsider, result["id"])


def test_plaintext_and_bearer_never_persist_and_offline_restart(network):
    store, owners, agents, now, keys, path = network
    room_id, _ = room_for(store, agents)
    owner = store.bootstrap_org("Credential test")
    secret = "super confidential institutional analysis 8bb8c09d"
    receipt = store.send_message(agents[0], room_id, "question", {"text": secret}, "encrypted")
    with store.engine.connect() as db:
        payload = db.execute(select(messages.c.payload)).scalar_one()
        assert secret not in payload
        assert store.crypto.decrypt(payload)["text"] == secret
    db_bytes = path.read_bytes()
    assert secret.encode() not in db_bytes
    assert owner["owner_token"].encode() not in db_bytes
    other = Store(path, **keys, clock=lambda: now[0])
    try:
        assert other.poll_deliveries(agents[1])["items"][0]["message"]["id"] == receipt["id"]
        assert other.verify_room(agents[0], room_id)["valid"]
    finally:
        other.close()
    with pytest.raises(ValueError, match="Keys do not match"):
        Store(path, **generate_keys())


def test_atomic_idempotency_across_two_store_instances(network):
    store, _, agents, now, keys, path = network
    room_id, _ = room_for(store, agents)
    other = Store(path, **keys, clock=lambda: now[0])
    def send(instance):
        return instance.send_message(agents[0], room_id, "question", {"text": "same"}, "retry")
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(send, [store, other] * 8))
        assert len({r["id"] for r in results}) == 1
        assert sum(not r["duplicate"] for r in results) == 1
        assert len(store.list_messages(agents[0], room_id)) == 1
        with store.engine.connect() as db:
            assert len(db.execute(select(deliveries)).all()) == 1
        with pytest.raises(NetworkError) as error:
            store.send_message(agents[0], room_id, "question", {"text": "changed"}, "retry")
        assert error.value.code == "idempotency_conflict"
    finally:
        other.close()


@pytest.mark.parametrize("revoke", ["membership", "agent", "contact", "block"])
def test_revocation_removes_queued_and_historical_access(network, revoke):
    store, owners, agents, *_ = network
    a, b, _ = agents
    room_id, contact_id = room_for(store, agents)
    message = store.send_message(a, room_id, "question", {"text": "private"}, "revoke")
    assert store.poll_deliveries(b)["items"]
    if revoke == "membership":
        store.remove_member(a, room_id, b.agent_id)
    elif revoke == "agent":
        store.set_agent_status(owners[1], b.agent_id, "revoked")
    elif revoke == "contact":
        store.revoke_contact(a, contact_id)
    else:
        store.block_agent(a, b.agent_id)
    with pytest.raises(NetworkError):
        store.list_messages(b, room_id)
    with pytest.raises(NetworkError):
        store.acknowledge(b, message["id"])
    if revoke != "agent":
        assert store.poll_deliveries(b)["items"] == []
    else:
        with pytest.raises(NetworkError):
            store.poll_deliveries(b)


def test_rotation_invalidates_existing_principal_and_pause(network):
    store, owners, agents, *_ = network
    a = agents[0]
    rotated = store.rotate_agent_token(owners[0], a.agent_id)
    with pytest.raises(NetworkError):
        store.me(a)
    fresh = store.authenticate(rotated["token"])
    store.set_agent_status(owners[0], a.agent_id, "paused")
    with pytest.raises(NetworkError):
        store.me(fresh)
    store.set_agent_status(owners[0], a.agent_id, "active")
    assert store.me(fresh)["agent_id"] == a.agent_id
    owner_token = store.rotate_owner_token(owners[0])["owner_token"]
    with pytest.raises(NetworkError):
        store.list_owned_agents(owners[0])
    assert store.authenticate(owner_token).org_id == owners[0].org_id
    with pytest.raises(NetworkError):
        store.rotate_agent_token(owners[1], a.agent_id)


def test_reaccepted_contact_does_not_restore_old_private_deliveries(network):
    store, _, agents, *_ = network
    a, b, _ = agents
    room_id, contact_id = room_for(store, agents)
    old = store.send_message(a, room_id, "question", {"text": "old permission"}, "old")
    store.revoke_contact(a, contact_id)
    invitation = store.invite_contact(a, b.agent_id)
    store.accept_contact(b, invitation["id"])
    assert store.poll_deliveries(b)["items"] == []
    assert store.list_messages(b, room_id) == []
    with pytest.raises(NetworkError):
        store.get_task(b, old["id"])
    new = store.send_message(a, room_id, "question", {"text": "new permission"}, "new")
    assert [m["id"] for m in store.list_messages(b, room_id)] == [new["id"]]


def test_expiry_purge_signed_receipts_and_cursor(network):
    store, _, agents, now, *_ = network
    room_id, _ = room_for(store, agents)
    receipt = store.send_message(agents[0], room_id, "question", {"text": "expires"}, "expire", expires_in=5)
    old_cursor = store.poll_deliveries(agents[1])["next_cursor"]
    now[0] += 6
    assert store.poll_deliveries(agents[1])["items"] == []
    assert store.list_messages(agents[0], room_id)[0]["payload"] is None
    with pytest.raises(NetworkError) as exc:
        store.acknowledge(agents[1], receipt["id"])
    assert exc.value.status == 410
    assert store.purge_expired()["purged_payloads"] == 1
    with store.engine.connect() as db:
        assert db.execute(select(messages.c.payload)).scalar_one() is None
    # The recipient still has its signed receipt, without the erased private body.
    assert store.list_messages(agents[1], room_id)[0]["payload"] is None
    assert store.list_messages(agents[1], room_id)[0]["id"] == receipt["id"]
    assert store.verify_room(agents[0], room_id)["valid"]
    next_message = store.send_message(agents[0], room_id, "question", {"text": "next"}, "next")
    items = store.poll_deliveries(agents[1], after=old_cursor)
    assert items["items"][0]["message"]["id"] == next_message["id"]
    assert store.poll_deliveries(agents[1], after=items["next_cursor"])["items"] == []


def test_room_metadata_retention_bounds_access_and_deletes_chain(network):
    store, _, agents, now, *_ = network
    room_id, _ = room_for(store, agents)
    store.send_message(agents[0], room_id, "question", {"text": "bounded"}, "metadata")
    now[0] += 91 * 86400
    with pytest.raises(NetworkError) as exc:
        store.list_messages(agents[0], room_id)
    assert exc.value.status == 410
    assert store.poll_deliveries(agents[1])["items"] == []
    assert store.list_rooms(agents[0]) == []
    assert store.purge_expired()["purged_rooms"] == 1
    with store.engine.connect() as db:
        assert db.execute(select(messages)).all() == []


def test_multi_recipient_task_claim_is_exclusive(network):
    store, _, agents, *_ = network
    a, b, c = agents
    for target in (b, c):
        invite = store.invite_contact(a, target.agent_id)
        store.accept_contact(target, invite["id"])
    room = store.create_room(a, "Shared task", [b.agent_id, c.agent_id])
    store.accept_room(b, room["id"])
    store.accept_room(c, room["id"])
    task = store.send_message(a, room["id"], "question", {}, "claim")
    store.set_task_status(b, task["id"], "accepted")
    with pytest.raises(NetworkError, match="already owns"):
        store.set_task_status(c, task["id"], "completed")
    store.cancel_task(a, task["id"])
    with pytest.raises(NetworkError, match="Terminal"):
        store.set_task_status(b, task["id"], "completed")


def test_late_joiner_cannot_read_past_messages(network):
    store, _, agents, *_ = network
    a, b, c = agents
    for target in (b, c):
        invite = store.invite_contact(a, target.agent_id)
        store.accept_contact(target, invite["id"])
    room = store.create_room(a, "Consent boundary", [b.agent_id, c.agent_id])
    store.accept_room(b, room["id"])
    old = store.send_message(a, room["id"], "question", {"text": "Before acceptance"}, "before")
    store.accept_room(c, room["id"])
    assert store.list_messages(c, room["id"]) == []
    with pytest.raises(NetworkError):
        store.get_task(c, old["id"])
    new = store.send_message(a, room["id"], "question", {"text": "After acceptance"}, "after")
    assert [m["id"] for m in store.list_messages(c, room["id"])] == [new["id"]]


def test_invalid_domain_inputs_fail_closed(network):
    store, owners, agents, *_ = network
    for bad_members in ([{}], [None], [agents[1].agent_id, agents[1].agent_id]):
        with pytest.raises(NetworkError):
            store.create_room(agents[0], "bad", bad_members)
    with pytest.raises(NetworkError):
        store.register_agent(owners[0], "bad", public="false")
    with pytest.raises(NetworkError):
        store.directory(agents[0], "q" * 201)
    with pytest.raises(NetworkError):
        store.get_agent(agents[0], "x" * 129)


def test_bounds_directory_and_ownership(network):
    store, owners, agents, *_ = network
    room_id, _ = room_for(store, agents)
    store.update_agent(owners[1], agents[1].agent_id, public=False)
    assert agents[1].agent_id not in {a["id"] for a in store.directory(agents[2])}
    with pytest.raises(NetworkError):
        store.get_agent(agents[2], agents[1].agent_id)
    assert store.get_agent(agents[0], agents[1].agent_id)["id"] == agents[1].agent_id
    assert all(a["organization_verification"] == "self_declared" for a in store.directory(agents[0]))
    store.verify_organization(owners[0].org_id)
    assert store.get_agent(agents[0], agents[0].agent_id)["organization_verification"] == "operator_verified"
    with pytest.raises(NetworkError):
        store.register_agent(agents[0], "unauthorized")
    for payload in ({"too_big": "x" * 17000}, {"bad": float("nan")}):
        with pytest.raises(NetworkError):
            store.send_message(agents[0], room_id, "question", payload, "invalid")
    with pytest.raises(NetworkError):
        store.list_messages(agents[0], room_id, limit=101)
    store.max_turns = 1
    store.send_message(agents[0], room_id, "question", {"text": "ok"}, "turn")
    with pytest.raises(NetworkError) as exc:
        store.send_message(agents[0], room_id, "question", {"text": "overflow"}, "turn2")
    assert exc.value.code == "turn_limit"


def test_chain_detects_ciphertext_and_receipt_tampering(network):
    store, _, agents, *_ = network
    room_id, _ = room_for(store, agents)
    receipt = store.send_message(agents[0], room_id, "question", {"text": "signed"}, "chain")
    with store.engine.begin() as db:
        db.execute(update(messages).where(messages.c.id == receipt["id"]).values(payload=store.crypto.encrypt({"text": "forged"})))
    assert store.verify_room(agents[0], room_id)["valid"] is False


def test_delete_room_and_tenant_are_isolated(network):
    store, owners, agents, *_ = network
    room_id, _ = room_for(store, agents)
    store.send_message(agents[0], room_id, "question", {"text": "delete"}, "delete")
    with pytest.raises(NetworkError):
        store.delete_room(owners[1], room_id)
    result = store.delete_org(owners[0])
    assert result["deleted"]
    with pytest.raises(NetworkError):
        store.me(owners[0])
    assert store.me(owners[1])["org_id"] == owners[1].org_id
    assert store.poll_deliveries(agents[1])["items"] == []
    assert store.list_rooms(agents[1]) == []


def test_rate_limit_and_rollover(network):
    store, _, agents, now, *_ = network
    room_id, _ = room_for(store, agents)
    store.rate_limit = 1
    store.send_message(agents[0], room_id, "question", {}, "r1")
    # Retry does not consume another operation.
    assert store.send_message(agents[0], room_id, "question", {}, "r1")["duplicate"]
    with pytest.raises(NetworkError) as exc:
        store.send_message(agents[0], room_id, "question", {}, "r2")
    assert exc.value.code == "rate_limited"
    now[0] += 60
    store.send_message(agents[0], room_id, "question", {}, "r2")
