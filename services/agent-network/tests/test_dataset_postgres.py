"""Optional real PostgreSQL tests; fixture owns an isolated disposable schema."""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier, Event

import pytest
from sqlalchemy import func, select

from test_postgres import postgres
from seiche_agent_network import demand
from seiche_agent_network.analytics import Analytics, daily as operational
from seiche_agent_network.data_catalog import tool_registry
from seiche_agent_network.models import NetworkError
from seiche_agent_network.store import Store

pytestmark = pytest.mark.skipif(not os.environ.get('SEICHE_TEST_POSTGRES_URL'), reason='SEICHE_TEST_POSTGRES_URL not configured')


def record(analytics, principal, country='IN'):
    analytics.record(principal, product='seiche', tool='funding_stress_now', method='tools/call',
                     http_status=200, duration_ms=1, traffic_class='synthetic', country=country)


def test_dataset_cross_instance_increment_for_shared_and_distinct_operational_buckets(postgres):
    analytics = [Analytics(store, tool_registry=tool_registry()) for store in postgres['stores']]
    principal = postgres['principals'][0]
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda n: record(analytics[n % 4], principal, 'US' if n % 2 else 'IN'), range(80)))
    report = demand.report(postgres['stores'][1], postgres['owners'][0])
    assert report['totals']['requests'] == 80 and report['totals']['authenticated_agent_requests'] == 80
    assert analytics[0].report()['totals']['requests'] == 80
    with postgres['stores'][0].engine.connect() as db:
        assert db.scalar(select(func.count()).select_from(demand.daily)) == 1
        assert db.scalar(select(func.count()).select_from(operational)) == 2


def test_dataset_extension_bootstrap_is_serialized_across_instances(postgres):
    with postgres['stores'][0]._transaction() as db:
        demand.meta.drop_all(db)
    barrier = Barrier(4)
    def construct(_):
        barrier.wait(10)
        return Store(postgres['url'], **postgres['keys'])
    with ThreadPoolExecutor(max_workers=4) as pool:
        added = list(pool.map(construct, range(4)))
    postgres['stores'].extend(added)
    for store in added:
        assert demand.report(store)['totals']['requests'] == 0


def test_dataset_both_tables_rollback_and_deletion_waits_for_inflight_record(postgres, monkeypatch):
    store, second = postgres['stores'][:2]
    analytics = Analytics(store, tool_registry=tool_registry())
    original = demand.record
    def failure(*args):
        original(*args)
        raise OSError('synthetic failure')
    monkeypatch.setattr(demand, 'record', failure)
    with pytest.raises(OSError): record(analytics, postgres['principals'][0])
    assert demand.report(second)['totals']['requests'] == 0
    assert analytics.report()['totals']['requests'] == 0
    entered, release = Event(), Event()
    def delayed(*args):
        original(*args)
        entered.set()
        assert release.wait(10)
    monkeypatch.setattr(demand, 'record', delayed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(record, analytics, postgres['principals'][0])
        assert entered.wait(5)
        deleting = pool.submit(second.delete_org, postgres['owners'][0])
        try:
            assert not deleting.done()
        finally:
            release.set()
        writing.result(); deleting.result()
    assert demand.report(second)['totals']['requests'] == 0
    assert analytics.report()['totals']['requests'] == 0
    with pytest.raises(NetworkError): record(analytics, postgres['principals'][0])


def test_dataset_report_has_one_snapshot_during_concurrent_write(postgres):
    from sqlalchemy import event
    reporting, writing = postgres['stores'][:2]
    analytics = Analytics(writing, tool_registry=tool_registry())
    captured, release = Event(), Event()
    def after_select(connection, cursor, statement, parameters, context, executemany):
        if 'network_dataset_daily' in statement and 'sum(' in statement.lower() and not captured.is_set():
            # PostgreSQL has executed the report's aggregate statement but the
            # application has not read its results or any subsequent projection.
            captured.set()
            assert release.wait(10)
    event.listen(reporting.engine, 'after_cursor_execute', after_select)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(demand.report, reporting)
            assert captured.wait(5)
            try:
                # A shared policy lock must allow this independent write.
                future = pool.submit(record, analytics, postgres['principals'][0])
                future.result(timeout=5)
            finally:
                release.set()
            report = pending.result()
        assert report['totals']['requests'] == 0
        assert report['rows'] == [] and report['traffic_classes'] == {}
        assert demand.report(writing)['totals']['requests'] == 1
    finally:
        release.set()
        event.remove(reporting.engine, 'after_cursor_execute', after_select)
