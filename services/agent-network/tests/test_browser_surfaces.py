"""Public source, banking and history clients keep their native contracts."""
import json

import pytest
from sqlalchemy import select

from seiche_agent_network import analytics, browser_surfaces as surfaces, demand
from seiche_agent_network.extra_products import EXTRA_PRODUCTS
from test_dataset_proxy import platform
from test_hosted_surfaces import BASE, set_json


def source_schema():
    paths = {}
    for path in ('workspace', 'series_summary', 'tracked_institutions', 'catalog', 'series',
                 'entities', 'observations', 'analysis', 'export.csv'):
        paths['/' + path] = {'get': {'parameters': [
            {'name': 'dataset', 'in': 'query', 'schema': {'type': 'string'}},
            {'name': 'series', 'in': 'query', 'schema': {'type': 'string'}},
            {'name': 'as_known_at', 'in': 'query', 'schema': {'type': 'string'}},
        ]}}
    return {'openapi': '3.1.0', 'info': {'version': '1.2.0'}, 'paths': paths,
            'servers': [{'url': surfaces.SOURCE_ORIGIN}], 'externalDocs': {'url': surfaces.SOURCE_ORIGIN + '/'},
            'x-mcp': {'url': surfaces.SOURCE_ORIGIN + '/mcp'}}


def test_source_ui_and_schema_resolve_on_owned_central_assets(platform):
    client, _, _, requests, reply, _ = platform
    set_json(reply, source_schema())
    for route in ('/v1/source-data?product=liquilens', '/v1/source-data/?product=undertow'):
        response = client.get(route)
        assert response.status_code == 200 and not response.history
        assert response.headers['content-type'].startswith('text/html')
        assert 'script-src \'self\'' in response.headers['content-security-policy']
        assert '<script src="' + BASE + '/static/source-data.js" defer></script>' in response.text
        assert '<style>' not in response.text and '<script>' not in response.text
        assert BASE + '/products/research-data/mcp' in response.text
        assert str(requests[-1].url) == surfaces.SOURCE_ORIGIN + '/openapi.json'
    for asset in ('source-data.js', 'source-data.css'):
        assert client.get('/static/' + asset).status_code == 200
    js = client.get('/static/source-data.js').text
    assert "new URL('../v1/source-data',document.currentScript.src)" in js
    assert "credentials:'omit',redirect:'error'" in js and 'size>4194304' in js
    schema = client.get('/v1/source-data/openapi.json').json()
    assert schema['servers'] == [{'url': BASE + '/v1/source-data'}]
    assert schema['x-mcp']['url'] == BASE + '/products/research-data/mcp'
    assert len(schema['paths']['/observations']['get']['parameters'][0]['schema']['enum']) == 16
    invalid = source_schema()
    invalid['paths']['/delete'] = {'delete': {}}
    set_json(reply, invalid)
    assert client.get('/v1/source-data/').status_code == 502


@pytest.mark.parametrize('operation,params', [
    ('catalog', {'product': 'seiche'}), ('healthz', {}), ('workspace', {'product': 'liquilens'}),
    ('analysis', {'product': 'undertow'}),
    ('series', {'dataset': 'fdic-financials', 'q': 'private-query-canary', 'limit': '10000', 'offset': '10000000'}),
    ('entities', {'dataset': 'india-reviewed-filings', 'q': 'private-query-canary'}),
    ('observations', {'dataset': 'ofr-repo', 'series': 'private-series-canary', 'entity': 'private-entity-canary',
                      'start': '2024-01-01', 'end': '2026-01-01', 'as_known_at': '2026-01-02T01:02:03.123456+05:30', 'limit': '2', 'offset': '1'}),
    ('series_summary', {'dataset': 'liquid-settlement', 'series': 'private-series-canary', 'entity': '', 'window': '260'}),
    ('tracked_institutions', {'scope': 'rbi_registry', 'q': 'private-query-canary', 'limit': '500', 'offset': '10000000'}),
])
def test_source_queries_keep_schema_receipts_and_do_not_retain_selectors(platform, operation, params):
    client, store, owner, requests, reply, _ = platform
    payload = {'schema': 'liquilens.research-data.v1', 'rows': [{'value': None, 'knowledge_time': '2025-01-01T00:00:00Z'}],
               'receipts': {'a': {'url': 'https://source.example/private-payload-canary', 'sha256': 'b' * 64}}}
    set_json(reply, payload)
    reply['headers']['x-research-source-sha'] = 'c' * 40
    response = client.get('/v1/source-data/' + operation, params=params,
                          headers={'Authorization': 'Bearer ' + owner['owner_token'], 'Cookie': 'private-cookie-canary=1'})
    assert response.status_code == 200 and response.content == reply['content']
    assert str(requests[-1].url).split('?')[0] == surfaces.SOURCE_ORIGIN + '/' + operation
    assert dict(requests[-1].url.params) == params
    assert 'authorization' not in requests[-1].headers and 'cookie' not in requests[-1].headers
    assert response.headers['x-research-source-sha'] == 'c' * 40
    with store.engine.connect() as db:
        retained = str(db.execute(select(demand.daily)).all()) + str(db.execute(select(analytics.daily)).all())
    assert 'canary' not in retained
    totals = demand.report(store)['totals']
    assert totals['requests'] == 1
    assert totals['exact_requests'] == int('dataset' in params)


@pytest.mark.parametrize('route', [
    '/v1/source-data/observations?dataset=ofr-repo&series=x&dataset=ofr-mmf',
    '/v1/source-data/catalog?url=https://private.example', '/v1/source-data/catalog?product=unknown',
    '/v1/source-data/series?dataset=unreviewed', '/v1/source-data/series?dataset=ofr-repo&limit=10001',
    '/v1/source-data/series?dataset=ofr-repo&offset=10000001',
    '/v1/source-data/tracked_institutions?limit=501', '/v1/source-data/tracked_institutions?scope=all',
    '/v1/source-data/series_summary?dataset=ofr-repo&series=x&window=261',
    '/v1/source-data/observations?dataset=ofr-repo',
    '/v1/source-data/observations?dataset=ofr-repo&series=x&start=2025-02-30',
    '/v1/source-data/observations?dataset=ofr-repo&series=x&as_known_at=2025-01-01T00:00:00',
    '/v1/source-data/observations?dataset=ofr-repo&series=x&start=2026-01-01&end=2025-01-01',
    '/v1/source-data/series?dataset=ofr-repo&q=private%0Aheader',
    '/v1/source-data/raw-files', '/v1/source-data/https%3A%2F%2Fprivate.example',
    '/v1/banking/monitoring/watchlist?slugs=cosmos-ucb,cosmos-ucb',
    '/v1/banking/monitoring/watchlist?slugs=', '/v1/banking/monitoring/watchlist?as_of=2999-01-01',
    '/v1/banking/institutions/cosmos-ucb?include_history=yes',
    '/v1/banking/institutions/cosmos-ucb?as_of=2026-02-30',
    '/v1/banking/institutions/cosmos-ucb?as_of=2025-01-01&as_of=2024-01-01',
    '/v1/banking/coverage?sector=nbfc', '/v1/banking/private',
    '/v1/economy/series/SOFR?n=2001', '/v1/economy/series/SOFR?n=0',
    '/v1/economy/series/SOFR.csv?n=120', '/v1/economy/series/SP500',
    '/v1/economy/series/index.json?url=https://private.example',
    '/v1/corpus/v1/catalog?source=private', '/v1/corpus/v1/datasets',
])
def test_unreviewed_routes_and_query_shapes_do_not_reach_upstream(platform, route):
    client, _, _, requests, _, _ = platform
    assert client.get(route).status_code in {400, 404}
    assert not requests


def test_full_monitor_and_filing_history_are_distinct_and_unchanged(platform):
    client, store, _, requests, reply, _ = platform
    monitor = {'schema': 'liquilens.institution-monitoring.v1', 'as_of': '2026-01-01',
               'watchlist_basis': 'All tracked Indian institution records', 'counts': {'tracked': 59, 'selected': 59},
               'rows': [{'slug': 'cosmos-ucb', 'content_sha256': 'a' * 64,
                         'review_url': '/api/experimental/v1/banking/monitoring/institutions/cosmos-ucb'}]}
    set_json(reply, monitor)
    all_rows = client.get('/v1/banking/monitoring/watchlist')
    assert all_rows.status_code == 200 and all_rows.content == reply['content']
    assert not requests[-1].url.query
    detail = client.get('/v1/banking/monitoring/institutions/cosmos-ucb?as_of=2026-01-01&include_history=true')
    assert detail.status_code == 200 and detail.content == reply['content']
    assert requests[-1].url.path.endswith('/monitoring/institutions/cosmos-ucb')
    assert client.get('/v1/banking/monitoring/watchlist', params={'slugs': ','.join('bank-' + str(i) for i in range(100))}).status_code == 200
    assert client.get('/v1/banking/monitoring/watchlist', params={'slugs': ','.join('bank-' + str(i) for i in range(101))}).status_code == 400
    assert client.get('/v1/data/liquilens.institution_watchlist').status_code == 400
    set_json(reply, {'schema': 'native-filing', 'history': [], 'period_end': '2025-03-31'})
    for route in ('coverage?sector=all&as_of=2026-01-01', 'institutions/private-slug-canary?include_history=true'):
        response = client.get('/v1/banking/' + route)
        assert response.status_code == 200 and response.content == reply['content']
    with store.engine.connect() as db:
        assert 'private-slug-canary' not in str(db.execute(select(demand.daily)).all())


@pytest.mark.parametrize('route,target', [
    ('/v1/source-data/export.csv?dataset=ofr-repo&series=x&limit=1', surfaces.SOURCE_ORIGIN + '/export.csv'),
    ('/v1/economy/series/SOFR.csv', surfaces.ECONOMY_ORIGIN + '/SOFR.csv'),
])
def test_csv_bytes_and_rights_failures_are_preserved(platform, route, target):
    client, _, _, requests, reply, _ = platform
    reply.update(content=b'\xef\xbb\xbf# source and clocks\r\ndate,value\r\n2025-01-01,1.0\r\n',
                 headers={'content-type': 'text/csv; charset=utf-8', 'content-disposition': 'attachment; filename="source.csv"',
                          'x-seiche-release-sha': 'c' * 40, 'set-cookie': 'private=1'})
    response = client.get(route)
    assert response.status_code == 200 and response.content == reply['content']
    assert str(requests[-1].url).split('?')[0] == target
    assert response.headers['content-disposition'] == 'attachment; filename="source.csv"'
    assert response.headers['x-seiche-release-sha'] == 'c' * 40 and 'set-cookie' not in response.headers
    for status in (403, 404, 422, 429, 503):
        set_json(reply, {'detail': 'Native rights or availability restriction'}, status)
        response = client.get(route)
        assert response.status_code == status and response.content == reply['content']
        assert response.headers['content-type'].startswith('application/json')


def test_economic_catalog_only_changes_reviewed_transport_fields(platform):
    client, _, _, requests, reply, _ = platform
    value = {'schema': 'seiche.series-index.v1', 'generated_at': '2026-01-01T00:00:00Z', 'series': [
        {'mnemonic': 'SOFR', 'source': 'fred', 'remote_id': 'SOFR', 'available': True, 'csv_restricted': None,
         'json': '/api/series/SOFR', 'csv': '/api/series/SOFR.csv'},
        {'mnemonic': 'SP500', 'available': True, 'csv_restricted': True, 'json': None, 'csv': None},
        {'mnemonic': 'DFF', 'available': True, 'csv_restricted': None, 'json': '/api/series/DFF', 'csv': '/api/series/DFF.csv'},
    ]}
    set_json(reply, value)
    response = client.get('/v1/economy/series/index.json')
    assert response.status_code == 200
    output = response.json()
    assert output['generated_at'] == value['generated_at']
    assert output['series'][0]['json'] == BASE + '/v1/economy/series/SOFR'
    assert output['series'][0]['source'] == 'fred' and output['series'][0]['remote_id'] == 'SOFR'
    assert output['series'][1]['csv_restricted'] is True and output['series'][1]['json'] is None
    assert output['series'][2]['available'] is True and output['series'][2]['json'] is None
    assert output['series'][2]['central_transport_available'] is False
    value['series'][0]['json'] = 'https://private.example/steal'
    set_json(reply, value)
    assert client.get('/v1/economy/series/index.json').status_code == 502
    set_json(reply, {'provenance': {'asof': '2026-01-01'}, 'points': [['2026-01-01', 1.5]]})
    assert client.get('/v1/economy/series/SOFR?n=120').content == reply['content']
    assert requests[-1].url.params['n'] == '120'


def test_native_etag_only_survives_unchanged_representation(platform):
    client, _, _, _, reply, _ = platform
    set_json(reply, source_schema())
    reply['headers']['etag'] = '"native-schema"'
    for route in ('/v1/source-data/', '/v1/source-data/openapi.json'):
        response = client.get(route)
        assert response.status_code == 200 and 'etag' not in response.headers
    set_json(reply, {'schema': 'seiche.series-index.v1', 'series': []})
    reply['headers']['etag'] = '"native-series"'
    assert 'etag' not in client.get('/v1/economy/series/index.json').headers
    set_json(reply, {'schema': 'liquilens.research-data.v1', 'datasets': []})
    reply['headers']['etag'] = '"native-catalog"'
    assert client.get('/v1/source-data/catalog').headers['etag'] == '"native-catalog"'
    reply.update(content=b'date,value\r\n2026-01-01,1\r\n', headers={'content-type': 'text/csv', 'etag': '"native-csv"'})
    response = client.get('/v1/economy/series/SOFR.csv')
    assert response.content == reply['content'] and response.headers['etag'] == '"native-csv"'


def test_fixed_browser_data_and_corpus_do_not_forward_any_credentials(platform):
    client, _, owner, requests, reply, gateway = platform
    gateway.client.cookies.set('native_secret', 'never-forward', domain='api.seiche.info', path='/')
    targets = {
        'riptide.allocation': 'https://api.seiche.info/riptide/api/v1/allocation',
        'seiche.gauge': 'https://api.seiche.info/api/gauge', 'seiche.public': 'https://api.seiche.info/api/public',
        'undertow.board': 'https://api.seiche.info/undertow/board.json',
    }
    for dataset, target in targets.items():
        response = client.get('/v1/data/' + dataset, headers={'Authorization': 'Bearer ' + owner['owner_token'],
                                                           'X-Liquilens-Traffic-Class': 'synthetic'})
        assert response.status_code == 200 and response.content == reply['content']
        assert str(requests[-1].url) == target
        assert 'authorization' not in requests[-1].headers and 'cookie' not in requests[-1].headers
        assert requests[-1].headers['x-financial-evidence-traffic-class'] == 'synthetic'
        assert client.get('/v1/data/' + dataset + '?url=https://private.example').status_code == 400
    assert client.get('/v1/corpus/v1/catalog').content == reply['content']
    assert str(requests[-1].url) == surfaces.CORPUS_ORIGIN + '/v1/catalog'


def test_extra_research_mcp_tools_use_only_fixed_targets(platform):
    client, _, _, requests, reply, _ = platform
    set_json(reply, {'jsonrpc': '2.0', 'id': 1, 'result': {'content': [{'type': 'text', 'text': '{}'}]}})
    for product in ('research-data', 'seiche-market-corpus'):
        info = EXTRA_PRODUCTS[product]
        for tool in info['read_tools']:
            response = client.post('/products/' + product + '/mcp', json={'jsonrpc': '2.0', 'id': 1,
                'method': 'tools/call', 'params': {'name': tool, 'arguments': {}}},
                headers={'MCP-Protocol-Version': '2025-11-25', 'X-Liquilens-Traffic-Class': 'synthetic'})
            assert response.status_code == 200 and str(requests[-1].url) == info['url']
            assert 'authorization' not in requests[-1].headers
            assert requests[-1].headers['x-financial-evidence-traffic-class'] == 'synthetic'
        before = len(requests)
        assert client.post('/products/' + product + '/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': 'delete_archive', 'arguments': {}}}).status_code == 403
        assert len(requests) == before


def test_browser_cors_matches_only_admitted_read_contracts(platform):
    client, _, _, _, _, _ = platform
    headers = {'Origin': 'https://liquilens.in', 'Access-Control-Request-Method': 'GET',
               'Access-Control-Request-Headers': 'X-Liquilens-Traffic-Class'}
    for route in ('/v1/source-data/catalog', '/v1/source-data/', '/v1/banking/monitoring/watchlist',
                  '/v1/banking/institutions/cosmos-ucb', '/v1/economy/series/SOFR.csv', '/v1/corpus/v1/catalog'):
        response = client.options(route, headers=headers)
        assert response.status_code == 200 and response.headers['access-control-allow-origin'] == 'https://liquilens.in'
        assert 'access-control-allow-origin' not in client.options(route, headers={**headers, 'Access-Control-Request-Method': 'POST'}).headers
    for route in ('/v1/source-data/private', '/v1/corpus/v1/bis/shards', '/v1/economy/series/SP500', '/v1/rooms', '/v1/operator/dataset-usage'):
        assert 'access-control-allow-origin' not in client.options(route, headers=headers).headers
    response = client.get('/v1/banking/coverage', headers={'Origin': 'https://liquilens.in'})
    assert 'x-research-source-sha' in response.headers['access-control-expose-headers'].lower()
