"""Public read boundaries preserve qualified values and withhold FRED dependencies."""
import asyncio
import copy
import json
from datetime import UTC, datetime

import pytest

from seiche import public_rights, mcp_server, api
from seiche.engines import money_market
from seiche.markets.world import project_world_markets
from test_money_market import _world, _card

RESTRICTED = {"policy.sofr", "policy.effr", "policy.iorb", "liquidity.reserves", "liquidity.on_rrp"}
DIRECT = {"distribution.sofr.p99", "distribution.sofr.volume", "liquidity.tga", "liquidity.srf"}


def desk():
    inputs = _world(120)
    for key in ("sofr", "effr", "iorb", "reserves", "on_rrp"):
        inputs[key] = inputs[key] * 0 + 777.123
    result = money_market.analyze(**inputs)
    result["snapshot_generated_at"] = "2021-06-18T12:00:00Z"
    result["new_unreviewed_field"] = {"value": 777.123}
    return result


def test_money_projection_preserves_required_dated_rows_and_direct_sources():
    original = desk()
    before = copy.deepcopy(original)
    result = public_rights.money_desk(original)
    assert original == before
    assert "777.123" not in json.dumps(result)
    for name in RESTRICTED:
        row, prior = _card(result, name), _card(original, name)
        assert row["value"] is None and row["status"] == "restricted"
        assert row["publication_allowed"] is False
        for field in ("id", "source", "unit", "asof", "cadence"):
            assert row[field] == prior[field]
    for name in DIRECT:
        assert _card(result, name) == _card(original, name)
    assert result["regime"]["state"] == "CANNOT_ASSESS"
    assert result["quant_read"] is None
    assert result["charts"]["sofr_distribution"]["rows"] == original["charts"]["sofr_distribution"]["rows"]
    liquidity = result["charts"]["liquidity"]
    for row in liquidity["rows"]:
        assert all(value is None for key, value in zip(liquidity["columns"], row)
                   if key in {"reserves_b", "on_rrp_b", "discount_window_b"})
    assert public_rights.money_desk(result) == result


def test_unknown_shapes_and_new_source_ids_cannot_publish_values():
    for value in (None, [777.123], {"schema": "future", "sections": []},
                  {"schema": "seiche.money-market-desk.v1", "sections": [{"metrics": [777.123]}]}):
        projected = public_rights.money_desk(value)
        assert projected["ok"] is False
        assert "777.123" not in json.dumps(projected)
    original = desk()
    original["sections"][0]["metrics"].append({
        "id": "policy.future_direct", "source": "Original publisher maybe",
        "asof": "2021-06-18", "value": 777.123, "status": "available"})
    assert _card(public_rights.money_desk(original), "policy.future_direct")["value"] is None


def test_mcp_rest_and_world_apply_same_current_policy_without_mutating_cache(monkeypatch):
    original = desk()
    snapshot = {"generated_at": original["snapshot_generated_at"], "engines": {"money_market": original}}
    before = copy.deepcopy(snapshot)
    monkeypatch.setattr(mcp_server, "_get_completed_snapshot", lambda: snapshot)
    monkeypatch.setattr(money_market, "refresh_for_evaluation", lambda value, **kwargs: copy.deepcopy(value))
    async def completed():
        return snapshot
    monkeypatch.setattr(api.assemble, "snapshot", completed)
    mcp = mcp_server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "money_market_context", "arguments": {"section": "all"}}}, public=True)
    assert not mcp["result"].get("isError")
    assert "777.123" not in json.dumps(mcp)
    from fastapi import Response
    rest = asyncio.run(api.money_market_context(Response()))
    assert "777.123" not in json.dumps(rest)
    world = project_world_markets(snapshot, selector="money_markets", evaluation_asof=datetime(2021,6,18,12,tzinfo=UTC))
    assert "777.123" not in json.dumps(world)
    for name in RESTRICTED | DIRECT:
        assert _card(rest, name)["value"] == _card(public_rights.money_desk(original), name)["value"]
    assert snapshot == before
