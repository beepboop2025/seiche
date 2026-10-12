"""Optional real PostgreSQL qualification, isolated in a generated schema.

Set SEICHE_TEST_POSTGRES_URL to a disposable PostgreSQL test database. The fixture
creates and drops only its own random schema; public/existing schemas are untouched.
"""
from concurrent.futures import ThreadPoolExecutor
import os
import re
import uuid

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from seiche_agent_network.analytics import Analytics
from seiche_agent_network.crypto import generate_keys
from seiche_agent_network.models import NetworkError
from seiche_agent_network.store import Store, messages

pytestmark = pytest.mark.skipif(not os.environ.get("SEICHE_TEST_POSTGRES_URL"), reason="SEICHE_TEST_POSTGRES_URL not configured")


@pytest.fixture
def postgres():
    url = make_url(os.environ["SEICHE_TEST_POSTGRES_URL"])
    if url.get_backend_name() != "postgresql":
        pytest.fail("SEICHE_TEST_POSTGRES_URL must name PostgreSQL")
    url = url.set(drivername="postgresql+psycopg")
    schema = "seiche_test_" + uuid.uuid4().hex
    assert re.fullmatch(r"seiche_test_[a-f0-9]{32}", schema)
    admin = create_engine(url, hide_parameters=True)
    created = False
    stores = []
    try:
        with admin.begin() as db:
            db.execute(CreateSchema(schema))
            created = True
        isolated = url.update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(hide_password=False)
        keys = generate_keys()
        for _ in range(4):
            stores.append(Store(isolated, **keys))
        store = stores[0]
        owners, principals, tokens = [], [], []
        for name in ("Synthetic A", "Synthetic B", "Synthetic outsider"):
            owner = store.authenticate(store.bootstrap_org(name)["owner_token"])
            token = store.register_agent(owner, name)["token"]
            owners.append(owner)
            tokens.append(token)
            principals.append(store.authenticate(token))
        invitation = store.invite_contact(principals[0], principals[1].agent_id)
        store.accept_contact(principals[1], invitation["id"])
        room = store.create_room(principals[0], "Synthetic PostgreSQL test", [principals[1].agent_id])
        store.accept_room(principals[1], room["id"])
        yield {"stores": stores, "owners": owners, "principals": principals, "tokens": tokens,
               "room_id": room["id"], "url": isolated, "keys": keys}
    finally:
        for store in stores:
            store.close()
        if created:
            # Only this locally generated schema is eligible for removal.
            assert re.fullmatch(r"seiche_test_[a-f0-9]{32}", schema)
            with admin.begin() as db:
                db.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def test_postgres_cross_tenant_isolation_and_revocation(postgres):
    store = postgres["stores"][0]
    a, b, stranger = postgres["principals"]
    message = store.send_message(a, postgres["room_id"], "question", {"synthetic": True}, "postgres:isolation")
    assert store.poll_deliveries(b)["items"][0]["message"]["id"] == message["id"]
    with pytest.raises(NetworkError):
        store.list_messages(stranger, postgres["room_id"])
    store.remove_member(a, postgres["room_id"], b.agent_id)
    assert store.poll_deliveries(b)["items"] == []
    with pytest.raises(NetworkError):
        store.get_task(b, message["id"])


def test_postgres_cross_instance_locks_idempotency_and_sequences(postgres):
    stores, a, room_id = postgres["stores"], postgres["principals"][0], postgres["room_id"]
    def duplicate(index):
        return stores[index % 4].send_message(a, room_id, "question", {"synthetic": True}, "postgres:duplicate")
    with ThreadPoolExecutor(max_workers=8) as pool:
        repeated = list(pool.map(duplicate, range(16)))
        assert len({receipt["id"] for receipt in repeated}) == 1
        assert sum(not receipt["duplicate"] for receipt in repeated) == 1
        results = list(pool.map(lambda index: stores[index % 4].send_message(a, room_id, "question", {"synthetic": True}, f"postgres:unique:{index}"), range(24)))
    assert {receipt["sequence"] for receipt in results} == set(range(2, 26))
    assert stores[0].verify_room(a, room_id)["valid"]
    with stores[0].engine.connect() as db:
        assert db.scalar(select(func.count()).select_from(messages)) == 25
    with pytest.raises(NetworkError) as exc:
        stores[0].send_message(a, room_id, "question", {"changed": True}, "postgres:duplicate")
    assert exc.value.code == "idempotency_conflict"


def test_postgres_reopen_keeps_encrypted_inbox_and_receipts(postgres):
    store, a, b = postgres["stores"][0], *postgres["principals"][:2]
    receipt = store.send_message(a, postgres["room_id"], "question", {"synthetic": "encrypted private body"}, "postgres:restart")
    with store.engine.connect() as db:
        encrypted = db.scalar(select(messages.c.payload).where(messages.c.id == receipt["id"]))
        assert "encrypted private body" not in encrypted
    for instance in postgres["stores"]:
        instance.close()
    reopened = Store(postgres["url"], **postgres["keys"])
    postgres["stores"].append(reopened)
    recipient = reopened.authenticate(postgres["tokens"][1])
    assert reopened.poll_deliveries(recipient)["items"][0]["message"]["id"] == receipt["id"]
    assert reopened.verify_room(recipient, postgres["room_id"])["valid"]


def test_postgres_analytics_constraints_and_tenant_delete(postgres):
    store = postgres["stores"][0]
    analytics = Analytics(store, tool_registry={"seiche": {"data_health"}})
    analytics.record(postgres["principals"][0], product="seiche", tool="data_health", method="tools/call",
                     http_status=200, duration_ms=12.5, country="IN", freshness="restricted", tool_outcome="success")
    assert analytics.report(postgres["owners"][0])["freshness"]["restricted"] == 1
    assert analytics.report(postgres["owners"][1])["totals"]["requests"] == 0
    store.delete_org(postgres["owners"][0])
    assert analytics.report()["totals"]["requests"] == 0


def test_postgres_independent_data_progress_and_policy_revocation_barrier(postgres):
    from threading import Event
    store, other = postgres["stores"][:2]
    entered, release, read_done = Event(), Event(), Event()

    def hold_shared():
        with store._transaction(shared=True, keys=["synthetic:held"]):
            entered.set()
            assert release.wait(10)

    def independent():
        other.authenticate(postgres["tokens"][0])
        with other._transaction(shared=True, keys=["synthetic:other"]):
            read_done.set()

    with ThreadPoolExecutor(max_workers=3) as pool:
        held = pool.submit(hold_shared)
        assert entered.wait(3)
        reading = pool.submit(independent)
        try:
            assert read_done.wait(3), "independent reads/data writes should not queue behind shared lock"
            revoke = pool.submit(postgres["stores"][2].set_agent_status, postgres["owners"][0], postgres["principals"][0].agent_id, "paused")
            assert not revoke.done()
        finally:
            release.set()
        held.result()
        reading.result()
        revoke.result()
    with pytest.raises(NetworkError):
        other.authenticate(postgres["tokens"][0])


def test_postgres_concurrent_organization_limit_rolls_back_rejected_acceptance(postgres):
    store = postgres["stores"][0]
    pairs = []
    for number in range(11):
        a, b = [store.authenticate(store.register_agent(owner, f"Synthetic quota {number}")["token"])
                for owner in postgres["owners"][:2]]
        invite = store.invite_contact(a, b.agent_id)
        store.accept_contact(b, invite["id"])
        room = store.create_room(a, "Synthetic quota room", [b.agent_id])
        store.accept_room(b, room["id"])
        pairs.append((a, b, room["id"]))
    for instance in postgres["stores"]:
        instance.rate_limit = 1  # One per agent, ten across the organization.
    def send(number):
        try:
            a, _, room = pairs[number]
            postgres["stores"][number % 4].send_message(a, room, "question", {}, f"quota:{number}")
            return "accepted"
        except NetworkError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(send, range(11)))
    assert results.count("accepted") == 10 and results.count("rate_limited") == 1
    assert sum(len(store.poll_deliveries(b)["items"]) for _, b, _ in pairs) == 10
    assert all(store.verify_room(a, room)["valid"] for a, _, room in pairs)
