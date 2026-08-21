"""Public money-market evidence must stay crawlable, bounded and citable."""

from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlparse

import pytest

from seiche import dispatch_pages


ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "frontend" / "public"
PAGE = PUBLIC / "money-markets" / "index.html"
CATALOG = PUBLIC / "money-markets" / "catalog.json"


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.hidden_depth == 0:
            self.parts.append(data)

    @property
    def text(self) -> str:
        return " ".join(" ".join(self.parts).split())


def _catalog() -> dict:
    return json.loads(CATALOG.read_text())


def _page_parts() -> tuple[str, str, dict]:
    page = PAGE.read_text()
    parser = _VisibleText()
    parser.feed(page)
    match = re.search(
        r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
        page,
        flags=re.S,
    )
    assert match, "money-market page is missing its JSON-LD graph"
    return page, parser.text, json.loads(match.group(1))


def _indexnow_module():
    path = ROOT / "backend" / "scripts" / "ping_indexnow.py"
    spec = importlib.util.spec_from_file_location("test_money_market_indexnow", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_catalog_rows_derive_the_published_coverage_receipt():
    catalog = _catalog()
    snapshot = catalog["snapshot"]
    markets = catalog["markets"]
    states = Counter(market["status"] for market in markets)

    assert catalog["schema"] == "seiche.money-market-evidence-catalog.v1"
    assert catalog["canonical_url"] == "https://seiche.info/money-markets/"
    assert len(markets) == snapshot["registered_markets"] == 11
    assert states == {
        "LIVE_REFERENCE": 6,
        "DERIVED_CONTEXT": 1,
        "POLICY_ONLY": 1,
        "DECLARED_UNAVAILABLE": 3,
    }
    assert snapshot["raw_live_benchmarks"] == states["LIVE_REFERENCE"]
    assert snapshot["derived_context_benchmarks"] == states["DERIVED_CONTEXT"]
    assert snapshot["policy_only_markets"] == states["POLICY_ONLY"]
    assert snapshot["declared_unavailable_markets"] == states[
        "DECLARED_UNAVAILABLE"
    ]
    assert snapshot["discovery_candidates"] == 52
    assert snapshot["discovery_regions"] == 9
    assert snapshot["global_discovery_universe"] == 63
    assert snapshot["supported_market_packs"] == 0
    assert snapshot["evidence_eligible_market_packs"] == 0
    assert snapshot["global_score"] is None
    assert snapshot["global_tide_status"] == "UNAVAILABLE"
    assert not any(market["evidence_eligible"] for market in markets)


def test_catalog_keeps_source_rights_and_missingness_attached():
    catalog = _catalog()
    markets = catalog["markets"]
    assert len({market["market_id"] for market in markets}) == len(markets)
    assert all(re.fullmatch(r"[A-Z]+-[A-Z]+", market["market_id"])
               for market in markets)
    assert all(urlparse(market["source_url"]).scheme == "https"
               for market in markets)

    live = [market for market in markets if market["status"] == "LIVE_REFERENCE"]
    assert {market["benchmark"] for market in live} == {
        "AONIA", "ESTR", "CALL WAR", "TONA", "SORA", "SOFR"
    }
    assert all(market["raw_value_public"] and market["as_of"] for market in live)

    derived = next(market for market in markets if market["status"] == "DERIVED_CONTEXT")
    assert derived["benchmark"] == "SONIA"
    assert derived["rights_status"] == "derived_only"
    assert derived["raw_value_public"] is False
    assert derived["as_of"] is None

    policy = next(market for market in markets if market["status"] == "POLICY_ONLY")
    assert policy["benchmark"] == "HKMA BASE RATE"
    assert "policy_anchor" in policy["rights_status"]

    missing = [market for market in markets
               if market["status"] == "DECLARED_UNAVAILABLE"]
    assert {market["market_id"] for market in missing} == {
        "CN-CNY", "KR-KRW", "NZ-NZD"
    }
    assert all(market["benchmark"] is None and market["as_of"] is None
               and not market["raw_value_public"] for market in missing)


def test_no_js_page_is_canonical_answer_first_and_matches_the_catalog():
    page, visible, _ = _page_parts()
    snapshot = _catalog()["snapshot"]

    assert '<link rel="canonical" href="https://seiche.info/money-markets/">' in page
    assert 'type="application/json" href="https://seiche.info/money-markets/catalog.json"' in page
    assert page.count("<script") == 1
    assert page.count('type="application/ld+json"') == 1
    assert "Seiche does not publish a universal world-economy score." in visible
    assert "Registration, availability, validation, and evidence eligibility remain separate claims." in visible
    for label, value in (
        ("Registered packs", snapshot["registered_markets"]),
        ("Raw live benchmarks", snapshot["raw_live_benchmarks"]),
        ("Derived context", snapshot["derived_context_benchmarks"]),
        ("Policy only", snapshot["policy_only_markets"]),
        ("Unavailable", snapshot["declared_unavailable_markets"]),
        ("Discovery candidates", snapshot["discovery_candidates"]),
        ("Discovery universe", snapshot["global_discovery_universe"]),
    ):
        assert f"{label} {value}" in visible
    for endpoint in _catalog()["access"].values():
        assert endpoint in page or endpoint == "https://seiche.info/money-markets/"


def test_dataset_jsonld_matches_visible_and_machine_receipts():
    _, visible, jsonld = _page_parts()
    graph = jsonld["@graph"]
    data_catalog = next(node for node in graph if node["@type"] == "DataCatalog")
    dataset = next(node for node in graph if node["@type"] == "Dataset")
    snapshot = _catalog()["snapshot"]
    measured = {item["name"]: item["value"] for item in dataset["variableMeasured"]}

    assert jsonld["@context"] == "https://schema.org"
    assert data_catalog["dataset"]["@id"] == dataset["@id"]
    assert dataset["includedInDataCatalog"]["@id"] == data_catalog["@id"]
    assert data_catalog["dateModified"] == _catalog()["updated"]
    assert measured == {
        "Registered monetary-area packs": snapshot["registered_markets"],
        "Raw public live benchmarks": snapshot["raw_live_benchmarks"],
        "Derived-context benchmarks": snapshot["derived_context_benchmarks"],
        "Policy-only markets": snapshot["policy_only_markets"],
        "Declared-unavailable markets": snapshot["declared_unavailable_markets"],
        "Discovery candidates": snapshot["discovery_candidates"],
        "Global discovery universe": snapshot["global_discovery_universe"],
    }
    assert all(item["contentUrl"].startswith("https://")
               for item in dataset["distribution"])
    assert "AGPL-3.0 covers Seiche code, not upstream data" in dataset[
        "conditionsOfAccess"
    ]
    assert "Zero of 11 packs" in visible
    assert "Global Tide is unavailable" in visible


def test_discovery_generators_link_the_money_market_evidence_pair():
    assert ("/money-markets/", "weekly", "0.9") in dispatch_pages.BASE_URLS
    sitemap = dispatch_pages.render_sitemap([])
    llms = dispatch_pages.render_llms_txt([])
    assert "<loc>https://seiche.info/money-markets/</loc>" in sitemap
    assert "https://seiche.info/money-markets/" in llms
    assert "https://seiche.info/money-markets/catalog.json" in llms
    assert "six redistributable" in llms
    assert "63-market discovery" in llms
    assert "does not publish a universal" in llms
    assert "used as AI input or training material" not in llms


def test_product_and_agent_catalogs_route_to_the_evidence_pair():
    card = json.loads((PUBLIC / "product-card.json").read_text())
    receipt = card["evidence"]["money_market_coverage_receipt_2026_08_21"]
    assert receipt["registered_market_packs"] == 11
    assert receipt["raw_live_benchmarks"] == 6
    assert receipt["derived_context_benchmarks"] == 1
    assert receipt["policy_only_markets"] == 1
    assert receipt["declared_unavailable_markets"] == 3
    assert receipt["global_discovery_universe"] == 63
    assert receipt["global_score"] is None
    assert card["access"]["money_market_evidence_map"] == (
        "https://seiche.info/money-markets/"
    )
    assert card["access"]["money_market_evidence_catalog"] == (
        "https://seiche.info/money-markets/catalog.json"
    )

    agent_catalog = json.loads(
        (PUBLIC / ".well-known" / "ai-catalog.json").read_text()
    )
    entry = next(item for item in agent_catalog["entries"]
                 if item["identifier"] ==
                 "urn:air:seiche.info:dataset:money-market-evidence")
    assert entry["url"] == "https://seiche.info/money-markets/catalog.json"
    assert entry["type"] == "application/json"
    assert entry["metadata"]["registeredMarketPacks"] == 11
    assert entry["metadata"]["rawLiveBenchmarks"] == 6
    assert entry["metadata"]["universalScore"] is False
    assert 2 <= len(entry["representativeQueries"]) <= 5
    mcp = next(item for item in agent_catalog["entries"]
               if item["type"] == "application/mcp-server-card+json")
    assert mcp["metadata"]["publicToolCount"] == 11
    assert "world_markets_context" in mcp["capabilities"]
    assert card["access"]["public_world_markets_mcp_tool"] == (
        "world_markets_context")


def test_catalog_json_has_cross_origin_type_and_cache_headers():
    headers = (PUBLIC / "_headers").read_text()
    block = headers[headers.index("/money-markets/catalog.json"):]
    block = block[:block.index("\n\n")]
    assert "Access-Control-Allow-Origin: *" in block
    assert "Content-Type: application/json; charset=utf-8" in block
    assert "Cache-Control: public" in block


def test_robots_separates_retrieval_from_model_training():
    robots = (PUBLIC / "robots.txt").read_text()
    terms = (PUBLIC / "terms.html").read_text()
    assert "Content-Signal: search=yes, ai-input=yes, ai-train=no" in robots
    assert "cannot override Cloudflare AI Crawl Control or WAF" in robots
    assert "used for search or user-directed AI retrieval with attribution" in terms
    assert "Permission to use published pages for model training is not granted" in terms
    assert "AI input or training material" not in terms
    assert "does not grant model training" in dispatch_pages._LLMS_PREAMBLE
    for agent in (
        "OAI-SearchBot", "ChatGPT-User", "Claude-SearchBot", "Claude-User",
        "Googlebot", "GoogleOther", "Bingbot", "PerplexityBot",
        "Perplexity-User", "DuckAssistBot", "MistralAI-User",
    ):
        assert f"User-agent: {agent}\nAllow: /" in robots
    for agent in (
        "GPTBot", "ClaudeBot", "anthropic-ai", "Google-Extended", "CCBot",
        "meta-externalagent", "Applebot-Extended", "Amazonbot", "Bytespider",
        "cohere-ai",
    ):
        assert f"User-agent: {agent}\nDisallow: /" in robots
        assert f"User-agent: {agent}\nAllow: /" not in robots


def test_indexnow_urls_are_deterministic_deduplicated_and_same_host(tmp_path):
    module = _indexnow_module()
    dispatch_dir = tmp_path / "frontend" / "public" / "dispatches"
    article_dir = tmp_path / "frontend" / "public" / "articles"
    dispatch_dir.mkdir(parents=True)
    article_dir.mkdir(parents=True)
    (dispatch_dir / "index.json").write_text(json.dumps([
        {"slug": "2026-08-21-daily"},
        {"slug": "2026-08-20-daily"},
        {"slug": "2026-08-21-daily"},
    ]))
    (article_dir / "index.json").write_text(json.dumps([
        {"slug": "why-native-clocks-matter"},
        {"slug": "why-native-clocks-matter"},
    ]))

    first = module.build_urls(tmp_path)
    second = module.build_urls(tmp_path)
    assert first == second
    assert len(first) == len(set(first))
    assert first[:len(module.STATIC_PATHS)] == [
        f"https://seiche.info{path}" for path in module.STATIC_PATHS
    ]
    assert "https://seiche.info/money-markets/" in first
    assert "https://seiche.info/money-markets/catalog.json" in first
    assert "https://seiche.info/articles/why-native-clocks-matter/" in first
    assert "https://seiche.info/dispatches/2026-08-21-daily" in first
    assert all(urlparse(url).scheme == "https" and
               urlparse(url).netloc == "seiche.info" for url in first)
    assert not any("api.seiche.info" in url for url in first)


def test_indexnow_rejects_an_unsafe_public_slug(tmp_path):
    module = _indexnow_module()
    dispatch_dir = tmp_path / "frontend" / "public" / "dispatches"
    dispatch_dir.mkdir(parents=True)
    (dispatch_dir / "index.json").write_text(
        json.dumps([{"slug": "../api.seiche.info"}])
    )
    with pytest.raises(SystemExit, match="unsafe public slug"):
        module.build_urls(tmp_path)
