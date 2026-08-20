"""The selection surface must stay crawlable, honest and machine-readable."""

from pathlib import Path
import json
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

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
    assert card["evidence"]["status_source"] == (
        "https://api.seiche.info/api/health")
    assert card["evidence"]["public_scoreboard"] == (
        "https://api.seiche.info/api/public")
    assert card["evidence"]["status"] == "FINAL_VINTAGE_CONSTRUCTION_PIT"
    assert card["evidence"]["validated_backtest_eligible"] is False
    assert card["evidence"]["real_money_eligible"] is False
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
    assert len(mcp["capabilities"]) == 9
    assert "latest_article" in mcp["capabilities"]
    assert catalog["host"]["documentationUrl"] == (
        "https://seiche.info/developers")
    assert all(".html" not in json.dumps(entry)
               for entry in catalog["entries"])


def test_security_txt_is_present_and_not_stale():
    text = (PUBLIC / ".well-known" / "security.txt").read_text()
    assert "Contact: https://github.com/beepboop2025/seiche/security/advisories/new" in text
    assert "Canonical: https://seiche.info/.well-known/security.txt" in text
    assert "Expires: 2027-08-19T00:00:00.000Z" in text
    assert "—" not in text
    assert "–" not in text
    headers = (PUBLIC / "_headers").read_text()
    assert "/.well-known/security.txt" in headers
    assert "text/plain" in headers


def test_security_txt_is_served_on_the_well_known_route():
    static_site = FastAPI()
    static_site.mount("/", StaticFiles(directory=PUBLIC), name="public")

    with TestClient(static_site) as client:
        response = client.get("/.well-known/security.txt")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text.startswith("Contact: https://github.com/")
    assert "Canonical: https://seiche.info/.well-known/security.txt" in response.text


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
    assert '<link rel="canonical" href="https://seiche.info/use-cases">' in page
    for required in ("/methodology", "/skeptic", "/developers",
                     "/product-card.json", "https://api.seiche.info/mcp"):
        assert required in page
    assert "Do not use Seiche for" in page
    assert "not investment advice" in page.lower()
    assert "Content-Security-Policy" in page
    assert "static.cloudflareinsights.com/beacon.min.js" in page


def test_developer_activation_converts_to_attributed_ongoing_delivery():
    page = (PUBLIC / "developers.html").read_text()
    script = (PUBLIC / "developers.js").read_text()

    assert "https://t.me/LiquidityLabDesk" in page
    assert "https://t.me/seiche_desk_bot?start=agent_developers" in page
    assert "11:30 UTC" in page
    assert 'id="toolHandoff" hidden' in page
    assert "delete value.delivery" in script
    assert 'getElementById("toolHandoff").hidden = false' in script


def test_generated_discovery_indexes_include_the_selection_surface():
    assert ("/use-cases", "monthly", "0.9") in dispatch_pages.BASE_URLS
    for url in ("https://seiche.info/use-cases",
                "https://seiche.info/product-card.json"):
        assert url in dispatch_pages._LLMS_PREAMBLE
    assert "https://seiche.info/articles/feed.json" in dispatch_pages._LLMS_PREAMBLE


def test_home_exposes_the_pre_open_daily_letter():
    today = (ROOT / "frontend" / "src" / "tabs" / "Today.tsx").read_text()

    assert "https://t.me/LiquidityLabDesk" in today
    assert "Get the 11:30 UTC daily letter" in today
    assert "Free channel. Public data. Misses kept." in today
    assert 'target="_blank"' in today
    assert 'rel="noopener noreferrer"' in today


def test_clean_public_urls_match_cloudflare_redirect_targets():
    for path, _, _ in dispatch_pages.BASE_URLS:
        assert not path.endswith(".html")
    for name, canonical in (
        ("developers.html", "https://seiche.info/developers"),
        ("guide.html", "https://seiche.info/guide"),
        ("privacy.html", "https://seiche.info/privacy"),
        ("support.html", "https://seiche.info/support"),
        ("terms.html", "https://seiche.info/terms"),
        ("use-cases.html", "https://seiche.info/use-cases"),
    ):
        assert f'<link rel="canonical" href="{canonical}">' in (PUBLIC / name).read_text()


def test_terminal_navigation_exposes_the_selection_surface():
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text()
    nav = app[app.index('<nav className="tabs">'):app.index("</nav>", app.index('<nav className="tabs">'))]
    assert 'href="/use-cases"' in nav
    assert "USE CASES" in nav
    commands = (ROOT / "frontend" / "src" / "commands.ts").read_text()
    assert 'url: "/guide"' in commands
    assert 'url: "/support"' in commands


def test_contextual_product_network_is_visible_and_machine_readable():
    hub = "https://myquantdoesntspeakenglish.com/"
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text()
    card = json.loads((PUBLIC / "product-card.json").read_text())
    assert hub in app
    assert hub in dispatch_pages._LLMS_PREAMBLE
    assert any(sibling["url"] == hub for sibling in card["siblings"])


def test_search_and_answer_crawlers_are_explicitly_welcome():
    robots = (PUBLIC / "robots.txt").read_text()
    for agent in ("OAI-SearchBot", "ChatGPT-User", "Claude-SearchBot",
                  "Claude-User", "PerplexityBot", "Perplexity-User",
                  "Google-Extended", "Googlebot", "Bingbot"):
        assert f"User-agent: {agent}\nAllow: /" in robots


def test_public_historical_copy_keeps_the_vintage_boundary_attached():
    """A dated reconstruction must never read like an archived publication.

    The forward PIT ledger is genuinely as-published. Time Machine and PROOF
    are a different evidence class: chronological transforms over final/current
    source vintages. This checks the canonical page generators and interactive
    copy before ignored build artifacts exist, so either deployment path fails
    if the distinction is lost.
    """
    paths = [
        PUBLIC / "guide.html",
        ROOT / "backend" / "seiche" / "methodology.py",
        ROOT / "backend" / "seiche" / "skeptic.py",
        ROOT / "frontend" / "src" / "tabs" / "TimeMachine.tsx",
        ROOT / "frontend" / "src" / "tabs" / "Proof.tsx",
        ROOT / "frontend" / "src" / "Wrecks.tsx",
    ]
    text = "\n".join([
        *(path.read_text() for path in paths),
        dispatch_pages._LLMS_PREAMBLE,
    ])
    lowered = text.lower()
    for stale_claim in (
        "replay the board as it stood",
        "whole board replayed as it stood",
        "the point-in-time proof",
        "the backtest record",
        "the value on any date uses only data available on that date",
    ):
        assert stale_claim not in lowered, stale_claim
    assert "final/current-vintage" in lowered
    assert "construction-pit" in lowered
    assert "not validated-backtest evidence" in lowered


def test_proof_failure_is_labeled_as_withheld_evidence_not_engine_failure():
    proof = (ROOT / "frontend" / "src" / "tabs" / "Proof.tsx").read_text()
    failure_branch = proof[proof.index("if (!bt.ok)"):proof.index("const cap", proof.index("if (!bt.ok)"))]
    assert "historical diagnostic withheld" in failure_branch
    assert "ENGINE DOWN" not in failure_branch
