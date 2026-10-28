"""Public metadata keeps stable action names and factual scope."""
from seiche import mcp_server as mcp


def test_reviewed_metadata_preserves_actions_and_caveats():
    tools = mcp.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, public=True)["result"]["tools"]
    by_name = {tool["name"]: tool for tool in tools}
    assert "proof_backtest" not in by_name
    assert "source_rights_status" in by_name
    world = by_name["world_markets_context"]
    assert "outside this tool's scope" in world["description"]
    assert "Undertow instead" not in world["description"]
    assert "not a per-record licensing audit" in world["description"]
