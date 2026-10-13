"""Guard the reviewed origin and read-only authority boundaries."""
import re
from urllib.parse import urlsplit

from seiche_agent_network.extra_products import (
    EXTRA_FAMILIES, EXTRA_PRODUCTS, EXTRA_PRODUCT_TARGETS, EXTRA_TOOL_REGISTRY,
)


def test_extra_products_have_only_reviewed_fixed_https_origins():
    expected = {
        "riptide": "https://api.seiche.info/riptide/mcp",
        "myquant-editorial": "https://myquantdoesntspeakenglish.com/mcp",
        "myquant-app": "https://myquant-app.vercel.app/mcp",
        "trade-safety": "https://trade-safety.liquilens.in/mcp",
        "financial-evidence": "https://api.seiche.info/openbb/mcp",
        "research-data": "https://api.seiche.info/api/v2/research-data/mcp",
        "seiche-market-corpus": "https://api.seiche.info/api/v2/corpus/mcp",
    }
    assert {key: value["url"] for key, value in EXTRA_PRODUCTS.items()} == expected
    for key, product in EXTRA_PRODUCTS.items():
        assert re.fullmatch(r"[a-z][a-z0-9-]*", key)
        url = urlsplit(product["url"])
        assert url.scheme == "https" and url.hostname
        assert not url.username and not url.password and not url.query and not url.fragment
        assert url.port in (None, 443)
        assert set(product) == {"name", "url", "first_tool", "first_prompt", "kind", "read_tools"}
        assert 1 <= len(product["first_prompt"]) <= 240


def test_derived_gateway_and_classifier_views_share_exact_allowlists():
    assert EXTRA_PRODUCTS.keys() == EXTRA_PRODUCT_TARGETS.keys() == EXTRA_TOOL_REGISTRY.keys() == EXTRA_FAMILIES.keys()
    for key, product in EXTRA_PRODUCTS.items():
        tools = product["read_tools"]
        assert isinstance(tools, tuple) and 1 <= len(tools) <= 16
        assert len(tools) == len(set(tools))
        assert all(re.fullmatch(r"[a-z][a-z0-9_]*", tool) for tool in tools)
        assert product["first_tool"] in tools
        assert EXTRA_PRODUCT_TARGETS[key] == (product["name"], product["url"])
        assert EXTRA_TOOL_REGISTRY[key] == list(tools)
        assert EXTRA_FAMILIES[key].startswith(key + ".")


def test_metadata_and_sandbox_admission_cannot_expand_into_private_or_execution_tools():
    assert EXTRA_PRODUCTS["myquant-app"]["kind"] == "metadata_only"
    assert set(EXTRA_TOOL_REGISTRY["myquant-app"]) == {"list_capabilities", "get_health"}
    assert EXTRA_PRODUCTS["trade-safety"]["kind"] == "read_only_sandbox"
    assert set(EXTRA_TOOL_REGISTRY["trade-safety"]) == {"assess_trade_safety", "trade_safety_capabilities"}
    assert EXTRA_PRODUCTS["financial-evidence"]["kind"] == "research_wrapper"
    assert len(EXTRA_TOOL_REGISTRY["riptide"]) == 7
    assert len(EXTRA_TOOL_REGISTRY["myquant-editorial"]) == 5
    assert len(EXTRA_TOOL_REGISTRY["financial-evidence"]) == 9
    for tools in EXTRA_TOOL_REGISTRY.values():
        assert not {"place_order", "execute_trade", "get_saved_stories", "set_preferences", "collect", "rebalance"}.intersection(tools)
