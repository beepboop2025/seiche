"""Hosted MCP exercises the actual anonymous HTTP transport and publication scope."""
import json
from uuid import uuid4

from fastapi.testclient import TestClient

from seiche_agent_network.api import create_app
from seiche_agent_network.crypto import generate_keys
from seiche_agent_network.social import POLICY, PostCreate, ProfilePublish, Social
from seiche_agent_network.store import Store


def rpc(client, method, params=None, **kwargs):
    return client.post('/social/mcp', json={'jsonrpc':'2.0', 'id':1, 'method':method,
        'params':params or {}}, headers={'Accept':'application/json, text/event-stream'}, **kwargs)


def call(client, name, arguments=None):
    response = rpc(client, 'tools/call', {'name':name, 'arguments':arguments or {}})
    assert response.status_code == 200
    return response.json()['result']


def structured(result):
    assert not result.get('isError'), result
    return result.get('structuredContent') or json.loads(result['content'][0]['text'])


def test_hosted_mcp_has_only_public_read_tools_and_no_private_projection():
    store = Store(':memory:', **generate_keys())
    owner_record = store.bootstrap_org('PRIVATE_ORGANIZATION_SENTINEL')
    owner = store.authenticate(owner_record['owner_token'])
    agent_record = store.register_agent(owner, 'PRIVATE_AGENT_SENTINEL', public=True)
    actor = store.authenticate(agent_record['token'])
    social = Social(store)
    with TestClient(create_app(store)) as client:
        initialization = rpc(client, 'initialize', {'protocolVersion':'2025-06-18', 'capabilities':{},
            'clientInfo':{'name':'synthetic-qualification','version':'1'}})
        assert initialization.status_code == 200
        tools = rpc(client, 'tools/list').json()['result']['tools']
        assert {tool['name'] for tool in tools} == {
            'community_contract','community_profiles','community_profile','community_feed','community_post'}
        assert all(tool['annotations']['readOnlyHint'] for tool in tools)
        assert structured(call(client, 'community_profiles'))['profiles'] == []
        grant = social.publish(owner, actor.agent_id, ProfilePublish(display_name='Public research agent',
            bio='Investigates sources.',role='finance_research',topics=['funding'],traffic_class='real',
            policy_version=POLICY,acknowledged=True))['grant_id']
        record = social.create_post(actor, PostCreate(event_id=str(uuid4()),grant_id=grant,
            topic='funding',kind='question',text='Which source covers this observation?'))['post']
        profiles = structured(call(client, 'community_profiles'))['profiles']
        assert len(profiles) == 1
        assert not {'grant_id','org_id','capabilities','token'} & profiles[0].keys()
        assert 'PRIVATE_' not in json.dumps(profiles)
        feed = structured(call(client, 'community_feed', {'topic':'funding'}))['posts']
        assert [row['id'] for row in feed] == [record['id']]
        assert structured(call(client,'community_post',{'post_id':record['id']}))['id'] == record['id']
        social.withdraw(owner, actor.agent_id)
        assert structured(call(client,'community_feed'))['posts'] == []
        assert call(client,'community_post',{'post_id':record['id']})['isError']
    store.close()


def test_hosted_mcp_rejects_unknown_arguments_writes_and_sent_credentials():
    store = Store(':memory:', **generate_keys())
    with TestClient(create_app(store)) as client:
        assert call(client,'community_profiles',{'limit':51})['isError']
        assert call(client,'community_profiles',{'limit':True})['isError']
        result = call(client,'community_feed',{'private_prompt':'NEVER_ECHO_THIS_SENTINEL'})
        assert result['isError'] and 'NEVER_ECHO_THIS_SENTINEL' not in json.dumps(result)
        assert call(client,'network_social_create_post',{})['isError']
        response = client.post('/social/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/list'},
            headers={'Accept':'application/json, text/event-stream','Authorization':'Bearer NEVER_ECHO_THIS_SENTINEL'})
        assert response.status_code == 401 and 'NEVER_ECHO_THIS_SENTINEL' not in response.text
        response = client.post('/social/mcp',content='x'*65537,
            headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
        assert response.status_code == 413
        config = client.get('/community/mcp-config.json').json()
        assert config['mcpServers']['seiche-community']['url'].endswith('/social/mcp')
        workspace = client.get('/community/workspace-mcp-config.json').json()['mcpServers']['seiche-workspace']
        assert workspace['command'] == 'uvx'
        assert workspace['env'] == {'SEICHE_NETWORK_TOKEN': 'YOUR_AGENT_KEY'}
        assert workspace['args'][-1] == 'http://127.0.0.1:8765'
        assert 'financial-evidence[workspace] @ git+https://github.com/beepboop2025/financial-evidence-skills.git@27d7ba5991ac57bb538770c9984c4b1d48ba790f' in workspace['args']
        assert len(client.get('/mcp-config.json').json()['mcpServers']) == 14
    store.close()
