import asyncio
import json
import threading
from datetime import datetime, timezone
import httpx
import pytest
from fastapi.testclient import TestClient
from seiche_agent_network.api import create_app
from seiche_agent_network.crypto import generate_keys
from seiche_agent_network.gateway import mount_gateway
from seiche_agent_network.store import Store


@pytest.fixture
def platform(tmp_path):
    store=Store(tmp_path/'db',**generate_keys())
    owners=[store.bootstrap_org(x) for x in ['One','Two']]
    app=create_app(store,purge_interval=0)
    requests=[]
    async def upstream(request):
        requests.append(request)
        body=json.loads(request.content) if request.content else {}
        if body.get('method')=='initialize':
            return httpx.Response(200,json={'jsonrpc':'2.0','id':1,'result':{}},headers={'Mcp-Session-Id':'private-upstream-session'})
        return httpx.Response(200,json={'jsonrpc':'2.0','id':2,'result':{'isError':True,'content':[{'type':'text','text':'private result'}]}})
    mount_gateway(app,store,transport=httpx.MockTransport(upstream))
    with TestClient(app) as client:yield client,store,owners,requests,app.state.gateway
    store.close()


def test_central_routes_strip_tokens_and_record_only_safe_fields(platform):
    client,store,owners,requests,gateway=platform
    token=owners[0]['owner_token'];headers={'Authorization':'Bearer '+token,'CF-IPCountry':'US'}
    result=client.post('/products/seiche/mcp',headers=headers,json={'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'funding_stress_now','arguments':{'private':'private strategy'}}})
    assert result.status_code==200
    assert 'authorization' not in requests[0].headers
    assert requests[0].headers['accept-encoding'] == 'identity'
    report=client.get('/v1/product-usage',headers=headers).json()
    assert report['totals']['requests']==1 and report['totals']['tool_errors']==1
    assert report['countries']==[{'country':'unknown','requests':1}]
    assert 'private strategy' not in json.dumps(report) and 'private result' not in json.dumps(report)
    other=client.get('/v1/product-usage',headers={'Authorization':'Bearer '+owners[1]['owner_token']}).json()
    assert other['totals']['requests']==0


def test_session_is_bound_to_product_and_credential(platform):
    client,store,owners,requests,gateway=platform
    first={'Authorization':'Bearer '+owners[0]['owner_token']}
    response=client.post('/products/seiche/mcp',headers=first,json={'jsonrpc':'2.0','id':1,'method':'initialize'})
    session=response.headers['mcp-session-id']
    assert session!='private-upstream-session'
    second={'Authorization':'Bearer '+owners[1]['owner_token'],'Mcp-Session-Id':session}
    assert client.get('/products/seiche/mcp',headers=second).status_code==404
    assert client.get('/products/undertow/mcp',headers={**first,'Mcp-Session-Id':session}).status_code==404
    assert client.post('/products/seiche/mcp',headers={**first,'Mcp-Session-Id':session},json={'jsonrpc':'2.0','id':2,'method':'ping'}).status_code==200
    assert requests[-1].headers['mcp-session-id']=='private-upstream-session'


def test_missing_auth_and_operator_token_never_downgrade(platform,monkeypatch):
    client,*_=platform
    monkeypatch.setenv('SEICHE_OPERATOR_REPORT_TOKEN','operator-secret-'*4)
    assert client.post('/products/seiche/mcp',headers={'Authorization':'Bearer invalid'},json={'jsonrpc':'2.0','method':'ping'}).status_code==401
    assert client.get('/v1/operator/usage').status_code==401
    report=client.get('/v1/operator/usage',headers={'Authorization':'Bearer '+'operator-secret-'*4})
    assert report.status_code==200 and report.json()['scope']=='operator_global'
    assert client.post('/products/not-a-product/mcp',json={}).status_code==404


def test_undertow_service_identity_only_reaches_public_calls(platform, upstream_credential):
    client, store, owners, requests, gateway = platform
    token, _ = upstream_credential()
    visitor = owners[0]['owner_token']
    def call(name, **kwargs):
        return client.post('/products/undertow/mcp', json={'jsonrpc':'2.0','id':2,'method':'tools/call',
            'params':{'name':name,'arguments':{}}}, **kwargs)
    assert call('liquidity_tiers', headers={'Authorization':'Bearer '+visitor}).status_code == 200
    assert requests[-1].headers['authorization'] == 'Bearer '+token
    assert visitor not in str(requests[-1].headers)
    assert call('agent_access_status').status_code == 200
    assert 'authorization' not in requests[-1].headers
    gateway.client.cookies.set('owner', 'privileged', domain='api.seiche.info', path='/')
    response = client.post('/products/undertow/mcp', json={'jsonrpc':'2.0','id':3,'method':'tools/list'})
    assert response.status_code == 200
    assert 'authorization' not in requests[-1].headers and 'cookie' not in requests[-1].headers
    response = client.post('/products/seiche/mcp', json={'jsonrpc':'2.0','id':3,'method':'tools/list'})
    assert response.status_code == 200 and 'cookie' not in requests[-1].headers
    assert 'authorization' not in requests[-1].headers
    count = len(requests)
    for tool in ['board_full', 'exit_desk_full', 'unknown_tool']:
        assert call(tool).status_code == 403
    assert len(requests) == count
    assert token not in json.dumps(gateway.report(None))


def test_undertow_credentials_rotate_and_expiry_does_not_downgrade(platform, upstream_credential):
    client, _, _, requests, gateway = platform
    now = int(gateway.clock())
    first, _ = upstream_credential(now=now)
    payload = {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'exit_cost'}}
    assert client.post('/products/undertow/mcp', json=payload).status_code == 200
    assert requests[-1].headers['authorization'] == 'Bearer '+first
    second, _ = upstream_credential(now=now, name='rotated')
    assert client.post('/products/undertow/mcp', json=payload).status_code == 200
    assert requests[-1].headers['authorization'] == 'Bearer '+second
    upstream_credential(now=now-3600)
    count = len(requests)
    failure = client.post('/products/undertow/mcp', json=payload)
    assert failure.status_code == 503 and len(requests) == count
    assert 'token' not in failure.text and second not in failure.text


def test_undertow_changed_session_contract_is_never_exposed(platform, upstream_credential):
    client, _, _, requests, gateway = platform
    upstream_credential()
    response = client.post('/products/undertow/mcp', json={'jsonrpc':'2.0','id':1,'method':'initialize'})
    assert response.status_code == 502 and 'mcp-session-id' not in response.headers
    assert not gateway.sessions and gateway.active == 0
    count = len(requests)
    response = client.post('/products/undertow/mcp', headers={'Mcp-Session-Id':'private-upstream-session'},
        json={'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'agent_access_status'}})
    assert response.status_code == 404 and len(requests) == count


def test_failed_measurement_does_not_fail_data_response(platform,monkeypatch):
    client,_,_,_,gateway=platform
    def fail(*args,**kwargs):raise OSError('database offline')
    monkeypatch.setattr(gateway,'record',fail)
    result=client.post('/products/seiche/mcp',json={'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'funding_stress_now'}})
    assert result.status_code==200 and gateway.measurement_failures==1 and gateway.active==0


def test_optional_iserror_uses_mcp_default_only_for_valid_tool_results(platform):
    _, _, _, _, gateway = platform
    for value in [{'content':[]}, {'structuredContent':{}}, {'content':[], 'isError':False}]:
        gateway.record(None, 'seiche', 'tools/call', 'unknown', 200, 1,
                       tool='funding_stress_now', result={'result':value})
    for value in [{'content':'malformed'}, {'content':[], 'isError':'false'}, {}]:
        gateway.record(None, 'seiche', 'tools/call', 'unknown', 200, 1,
                       tool='funding_stress_now', result={'result':value})
    gateway.record(None, 'seiche', 'initialize', 'unknown', 200, 1,
                   result={'result':{'content':[]}})
    totals = gateway.report(None)['totals']
    assert totals['tool_successes'] == 3 and totals['requests'] == 7


@pytest.mark.asyncio
async def test_cancelled_upstream_releases_ingress_slot(tmp_path):
    from starlette.requests import Request
    from seiche_agent_network.gateway import Gateway
    store=Store(tmp_path/'db',**generate_keys())
    async def cancelled(request):raise asyncio.CancelledError()
    gateway=Gateway(store,transport=httpx.MockTransport(cancelled))
    async def receive():return {'type':'http.request','body':b'{"jsonrpc":"2.0","method":"ping"}','more_body':False}
    request=Request({'type':'http','method':'POST','path':'/products/seiche/mcp','query_string':b'','headers':[],'server':('testserver',80),'scheme':'http'},receive)
    with pytest.raises(asyncio.CancelledError):await gateway.proxy('seiche',request)
    assert gateway.active==0
    await gateway.aclose();store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('route', ['mcp', 'dataset'])
@pytest.mark.parametrize('status', [401, 403, 429, 451])
async def test_access_refusal_closes_unread_body_and_preserves_failure_semantics(tmp_path, route, status):
    from starlette.datastructures import QueryParams
    from starlette.requests import Request
    from seiche_agent_network import data_catalog
    from seiche_agent_network.gateway import Gateway

    class RefusedBody(httpx.AsyncByteStream):
        read = False
        closed = False
        async def __aiter__(self):
            self.read = True
            raise AssertionError('access-refusal bytes must never be consumed')
            yield b''
        async def aclose(self):
            self.closed = True

    stream, requests = RefusedBody(), []
    async def upstream(request):
        requests.append(request)
        return httpx.Response(status, stream=stream, headers={
            'content-type': 'text/html', 'content-encoding': 'unsupported',
            'retry-after': '90', 'mcp-session-id': 'private-session',
            'set-cookie': 'private-cookie', 'www-authenticate': 'Bearer private-credential',
        })

    store = Store(tmp_path / 'db', **generate_keys())
    gateway = Gateway(store, transport=httpx.MockTransport(upstream))
    async def receive():
        return {'type': 'http.request', 'body': b'{"jsonrpc":"2.0","id":1,"method":"initialize"}', 'more_body': False}
    request = Request({'type': 'http', 'method': 'POST' if route == 'mcp' else 'GET',
                       'path': '/products/seiche/mcp' if route == 'mcp' else '/v1/data/seiche.money_markets',
                       'query_string': b'', 'headers': [], 'server': ('testserver', 80), 'scheme': 'http'}, receive)
    try:
        if route == 'mcp':
            response = await gateway.proxy('seiche', request)
        else:
            url, params, classification = data_catalog.fixed_request('seiche.money_markets', QueryParams())
            response = await gateway.fixed_response(request, url, params, classification)
        assert response.status_code == status and len(requests) == 1
        assert response.headers.get('retry-after') == ('90' if status == 429 else None)
        assert json.loads(response.body)['error']['upstream_status'] == status
        assert not stream.read and stream.closed and gateway.active == 0 and not gateway.sessions
        assert not {'authorization', 'cookie'} & set(requests[0].headers)
        assert 'private-' not in response.body.decode() + str(response.headers)
        assert gateway.report(None)['totals']['requests'] == 1
    finally:
        await gateway.aclose()
        store.close()


@pytest.mark.asyncio
async def test_compressed_response_rejected_before_reading_body(tmp_path):
    from starlette.requests import Request
    from seiche_agent_network.gateway import Gateway
    from seiche_agent_network.models import NetworkError
    class Compressed(httpx.AsyncByteStream):
        read = False
        closed = False
        async def __aiter__(self):
            self.read = True
            raise AssertionError('compressed bytes must never be consumed')
            yield b''
        async def aclose(self): self.closed = True
    stream = Compressed()
    async def upstream(request):
        assert request.headers['accept-encoding'] == 'identity'
        return httpx.Response(200, stream=stream, headers={'content-encoding':'gzip'})
    store = Store(tmp_path/'db', **generate_keys())
    gateway = Gateway(store, transport=httpx.MockTransport(upstream))
    async def receive(): return {'type':'http.request','body':b'{"jsonrpc":"2.0","method":"ping"}','more_body':False}
    request = Request({'type':'http','method':'POST','path':'/products/seiche/mcp','query_string':b'','headers':[],'server':('testserver',80),'scheme':'http'}, receive)
    try:
        with pytest.raises(NetworkError) as failure:
            await gateway.proxy('seiche', request)
        assert failure.value.status == 502
        assert not stream.read and stream.closed and gateway.active == 0
    finally:
        await gateway.aclose(); store.close()


@pytest.mark.asyncio
async def test_metadata_capacity_is_retained_after_deadline_and_cancel(tmp_path, monkeypatch):
    import seiche_agent_network.gateway as module
    from starlette.requests import Request
    monkeypatch.setattr(module, 'METADATA_WAIT_SECONDS', 0.01)
    store = Store(tmp_path/'db', **generate_keys())
    gateway = module.Gateway(store, transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    release = threading.Event()
    entered = []
    def blocked(*args, **kwargs):
        entered.append(1)
        release.wait(3)
    monkeypatch.setattr(gateway, 'record', blocked)
    try:
        waiting = asyncio.create_task(gateway._record())
        await asyncio.sleep(0.005)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError): await waiting
        assert len(gateway._record_tasks) == 1
        for _ in range(20): await gateway._record()
        assert len(gateway._record_tasks) == module.METADATA_PENDING_LIMIT
        assert len(entered) == module.METADATA_PENDING_LIMIT
        assert gateway.measurement_failures >= 13
        # Admission for product work does not depend on metadata write slots.
        async def receive(): return {'type':'http.request','body':b'{"jsonrpc":"2.0","method":"ping"}','more_body':False}
        request = Request({'type':'http','method':'POST','path':'/products/seiche/mcp','query_string':b'','headers':[],'server':('testserver',80),'scheme':'http'}, receive)
        response = await gateway.proxy('seiche', request)
        assert b''.join([part async for part in response.body_iterator]) == b'{}'
        assert gateway.active == 0 and not gateway._cleanup_tasks
        assert len(gateway._record_tasks) == module.METADATA_PENDING_LIMIT
    finally:
        release.set()
        await asyncio.gather(*gateway._record_tasks)
        await asyncio.sleep(0)
        assert not gateway._record_tasks
        await gateway.aclose(); store.close()


@pytest.mark.asyncio
async def test_close_deadline_releases_cleanup_capacity(tmp_path, monkeypatch):
    import seiche_agent_network.gateway as module
    monkeypatch.setattr(module, 'UPSTREAM_CLOSE_SECONDS', 0.01)
    store = Store(tmp_path/'db', **generate_keys())
    gateway = module.Gateway(store)
    stopped = asyncio.Event()
    class SlowClose:
        async def aclose(self):
            try: await asyncio.sleep(3)
            finally: stopped.set()
    try:
        await asyncio.wait_for(gateway._cleanup(SlowClose(),
            (None, 'seiche', 'ping', 'unknown', 200, 1), {}), 0.5)
        assert stopped.is_set() and not gateway._cleanup_tasks
        assert gateway.measurement_failures == 1
        assert gateway.report(None)['totals']['requests'] == 1
    finally:
        await gateway.aclose(); store.close()


def test_public_source_report_projects_fields_and_requires_aware_clock(platform, tmp_path, monkeypatch):
    client, *_ = platform
    path = tmp_path/'report.json'
    monkeypatch.setenv('SEICHE_FRESHNESS_REPORT', str(path))
    report = {'schema':'seiche-agent-network.freshness.v1',
        'evaluated_at':datetime.now(timezone.utc).isoformat(), 'private_notes':'not-public',
        'products':[{'product':'seiche','transport':'available','freshness':'unknown',
            'raw_payload':{'secret':'not-public'}, 'reasons':['future_observation_clock'],
            'critical_subset':{'definition':'seiche-us-funding-core-v1','freshness':'unknown',
                'clocks':[{'source_id':'fred:IORB','clock_role':'announced_forward_effective',
                    'current_observation_available':False,'available_at':'2026-10-11T12:00:00Z',
                    'credentials':'not-public'}]}}]}
    path.write_text(json.dumps(report))
    output = client.get('/v1/data-status').json()
    assert output['monitor_current'] is True and 'not-public' not in json.dumps(output)
    clock = output['products'][0]['critical_subset']['clocks'][0]
    assert clock['clock_role'] == 'announced_forward_effective'
    assert clock['current_observation_available'] is False
    for invalid in [None, True, 'NaT', '2026-10-11T12:00:00', 'invalid']:
        path.write_text(json.dumps({**report, 'evaluated_at':invalid}))
        output = client.get('/v1/data-status').json()
        assert output['status'] == 'invalid_report' and output['monitor_current'] is False
    path.write_text(json.dumps({**report, 'schema':'unknown'}))
    assert client.get('/v1/data-status').json()['status'] == 'invalid_report'


def test_oversized_json_is_rejected_before_upstream(platform):
    client, *_ = platform
    response = client.post('/products/seiche/mcp', content=b' '*65537, headers={'content-type':'application/json'})
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_raw_response_cap_closes_transport(tmp_path):
    from starlette.requests import Request
    from seiche_agent_network.gateway import Gateway
    class Large(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            for _ in range(5): yield b'x'*1048576
        async def aclose(self): self.closed = True
    stream = Large()
    async def upstream(request): return httpx.Response(200, stream=stream)
    store = Store(tmp_path/'db', **generate_keys())
    gateway = Gateway(store, transport=httpx.MockTransport(upstream))
    async def receive(): return {'type':'http.request','body':b'{"jsonrpc":"2.0","method":"ping"}','more_body':False}
    request = Request({'type':'http','method':'POST','path':'/products/seiche/mcp','query_string':b'','headers':[],'server':('testserver',80),'scheme':'http'}, receive)
    try:
        response = await gateway.proxy('seiche', request)
        received = 0
        with pytest.raises(RuntimeError, match='exceeded ingress limit'):
            async for chunk in response.body_iterator: received += len(chunk)
        assert received == 4194304 and stream.closed and gateway.active == 0
    finally:
        await gateway.aclose(); store.close()


def test_sse_outcome_matches_request_and_never_retains_body():
    from seiche_agent_network.gateway import _rpc_result
    body=b'event: message\r\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\r\n\r\ndata: {"jsonrpc":"2.0","id":9,"result":{"isError":true}}\r\n\r\n'
    assert _rpc_result(body,'text/event-stream',9)['result']['isError'] is True
    assert _rpc_result(body,'text/event-stream',8) is None
    assert _rpc_result(b'{"id":8,"result":{}}','application/json',9) is None


@pytest.mark.asyncio
async def test_disconnected_response_releases_slot_and_closes_upstream(tmp_path):
    from starlette.requests import Request
    from seiche_agent_network.gateway import Gateway
    class Stream(httpx.AsyncByteStream):
        closed=False
        async def __aiter__(self):yield b'{}'
        async def aclose(self):self.closed=True
    stream=Stream()
    async def upstream(request):return httpx.Response(200,stream=stream,headers={'content-type':'application/json'})
    store=Store(tmp_path/'db',**generate_keys());gateway=Gateway(store,transport=httpx.MockTransport(upstream))
    scope={'type':'http','method':'POST','path':'/products/seiche/mcp','query_string':b'','headers':[],'server':('testserver',80),'scheme':'http','asgi':{'spec_version':'2.4'}}
    async def receive():return {'type':'http.request','body':b'{"jsonrpc":"2.0","method":"ping","id":1}','more_body':False}
    response=await gateway.proxy('seiche',Request(scope,receive))
    async def send(message):raise OSError('client disconnected before response body')
    with pytest.raises(Exception):await response(scope,receive,send)
    assert gateway.active==0 and stream.closed
    assert gateway.report(None)['totals']['http_errors']==1
    await gateway.aclose();store.close()


@pytest.mark.parametrize("tool,arguments", [
    ("financial_evidence_funding_workflow", {"currency": "USD"}),
    ("financial_evidence_institutions_workflow", {"institution_ids": ["au-sfb"]}),
    ("financial_evidence_exit_workflow", {"sizes_usd": [10000, 100000]}),
])
def test_explicit_financial_workflows_reach_fixed_read_only_upstream(platform, tool, arguments):
    client, _, _, requests, _ = platform
    payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
               "params": {"name": tool, "arguments": arguments}}
    response = client.post("/products/financial-evidence/mcp", json=payload)
    assert response.status_code == 200
    assert str(requests[-1].url) == "https://api.seiche.info/openbb/mcp"
    assert json.loads(requests[-1].content)["params"] == payload["params"]
    assert "authorization" not in requests[-1].headers
    count = len(requests)
    payload["params"]["name"] = "execute_trade"
    assert client.post("/products/financial-evidence/mcp", json=payload).status_code == 403
    assert len(requests) == count
