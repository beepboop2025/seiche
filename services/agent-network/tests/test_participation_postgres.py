"""Cross-process locking qualification for owner-authorized task signals."""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier, Event
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from seiche_agent_network.models import NetworkError
from seiche_agent_network.participation import (
    POLICY, Participation, ParticipationRequest, TaskSignal, limits, meta, signals,
)
from seiche_agent_network.store import Store
from test_postgres import postgres  # noqa: F401; isolated generated-schema fixture

pytestmark = pytest.mark.skipif(not os.environ.get("SEICHE_TEST_POSTGRES_URL"),
                                reason="SEICHE_TEST_POSTGRES_URL not configured")


def enabled(postgres):
    services = [Participation(store) for store in postgres["stores"]]
    grant = services[0].configure(postgres["owners"][0], ParticipationRequest(
        enabled=True, policy_version=POLICY, data_notice_acknowledged=True))["grant_id"]
    return services, grant


def event(grant):
    return TaskSignal(event_id=str(uuid4()), grant_id=grant, goal="research", topic="ai_economy",
                      stage="exploring", traffic_class="synthetic")


def test_postgres_participation_cross_instance_idempotency(postgres):
    services, grant = enabled(postgres)
    principal = postgres["principals"][0]
    signal = event(grant)
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(lambda i: services[i % 4].record(principal, signal), range(16)))
    assert sum(value["status"] == "accepted" for value in receipts) == 1
    assert len({value["received_at"] for value in receipts}) == 1
    with pytest.raises(NetworkError) as failure:
        services[1].record(principal, signal.model_copy(update={"stage": "completed"}))
    assert failure.value.code == "idempotency_conflict"
    with postgres["stores"][0].engine.connect() as db:
        assert db.scalar(select(func.count()).select_from(signals)) == 1
        assert db.scalar(select(limits.c.count)) == 1


def test_postgres_participation_withdrawal_serializes_and_erases_inflight_report(postgres, monkeypatch):
    services, grant = enabled(postgres)
    principal, owner = postgres["principals"][0], postgres["owners"][0]
    entered, release, withdraw_started = Event(), Event(), Event()
    original = services[0]._status

    def held_status(db, principal):
        # record() has acquired its shared policy + organization locks here.
        entered.set()
        assert release.wait(10)
        return original(db, principal)

    monkeypatch.setattr(services[0], "_status", held_status)
    def withdraw():
        withdraw_started.set()
        return services[1].withdraw(owner)

    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(services[0].record, principal, event(grant))
        try:
            assert entered.wait(3)
            withdrawing = pool.submit(withdraw)
            assert withdraw_started.wait(3)
            assert not withdrawing.done()
        finally:
            release.set()
        assert writing.result()["status"] == "accepted"
        assert withdrawing.result()["enabled"] is False
    assert services[2].summary(owner)["total_events"] == 0
    with postgres["stores"][0].engine.connect() as db:
        assert db.scalar(select(func.count()).select_from(signals)) == 0
        assert db.scalar(select(limits.c.count)) == 1
    with pytest.raises(NetworkError) as failure:
        services[3].record(principal, event(grant))
    assert failure.value.code == "participation_disabled"


def test_postgres_participation_organization_quota_rolls_back_across_instances(postgres, monkeypatch):
    import seiche_agent_network.participation as module
    monkeypatch.setattr(module, "AGENT_DAILY_LIMIT", 4)
    monkeypatch.setattr(module, "WORKSPACE_DAILY_LIMIT", 3)
    services, grant = enabled(postgres)
    store, owner = postgres["stores"][0], postgres["owners"][0]
    second = store.authenticate(store.register_agent(owner, "Synthetic quota second agent")["token"])
    agents = (postgres["principals"][0], second)
    def write(index):
        try:
            return services[index % 4].record(agents[index % 2], event(grant))["status"]
        except NetworkError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write, range(12)))
    assert results.count("accepted") == 3
    assert results.count("participation_capacity") == 9
    with store.engine.connect() as db:
        assert db.scalar(select(func.count()).select_from(signals)) == 3
        assert db.scalar(select(func.sum(limits.c.count))) == 3


def test_postgres_participation_upgrade_bootstrap_serialized_across_workers(postgres):
    # The fixture owns this generated schema. Simulate a pre-upgrade database
    # by dropping only the three empty extension tables, preserving core state.
    assert {table.name for table in meta.tables.values()} == {
        "network_participation_grants", "network_participation_signals", "network_participation_limits"}
    with postgres["stores"][0]._transaction() as db:
        meta.drop_all(db)
    start = Barrier(4)

    def open_store(_):
        start.wait(timeout=5)
        store = Store(postgres["url"], **postgres["keys"])
        postgres["stores"].append(store)  # Fixture disposes every added engine.
        return Participation(store).status(postgres["owners"][0])

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(open_store, range(4)))
    assert all(value["enabled"] is False for value in results)
    service = Participation(postgres["stores"][-1])
    configured = service.configure(postgres["owners"][0], ParticipationRequest(
        enabled=True, policy_version=POLICY, data_notice_acknowledged=True))
    assert service.record(postgres["principals"][0], event(configured["grant_id"]))["status"] == "accepted"
