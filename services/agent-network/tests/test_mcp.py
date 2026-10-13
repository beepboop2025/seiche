"""Official MCP SDK behaviour, authority parity and a real stdio handshake."""
import json
import os
from pathlib import Path
import sys

from fastapi.testclient import TestClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp.exceptions import ToolError
import pytest

from seiche_agent_network.api import create_app
from seiche_agent_network.cli import private_output
from seiche_agent_network.crypto import generate_keys
from seiche_agent_network.mcp import create_mcp
from seiche_agent_network.store import Store


@pytest.fixture
def state(tmp_path):
    keys = generate_keys()
    db = tmp_path / "network.db"
    store = Store(db, **keys)
    owner = store.bootstrap_org("SDK owner")
    p = store.authenticate(owner["owner_token"])
    agent = store.register_agent(p, "SDK agent", public=True)
    yield store, owner, p, agent, db, keys
    store.close()


def unpack(result):
    if isinstance(result, tuple):
        return result[1]["result"]
    return json.loads(result[0].text)


async def test_sdk_tool_inventory_and_http_identity_parity(state):
    store, owner, p, agent, _, _ = state
    server = create_mcp(store, owner["owner_token"])
    inventory = await server.list_tools()
    names = {tool.name for tool in inventory}
    assert len(names) == 41
    assert {"network_send", "network_inbox", "network_cancel_task", "network_verify_room", "network_rotate_agent", "network_metrics"} <= names
    with TestClient(create_app(store)) as client:
        expected = client.get("/v1/me", headers={"Authorization": "Bearer " + owner["owner_token"]}).json()
        assert unpack(await server.call_tool("network_identity", {})) == expected
        actual = unpack(await server.call_tool("network_owned_agents", {}))
        assert actual == client.get("/v1/agents", headers={"Authorization": "Bearer " + owner["owner_token"]}).json()
    status_schema = next(tool for tool in inventory if tool.name == "network_agent_status").inputSchema
    assert status_schema["properties"]["update"]


async def test_mcp_revocation_and_owner_boundaries_are_live(state):
    store, owner, p, agent, _, _ = state
    server = create_mcp(store, agent["token"])
    with pytest.raises(ToolError, match="forbidden"):
        await server.call_tool("network_register_agent", {"agent": {"label": "Not an owner"}})
    assert unpack(await server.call_tool("network_identity", {}))["role"] == "agent"
    store.rotate_agent_token(p, agent["agent"]["id"])
    with pytest.raises(ToolError, match="unauthorized"):
        await server.call_tool("network_identity", {})


async def test_mcp_invalid_arguments_are_bounded_and_redacted(state):
    store, owner, _, _, _, _ = state
    server = create_mcp(store, owner["owner_token"])
    secret = "do-not-echo-private-input"
    with pytest.raises(ToolError) as err:
        await server.call_tool("network_register_agent", {"agent": {"label": "test", "public": secret}})
    assert secret not in str(err.value) and "invalid_request" in str(err.value)
    with pytest.raises(ToolError, match="invalid_request"):
        await server.call_tool("network_identity", {"unexpected": secret})
    with pytest.raises(ToolError, match="invalid_request"):
        await server.call_tool("network_inbox", {"after": True})
    with pytest.raises(ToolError, match="invalid_request"):
        await server.call_tool("network_directory", {"query": "x" * 70000})


async def test_real_sdk_stdio_initialize_list_and_call(state, tmp_path):
    store, owner, _, _, db, keys = state
    key_path, credential_path = tmp_path / "keys.json", tmp_path / "credential.json"
    with private_output(key_path) as handle:
        json.dump(keys, handle)
    with private_output(credential_path) as handle:
        json.dump({"owner_token": owner["owner_token"]}, handle)
    src = str(Path(__file__).resolve().parents[1] / "src")
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "seiche_agent_network.cli", "--database", str(db), "--keys-file", str(key_path),
              "mcp", "--credential-file", str(credential_path)], env={"PYTHONPATH": src})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "Seiche Agent Network"
            inventory = await session.list_tools()
            assert len(inventory.tools) == 41
            result = await session.call_tool("network_identity", {})
            assert result.isError is False
            assert json.loads(result.content[0].text)["org_id"] == store.authenticate(owner["owner_token"]).org_id
            failure = await session.call_tool("network_inbox", {"after": -1})
            assert failure.isError is True


async def test_task_reports_require_live_owner_grant_and_retries_count_once(state):
    from uuid import uuid4
    from seiche_agent_network.participation import POLICY, Participation, ParticipationRequest
    store, owner, p, agent, _, _ = state
    sharing = Participation(store)
    server = create_mcp(store, agent["token"])
    assert unpack(await server.call_tool("network_participation_status", {}))["enabled"] is False
    contract = unpack(await server.call_tool("network_task_signal_contract", {}))
    assert contract["policy_version"] == POLICY
    grant = sharing.configure(p, ParticipationRequest(enabled=True, policy_version=POLICY, data_notice_acknowledged=True))
    signal = {"event_id": str(uuid4()), "grant_id": grant["grant_id"], "goal": "compare",
              "topic": "ai_economy", "stage": "comparing", "traffic_class": "synthetic"}
    first = unpack(await server.call_tool("network_report_task_signal", {"signal": signal}))
    assert first["status"] == "accepted" and first["evidence_kind"] == "self_reported"
    assert unpack(await server.call_tool("network_report_task_signal", {"signal": signal}))["status"] == "duplicate"
    assert unpack(await server.call_tool("network_task_signal_summary", {}))["total_events"] == 1
    sharing.withdraw(p)
    with pytest.raises(ToolError, match="participation_disabled"):
        await server.call_tool("network_report_task_signal", {"signal": signal})
    assert unpack(await server.call_tool("network_task_signal_summary", {}))["total_events"] == 0


async def test_task_report_tools_cannot_expand_permissions_or_echo_private_fields(state):
    store, owner, p, agent, _, _ = state
    server = create_mcp(store, agent["token"])
    names = {tool.name for tool in await server.list_tools()}
    assert not any("enable" in name or "configure_participation" in name for name in names)
    private = "private-agent-prompt"
    with pytest.raises(ToolError) as err:
        await server.call_tool("network_report_task_signal", {"signal": {"prompt": private}})
    assert private not in str(err.value)
    store.rotate_agent_token(p, agent["agent"]["id"])
    with pytest.raises(ToolError, match="unauthorized"):
        await server.call_tool("network_participation_status", {})


async def test_social_mcp_publication_grant_retries_threads_and_withdrawal(state):
    from uuid import uuid4
    from seiche_agent_network.social import Social, ProfilePublish
    store, owner, p, agent, _, _ = state
    social = Social(store)
    server = create_mcp(store, agent["token"])
    terms = unpack(await server.call_tool("network_social_contract", {}))
    publication = ProfilePublish(display_name="Synthetic community agent", bio="Test public research",
        role="finance_research", topics=["funding"], website_url=None, traffic_class="synthetic",
        policy_version=terms["policy_version"], acknowledged=True)
    profile = social.publish(p, agent["agent"]["id"], publication)
    post = {"event_id": str(uuid4()), "grant_id": profile["grant_id"], "topic": "funding",
            "kind": "research_note", "text": "Synthetic public research", "source_urls": ["https://example.org/evidence"]}
    first = unpack(await server.call_tool("network_social_create_post", {"post": post}))
    assert first["status"] == "accepted"
    assert first["post"]["evidence_kind"] == "agent_authored"
    assert unpack(await server.call_tool("network_social_create_post", {"post": post}))["status"] == "duplicate"
    public_profile = unpack(await server.call_tool("network_social_profile", {"agent_id": agent["agent"]["id"]}))
    assert "grant_id" not in public_profile
    assert "grant_id" not in first["post"]["author"]
    feed = unpack(await server.call_tool("network_social_feed", {"include_synthetic": True}))
    assert [item["id"] for item in feed["posts"]] == [first["post"]["id"]]
    reply = {"event_id": str(uuid4()), "grant_id": profile["grant_id"], "topic": "funding",
             "kind": "reply", "text": "Synthetic question", "parent_id": first["post"]["id"], "source_urls": []}
    answered = unpack(await server.call_tool("network_social_create_post", {"post": reply}))
    thread = unpack(await server.call_tool("network_social_feed", {
        "parent_id": first["post"]["id"], "include_synthetic": True}))
    assert [item["id"] for item in thread["posts"]] == [answered["post"]["id"]]
    with pytest.raises(ToolError):
        await server.call_tool("network_social_create_post", {"post": reply | {
            "event_id": str(uuid4()), "parent_id": answered["post"]["id"]}})
    await server.call_tool("network_social_delete_post", {"post_id": first["post"]["id"]})
    with pytest.raises(ToolError):
        await server.call_tool("network_social_post", {"post_id": answered["post"]["id"]})
    with pytest.raises(ToolError):
        await server.call_tool("network_social_create_post", {"post": post})
    social.withdraw(p, agent["agent"]["id"])
    fresh = social.publish(p, agent["agent"]["id"], publication)
    assert fresh["grant_id"] != profile["grant_id"]
    with pytest.raises(ToolError):
        await server.call_tool("network_social_create_post", {"post": post | {"event_id": str(uuid4())}})


async def test_social_mcp_reauthenticates_and_cannot_expand_owner_publication(state):
    store, owner, p, agent, _, _ = state
    server = create_mcp(store, agent["token"])
    names = {tool.name for tool in await server.list_tools() if tool.name.startswith("network_social_")}
    assert names == {"network_social_" + name for name in (
        "contract", "profiles", "profile", "me", "feed", "post", "create_post", "delete_post", "report_post")}
    private = "private-community-canary"
    for name, args in (
        ("network_social_create_post", {"post": {"prompt": private}}),
        ("network_social_profiles", {"limit": True}),
        ("network_social_feed", {"include_synthetic": "true"}),
        ("network_social_profile", {"agent_id": private}),
    ):
        with pytest.raises(ToolError) as error:
            await server.call_tool(name, args)
        assert private not in str(error.value)
    store.rotate_agent_token(p, agent["agent"]["id"])
    for name in ("network_social_contract", "network_social_profiles", "network_social_me", "network_social_feed"):
        with pytest.raises(ToolError, match="unauthorized"):
            await server.call_tool(name, {})
