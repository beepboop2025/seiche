"""Catalog classifications, aggregate integrity and bounded owner/operator reports."""
from concurrent.futures import ThreadPoolExecutor
import json

import pytest
from sqlalchemy import func, select
from starlette.datastructures import QueryParams

from seiche_agent_network import demand
from seiche_agent_network.analytics import Analytics, daily as operational
from seiche_agent_network.crypto import generate_keys
from seiche_agent_network.data_catalog import (FIXED_DATA, classify, catalog, fixed_request,
                                               research_request, tool_registry)
from seiche_agent_network.models import NetworkError
from seiche_agent_network.store import Store


@pytest.fixture
def network(tmp_path):
    now = [1_800_000_000.0]
    store = Store(tmp_path / 'db', **generate_keys(), clock=lambda: now[0])
    owners = [store.authenticate(store.bootstrap_org(name)['owner_token']) for name in ('One', 'Two')]
    agents = [store.authenticate(store.register_agent(owner, 'Agent')['token']) for owner in owners]
    analytics = Analytics(store, tool_registry=tool_registry())
    yield store, analytics, owners, agents, now
    store.close()


def record(analytics, principal=None, **kwargs):
    analytics.record(principal, **(dict(product='seiche', tool='funding_stress_now', method='tools/call',
                     http_status=200, duration_ms=12) | kwargs))


def test_every_registered_tool_has_reviewed_family_or_discovery():
    document = catalog()
    assert len(tool_registry()) == 14
    assert all(item['basis'] in {'family', 'discovery'} for item in document['mcp_classification'])
    assert {item['id'] for item in document['datasets']} >= set(FIXED_DATA)
    assert document['raw_arguments_retained'] is False and document['data_resale_permission'] is False
    assert classify('seiche', 'tools/call', 'private portfolio XYZ').dataset_id == 'seiche.unknown'
    assert classify('private_org', 'private_method', 'private_query').dataset_id == 'other.unknown'
    for name in ('financial_evidence_datasets', 'financial_evidence_route'):
        assert classify('financial-evidence', 'tools/call', name).basis == 'discovery'
    assert classify('financial-evidence', 'tools/call', 'financial_evidence_sources').basis == 'family'


def test_historical_catalog_buckets_remain_in_reports_after_extension(network):
    store, analytics, owners, _, _ = network
    record(analytics, owners[0], tool='funding_stress_now')
    with store._transaction() as db:
        row = db.execute(select(demand.daily)).mappings().one()
        values = dict(row)
        values.update(key='historic-catalog-bucket', catalog_version='central-data-2026-10-12', requests=3)
        db.execute(demand.daily.insert().values(**values))
    report = demand.report(store, owners[0])
    assert report['totals']['requests'] == 4 and report['totals']['family_requests'] == 4
    assert len(report['rows']) == 1
    assert report['rows'][0]['dataset_id'] == 'seiche.money_markets' and report['rows'][0]['requests'] == 4
    with store.engine.connect() as db:
        assert set(db.scalars(select(demand.daily.c.catalog_version))) == {'central-data-2026-10-12', 'central-data-2026-10-13'}


def test_two_tables_commit_or_rollback_together(network, monkeypatch):
    store, analytics, _, agents, _ = network
    original = demand.record
    def failure(*args):
        original(*args)
        raise RuntimeError('synthetic failure after both inserts')
    monkeypatch.setattr(demand, 'record', failure)
    with pytest.raises(RuntimeError): record(analytics, agents[0])
    with store.engine.connect() as db:
        assert db.scalar(select(func.count()).select_from(operational)) == 0
        assert db.scalar(select(func.count()).select_from(demand.daily)) == 0
    monkeypatch.setattr(demand, 'record', original)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: record(analytics, agents[0], country='IN' if n % 2 else 'US'), range(40)))
    assert demand.report(store)['totals']['requests'] == 40
    with store.engine.connect() as db:
        # Operational country buckets differ, but they share one dataset lock.
        assert db.scalar(select(func.count()).select_from(operational)) == 2
        assert db.scalar(select(func.count()).select_from(demand.daily)) == 1


def test_scopes_top_n_traffic_actor_counts_and_unknowns(network):
    store, analytics, owners, agents, _ = network
    record(analytics, agents[0], traffic_class='synthetic')
    record(analytics, owners[0], tool='data_health', traffic_class='internal')
    record(analytics, agents[1], http_status=502, tool_outcome='error')
    record(analytics, None, tool='private_portfolio_canary')
    record(analytics, owners[0], tool='gift_city_context', tool_outcome='success', response_state='empty')
    sibling = store.authenticate(store.register_agent(owners[0], 'Sibling')['token'])
    record(analytics, sibling)
    assert demand.report(store, owners[0])['totals']['requests'] == 4
    assert demand.report(store, agents[0])['totals']['requests'] == 1
    report = demand.report(store, limit=1)
    assert len(report['rows']) == 1 and report['truncated']
    assert report['totals'] == dict(requests=6, http_errors=1, tool_errors=1, tool_successes=1,
        empty_responses=1, anonymous_requests=1, authenticated_agent_requests=3, owner_requests=2,
        unknown_requests=1, discovery_requests=1, data_requests=4, exact_requests=0, family_requests=4)
    assert demand.report(store, traffic='non_test')['totals']['requests'] == 4
    assert demand.report(store, traffic='synthetic')['totals']['requests'] == 1
    assert 'private_portfolio_canary' not in json.dumps(report)
    with store.engine.connect() as db:
        assert 'private_portfolio_canary' not in str(db.execute(select(demand.daily)).all())
    for bad in ('private', '', 'ALL'):
        with pytest.raises(NetworkError): demand.report(store, traffic=bad)
    for bad in (0, 201, True, '10'):
        with pytest.raises(NetworkError): demand.report(store, limit=bad)
    store.set_agent_status(owners[0], agents[0].agent_id, 'revoked')
    with pytest.raises(NetworkError): demand.report(store, agents[0])


def test_expiry_and_workspace_erasure(network):
    store, analytics, owners, agents, now = network
    record(analytics, agents[0]); record(analytics, agents[1]); record(analytics)
    store.delete_org(owners[0])
    assert demand.report(store)['totals']['requests'] == 2
    now[0] += 29 * 86400
    assert demand.report(store)['totals']['requests'] == 2
    now[0] += 86400
    assert demand.report(store)['totals']['requests'] == 0
    assert store.purge_expired()['purged_dataset_buckets'] == 2
    with store.engine.connect() as db:
        assert db.scalar(select(func.count()).select_from(demand.daily)) == 0


def test_daily_history_separates_closed_days_and_anonymous_repeat_identity(network):
    store, analytics, owners, agents, now = network
    record(analytics, agents[0], traffic_class="synthetic")
    record(analytics, agents[1], tool="data_health")  # discovery is not dataset demand
    record(analytics, agents[0])
    record(analytics)
    assert demand.history(store)["rows"] == []
    now[0] += 86400
    record(analytics, agents[0])
    record(analytics)
    now[0] += 86400
    result = demand.history(store)
    assert [row["requests"] for row in result["rows"]] == [2, 2]
    assert [row["registered_agents"] for row in result["rows"]] == [1, 1]
    assert [row["returning_registered_agents"] for row in result["rows"]] == [0, 1]
    assert {track["id"] for track in result["model_tracks"]} == {"market_liquidity", "economic_demand"}
    assert not result["model_training"] and not result["production_model_eligible"]
    assert agents[0].agent_id not in json.dumps(result)
    store.delete_org(owners[0])
    assert all(row["registered_agents"] == 0 for row in demand.history(store)["rows"])
    now[0] += 30 * 86400
    assert demand.history(store)["rows"] == []


@pytest.mark.parametrize('dataset,query', [
    ('seiche.money_markets','url=https://private.example'),
    ('liquilens.institution_watchlist','slugs=a&slugs=b'),
    ('liquilens.institution_watchlist','slugs=Upper'),
    ('liquilens.institution_watchlist','slugs=a,b,c,d,e,f'),
    ('undertow.exit_check','sizes_usd=0'), ('undertow.exit_check','sizes_usd=1000000.01'),
    ('undertow.exit_check','sizes_usd=1e3'), ('undertow.exit_check','sizes_usd=1.123'),
])
def test_fixed_query_rejects_unreviewed_values(dataset, query):
    with pytest.raises(NetworkError): fixed_request(dataset, QueryParams(query))


def test_parameterized_targets_normalize_only_allowlisted_inputs():
    url, params, classification = fixed_request('undertow.exit_check', QueryParams('sizes_usd=1000,1000.00,2500.50'))
    assert params == {'sizes_usd':'1000,2500.5'} and classification.basis == 'exact'
    assert url == 'https://api.seiche.info/undertow/mcp/exit-check'
    _, params, _ = fixed_request('liquilens.institution_watchlist', QueryParams('slugs=bank-a,bank-b,bank-a'))
    assert params == {'slugs':'bank-a,bank-b'}
    _, params, classification = research_request('query', QueryParams('dataset=bank_risk&entity=private-bank&limit=2000&offset=100000'))
    assert params['entity'] == 'private-bank' and classification.dataset_id == 'research.bank_risk'
    assert 'private-bank' not in str(classification)


def test_research_discovery_and_native_empty_optional_filters():
    url, params, classification = research_request('datasets', QueryParams())
    assert url.endswith('/api/v1/datasets') and params == {} and classification.basis == 'discovery'
    _, params, _ = research_request('agent-query', QueryParams('dataset=bank_risk&entity=&start_date=&end_date=&previous_revision='))
    assert params == dict(dataset='bank_risk', entity='', start_date='', end_date='', previous_revision='')
    _, params, classification = research_request('agent-review', QueryParams('bank=&limit=25&previous_revision='))
    assert params['bank'] == '' and classification.basis == 'family'
    assert research_request('agent-review', QueryParams())[1] == {}
    with pytest.raises(NetworkError): research_request('datasets', QueryParams('private=query'))


@pytest.mark.parametrize('operation,basis', [('start','discovery'), ('release','discovery'),
                                          ('sources','family'), ('funding-review','family')])
def test_reviewed_research_kit_fixed_reads(operation, basis):
    url, params, classification = research_request(operation, QueryParams())
    assert url == 'https://api.seiche.info/openbb/api/v1/' + operation
    assert params == {} and classification.basis == basis and classification.operation == 'research_wrapper'
    with pytest.raises(NetworkError): research_request(operation, QueryParams('private=query'))


@pytest.mark.parametrize('operation,query', [
    ('query','dataset=private'), ('query','dataset=bank_risk&limit=2001'),
    ('agent-query','dataset=bank_risk&limit=101'), ('query','dataset=bank_risk&offset=100001'),
    ('query','dataset=bank_risk&start_date=2026-02-30'),
    ('query','dataset=bank_risk&start_date=2026-03-01&end_date=2026-02-01'),
    ('query','dataset=bank_risk&url=https://elsewhere'),
    ('agent-query','dataset=bank_risk&previous_revision=private'),
    ('agent-review','bank=x&limit=26'), ('workflow','workflow=funding&selection=USDX'),
    ('workflow','workflow=institutions&selection=UPPER'),
])
def test_research_rejects_unknown_or_unbounded_queries(operation, query):
    with pytest.raises(NetworkError): research_request(operation, QueryParams(query))
