"""Central OpenBB resolution and bounded NoiseFloor caller-input contracts."""
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from seiche_agent_network import analytics, demand, hosted_surfaces as surfaces
from test_dataset_proxy import platform


BASE = "http://127.0.0.1:8765"


def set_json(reply, value, status=200):
    reply.update(status=status, content=json.dumps(value).encode(), headers={"content-type": "application/json"})


def schema():
    return {"openapi": "3.1.0", "info": {"title": "Research", "version": "1"},
            "servers": [{"url": "/openbb"}], "paths": {
                "/api/v1/query": {"get": {"summary": "Read <script>unsafe</script>", "parameters": [
                    {"name": "dataset", "in": "query", "required": True, "schema": {"type": "string", "enum": ["money_markets", "bank_risk"]}},
                    {"name": "entity", "in": "query", "schema": {"type": "string", "default": ""}},
                    {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 200}}]}},
                "/api/v1/funding-review.csv": {"get": {"summary": "Download funding"}},
                "/api/v1/applications/current": {"delete": {"summary": "Private"}},
                "/api/v1/packet": {"get": {"summary": "Not admitted"}},
            }}


def test_complete_openbb_manifest_widget_and_document_urls(platform):
    client, store, _, requests, reply, _ = platform
    set_json(reply, {"docs": surfaces.RESEARCH_ORIGIN + "/docs", "workspace_backend": surfaces.RESEARCH_ORIGIN,
                     "source_url": "https://source.example/evidence", "release": {"release_id": "native-identity"}})
    for suffix in ("", "/"):
        root = client.get("/v1/research" + suffix)
        assert root.status_code == 200 and not root.history
        assert root.json()["workspace_backend"] == BASE + "/v1/research"
        assert root.json()["mcp"] == BASE + "/products/financial-evidence/mcp"
        assert root.json()["source_url"] == "https://source.example/evidence"
        assert root.json()["release"]["release_id"] == "native-identity"
    set_json(reply, [{"img": surfaces.RESEARCH_ORIGIN + "/thumbnail.svg", "tabs": {"funding": {"id": "funding"}},
                      "mcp_servers": [{"name": "Financial Evidence Workspace", "url": surfaces.RESEARCH_ORIGIN + "/mcp"}]}])
    app = client.get("/v1/research/apps.json").json()[0]
    assert app["img"] == BASE + "/v1/research/thumbnail.svg"
    assert app["mcp_servers"][0]["url"] == BASE + "/products/financial-evidence/mcp"
    assert app["tabs"] == {"funding": {"id": "funding"}}
    definitions = {"guide": {"endpoint": "guide"}, "table": {"endpoint": "api/v1/query", "params": [
        {"paramName": "dataset", "value": "money_markets"}, {"paramName": "entity", "value": ""},
        {"paramName": "limit", "value": 200}, {"paramName": "offset", "value": 0}]},
        "funding": {"endpoint": "api/v1/funding-review", "params": []}}
    set_json(reply, definitions)
    widgets = client.get("/v1/research/widgets.json").json()
    assert widgets == definitions
    for item in widgets.values():
        if item["endpoint"] == "guide":
            reply.update(content=b"Scope and original clocks", headers={"content-type": "text/plain"})
        else:
            set_json(reply, {"results": [], "source_url": "https://source.example/evidence"})
        response = client.get("/v1/research/" + item["endpoint"], params={p["paramName"]: p["value"] for p in item.get("params", [])})
        assert response.status_code == 200
        assert str(requests[-1].url).startswith(surfaces.RESEARCH_ORIGIN + "/" + item["endpoint"])
    assert demand.report(store)["totals"]["discovery_requests"] == 5


def test_schema_and_interactive_docs_stay_on_central_surface_with_strict_csp(platform):
    client, _, _, requests, reply, _ = platform
    set_json(reply, schema())
    spec = client.get("/v1/research/openapi.json").json()
    assert spec["servers"] == [{"url": BASE + "/v1/research"}]
    assert set(spec["paths"]) == {"/api/v1/query", "/api/v1/funding-review.csv"}
    response = client.get("/v1/research/docs")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/html")
    assert 'action="' + BASE + '/v1/research/api/v1/query"' in response.text
    assert '<select name="dataset">' in response.text
    assert '<script' not in response.text and '&lt;script&gt;unsafe' in response.text
    assert 'script-src \'self\'' in response.headers['content-security-policy']
    assert str(requests[-1].url) == surfaces.RESEARCH_ORIGIN + "/openapi.json"


def test_startup_task_links_use_central_api_and_preserve_browser_and_source_urls(platform):
    client, _, _, _, reply, _ = platform
    value = {'schema': 'financial-evidence.start.v1', 'api_base': surfaces.RESEARCH_ORIGIN,
             'mcp_url': surfaces.RESEARCH_ORIGIN + '/mcp',
             'guide_url': 'https://beepboop2025.github.io/financial-evidence-skills/connect/',
             'tasks': [{'id': 'exit', 'api_url': surfaces.RESEARCH_ORIGIN + '/api/v1/workflow?workflow=exit&selection=10000%2C100000',
                        'browser_url': 'https://beepboop2025.github.io/financial-evidence-skills/start/workflows.html?workflow=exit',
                        'source_url': 'https://source.example/original'}]}
    set_json(reply, value)
    result = client.get('/v1/research/api/v1/start')
    assert result.status_code == 200
    result = result.json()
    assert result['api_base'] == BASE + '/v1/research' and result['mcp_url'] == BASE + '/products/financial-evidence/mcp'
    assert result['tasks'][0]['api_url'] == BASE + '/v1/research/api/v1/workflow?workflow=exit&selection=10000%2C100000'
    assert result['guide_url'] == value['guide_url']
    assert result['tasks'][0]['browser_url'] == value['tasks'][0]['browser_url']
    assert result['tasks'][0]['source_url'] == value['tasks'][0]['source_url']
    value['tasks'][0]['api_url'] = surfaces.RESEARCH_ORIGIN + '/api/v1/applications/current'
    set_json(reply, value)
    assert client.get('/v1/research/api/v1/start').status_code == 502


def test_docs_resolve_dataset_choices_and_require_the_central_workflow_selector(platform):
    client, _, _, _, reply, _ = platform
    value = schema()
    value['components'] = {'schemas': {'Dataset': {'type': 'string', 'enum': ['money_markets', 'bank_risk']}}}
    value['paths']['/api/v1/query']['get']['parameters'][0]['schema'] = {'$ref': '#/components/schemas/Dataset'}
    value['paths']['/api/v1/workflow'] = {'get': {'servers': [{'url': surfaces.RESEARCH_ORIGIN}], 'parameters': [
        {'name': 'workflow', 'in': 'query', 'required': True, 'schema': {'type': 'string'}},
        {'name': 'selection', 'in': 'query', 'required': False, 'schema': {'type': 'string', 'default': ''}}]}}
    set_json(reply, value)
    spec = client.get('/v1/research/openapi.json').json()
    workflow = spec['paths']['/api/v1/workflow']['get']
    assert 'servers' not in workflow
    assert workflow['parameters'][1]['required'] is True
    document = client.get('/v1/research/docs').text
    assert '<select name="dataset"><option>money_markets</option>' in document
    assert '<select name="workflow"><option>funding</option>' in document
    assert '<input name="selection" value="USD" required>' in document


def test_unreviewed_active_svg_and_widget_endpoint_are_rejected(platform):
    client, _, _, _, reply, _ = platform
    reply.update(content=Path(surfaces.__file__).with_name('static').joinpath('research-thumbnail.svg').read_bytes(),
                 headers={"content-type": "image/svg+xml"})
    assert client.get('/v1/research/thumbnail.svg').content == reply['content']
    reply['content'] = b'<svg><script>alert(1)</script></svg>'
    assert client.get('/v1/research/thumbnail.svg').status_code == 502
    set_json(reply, {'widget': {'endpoint': 'https://unexpected.example/private'}})
    assert client.get('/v1/research/widgets.json').status_code == 502


def test_csv_preserves_bytes_metadata_and_stale_status(platform):
    client, _, _, _, reply, _ = platform
    raw = b'metric,value,review_asof\r\nsofr,4.1,2026-10-12\r\n'
    headers = {name: "native-value" for name in surfaces.CSV_HEADERS}
    headers.update({'content-type': 'text/csv; charset=utf-8', 'etag': '"' + hashlib.sha256(raw).hexdigest() + '"',
                    'set-cookie': 'private=never-forward', 'x-private': 'never-forward'})
    reply.update(content=raw, headers=headers)
    response = client.get('/v1/research/api/v1/funding-review.csv')
    assert response.status_code == 200 and response.content == raw
    for name in surfaces.CSV_HEADERS:
        assert response.headers[name] == headers[name]
    assert 'set-cookie' not in response.headers and 'x-private' not in response.headers
    set_json(reply, {'status': 'capture_unavailable_or_stale', 'review': surfaces.RESEARCH_ORIGIN + '/api/v1/funding-review'}, 503)
    response = client.get('/v1/research/api/v1/funding-review.csv')
    assert response.status_code == 503 and response.json()['review'] == BASE + '/v1/research/api/v1/funding-review'
    set_json(reply, {'results': []})
    assert client.get('/v1/research/api/v1/funding-review.csv').status_code == 502


def test_noise_discovery_retains_native_identity_and_has_central_transport(platform):
    client, store, _, _, reply, _ = platform
    set_json(reply, {'name': 'noisefloor', 'version': '0.4.0', 'implementation_sha256': 'a' * 64})
    assert client.get('/v1/noisefloor/healthz').json()['implementation_sha256'] == 'a' * 64
    set_json(reply, {'openapi': '/openapi.json', 'rest': {'/v1/market/assess': 'market_assessment'},
                     'posture': {'persists_submitted_data': False, 'fetches_sources': False}})
    response = client.get('/v1/noisefloor/v1/capabilities').json()
    assert response['openapi'] == BASE + '/v1/noisefloor/openapi.json'
    assert set(response['rest']) == {BASE + '/v1/noisefloor/' + name for name in surfaces.NOISEFLOOR_POST}
    assert response['posture']['persists_submitted_data'] is False
    set_json(reply, surfaces.noisefloor_schema())
    response = client.get('/v1/noisefloor/openapi.json').json()
    assert response['servers'][0]['url'] == BASE + '/v1/noisefloor'
    assert response['paths'] == surfaces.noisefloor_schema()['paths']
    assert demand.report(store)['totals']['discovery_requests'] == 3


def test_noise_success_requires_object_response_and_never_follows_redirects(platform):
    client, store, _, requests, reply, gateway = platform
    set_json(reply, [])
    assert client.post('/v1/noisefloor/v1/research/dyson', json={}).status_code == 502
    reply.update(status=302, content=b'{}', headers={'content-type': 'application/json', 'location': 'https://private.example'})
    assert client.post('/v1/noisefloor/v1/research/dyson', json={}).status_code == 502
    assert len(requests) == 2 and gateway.noise_active == 0
    assert demand.report(store)['totals']['http_errors'] == 2


def test_large_valid_noise_panel_is_forwarded_without_credentials_or_retention(platform):
    client, store, owner, requests, reply, gateway = platform
    panel = {'series': [{'id': 'private-panel', 'kind': 'rate', 'unit': 'percent',
                        'source': {'id': 'private-source', 'url': 'https://private-source.example/raw', 'rights': 'permitted'},
                        'max_age_seconds': 86400, 'observations': [{'observed_at': '2026-10-12T00:00:00Z', 'value': 1.2}] * 1500}],
             'as_of': '2026-10-13T00:00:00Z', 'policy': {'window_points': 20}}
    raw = json.dumps(panel).encode()
    assert 65536 < len(raw) < surfaces.NOISEFLOOR_MAX_BODY
    native = b' {"status":"unavailable","diagnostic":"private-response"}\n'
    reply['content'] = native
    gateway.client.cookies.set('shared', 'never-forward', domain='api.seiche.info')
    response = client.post('/v1/noisefloor/v1/spectral/assess', content=raw,
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + owner['owner_token'],
                 'Cookie': 'caller=secret', 'X-Financial-Evidence-Traffic-Class': 'synthetic'})
    assert response.status_code == 200 and response.content == native
    sent = requests[-1]
    assert sent.content == raw and str(sent.url) == surfaces.NOISEFLOOR_ORIGIN + '/v1/spectral/assess'
    assert int(sent.headers['content-length']) == len(raw)
    assert 'authorization' not in sent.headers and 'cookie' not in sent.headers and 'transfer-encoding' not in sent.headers
    assert sent.headers['x-financial-evidence-traffic-class'] == 'synthetic'
    assert sent.headers['x-liquilens-traffic-class'] == 'synthetic'
    assert response.headers['x-dataset-id'] == 'noisefloor.statistical_methods'
    report = client.get('/v1/dataset-usage', headers={'Authorization': 'Bearer ' + owner['owner_token']}).json()
    assert report['totals']['owner_requests'] == 1
    with store.engine.connect() as db:
        retained = str(db.execute(select(demand.daily)).all()) + str(db.execute(select(analytics.daily)).all())
    assert 'private-panel' not in retained and 'private-source' not in retained and 'private-response' not in retained
    assert 'spectral_assessment' in retained and 'synthetic' in retained
    assert gateway.noise_active == 0


@pytest.mark.parametrize('operation,body', [
    ('v1/market/assess', {'series': [], 'as_of': '2026-10-13T00:00:00Z'}),
    ('v1/narrative/triage', {'events': [], 'as_of': '2026-10-13T00:00:00+00:00'}),
    ('v1/spectral/assess', {'series': [], 'as_of': '2026-10-13T00:00:00.123456Z'}),
    ('v1/research/dyson', {'dimension': 2, 'steps': 1, 'dt': .001, 'seed': 4294967295}),
])
def test_all_reviewed_noise_posts_and_native_validation_outcomes(platform, operation, body):
    client, store, _, requests, reply, _ = platform
    set_json(reply, {'error': 'native unavailable'}, 422)
    response = client.post('/v1/noisefloor/' + operation, json=body)
    assert response.status_code == 422 and response.content == reply['content']
    assert str(requests[-1].url) == surfaces.NOISEFLOOR_ORIGIN + '/' + operation
    assert demand.report(store)['totals']['http_errors'] == 1


@pytest.mark.parametrize('raw,status', [
    (b'{"dimension":2,"dimension":4}', 400), (b'{"dt":NaN}', 400), (b'{"dt":1e999}', 400),
    (b'{"dimension":true}', 422), (b'{"dimension":17}', 422), (b'{"steps":101}', 422),
    (b'{"seed":4294967296}', 422), (b'{"unknown_private_field":1}', 422), (b'[]', 422), (b'', 400),
])
def test_noise_rejects_strict_json_and_schema_before_forwarding(platform, raw, status):
    client, _, _, requests, _, _ = platform
    response = client.post('/v1/noisefloor/v1/research/dyson', content=raw, headers={'Content-Type': 'application/json'})
    assert response.status_code == status and not requests
    assert 'unknown_private_field' not in response.text


def test_noise_size_media_query_paths_auth_and_capacity_boundaries(platform):
    client, _, _, requests, _, gateway = platform
    path = '/v1/noisefloor/v1/research/dyson'
    assert client.post(path, content=b'x' * (surfaces.NOISEFLOOR_MAX_BODY + 1), headers={'Content-Type': 'application/json'}).status_code == 413
    assert client.post('/v1/organizations', content=b'x' * 65537).status_code == 413
    assert client.post(path, content=b'{}', headers={'Content-Type': 'text/plain'}).status_code == 415
    assert client.post(path + '?url=https://private.example', json={}).status_code == 400
    assert client.post(path + '?x=1&x=2', json={}).status_code == 400
    assert client.get('/v1/research/apps.json?target=private').status_code == 400
    assert client.get('/v1/research/api/v1/funding-review.csv?entity=private').status_code == 400
    assert client.post('/v1/noisefloor/v1/unknown', json={}).status_code == 404
    assert client.post(path, json={}, headers={'Authorization': 'Bearer wrong'}).status_code == 401
    assert client.post(path, json={}, headers={'Content-Encoding': 'gzip'}).status_code == 415
    assert client.get(path).status_code == 405
    assert client.post('/v1/noisefloor/healthz', json={}).status_code == 405
    gateway.noise_active = 4
    assert client.post(path, json={}).status_code == 429
    gateway.noise_active = 0
    assert not requests


def test_noise_exact_body_limit_and_only_reviewed_post_gets_larger_allowance(platform):
    client, _, _, requests, _, _ = platform
    raw = b'{}' + b' ' * (surfaces.NOISEFLOOR_MAX_BODY - 2)
    response = client.post('/v1/noisefloor/v1/research/dyson', content=raw, headers={'Content-Type': 'application/json'})
    assert response.status_code == 200 and requests[-1].content == raw
    count = len(requests)
    response = client.post('/v1/noisefloor/v1/unknown', content=raw, headers={'Content-Type': 'application/json'})
    assert response.status_code == 413 and len(requests) == count


@pytest.mark.parametrize('operation,body', [
    ('v1/market/assess', {'series': [{}] * 33, 'as_of': '2026-10-13T00:00:00Z'}),
    ('v1/narrative/triage', {'events': [{}] * 501, 'as_of': '2026-10-13T00:00:00Z'}),
    ('v1/spectral/assess', {'series': [], 'as_of': '2026-10-13T00:00:00+01:00'}),
    ('v1/spectral/assess', {'series': [], 'as_of': '2026-10-13T00:00:00.1234567Z'}),
    ('v1/spectral/assess', {'series': [], 'as_of': '2026-10-13T00:00:00Z', 'policy': {'max_windows': 9}}),
    ('v1/spectral/assess', {'series': [], 'as_of': '2026-10-13T00:00:00Z', 'policy': {'step_points': True}}),
])
def test_noise_panel_and_clock_limits_are_checked_before_forwarding(platform, operation, body):
    client, _, _, requests, _, _ = platform
    assert client.post('/v1/noisefloor/' + operation, json=body).status_code == 422
    assert not requests


@pytest.mark.parametrize('origin', sorted(surfaces.PUBLIC_ORIGINS))
def test_reviewed_cors_allows_widgets_noise_and_workspace_mcp_only(platform, origin):
    client, _, owner, _, reply, _ = platform
    for path, method, headers in [('/v1/research/widgets.json', 'GET', 'authorization'),
                                  ('/v1/research/api/v1/funding-review.csv', 'GET', 'authorization'),
                                  ('/v1/noisefloor/v1/spectral/assess', 'POST', 'content-type,x-financial-evidence-traffic-class'),
                                  ('/products/financial-evidence/mcp', 'POST', 'content-type,mcp-protocol-version,mcp-session-id')]:
        response = client.options(path, headers={'Origin': origin, 'Access-Control-Request-Method': method,
                                                'Access-Control-Request-Headers': headers})
        assert response.status_code == 200 and response.headers['access-control-allow-origin'] == origin
    for path, method in [('/v1/operator/dataset-usage', 'GET'), ('/v1/agents', 'POST'),
                         ('/v1/noisefloor/v1/unknown', 'POST'), ('/v1/research/widgets.json', 'POST')]:
        response = client.options(path, headers={'Origin': origin, 'Access-Control-Request-Method': method})
        assert response.status_code == 403
    assert client.get('/v1/dataset-usage', headers={'Origin': origin, 'Authorization': 'Bearer ' + owner['owner_token']}).status_code == 403
    set_json(reply, {'status': 'ok'})
    response = client.post('/v1/noisefloor/v1/research/dyson', json={}, headers={'Origin': origin})
    assert response.headers['access-control-allow-origin'] == origin
    assert 'x-financial-evidence-capture' in response.headers['access-control-expose-headers'].lower()


def test_hosted_surface_contract_is_discoverable_without_payload_permissions(platform):
    response = platform[0].get('/v1/data-catalog').json()['hosted_surfaces']
    assert response['version'] == 'central-surfaces-2026-10-13'
    assert len(response['noisefloor_routes']) == 7
    assert response['submitted_payloads_retained'] is False


def test_every_registered_public_product_mcp_has_scoped_browser_cors(platform):
    client, _, owner, _, _, _ = platform
    assert len(surfaces.PUBLIC_MCP_PATHS) == 14
    for path in surfaces.PUBLIC_MCP_PATHS:
        response = client.options(path, headers={'Origin': 'https://liquilens.in', 'Access-Control-Request-Method': 'POST',
                                                'Access-Control-Request-Headers': 'content-type,authorization,mcp-protocol-version'})
        assert response.status_code == 200
        assert response.headers['access-control-allow-origin'] == 'https://liquilens.in'
        assert response.headers['access-control-allow-methods'] == 'GET, POST, DELETE'
    for path in ('/products/unreviewed/mcp', '/products/liquilens/mcp/private', '/v1/me', '/v1/operator/dataset-usage'):
        response = client.options(path, headers={'Origin': 'https://liquilens.in', 'Access-Control-Request-Method': 'POST'})
        assert response.status_code == 403


@pytest.mark.parametrize('primary,legacy,expected', [(None, 'synthetic', 'synthetic'), ('paper', 'synthetic', 'paper'),
                                                   ('private-canary', 'synthetic', 'unknown')])
def test_legacy_traffic_alias_fallback_and_primary_precedence_are_normalized(platform, primary, legacy, expected):
    client, store, _, requests, reply, _ = platform
    headers = {'X-Liquilens-Traffic-Class': legacy}
    if primary is not None:
        headers['X-Financial-Evidence-Traffic-Class'] = primary
    assert client.post('/v1/noisefloor/v1/research/dyson', json={}, headers=headers).status_code == 200
    for name in ('x-liquilens-traffic-class', 'x-financial-evidence-traffic-class'):
        assert requests[-1].headers[name] == expected
    set_json(reply, {'jsonrpc': '2.0', 'id': 1, 'result': {}})
    assert client.post('/products/financial-evidence/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'}, headers=headers).status_code == 200
    for name in ('x-liquilens-traffic-class', 'x-financial-evidence-traffic-class'):
        assert requests[-1].headers[name] == expected
    assert 'private-canary' not in json.dumps(demand.report(store))
