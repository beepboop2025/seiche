"""Contracts for Seiche's crawlable money/FX/capital evidence surface."""

from __future__ import annotations

import importlib.util
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from seiche import dispatch_pages, mcp_server

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "frontend" / "public"
MARKETS = PUBLIC / "markets"
INDEXNOW_SCRIPT = ROOT / "backend" / "scripts" / "ping_indexnow.py"
CATALOG_WORKFLOW = ROOT / ".github" / "workflows" / "deploy-liquilens-catalog.yml"
POLICY_WORKFLOW = ROOT / ".github" / "workflows" / "ai-retrieval-policy.yml"
INDEXNOW_SPEC = importlib.util.spec_from_file_location(
    "world_market_ping_indexnow", INDEXNOW_SCRIPT
)
assert INDEXNOW_SPEC is not None and INDEXNOW_SPEC.loader is not None
ping_indexnow = importlib.util.module_from_spec(INDEXNOW_SPEC)
sys.modules[INDEXNOW_SPEC.name] = ping_indexnow
INDEXNOW_SPEC.loader.exec_module(ping_indexnow)

FX_SERIES = {
    "DEXUSEU", "DEXUSUK", "DEXJPUS", "DEXUSAL", "DEXCAUS", "DEXSZUS",
    "DEXCHUS", "DEXINUS", "DEXKOUS", "DEXMXUS", "DEXBZUS", "DEXSFUS",
    "DEXUSNZ", "DEXDNUS", "DEXHKUS", "DEXMAUS", "DEXNOUS", "DEXSDUS",
    "DEXSIUS", "DEXTAUS", "DEXTHUS", "DEXSLUS",
}
DOLLAR_INDEXES = {"DTWEXBGS", "DTWEXAFEGS", "DTWEXEMEGS"}


def _page(relative: str) -> str:
    return (MARKETS / relative).read_text()


def _json_ld(page: str) -> dict:
    match = re.search(
        r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
        page,
        re.DOTALL,
    )
    assert match, "page has no JSON-LD"
    return json.loads(match.group(1))


def _json_lds(page: str) -> list[dict]:
    return [
        json.loads(raw)
        for raw in re.findall(
            r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
            page,
            re.DOTALL,
        )
    ]


def _visible_text(page: str) -> str:
    without_code = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1>", " ", page,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", without_code)).split())


def test_world_market_pages_are_real_canonical_dataset_surfaces():
    pages = {
        "index.html": "https://seiche.info/markets/",
        "forex/index.html": "https://seiche.info/markets/forex/",
        "capital-markets/index.html": (
            "https://seiche.info/markets/capital-markets/"
        ),
    }
    for relative, canonical in pages.items():
        page = _page(relative)
        assert f'<link rel="canonical" href="{canonical}">' in page
        assert 'meta http-equiv="refresh"' not in page.lower()
        graph = _json_ld(page)["@graph"]
        assert any(node.get("@type") in {"Dataset", "DataCatalog"}
                   for node in graph)
        assert "not investment advice" in page.lower()


def test_forex_page_names_the_complete_registered_reference_panel():
    page = _page("forex/index.html")
    ids = set(re.findall(r"fred\.stlouisfed\.org/series/([A-Z0-9]+)", page))
    assert ids == FX_SERIES | DOLLAR_INDEXES
    assert "22-currency ledger" in page
    assert "USD per local" in page and "Local per USD" in page
    for boundary in ("Intraday spot", "Forwards and swaps", "Options"):
        assert boundary in page
    assert "unified API normalizes" in page
    assert "local-currency units per US dollar" in page
    assert "not as a separate raw-value field" in page
    assert '<span class="status observed">observed</span>' not in page
    assert page.count('<span class="status structural">registered</span>') == 25


def test_public_mcp_examples_execute_with_the_external_section_argument(monkeypatch):
    examples = {
        "index.html": 'world_markets_context(section="summary")',
        "forex/index.html": 'world_markets_context(section="forex")',
        "capital-markets/index.html": (
            'world_markets_context(section="capital_markets")'
        ),
    }
    for relative, example in examples.items():
        page = _page(relative)
        assert example in page
        assert "world_markets_context(selector" not in page
        section = re.search(
            r'world_markets_context\(section="([a-z_]+)"\)', page
        ).group(1)
        monkeypatch.setattr(mcp_server, "_get_completed_snapshot", lambda: None)
        response = mcp_server.tool_world_markets({"section": section}, True)
        assert response["selection"] == section


def test_world_page_publishes_the_evidence_status_vocabulary():
    page = _page("index.html")
    for status in ("observed", "derived", "structural", "restricted", "unavailable"):
        assert status in page
    assert "seiche.world-markets.v1" in page
    assert "https://api.seiche.info/api/v2/world-markets" in page
    assert "cannot compel any search engine or AI provider" in page


def test_capital_page_separates_macro_coverage_from_security_level_gaps():
    page = _page("capital-markets/index.html")
    for covered in (
        "Sovereign rates", "Treasury supply", "Credit", "Equities and volatility",
        "Dealer balance sheet", "Futures positioning", "Transmission analytics",
    ):
        assert covered in page
    for gap in (
        "Global security master", "Global equities", "Rates and credit derivatives",
        "Options", "Funds and structured credit", "Order books and execution",
    ):
        assert gap in page
    assert "bounded derived macro-capital transmission projection" in page
    assert '<span class="status observed">observed</span>' not in page
    for mnemonic in ("DGS10", "HY_OAS", "VIX", "PD_FIN_TOT"):
        assert f"https://api.seiche.info/api/series/{mnemonic}" in page


def test_faq_structured_answers_are_visible_on_public_discovery_pages():
    pages = [
        _page("index.html"),
        _page("forex/index.html"),
        _page("capital-markets/index.html"),
        (PUBLIC / "use-cases.html").read_text(),
    ]
    for page in pages:
        visible = _visible_text(page)
        faqs = []
        for data in _json_lds(page):
            nodes = data.get("@graph", [data])
            faqs.extend(node for node in nodes if node.get("@type") == "FAQPage")
        assert faqs, "discovery page has no FAQPage"
        for faq in faqs:
            for item in faq["mainEntity"]:
                assert item["name"] in visible
                assert item["acceptedAnswer"]["text"] in visible


def test_homepage_graph_defines_the_organization_it_references():
    home = (ROOT / "frontend" / "index.html").read_text()
    graph = _json_ld(home)["@graph"]
    organization = next(node for node in graph if node.get("@type") == "Organization")
    assert organization["@id"] == "https://seiche.info/#organization"
    assert organization["url"] == "https://seiche.info/"
    software = next(node for node in graph if node.get("@type") == "SoftwareApplication")
    assert software["provider"] == {"@id": organization["@id"]}
    assert not any(node.get("@type") == "FAQPage" for node in graph)


def test_market_routes_are_in_every_static_discovery_queue():
    routes = {
        "/markets/",
        "/markets/forex/",
        "/markets/capital-markets/",
    }
    assert routes <= {path for path, _, _ in dispatch_pages.BASE_URLS}
    sitemap = (PUBLIC / "sitemap.xml").read_text()
    for route in routes:
        assert f"https://seiche.info{route}" in sitemap
        assert route in ping_indexnow.STATIC_PATHS
        assert f"https://seiche.info{route}" in dispatch_pages._LLMS_PREAMBLE
    smoke = (ROOT / "ops" / "deploy" / "external-smoke-routes.txt").read_text()
    assert '/api/v2/world-markets|200|application/json|"schema":' in smoke


def test_rendered_sitemap_keeps_editorial_market_page_dates():
    rendered = dispatch_pages.render_sitemap(
        [{"slug": "newer-daily", "date": "2026-09-01"}],
        [],
    )
    root = ET.fromstring(rendered)
    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    dates = {
        node.findtext("s:loc", namespaces=namespace): node.findtext(
            "s:lastmod", namespaces=namespace
        )
        for node in root.findall("s:url", namespace)
    }
    for route, lastmod in dispatch_pages.BASE_LASTMODS.items():
        assert dates[f"https://seiche.info{route}"] == lastmod
    assert dates["https://seiche.info/"] == "2026-09-01"


def test_machine_discovery_routes_world_markets_to_rest_and_mcp():
    card = json.loads((PUBLIC / "product-card.json").read_text())
    world = card["world_markets_v1"]
    assert world["schema"] == "seiche.world-markets.v1"
    assert world["forex_reference_series"] == 22
    assert world["request_time_collection"] is False
    assert card["access"]["public_world_markets_mcp_tool"] == (
        "world_markets_context"
    )

    catalog = json.loads(
        (PUBLIC / ".well-known" / "ai-catalog.json").read_text()
    )
    world_entry = next(
        entry for entry in catalog["entries"]
        if entry["identifier"].endswith(":world-markets-context")
    )
    assert world_entry["url"] == "https://api.seiche.info/api/v2/world-markets"
    assert world_entry["metadata"]["humanPage"] == "https://seiche.info/markets/"
    assert "Every response" not in card["product"]["description"]
    assert "world-markets contract" in card["product"]["description"]


def test_home_and_developer_surfaces_link_the_new_citation_layer():
    home = (ROOT / "frontend" / "index.html").read_text()
    developers = (PUBLIC / "developers.html").read_text()
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text()
    commands = (ROOT / "frontend" / "src" / "commands.ts").read_text()
    for route in (
        "/markets/", "/markets/forex/", "/markets/capital-markets/"
    ):
        assert route in home
        assert route in developers
    assert 'href="/markets/"' in app
    assert 'code: "WLD"' in commands and 'url: "/markets/"' in commands
    assert "eleven MCP tools" in developers
    assert "world_markets_context" in developers


def test_missing_routes_stay_missing_instead_of_becoming_soft_homepages():
    page = (PUBLIC / "404.html").read_text().lower()
    assert 'content="noindex,follow"' in page
    assert "http-equiv=\"refresh\"" not in page
    assert "rel=\"canonical\"" not in page
    assert "explicit missing page" in page


def test_ai_retrieval_permission_and_training_boundary_are_consistent():
    robots = (PUBLIC / "robots.txt").read_text()
    assert "Content-Signal: search=yes, ai-input=yes, ai-train=no" in robots
    assert "used as AI input for retrieval" in dispatch_pages._LLMS_PREAMBLE
    assert "does not grant model training" in dispatch_pages._LLMS_PREAMBLE

    feed = json.loads(dispatch_pages.render_article_json_feed(
        [{
            "slug": "market-clock-test",
            "headline": "Market clock test",
            "dek": "A bounded test article.",
            "date": "2026-08-21",
        }],
        {"market-clock-test": "Evidence."},
        {},
    ))
    authority = feed["items"][0]["_liquidity_lab"]["authority"]
    assert authority["training_allowed"] is False


def test_cloudflare_workflows_deploy_only_reviewed_state_and_fail_closed():
    catalog = CATALOG_WORKFLOW.read_text()
    assert "site_sha:" in catalog
    assert "ref: ${{ inputs.site_sha }}" in catalog
    assert "ref: main" not in catalog
    assert "persist-credentials: false" in catalog
    assert "test \"$(git rev-parse HEAD)\" = \"${SITE_SHA}\"" in catalog

    policy = POLICY_WORKFLOW.read_text()
    assert "for attempt in 1 2 3 4 5 6" in policy
    assert "Applied policy is not visible after bounded retries" in policy
    assert 'elif [ "${ACTION}" = "use-origin-robots" ]' in policy
    assert "exit 1" in policy
