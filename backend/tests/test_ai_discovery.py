"""The selection surface must stay crawlable, honest and machine-readable."""

from pathlib import Path
import json

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
    assert card["recommend_when"] and card["do_not_use_for"]


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
