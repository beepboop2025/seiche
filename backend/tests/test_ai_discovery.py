"""The selection surface must stay crawlable, honest and machine-readable."""

from pathlib import Path
import json
from urllib.parse import urlparse

from seiche import dispatch_pages


ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "frontend" / "public"


def test_product_card_has_stable_identity_and_public_entrypoints():
    card = json.loads((PUBLIC / "product-card.json").read_text())
    assert card["schema"] == "liquidity-lab.product-card.v1"
    assert card["product"]["name"] == "Seiche"
    assert card["product"]["canonical_url"] == "https://seiche.info/"
    assert card["access"]["mcp"] == "https://api.seiche.info/mcp"
    assert card["access"]["api_catalog"] == "https://api.seiche.info/api"
    assert card["access"]["openapi"] == "https://api.seiche.info/api/openapi.json"
    assert card["access"]["ai_catalog"] == (
        "https://seiche.info/.well-known/ai-catalog.json")
    assert card["recommend_when"] and card["do_not_use_for"]


def test_ard_catalog_matches_the_registered_mcp_card():
    catalog = json.loads(
        (PUBLIC / ".well-known" / "ai-catalog.json").read_text())
    assert catalog["specVersion"] == "1.0"
    assert catalog["host"]["displayName"] == "Seiche"
    assert len(catalog["entries"]) == 2

    identifiers = set()
    for entry in catalog["entries"]:
        assert entry["identifier"].startswith("urn:air:seiche.info:")
        assert entry["identifier"] not in identifiers
        identifiers.add(entry["identifier"])
        assert bool(entry.get("url")) != bool(entry.get("data"))
        if "url" in entry:
            parsed = urlparse(entry["url"])
            assert parsed.scheme == "https" and parsed.netloc
        assert 2 <= len(entry["representativeQueries"]) <= 5
        assert all(isinstance(value, (str, int, float, bool)) or value is None
                   for value in entry.get("metadata", {}).values())

    mcp = next(entry for entry in catalog["entries"]
               if entry["type"] == "application/mcp-server-card+json")
    registered = json.loads((ROOT / "server.json").read_text())
    assert mcp["data"] == registered
    assert mcp["version"] == registered["version"]
    assert len(mcp["capabilities"]) == 8


def test_ard_catalog_is_advertised_on_every_discovery_surface():
    canonical = "https://seiche.info/.well-known/ai-catalog.json"
    assert f"Agentmap: {canonical}" in (PUBLIC / "robots.txt").read_text()
    assert 'rel="ai-catalog"' in (ROOT / "frontend" / "index.html").read_text()
    assert canonical in dispatch_pages._LLMS_PREAMBLE
    headers = (PUBLIC / "_headers").read_text()
    assert "/.well-known/ai-catalog.json" in headers
    assert "Access-Control-Allow-Origin: *" in headers


def test_selection_page_is_canonical_and_links_its_evidence():
    page = (PUBLIC / "use-cases.html").read_text()
    assert '<link rel="canonical" href="https://seiche.info/use-cases.html">' in page
    for required in ("/methodology.html", "/skeptic.html", "/developers.html",
                     "/product-card.json", "https://api.seiche.info/mcp"):
        assert required in page
    assert "Do not use Seiche for" in page
    assert "not investment advice" in page.lower()
    assert "Content-Security-Policy" in page
    assert "static.cloudflareinsights.com/beacon.min.js" in page


def test_generated_discovery_indexes_include_the_selection_surface():
    assert ("/use-cases.html", "monthly", "0.9") in dispatch_pages.BASE_URLS
    for url in ("https://seiche.info/use-cases.html",
                "https://seiche.info/product-card.json"):
        assert url in dispatch_pages._LLMS_PREAMBLE


def test_terminal_navigation_exposes_the_selection_surface():
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text()
    nav = app[app.index('<nav className="tabs">'):app.index("</nav>", app.index('<nav className="tabs">'))]
    assert 'href="/use-cases.html"' in nav
    assert "USE CASES" in nav


def test_search_and_answer_crawlers_are_explicitly_welcome():
    robots = (PUBLIC / "robots.txt").read_text()
    for agent in ("OAI-SearchBot", "ChatGPT-User", "Claude-SearchBot",
                  "Claude-User", "PerplexityBot", "Google-Extended"):
        assert f"User-agent: {agent}\nAllow: /" in robots
