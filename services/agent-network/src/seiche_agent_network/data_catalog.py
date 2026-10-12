"""Versioned, reviewed endpoint and tool-family classifications; no raw arguments."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import json
from pathlib import Path
import re

from .models import NetworkError

VERSION = "central-data-2026-10-12"
FIXED_DATA = {
    "seiche.money_markets": ("seiche", "Money markets", "https://api.seiche.info/api/v2/money-markets"),
    "seiche.capital_markets": ("seiche", "Capital markets", "https://api.seiche.info/api/v2/world-markets?section=capital_markets"),
    "palimpsest.china_index": ("palimpsest", "China index", "https://www.palimpsest.info/readings/china-index-latest.json"),
    "seiche.china_macro": ("seiche", "China macro", "https://api.seiche.info/api/v2/world-markets?section=china_macro"),
    "liquilens.failure_radar": ("liquilens", "Failure radar", "https://api.liquilens.in/api/failure-radar/board"),
    "undertow.summary": ("undertow", "Market liquidity summary", "https://api.seiche.info/undertow/x402/summary"),
    "seiche.gift_city": ("seiche", "GIFT City", "https://api.seiche.info/api/v2/gift-city"),
    "seiche.forex": ("seiche", "Foreign exchange", "https://api.seiche.info/api/v2/world-markets?section=forex"),
    "liquilens.institution_watchlist": ("liquilens", "Institution monitoring watchlist", "https://api.liquilens.in/api/experimental/v1/banking/monitoring/watchlist"),
    "undertow.exit_check": ("undertow", "Exit size scenarios", "https://api.seiche.info/undertow/mcp/exit-check"),
}

# Each list was reviewed as a family, not an exact underlying source/record.
# Unknown registered tools remain unknown until this versioned map is reviewed.
GROUPS = {
    "seiche.money_markets": ("Money markets", "funding_stress_now money_market_context"),
    "seiche.gift_city": ("GIFT City", "gift_city_context"),
    "seiche.world_markets": ("World market context", "world_markets_context institutional_flows"),
    "seiche.cross_asset_context": ("Cross-asset funding context", "gold_inventory_carry oil_funding_context fx_materials_passage crypto_stress_record trade_safety_risk_context"),
    "seiche.research_methods": ("Research workbench and historical methods", "market_workbench historical_analogs proof_backtest"),
    "seiche.editorial": ("Published research", "latest_article"),
    "liquilens.banking_evidence": ("Banking evidence", "bank_asset_quality_review bank_npa_reconciliation banking_specialisation_coverage rbi_supervisory_tape household_credit_board"),
    "liquilens.institution_evidence": ("Institution research", "evidence_institution institution_evidence_monitor institution_research_coverage institution_review_packet forward_odds universe_search"),
    "liquilens.regional_evidence": ("Regional and market evidence", "evidence_europe evidence_india evidence_markets evidence_us corporate_transmission_board"),
    "liquilens.crypto_evidence": ("Digital-asset evidence", "crypto_exposure_board crypto_regime_board stablecoin_rails_board"),
    "liquilens.failure_radar": ("Failure radar", "failure_radar_board failure_radar_institution"),
    "liquilens.editorial": ("Published research and verification", "latest_article verify_published_record"),
    "undertow.exit_liquidity": ("Exit and market liquidity", "crypto_exit_check crypto_portfolio_stress depth_episodes exit_cost gold_cash_realisation liquidity_tiers market_corpus_context trade_safety_exit_context unwind_watch venue_concentration venue_price_reconciliation"),
    "undertow.funding_cost": ("Digital-asset funding cost", "crypto_funding_cost"),
    "undertow.editorial": ("Published research and receipts", "latest_article sealed_record"),
    "palimpsest.china_economy": ("China economic evidence", "get_signal query_economic_observations whats_happening"),
    "palimpsest.newsroom": ("China newsroom", "get_newsroom"),
    "palimpsest.internet_readings": ("Internet boundary readings", "gfw_reading"),
    "noisefloor.statistical_methods": ("Statistical assessment", "ab_test did_it_change forecast_next market_assessment narrative_triage real_or_sampling score_forecasts spectral_assessment which_metrics_matter"),
    "noisefloor.scientific_reference": ("Scientific reference", "dyson_reference"),
    "narcoscope.public_evidence": ("Public evidence atlas", "get_overview get_atlas get_entities get_federation get_connected_research"),
    "narcoscope.newsroom": ("Evidence newsroom", "get_newsroom get_story"),
    "narcoscope.china_bridge": ("China evidence connections", "get_palimpsest_bridge get_palimpsest_corridors get_palimpsest_bri_context"),
    "narcoscope.market_observations": ("Public market observations", "query_market_observations"),
    "wake.ai_economy": ("Public AI economy context", "wake_economy_now"),
}
DISCOVERY_TOOLS = {
    "seiche": "research_network data_health", "liquilens": "research_network",
    "undertow": "research_network agent_access_status", "palimpsest": "research_catalog list_signals",
    "noisefloor": "", "narcoscope": "research_network list_capabilities get_market_catalog",
    "wake": "wake_dataset_policy wake_collection_contract",
}
RESEARCH_DATASETS = {
    "money_markets": ("seiche", "Money market research"), "money_market_history": ("seiche", "Money market history"),
    "capital_markets": ("seiche", "Capital market research"), "bank_risk": ("liquilens", "Bank risk research"),
    "market_liquidity": ("undertow", "Market liquidity research"), "china_economy": ("palimpsest", "China economic research"),
    "source_health": ("seiche", "Research source health"),
}
RESEARCH_READS = {
    "start": ("financial-evidence", "financial-evidence.discovery", "discovery"),
    "release": ("financial-evidence", "financial-evidence.discovery", "discovery"),
    "datasets": ("financial-evidence", "financial-evidence.discovery", "discovery"),
    "sources": ("financial-evidence", "financial-evidence.research", "family"),
    "funding-review": ("seiche", "research.money_markets", "family"),
}


@lru_cache(maxsize=1)
def extra_products():
    from .extra_products import EXTRA_PRODUCTS
    return EXTRA_PRODUCTS


@lru_cache(maxsize=1)
def tool_registry():
    from .extra_products import EXTRA_TOOL_REGISTRY
    return json.loads(Path(__file__).with_name("product_tools.json").read_text()) | EXTRA_TOOL_REGISTRY


@lru_cache(maxsize=1)
def entries():
    result = {key: {"id": key, "product": value[0], "label": value[1], "route": "/v1/data/" + key,
                    "native_url": value[2], "tier": "primary"} for key, value in FIXED_DATA.items()}
    for key, (label, _) in GROUPS.items():
        result.setdefault(key, {"id": key, "product": key.split(".", 1)[0], "label": label,
                                "route": None, "tier": "primary"})
    registry = tool_registry()
    for product in registry:
        tier = "primary" if product in DISCOVERY_TOOLS else "adjacent"
        if extra_products().get(product, {}).get("kind") == "research_wrapper":
            tier = "research_wrapper"
        for suffix, label in (("discovery", "Capabilities and discovery"), ("unknown", "Unclassified access")):
            key = product + "." + suffix
            result[key] = {"id": key, "product": product, "label": label, "route": None, "tier": tier}
    for product, info in extra_products().items():
        key = product + ".research"
        result[key] = {"id": key, "product": product, "label": info["name"] + " reviewed tool family",
                       "route": None, "tier": "research_wrapper" if info["kind"] == "research_wrapper" else "adjacent"}
    result["financial-evidence.discovery"]["route"] = "/v1/research/api/v1/datasets"
    result["other.unknown"] = {"id": "other.unknown", "product": "other", "label": "Unclassified access", "route": None, "tier": "unknown"}
    for dataset, (product, label) in RESEARCH_DATASETS.items():
        key = "research." + dataset
        result[key] = {"id": key, "product": product, "label": label, "route": "/v1/research/api/v1/query?dataset=" + dataset,
                       "tier": "research_wrapper"}
    result["research.agent_review"] = {"id": "research.agent_review", "product": "liquilens", "label": "Institution research review",
                                         "route": "/v1/research/api/v1/agent-review", "tier": "research_wrapper"}
    return result


@dataclass(frozen=True)
class Classification:
    product: str
    dataset_id: str
    basis: str
    operation: str
    catalog_version: str = VERSION


def classify(product, method, tool):
    registry = tool_registry()
    if product not in registry:
        return Classification("other", "other.unknown", "unknown", "mcp_other")
    if method in {"initialize", "tools/list", "resources/list", "resources/templates/list", "prompts/list", "prompts/get", "server/discover"}:
        return Classification(product, product + ".discovery", "discovery", "mcp_discovery")
    if method != "tools/call" or tool not in registry[product]:
        return Classification(product, product + ".unknown", "unknown", "mcp_other" if method != "tools/call" else "mcp_tool")
    if tool in DISCOVERY_TOOLS.get(product, "").split() or tool in {
            "list_capabilities", "get_health", "trade_safety_capabilities",
            "financial_evidence_datasets", "financial_evidence_route"}:
        return Classification(product, product + ".discovery", "discovery", "mcp_discovery")
    for dataset_id, (_, names) in GROUPS.items():
        if dataset_id.startswith(product + ".") and tool in names.split():
            return Classification(product, dataset_id, "family", "mcp_tool")
    if product in extra_products():
        return Classification(product, product + ".research", "family", "mcp_tool")
    return Classification(product, product + ".unknown", "unknown", "mcp_tool")


def validate_classification(value, product):
    entry = entries().get(value.dataset_id) if isinstance(value, Classification) else None
    if (entry is None or entry["product"] != product or value.product != product or value.catalog_version != VERSION
            or value.basis not in {"exact", "family", "discovery", "unknown"}
            or value.operation not in {"rest_json", "mcp_tool", "mcp_discovery", "mcp_other", "research_wrapper"}):
        raise NetworkError("invalid_analytics", "Dataset classification is not in the reviewed catalog", 400)
    return value


def _bounded_pairs(query):
    pairs = list(query.multi_items())
    if len(pairs) > 8 or len({key for key, _ in pairs}) != len(pairs):
        raise NetworkError("invalid_query", "Repeated or excessive query parameters are unsupported", 400)
    if any(len(key) > 40 or len(value) > 200 for key, value in pairs):
        raise NetworkError("invalid_query", "Query parameter exceeds the bounded route contract", 400)
    return dict(pairs)


def _slugs(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 160:
        raise ValueError()
    values = value.split(",")
    if not 1 <= len(values) <= 5 or any(not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", item) for item in values):
        raise ValueError()
    return ",".join(dict.fromkeys(values))


def _sizes(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 160:
        raise ValueError()
    values = value.split(",")
    if not 1 <= len(values) <= 4 or any(not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", item) for item in values):
        raise ValueError()
    numbers = [Decimal(item) for item in values]
    if any(not 0 < item <= 1000000 for item in numbers):
        raise ValueError()
    return ",".join(dict.fromkeys(format(item.normalize(), "f") for item in numbers))


def fixed_request(dataset_id, query):
    if dataset_id not in FIXED_DATA:
        raise NetworkError("not_found", "Unknown reviewed dataset endpoint", 404)
    params = _bounded_pairs(query)
    product, _, url = FIXED_DATA[dataset_id]
    try:
        if dataset_id == "liquilens.institution_watchlist":
            if set(params) != {"slugs"}: raise ValueError()
            params = {"slugs": _slugs(params["slugs"])}
        elif dataset_id == "undertow.exit_check":
            if set(params) != {"sizes_usd"}: raise ValueError()
            params = {"sizes_usd": _sizes(params["sizes_usd"])}
        elif params:
            raise ValueError()
    except (ValueError, InvalidOperation):
        raise NetworkError("invalid_query", "Unsupported parameters for this fixed data endpoint", 400) from None
    return url, params, Classification(product, dataset_id, "exact", "rest_json")


def research_request(operation, query):
    params = _bounded_pairs(query)
    if operation not in RESEARCH_READS and operation not in {"query", "agent-query", "agent-review", "workflow"}:
        raise NetworkError("not_found", "Unknown reviewed research operation", 404)
    if operation in RESEARCH_READS:
        if params:
            raise NetworkError("invalid_query", "This fixed research endpoint accepts no parameters", 400)
        product, dataset_id, basis = RESEARCH_READS[operation]
        return ("https://api.seiche.info/openbb/api/v1/" + operation, {},
                Classification(product, dataset_id, basis, "research_wrapper"))
    try:
        if operation in {"query", "agent-query"}:
            allowed = {"dataset", "entity", "start_date", "end_date", "limit", "offset"}
            if operation == "agent-query": allowed.add("previous_revision")
            if set(params) - allowed or params.get("dataset") not in RESEARCH_DATASETS: raise ValueError()
            dataset = params["dataset"]
            product = RESEARCH_DATASETS[dataset][0]
            dataset_id = "research." + dataset
            if "entity" in params and (len(params["entity"]) > 100 or any(ord(c) < 32 for c in params["entity"])): raise ValueError()
            for field in ("start_date", "end_date"):
                if params.get(field) and (not re.fullmatch(r"\d{4}-\d{2}-\d{2}", params[field]) or date.fromisoformat(params[field]).isoformat() != params[field]): raise ValueError()
            if params.get("start_date") and params.get("end_date") and params["start_date"] > params["end_date"]: raise ValueError()
            maximum = 2000 if operation == "query" else 100
        elif operation == "agent-review":
            if set(params) - {"bank", "limit", "previous_revision"} or len(params.get("bank", "")) > 100: raise ValueError()
            if any(ord(c) < 32 for c in params.get("bank", "")): raise ValueError()
            product, dataset_id, maximum = "liquilens", "research.agent_review", 25
        else:
            if set(params) != {"workflow", "selection"}: raise ValueError()
            workflow = params["workflow"]
            if workflow == "funding":
                if not re.fullmatch(r"[A-Z]{3}", params["selection"]): raise ValueError()
                product, dataset_id = "seiche", "seiche.money_markets"
            elif workflow == "institutions":
                params["selection"] = _slugs(params["selection"])
                product, dataset_id = "liquilens", "liquilens.institution_watchlist"
            elif workflow == "exit":
                params["selection"] = _sizes(params["selection"])
                product, dataset_id = "undertow", "undertow.exit_check"
            else: raise ValueError()
            maximum = 0
        if "limit" in params and (not re.fullmatch(r"[0-9]{1,4}", params["limit"]) or not 1 <= int(params["limit"]) <= maximum): raise ValueError()
        if "offset" in params and (not re.fullmatch(r"[0-9]{1,6}", params["offset"]) or not 0 <= int(params["offset"]) <= 100000): raise ValueError()
        if "previous_revision" in params and params["previous_revision"] and not re.fullmatch(r"[a-f0-9]{64}", params["previous_revision"]): raise ValueError()
    except (ValueError, InvalidOperation):
        raise NetworkError("invalid_query", "Unsupported research parameters", 400) from None
    return ("https://api.seiche.info/openbb/api/v1/" + operation, params,
            Classification(product, dataset_id, "family" if operation in {"workflow", "agent-review"} else "exact", "research_wrapper"))


def catalog():
    registry = tool_registry()
    return {"schema": "seiche-agent-network.data-catalog.v1", "version": VERSION,
            "datasets": list(entries().values()),
            "research_routes": [{"route": "/v1/research/api/v1/" + operation, "query_parameters": False}
                                for operation in RESEARCH_READS],
            "mcp_classification": [{"product": product, "tool": tool, **classify(product, "tools/call", tool).__dict__}
                                   for product, names in registry.items() for tool in names],
            "basis": {"exact": "Reviewed fixed endpoint or allowlisted dataset selector; not an individual record",
                      "family": "Reviewed tool or workflow family; exact underlying dataset is unproven",
                      "discovery": "Capabilities, catalog or protocol discovery", "unknown": "No admitted classification"},
            "retention_days": 30, "raw_arguments_retained": False, "raw_responses_retained": False,
            "empty_response_meaning": "Accepted empty body or top-level JSON null, empty array, object or string; nested rows and dataset completeness are not assessed",
            "training_permission": False, "data_resale_permission": False,
            "coverage": "Requests observed through these central routes only; native origins, old clients and offline copies remain outside coverage",
            "counting": "Observed exchanges, not people, unique tasks, completed trades or universal agent coverage. Wrapper and upstream calls may overlap."}
