"""OpenAI plugin-readiness contracts for Seiche's remote MCP surface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from seiche import mcp_server as mcp

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_STRUCTURED_CALLS = {
    "latest_article": {},
    "funding_stress_now": {},
    "trade_safety_risk_context": {},
    "money_market_context": {},
    "world_markets_context": {},
    "historical_analogs": {},
    "proof_backtest": {},
    "data_health": {},
    "crypto_stress_record": {},
    "institutional_flows": {},
    "oil_funding_context": {},
    "fx_materials_passage": {},
}
PAID_STRUCTURED_CALLS = {
    "funding_stress_forecast": {},
    "replay_asof": {"date": "2019-09-17"},
    "positioning_book": {},
    "ask_desk": {"question": "Is dollar funding tight?"},
}


def _rpc(method: str, params: dict | None = None) -> dict:
    message = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        message["params"] = params
    return message


def _tool_call(name: str, arguments: dict, *, public: bool) -> dict:
    return mcp.dispatch(
        _rpc("tools/call", {"name": name, "arguments": arguments}),
        public=public,
    )


def _type_matches(value: Any, expected: str) -> bool:
    checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "boolean": lambda item: isinstance(item, bool),
        "number": lambda item: (
            isinstance(item, (int, float)) and not isinstance(item, bool)
        ),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "null": lambda item: item is None,
    }
    return checks[expected](value)


def _assert_schema(value: Any, schema: dict, path: str = "$") -> None:
    """Validate the JSON Schema subset used by Seiche without a new dependency."""
    expected = schema.get("type")
    if isinstance(expected, str):
        assert _type_matches(value, expected), f"{path} is not {expected}"
    elif isinstance(expected, list):
        assert any(_type_matches(value, item) for item in expected), (
            f"{path} does not match any declared type {expected}"
        )

    if "const" in schema:
        assert value == schema["const"], f"{path} != {schema['const']!r}"
    if "enum" in schema:
        assert value in schema["enum"], f"{path} is outside {schema['enum']!r}"
    if isinstance(value, str) and "minLength" in schema:
        assert len(value) >= schema["minLength"], f"{path} is too short"
    if isinstance(value, dict) and "minProperties" in schema:
        assert len(value) >= schema["minProperties"], f"{path} has too few keys"

    if isinstance(value, dict):
        required = schema.get("required", [])
        assert set(required) <= set(value), (
            f"{path} is missing {set(required) - set(value)}"
        )
        properties = schema.get("properties", {})
        for key, child_schema in properties.items():
            if key in value:
                _assert_schema(value[key], child_schema, f"{path}.{key}")
        if schema.get("additionalProperties") is False:
            assert set(value) <= set(properties), f"{path} has undeclared keys"

    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            _assert_schema(item, schema["items"], f"{path}[{index}]")

    alternatives = schema.get("anyOf")
    if isinstance(alternatives, list):
        failures = []
        for alternative in alternatives:
            try:
                _assert_schema(value, alternative, path)
            except AssertionError as exc:
                failures.append(str(exc))
            else:
                break
        else:
            raise AssertionError(f"{path} matched no anyOf branch: {failures}")


@pytest.fixture()
def plugin_runtime(monkeypatch, fake_snap, asof_snap):
    """Keep every contract witness local, deterministic, and successful."""
    from seiche import ai, store, wakeflows

    monkeypatch.setattr(mcp, "PUBLIC_ONLY", False)
    monkeypatch.setattr(mcp, "_get_snapshot", lambda force=False: fake_snap)
    monkeypatch.setattr(mcp, "_get_completed_snapshot", lambda: fake_snap)
    monkeypatch.setattr(mcp, "_get_asof", lambda _date: asof_snap)
    monkeypatch.setattr(
        mcp,
        "_latest_article_from_feed",
        lambda: {
            "id": "seiche:article:contract",
            "url": "https://seiche.info/articles/contract/",
            "title": "A bounded funding read",
            "content_text": "The observed evidence supports a bounded conclusion.",
            "date_published": "2026-08-21T11:30:00Z",
            "_liquidity_lab": {
                "quality_gate": {"status": "PASS"},
                "authority": {
                    "factual_authority": "published_article_only",
                    "training_allowed": False,
                },
            },
        },
    )
    monkeypatch.setattr(
        store,
        "load_blob",
        lambda _key: {"schema_version": "seiche.wrecks.v1", "episodes": []},
    )
    monkeypatch.setattr(wakeflows, "load", lambda: {"schema_version": "test"})
    monkeypatch.setattr(
        wakeflows,
        "readings",
        lambda _pack: {"as_of": "2026-08-21", "basis_trade": {}},
    )
    monkeypatch.setattr(
        ai,
        "ask",
        lambda _question, _snapshot: {
            "ok": True,
            "answer": "The board is in STRAIN, while overnight cash remains orderly.",
            "grounding": "Restricted to the live board context pack.",
            "route": "funding_stress_now",
        },
    )
    monkeypatch.setattr(mcp, "_run", lambda value: value)


def test_tool_descriptors_publish_complete_openai_contracts():
    full = mcp.dispatch(_rpc("tools/list"), public=False)["result"]["tools"]
    public = mcp.dispatch(_rpc("tools/list"), public=True)["result"]["tools"]
    full_by_name = {tool["name"]: tool for tool in full}
    public_by_name = {tool["name"]: tool for tool in public}

    assert len(full) == 17
    assert len(public) == 12
    assert set(public_by_name) == set(PUBLIC_STRUCTURED_CALLS)
    assert set(mcp.STRUCTURED_OUTPUT_TOOLS) == set(mcp.TOOLS) - {"desk_brief"}
    assert {
        name for name, tool in full_by_name.items() if "outputSchema" in tool
    } == set(mcp.STRUCTURED_OUTPUT_TOOLS)
    assert "outputSchema" not in full_by_name["desk_brief"]

    for tool in full:
        assert tool["title"] and tool["description"]
        assert tool["inputSchema"]["type"] == "object"
        assert tool["annotations"] == {
            "title": tool["title"],
            "readOnlyHint": True,
            "idempotentHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        }
        if "outputSchema" in tool:
            schema = tool["outputSchema"]
            assert schema["type"] == "object"
            assert schema["description"]
            assert schema["properties"]
            assert schema["anyOf"]
            json.dumps(schema)


def test_every_successful_structured_result_matches_its_descriptor(plugin_runtime):
    public_descriptors = {
        tool["name"]: tool
        for tool in mcp.dispatch(_rpc("tools/list"), public=True)["result"]["tools"]
    }
    full_descriptors = {
        tool["name"]: tool
        for tool in mcp.dispatch(_rpc("tools/list"), public=False)["result"]["tools"]
    }
    witnesses = []

    for name, arguments in PUBLIC_STRUCTURED_CALLS.items():
        response = _tool_call(name, arguments, public=True)
        result = response["result"]
        assert result.get("isError") is not True, name
        payload = result["structuredContent"]
        schema = public_descriptors[name]["outputSchema"]
        _assert_schema(payload, schema, f"$.{name}")
        witnesses.append((payload, schema))

    for name, arguments in PAID_STRUCTURED_CALLS.items():
        response = _tool_call(name, arguments, public=False)
        result = response["result"]
        assert result.get("isError") is not True, name
        payload = result["structuredContent"]
        schema = full_descriptors[name]["outputSchema"]
        _assert_schema(payload, schema, f"$.{name}")
        witnesses.append((payload, schema))

    # Exercise the alternate, richer funding_stress_now success envelope too.
    full_stress = _tool_call("funding_stress_now", {}, public=False)["result"]
    _assert_schema(
        full_stress["structuredContent"],
        full_descriptors["funding_stress_now"]["outputSchema"],
        "$.funding_stress_now.full",
    )
    witnesses.append(
        (
            full_stress["structuredContent"],
            full_descriptors["funding_stress_now"]["outputSchema"],
        )
    )

    # When the optional standards package is present, validate the same real
    # witnesses with the complete Draft 2020-12 implementation as well.
    try:
        import jsonschema
    except ModuleNotFoundError:
        return
    for payload, schema in witnesses:
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema).validate(payload)


@pytest.mark.parametrize("section", mcp.WORLD_MARKETS_SELECTORS)
def test_world_markets_selector_results_match_the_advertised_schema(
    plugin_runtime, section
):
    descriptor = next(
        tool
        for tool in mcp.dispatch(_rpc("tools/list"), public=True)["result"]["tools"]
        if tool["name"] == "world_markets_context"
    )
    schema = descriptor["outputSchema"]
    result = _tool_call(
        "world_markets_context",
        {"section": section},
        public=True,
    )["result"]

    assert result.get("isError") is not True
    _assert_schema(result["structuredContent"], schema)

    try:
        import jsonschema
    except ModuleNotFoundError:
        return
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(result["structuredContent"])


def test_world_markets_unavailable_and_invalid_results_match_schema(
    plugin_runtime, monkeypatch
):
    descriptor = next(
        tool
        for tool in mcp.dispatch(_rpc("tools/list"), public=True)["result"]["tools"]
        if tool["name"] == "world_markets_context"
    )
    schema = descriptor["outputSchema"]
    monkeypatch.setattr(mcp, "_get_completed_snapshot", lambda: None)

    unavailable = _tool_call(
        "world_markets_context",
        {"section": "forex"},
        public=True,
    )["result"]
    assert unavailable.get("isError") is not True
    assert unavailable["structuredContent"]["status"] == "unavailable"
    _assert_schema(unavailable["structuredContent"], schema)

    invalid = _tool_call(
        "world_markets_context",
        {"section": "charts"},
        public=True,
    )["result"]
    assert invalid["isError"] is True
    assert invalid["structuredContent"]["status"] == "FAILED"
    _assert_schema(invalid["structuredContent"], schema)

    try:
        import jsonschema
    except ModuleNotFoundError:
        return
    validator = jsonschema.Draft202012Validator(schema)
    validator.validate(unavailable["structuredContent"])
    validator.validate(invalid["structuredContent"])


def test_structured_failure_matches_the_advertised_failure_arm(monkeypatch):
    original = mcp.TOOLS["data_health"]

    def unavailable(_arguments, _public):
        raise mcp.ToolError("source is unavailable")

    monkeypatch.setitem(
        mcp.TOOLS,
        "data_health",
        (*original[:3], unavailable, *original[4:]),
    )
    descriptor = next(
        tool
        for tool in mcp.dispatch(_rpc("tools/list"), public=True)["result"]["tools"]
        if tool["name"] == "data_health"
    )
    result = _tool_call("data_health", {}, public=True)["result"]

    assert result["isError"] is True
    _assert_schema(result["structuredContent"], descriptor["outputSchema"])


def test_submission_pack_has_review_cases_without_fake_portal_evidence():
    pack = ROOT / "integrations" / "openai"
    cases = json.loads((pack / "test-cases.json").read_text())
    positive = [case for case in cases["test_cases"] if case["kind"] == "positive"]
    negative = [case for case in cases["test_cases"] if case["kind"] == "negative"]

    assert len(positive) >= 5
    assert len(negative) >= 3
    assert cases["surface"] == "anonymous_public_twelve_tools"
    assert any(
        any(
            call.startswith("world_markets_context")
            for call in case["expected_tool_calls"]
        )
        for case in positive
    )
    assert len({case["id"] for case in cases["test_cases"]}) == len(cases["test_cases"])
    for case in cases["test_cases"]:
        assert case["prompt"]
        assert case["expected_behavior"]
        assert case["pass_criteria"]
    assert not (pack / "ai-plugin.json").exists()
    submission = (pack / "SUBMISSION.md").read_text()
    for owner_step in (
        "Apps Management",
        "identity verification",
        "openai-apps-challenge",
        "policy attestations",
    ):
        assert owner_step in submission
