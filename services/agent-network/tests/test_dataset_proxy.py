import asyncio
import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from seiche_agent_network import demand
from seiche_agent_network.api import create_app
from seiche_agent_network.crypto import generate_keys
from seiche_agent_network.data_catalog import FIXED_DATA
from seiche_agent_network.gateway import Gateway, mount_gateway
from seiche_agent_network.store import Store


@pytest.fixture
def platform(tmp_path):
    store = Store(tmp_path/'db', **generate_keys())
    owner = store.bootstrap_org('Synthetic owner')
    app = create_app(store, purge_interval=0)
    requests, reply = [], {'status':200, 'content':b' { "private_canary": [1,2] }\n', 'headers':{'content-type':'application/json'}}
    async def upstream(request):
        requests.append(request)
        return httpx.Response(reply['status'], content=reply['content'], headers=reply['headers'])
    mount_gateway(app, store, transport=httpx.MockTransport(upstream))
    with TestClient(app) as client:
        yield client, store, owner, requests, reply, app.state.gateway
    store.close()


def test_all_eight_exact_targets_preserve_bytes_static_query_and_credentials(platform):
    client, store, owner, requests, reply, gateway = platform
    gateway.client.cookies.set('private_cookie', 'must-not-forward', domain='api.seiche.info', path='/')
    for dataset_id, (_, _, target) in list(FIXED_DATA.items())[:8]:
        result = client.get('/v1/data/'+dataset_id, headers={'Authorization':'Bearer '+owner['owner_token'], 'Cookie':'caller=secret'})
        assert result.status_code == 200 and result.content == reply['content']
        assert str(requests[-1].url) == target
        assert result.headers['x-source-url'] == target
        assert result.headers['x-dataset-id'] == dataset_id
        assert 'content-encoding' not in result.headers
        assert requests[-1].headers['accept-encoding'] == 'identity'
        assert 'authorization' not in requests[-1].headers and 'cookie' not in requests[-1].headers
    report = client.get('/v1/dataset-usage', headers={'Authorization':'Bearer '+owner['owner_token']}).json()
    assert report['totals']['exact_requests'] == 8 and report['totals']['owner_requests'] == 8
    assert 'private_canary' not in json.dumps(report)
    with store.engine.connect() as db:
        assert 'private_canary' not in str(db.execute(select(demand.daily)).all())


def test_filters_are_forwarded_without_retention_or_provenance_echo(platform):
    client, store, owner, requests, _, _ = platform
    response = client.get('/v1/research/api/v1/query', params={'dataset':'bank_risk','entity':'private-bank-canary','limit':'2'})
    assert response.status_code == 200 and requests[-1].url.params['entity'] == 'private-bank-canary'
    assert response.headers['x-source-url'] == 'https://api.seiche.info/openbb/api/v1/query'
    assert response.headers['x-dataset-id'] == 'research.bank_risk'
    assert client.get('/v1/data/liquilens.institution_watchlist?slugs=private-bank-canary').status_code == 200
    with store.engine.connect() as db:
        assert 'private-bank-canary' not in str(db.execute(select(demand.daily)).all())
    assert demand.report(store)['totals']['exact_requests'] == 2


@pytest.mark.parametrize('status,content', [(200,b'[]'),(204,b''),(205,b''),(404,b'{"error":"native unavailable"}'),(503,b'{"error":"native offline"}')])
def test_empty_and_native_errors_preserve_status_and_are_counted(platform, status, content):
    client, store, _, _, reply, _ = platform
    reply.update(status=status, content=content)
    result = client.get('/v1/data/seiche.money_markets')
    assert result.status_code == status and result.content == content
    totals = demand.report(store)['totals']
    assert totals['requests'] == 1 and totals['http_errors'] == int(status >= 400)
    assert totals['empty_responses'] == int(status in {200,204,205})


@pytest.mark.parametrize('status,code', [
    (401, 'upstream_authentication_required'), (403, 'upstream_access_denied'),
    (429, 'upstream_rate_limited'), (451, 'upstream_restricted'),
])
@pytest.mark.parametrize('content,media', [
    (b'<html>private-token challenge</html>', 'text/html'),
    (b'{"error":"private-token"}', 'application/json'),
])
def test_access_failures_preserve_status_without_forwarding_source_body(platform, status, code, content, media):
    client, store, owner, requests, reply, gateway = platform
    reply.update(status=status, content=content, headers={
        'content-type': media, 'content-encoding': 'unsupported',
        'retry-after': '60', 'set-cookie': 'private-token',
        'location': 'https://private-token.example', 'www-authenticate': 'Bearer private-token',
    })
    result = client.get('/v1/data/seiche.money_markets',
                        headers={'Authorization': 'Bearer ' + owner['owner_token'], 'Cookie': 'private-token'})
    assert result.status_code == status
    failure = result.json()['error']
    assert failure['code'] == code and failure['upstream_status'] == status
    assert failure['failure_scope'] == 'upstream' and failure['automatic_retry'] is False
    assert failure['retryable'] is (status == 429) and failure['next_step']
    assert result.headers.get('retry-after') == ('60' if status == 429 else None)
    assert result.headers['x-dataset-id'] == 'seiche.money_markets'
    assert result.headers['x-source-url'] == FIXED_DATA['seiche.money_markets'][2]
    assert set(result.headers['cache-control'].split(', ')) == {'no-store'}
    assert 'private-token' not in result.text + str(result.headers)
    assert not {'authorization', 'cookie'} & set(requests[0].headers)
    assert len(requests) == 1 and gateway.active == 0 and not gateway.sessions
    totals = demand.report(store)['totals']
    assert totals['requests'] == 1 and totals['http_errors'] == 1 and totals['empty_responses'] == 0


@pytest.mark.parametrize('retry_after,expected', [
    ('0', '0'), ('00060', '60'), ('9999999999', '9999999999'),
    ('Wed, 21 Oct 2026 07:28:00 GMT', 'Wed, 21 Oct 2026 07:28:00 GMT'),
    ('-1', None), ('1.5', None), ('+60', None), ('private-token', None),
    ('Wed, 21 Oct 2026 07:28:00 GMT private-token', None),
    ('Thu, 21 Oct 2026 07:28:00 GMT', None), ('9' * 129, None),
])
def test_rate_limit_retry_after_is_strictly_bounded_metadata(platform, retry_after, expected):
    client, _, _, requests, reply, _ = platform
    reply.update(status=429, content=b'<html>private-token</html>',
                 headers={'content-type': 'text/html', 'retry-after': retry_after})
    result = client.get('/v1/data/seiche.money_markets')
    assert result.status_code == 429 and result.headers.get('retry-after') == expected
    assert len(requests) == 1 and 'private-token' not in result.text + str(result.headers)


@pytest.mark.parametrize('status,content,headers', [
    (302,b'{}',{'location':'https://private.example','content-type':'application/json'}),
    (200,b'{}',{'content-encoding':'br','content-type':'application/json'}),
    (200,b'<html>private</html>',{'content-type':'text/html'}),
    (200,b'{invalid',{'content-type':'application/json'}),
    (200,b'NaN',{'content-type':'application/json'}),
    (200,b'',{'content-type':'application/json'}),
    (200,b'"'+b'x'*4194304+b'"',{'content-type':'application/json'}),
])
def test_unsafe_native_response_is_bounded_error_and_recorded(platform, status, content, headers):
    client, store, _, requests, reply, gateway = platform
    reply.update(status=status, content=content, headers=headers)
    result = client.get('/v1/data/seiche.money_markets')
    assert result.status_code == 502 and len(requests) == 1 and gateway.active == 0
    assert demand.report(store)['totals']['http_errors'] == 1
    assert 'private' not in result.text


def test_auth_report_boundaries_and_invalid_requests_never_forward(platform, monkeypatch):
    client, store, owner, requests, _, _ = platform
    for auth in ('Basic private', 'Bearer ', ''):
        assert client.get('/v1/data/seiche.money_markets', headers={'Authorization':auth}).status_code == 401
        assert client.get('/products/seiche/mcp', headers={'Authorization':auth}).status_code == 401
    assert client.get('/v1/data/seiche.money_markets?url=https://private.example').status_code == 400
    assert not requests
    assert client.get('/v1/dataset-usage').status_code == 401
    key = 'operator-secret-'*4
    monkeypatch.setenv('SEICHE_OPERATOR_REPORT_TOKEN', key)
    for auth in (key, 'Basic '+key, 'Bearer '+owner['owner_token']):
        assert client.get('/v1/operator/dataset-usage', headers={'Authorization':auth}).status_code == 401
    result = client.get('/v1/operator/dataset-usage', headers={'Authorization':'Bearer '+key})
    assert result.status_code == 200 and result.json()['scope'] == 'operator_global'
    assert result.json()['measurement_diagnostics']['durable_queue'] is False
    headers = {'Authorization':'Bearer '+owner['owner_token']}
    assert client.get('/v1/dataset-usage?limit=201', headers=headers).status_code == 422
    assert client.get('/v1/dataset-usage?traffic=private', headers=headers).status_code == 400
    assert 'measurement_diagnostics' not in client.get('/v1/dataset-usage', headers=headers).json()
    assert client.get('/v1/data-catalog').json()['version'] == 'central-data-2026-10-13'


def test_extra_products_reject_unreviewed_tools_and_modern_protocol_headers(platform):
    client, _, _, requests, reply, _ = platform
    reply['content'] = b'{"jsonrpc":"2.0","id":1,"result":{}}'
    for product in ('riptide','myquant-editorial','myquant-app','trade-safety','financial-evidence'):
        result = client.post('/products/'+product+'/mcp', json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'new_write_tool','arguments':{}}})
        assert result.status_code == 403
        for params in (None, {}, {'name':None}, {'name':[]}):
            result = client.post('/products/'+product+'/mcp', json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':params})
            assert result.status_code == 403
    assert not requests
    headers = {'MCP-Protocol-Version':'2026-07-28','Mcp-Method':'malicious','Mcp-Name':'private'}
    assert client.post('/products/riptide/mcp', headers=headers, json={'jsonrpc':'2.0','id':1,'method':'server/discover'}).status_code == 200
    assert requests[-1].headers['Mcp-Method'] == 'server/discover'
    assert 'Mcp-Name' not in requests[-1].headers
    assert client.post('/products/riptide/mcp', headers=headers, json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'riptide_overview','arguments':{}}}).status_code == 200
    assert requests[-1].headers['Mcp-Name'] == 'riptide_overview'


def test_empty_count_does_not_guess_nested_wrapper_data_semantics(platform):
    client, store, _, _, reply, _ = platform
    reply['content'] = b'{"rows":[],"status":"available"}'
    assert client.get('/v1/research/api/v1/query?dataset=bank_risk').status_code == 200
    report = demand.report(store)
    assert report['totals']['requests'] == 1 and report['totals']['empty_responses'] == 0
    assert 'nested rows' in report['empty_response_meaning']


@pytest.mark.asyncio
async def test_diagnostics_distinguish_late_commit_capacity_and_permanent_error(tmp_path, monkeypatch):
    import seiche_agent_network.gateway as module
    monkeypatch.setattr(module, 'METADATA_WAIT_SECONDS', .005)
    monkeypatch.setattr(module, 'METADATA_PENDING_LIMIT', 1)
    store = Store(tmp_path/'db', **generate_keys())
    gateway = Gateway(store)
    original = gateway.record
    entered, release = threading.Event(), threading.Event()
    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(gateway, 'record', delayed)
    try:
        args = (None, 'seiche', 'tools/list', 'synthetic', 200, 1)
        await gateway._record(*args)
        assert entered.is_set()
        await gateway._record(*args)
        current = gateway.diagnostics()
        assert current['late'] == 1 and current['pending'] == 1 and current['dropped_capacity'] == 1
        assert current['permanent_errors'] == 0 and current['committed'] == 0
        release.set()
        await asyncio.gather(*gateway._record_tasks)
        await asyncio.sleep(0)
        assert gateway.diagnostics()['late_committed'] == 1
        assert demand.report(store)['totals']['requests'] == 1
        def fail(*args, **kwargs): raise OSError('synthetic database failure')
        monkeypatch.setattr(gateway, 'record', fail)
        await gateway._record(*args)
        await asyncio.sleep(0)
        current = gateway.diagnostics()
        assert current['permanent_errors'] == 1 and current['pending'] == 0
        assert current['submitted'] == 2 and current['committed'] == 1
        assert current['durable_queue'] is False and gateway.measurement_failures == 3
    finally:
        release.set()
        await gateway.aclose()
        store.close()


def test_modern_prompt_and_resource_mirrors_follow_native_encoding(platform):
    import base64
    client, store, _, requests, reply, _ = platform
    reply['content'] = b'{"jsonrpc":"2.0","id":1,"result":{}}'
    headers = {'MCP-Protocol-Version':'2026-07-28','Mcp-Name':'untrusted-header'}
    for method, field, value in (
        ('prompts/get', 'name', 'bank_asset_quality_brief'),
        ('resources/read', 'uri', 'evidence://public/catalog'),
        ('resources/read', 'uri', ' evidence://caf\u00e9/\u79c1\u6709 '),
        ('prompts/get', 'name', '=?base64?looks-like-encoded'),
    ):
        result = client.post('/products/liquilens/mcp', headers=headers,
            json={'jsonrpc':'2.0','id':1,'method':method,'params':{field:value}})
        assert result.status_code == 200
        mirrored = requests[-1].headers['Mcp-Name']
        decoded = base64.b64decode(mirrored[9:-2]).decode() if mirrored.startswith('=?base64?') else mirrored
        assert decoded == value and requests[-1].headers['Mcp-Method'] == method
    with store.engine.connect() as db:
        assert 'evidence://' not in str(db.execute(select(demand.daily)).all())
    count = len(requests)
    for value in (None, '', 'x'*2049, '\ud800'):
        result = client.post('/products/liquilens/mcp', headers=headers,
            content=json.dumps({'jsonrpc':'2.0','id':1,'method':'resources/read','params':{'uri':value}}))
        assert result.status_code == 400
    assert len(requests) == count


def test_reviewed_hub_cors_is_scoped_to_public_data_routes(platform):
    client, _, owner, requests, _, _ = platform
    origin = 'https://beepboop2025.github.io'
    preflight = {'Origin':origin, 'Access-Control-Request-Method':'GET',
                 'Access-Control-Request-Headers':'x-financial-evidence-traffic-class,x-liquilens-traffic-class'}
    for path in ('/v1/data/seiche.money_markets', '/v1/research/api/v1/datasets', '/v1/data-catalog'):
        response = client.options(path, headers=preflight)
        assert response.status_code == 200 and response.headers['access-control-allow-origin'] == origin
        assert response.headers['access-control-allow-methods'] == 'GET'
        response = client.get(path, headers={'Origin':origin, 'X-Financial-Evidence-Traffic-Class':'synthetic'})
        assert response.status_code == 200 and response.headers['access-control-allow-origin'] == origin
        assert 'X-Source-URL' in response.headers['access-control-expose-headers']
        assert 'access-control-allow-credentials' not in response.headers
    count = len(requests)
    for path in ('/v1/me', '/v1/dataset-usage', '/v1/operator/dataset-usage', '/v1/social/me', '/products/unreviewed/mcp'):
        response = client.get(path, headers={'Origin':origin,'Authorization':'Bearer '+owner['owner_token']})
        assert response.status_code == 403 and 'access-control-allow-origin' not in response.headers
        assert client.options(path, headers=preflight).status_code == 403
    for bad in ('https://evil.example', 'https://beepboop2025.github.io.evil.example', 'null', 'http://beepboop2025.github.io'):
        assert client.options('/v1/data/seiche.money_markets', headers={**preflight,'Origin':bad}).status_code == 403
        assert client.get('/v1/data/seiche.money_markets', headers={'Origin':bad}).status_code == 403
    assert client.options('/v1/data/seiche.money_markets', headers={**preflight,'Access-Control-Request-Method':'POST'}).status_code == 400
    assert len(requests) == count
