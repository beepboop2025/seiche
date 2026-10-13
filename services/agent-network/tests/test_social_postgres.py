"""Real PostgreSQL qualification; every test uses its generated private schema."""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier, Event
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from seiche_agent_network.models import NetworkError
from seiche_agent_network.social import (
    POLICY, PostCreate, PostReport, ProfilePublish, Social, meta, posts, profiles, quotas, reports,
)
from seiche_agent_network.store import Store
from test_postgres import postgres  # noqa: F401; generated-schema fixture

pytestmark = pytest.mark.skipif(not os.environ.get("SEICHE_TEST_POSTGRES_URL"),
                                reason="SEICHE_TEST_POSTGRES_URL not configured")


def profile():
    return ProfilePublish(display_name="Synthetic PG public agent", bio="Synthetic PostgreSQL test profile",
                          role="finance_research", topics=["funding"], website_url=None, traffic_class="synthetic",
                          policy_version=POLICY, acknowledged=True)


def event(grant):
    return PostCreate(event_id=str(uuid4()), grant_id=grant, topic="funding", kind="question",
                      text="Synthetic PostgreSQL test public post")


def setup(postgres):
    services = [Social(store) for store in postgres["stores"]]
    grant = services[0].publish(postgres["owners"][0], postgres["principals"][0].agent_id, profile())["grant_id"]
    return services, grant


def test_postgres_social_cross_instance_idempotency_and_conflict(postgres):
    services, grant = setup(postgres)
    principal = postgres["principals"][0]
    body = event(grant)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda index: services[index % 4].create_post(principal, body), range(16)))
    assert sum(item["status"] == "accepted" for item in results) == 1
    assert len({item["post"]["id"] for item in results}) == 1
    with pytest.raises(NetworkError) as failure:
        services[1].create_post(principal, body.model_copy(update={"text": "Synthetic changed body"}))
    assert failure.value.code == "idempotency_conflict"
    services[1].delete_post(principal, results[0]["post"]["id"])
    with pytest.raises(NetworkError) as failure:
        services[2].create_post(principal, body)
    assert failure.value.code == "post_unavailable"
    with postgres["stores"][0].engine.connect() as db:
        assert db.scalar(select(func.count()).select_from(posts)) == 1
        assert db.scalar(select(func.sum(quotas.c.count))) == 2


def test_postgres_social_withdrawal_waits_for_inflight_write_then_erases(postgres, monkeypatch):
    services, grant = setup(postgres)
    principal, owner = postgres["principals"][0], postgres["owners"][0]
    entered, release, withdrawing = Event(), Event(), Event()
    original = services[0]._public_posts

    def held_projection(db, rows):
        # create_post has inserted its row while holding shared policy/org locks.
        entered.set()
        assert release.wait(10)
        return original(db, rows)

    monkeypatch.setattr(services[0], "_public_posts", held_projection)
    def withdraw():
        withdrawing.set()
        return services[1].withdraw(owner, principal.agent_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(services[0].create_post, principal, event(grant))
        try:
            assert entered.wait(3)
            withdrawal = pool.submit(withdraw)
            assert withdrawing.wait(3)
            assert not withdrawal.done()
        finally:
            release.set()
        receipt = writing.result()
        assert withdrawal.result()["withdrawn"] is True
    assert services[2].feed(include_synthetic=True)["posts"] == []
    with pytest.raises(NetworkError):
        services[2].post(receipt["post"]["id"])
    with postgres["stores"][0].engine.connect() as db:
        assert db.scalar(select(posts.c.text)) is None
        assert db.scalar(select(func.count()).select_from(profiles)) == 0
        assert db.scalar(select(func.sum(quotas.c.count))) == 2


def test_postgres_social_organization_quota_atomic_across_agents(postgres, monkeypatch):
    import seiche_agent_network.social as module
    monkeypatch.setattr(module, "posting_limits", lambda: {"agent_per_day": 4, "organization_per_day": 3,
                                                         "agent_per_minute": 4, "organization_per_minute": 3})
    services, first_grant = setup(postgres)
    store, owner = postgres["stores"][0], postgres["owners"][0]
    second = store.authenticate(store.register_agent(owner, "Synthetic second public agent")["token"])
    second_grant = services[0].publish(owner, second.agent_id, profile())["grant_id"]
    actors = (postgres["principals"][0], second)
    grants = (first_grant, second_grant)
    def write(index):
        try:
            return services[index % 4].create_post(actors[index % 2], event(grants[index % 2]))["status"]
        except NetworkError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write, range(16)))
    assert results.count("accepted") == 3 and results.count("social_capacity") == 13
    with store.engine.connect() as db:
        assert db.scalar(select(func.count()).select_from(posts)) == 3
        assert db.scalar(select(func.sum(quotas.c.count)).where(quotas.c.bucket == "post_day")) == 3
        assert db.scalar(select(func.sum(quotas.c.count)).where(quotas.c.bucket == "post_minute")) == 3


def test_postgres_social_report_dedup_and_operator_hold_across_instances(postgres):
    services, grant = setup(postgres)
    principal = postgres["principals"][0]
    receipt = services[0].create_post(principal, event(grant))
    reporter = postgres["owners"][1]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda index: services[index % 4].report_post(reporter, receipt["post"]["id"],
                            PostReport(reason="Synthetic concurrent report")), range(12)))
    assert len({item["id"] for item in results}) == 1
    with postgres["stores"][0].engine.connect() as db:
        assert db.scalar(select(func.count()).select_from(reports)) == 1
        assert db.scalar(select(quotas.c.count).where(quotas.c.bucket == "report_day")) == 1
    services[0].suspend_organization(principal.org_id, "Synthetic operator hold")
    with pytest.raises(NetworkError) as failure:
        services[3].publish(postgres["owners"][0], principal.agent_id, profile())
    assert failure.value.code == "community_suspended"
    services[2].restore_organization(principal.org_id)
    assert services[1].profiles(include_synthetic=True)["profiles"] == []
    assert services[1].feed(include_synthetic=True)["posts"] == []


def test_postgres_social_rolling_upgrade_ddl_is_serialized(postgres):
    # All objects are inside the fixture's generated schema. Only this feature's
    # five empty tables are dropped to model an existing pre-community database.
    assert {table.name for table in meta.tables.values()} == {
        "network_social_profiles", "network_social_posts", "network_social_quotas",
        "network_social_reports", "network_social_holds"}
    with postgres["stores"][0]._transaction() as db:
        meta.drop_all(db)
    start = Barrier(4)
    def open_store(_):
        start.wait(timeout=5)
        store = Store(postgres["url"], **postgres["keys"])
        postgres["stores"].append(store)
        return Social(store).profiles()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(open_store, range(4)))
    assert all(value["profiles"] == [] for value in results)
    service = Social(postgres["stores"][-1])
    principal = postgres["principals"][0]
    grant = service.publish(postgres["owners"][0], principal.agent_id, profile())["grant_id"]
    assert service.create_post(principal, event(grant))["status"] == "accepted"
