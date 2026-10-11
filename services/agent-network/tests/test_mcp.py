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
    assert len(names) == 28
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
            assert len(inventory.tools) == 28
            result = await session.call_tool("network_identity", {})
            assert result.isError is False
            assert json.loads(result.content[0].text)["org_id"] == store.authenticate(owner["owner_token"]).org_id
            failure = await session.call_tool("network_inbox", {"after": -1})
            assert failure.isError is True
