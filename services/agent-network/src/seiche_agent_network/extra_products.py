"""Reviewed additional public MCP surfaces, pinned to fixed HTTPS origins.

Admission was checked against native source and live initialize/tools/list on
2026-10-12. Each first tool also returned a successful read-only response. These
are explicit tool allowlists, never permissions inferred from future discovery.
Research wrappers may read other products: count their requests separately from
nested source requests, not as additional people, agents, or completed tasks.
"""
from __future__ import annotations


EXTRA_PRODUCTS = {
    "riptide": {
        "name": "Riptide",
        "url": "https://api.seiche.info/riptide/mcp",
        "first_tool": "riptide_overview",
        "first_prompt": "Show Riptide's committed paper research record and its evidence limits.",
        "kind": "research_product",
        "read_tools": (
            "riptide_allocation", "riptide_crypto_record", "riptide_event_markets",
            "riptide_methodology", "riptide_overview", "riptide_paper_record",
            "riptide_risk_evidence",
        ),
    },
    "myquant-editorial": {
        "name": "MyQuant Editorial",
        "url": "https://myquantdoesntspeakenglish.com/mcp",
        "first_tool": "latest_stories",
        "first_prompt": "Show one recent published story with its sources, dates and editorial limits.",
        "kind": "editorial_archive",
        "read_tools": (
            "list_capabilities", "get_health", "latest_stories", "get_story",
            "search_stories",
        ),
    },
    "myquant-app": {
        "name": "MyQuant App Discovery",
        "url": "https://myquant-app.vercel.app/mcp",
        "first_tool": "list_capabilities",
        "first_prompt": "Show the MyQuant app's public capabilities and offline reading boundaries.",
        "kind": "metadata_only",
        "read_tools": ("list_capabilities", "get_health"),
    },
    "trade-safety": {
        "name": "LiquiLens Trade Safety",
        "url": "https://trade-safety.liquilens.in/mcp",
        "first_tool": "trade_safety_capabilities",
        "first_prompt": "Explain the Trade Safety sandbox's capabilities, input limits and lack of execution authority.",
        "kind": "read_only_sandbox",
        "read_tools": ("assess_trade_safety", "trade_safety_capabilities"),
    },
    "financial-evidence": {
        "name": "Financial Evidence Workspace",
        "url": "https://api.seiche.info/openbb/mcp",
        "first_tool": "financial_evidence_datasets",
        "first_prompt": "List the research datasets, their coverage, allowed filters and evidence limits.",
        "kind": "research_wrapper",
        "read_tools": (
            "financial_evidence_workflow", "financial_evidence_datasets",
            "financial_evidence_funding_review", "financial_evidence_query",
            "financial_evidence_sources", "financial_evidence_route",
            "financial_evidence_packet", "financial_evidence_agent_query",
            "financial_evidence_review",
        ),
    },
}

# Derived views let the gateway and usage classifier share the reviewed allowlist.
EXTRA_PRODUCT_TARGETS = {
    key: (product["name"], product["url"])
    for key, product in EXTRA_PRODUCTS.items()
}
EXTRA_TOOL_REGISTRY = {
    key: list(product["read_tools"]) for key, product in EXTRA_PRODUCTS.items()
}

# Whole-product labels only. Exact dataset selectors require separate validation;
# MyQuant app discovery is not evidence of access to a reader's on-device state.
EXTRA_FAMILIES = {
    "riptide": "riptide.public_research",
    "myquant-editorial": "myquant-editorial.public_archive",
    "myquant-app": "myquant-app.discovery",
    "trade-safety": "trade-safety.sandbox",
    "financial-evidence": "financial-evidence.research_wrapper",
}
