from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from seiche_agent_network.analytics import Analytics, daily
from seiche_agent_network.crypto import generate_keys
from seiche_agent_network.models import NetworkError
from seiche_agent_network.store import Store


@pytest.fixture
def data(tmp_path):
    now = [1_800_000_000.0]
    store = Store(tmp_path / "analytics.db", **generate_keys(), clock=lambda: now[0])
    owners, agents = [], []
    for name in ("One", "Two"):
        owner = store.authenticate(store.bootstrap_org(name)["owner_token"])
        owners.append(owner)
        agents.append(store.authenticate(store.register_agent(owner, name)["token"]))
    analytics = Analytics(store, tool_registry={"seiche": {"funding_snapshot"}}, source_registry={"seiche": {"central_bank"}})
    yield analytics, store, owners, agents, now
    store.close()


def record(analytics, principal, **kwargs):
    values = dict(product="seiche", tool="funding_snapshot", method="tools/call", http_status=200, duration_ms=10)
    analytics.record(principal, **(values | kwargs))


def test_tenant_isolation_anonymous_unknowns_and_tool_error(data):
    analytics, store, owners, agents, _ = data
    record(analytics, agents[0], country="IN", tool_outcome="error")
    record(analytics, agents[1], country="US", http_status=503, freshness="stale", source_age_seconds=3600)
    record(analytics, None)
    own = analytics.report(owners[0])
    assert own["totals"]["requests"] == 1
    assert own["totals"]["http_errors"] == 0
    assert own["totals"]["tool_errors"] == 1
    assert own["countries"] == [{"country": "IN", "requests": 1}]
    global_report = analytics.report()
    assert global_report["totals"]["requests"] == 3
    assert global_report["totals"]["anonymous_requests"] == 1
    assert global_report["totals"]["country_unknown_requests"] == 1
    assert global_report["activity"]["observed_org_ids"] == 2
    assert global_report["activity"]["verified_external_users"] is None
    assert global_report["freshness"] == {"fresh": 0, "stale": 1, "restricted": 0, "unknown": 2}
    assert global_report["model_training"] is False
    store.set_agent_status(owners[0], agents[0].agent_id, "revoked")
    with pytest.raises(NetworkError):
        analytics.report(agents[0])


def test_histogram_percentiles_are_intervals_not_false_precision(data):
    analytics, _, owners, agents, _ = data
    for _ in range(95):
        record(analytics, agents[0], duration_ms=10)
    for _ in range(5):
        record(analytics, agents[0], duration_ms=70000)
    latency = analytics.report(owners[0])["latency"]
    assert latency["observations"] == 100
    assert latency["p95"] == {"lower_bound_ms": 5, "upper_bound_ms": 10, "exact": False, "overflow": False}
    assert sum(b["count"] for b in latency["buckets"]) == 100


def test_registry_coarsens_unknown_tool_without_persisting_query(data):
    analytics, store, owners, agents, _ = data
    record(analytics, agents[0], tool="private_portfolio_MSFT_10000_shares")
    tool = analytics.report(owners[0])["tools"][0]
    assert (tool["product"], tool["tool"], tool["requests"]) == ("seiche", "other", 1)
    with store.engine.connect() as db:
        assert db.execute(select(daily.c.tool)).scalar_one() == "other"
    for kwargs in ({"country": "not-a-country"}, {"duration_ms": float("nan")}, {"source_age_seconds": -1},
                   {"tool_outcome": "bought"}, {"traffic_class": "user@email.com"}):
        with pytest.raises(NetworkError):
            record(analytics, agents[0], **kwargs)


def test_aggregate_increment_atomic_and_retention_exact_days(data):
    analytics, _, owners, agents, now = data
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: record(analytics, agents[0]), range(40)))
    assert analytics.report(owners[0])["totals"]["requests"] == 40
    now[0] += 29 * 86400
    assert analytics.report(owners[0])["totals"]["requests"] == 40
    now[0] += 86400
    assert analytics.report(owners[0])["totals"]["requests"] == 0
    assert analytics.purge()["purged_aggregate_buckets"] == 1


def test_weekly_returning_orgs_denominator_is_previous_window(data):
    analytics, store, owners, agents, now = data
    record(analytics, agents[0])
    record(analytics, agents[1])
    now[0] += 7 * 86400
    record(analytics, agents[0])
    store.verify_organization(owners[0].org_id)
    activity = analytics.report()["activity"]
    assert activity["weekly_returning_orgs"]["numerator"] == 1
    assert activity["weekly_returning_orgs"]["eligible_denominator"] == 2
    assert activity["weekly_returning_orgs"]["fraction"] == .5
    assert activity["operator_verified_org_ids"] == 1
    assert activity["verified_trading_adoption"] is None


def test_source_worker_separate_from_requests_and_tenant_reports(data):
    analytics, _, owners, _, _ = data
    analytics.record_source_freshness(product="seiche", source="central_bank", freshness="stale", source_age_seconds=500)
    report = analytics.report()
    assert report["totals"]["requests"] == 0
    assert report["source_monitor"][0]["checks"] == 1
    assert report["source_monitor"][0]["freshness"] == "stale"
    assert analytics.report(owners[0])["source_monitor"] == []


def test_restricted_source_is_distinct_from_freshness_and_transport(data):
    analytics, _, owners, agents, _ = data
    record(analytics, agents[0], freshness="restricted", http_status=200, tool_outcome="success")
    report = analytics.report(owners[0])
    assert report["freshness"]["restricted"] == 1
    assert report["freshness"]["fresh"] == 0
    assert report["totals"]["http_2xx"] == 1


def test_tenant_deletion_and_idle_store_maintenance_purge_analytics(data):
    analytics, store, owners, agents, now = data
    record(analytics, agents[0])
    record(analytics, agents[1])
    store.delete_org(owners[0])
    assert analytics.report()["totals"]["requests"] == 1
    now[0] += 30 * 86400
    assert store.purge_expired()["purged_aggregate_buckets"] == 1
    with store.engine.connect() as db:
        assert db.execute(select(daily)).all() == []
