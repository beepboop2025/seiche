"""The selection surface must stay crawlable, honest and machine-readable."""

import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

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
    assert card["access"]["direct_ofr_dataset"] == (
        "https://seiche.info/datasets/direct-ofr/"
    )
    assert card["access"]["direct_ofr_dcat"] == (
        "https://seiche.info/datasets/direct-ofr/catalog.jsonld"
    )
    assert card["access"]["ai_catalog"] == (
        "https://seiche.info/.well-known/ai-catalog.json"
    )
    assert card["access"]["mcp_discovery"] == (
        "https://api.seiche.info/.well-known/mcp.json"
    )
    assert card["evidence"]["status_source"] == ("https://api.seiche.info/api/health")
    assert card["evidence"]["public_scoreboard"] == (
        "https://api.seiche.info/api/public"
    )
    assert card["evidence"]["status"] == "FINAL_VINTAGE_CONSTRUCTION_PIT"
    assert card["evidence"]["validated_backtest_eligible"] is False
    assert card["evidence"]["real_money_eligible"] is False
    assert card["recommend_when"] and card["do_not_use_for"]


def test_ard_catalog_matches_the_registered_mcp_card():
    catalog = json.loads((PUBLIC / ".well-known" / "ai-catalog.json").read_text())
    assert catalog["specVersion"] == "1.0"
    assert catalog["host"]["displayName"] == "Seiche"
    assert len(catalog["entries"]) == 7

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
        assert all(
            isinstance(value, (str, int, float, bool)) or value is None
            for value in entry.get("metadata", {}).values()
        )

    mcp = next(
        entry
        for entry in catalog["entries"]
        if entry["identifier"] == "urn:air:seiche.info:mcp:funding-stress"
    )
    assert mcp["type"] == "application/json"
    registered = json.loads((ROOT / "server.json").read_text())
    assert mcp["data"] == registered
    assert mcp["version"] == registered["version"]
    assert len(mcp["capabilities"]) == 11
    assert mcp["prompts"] == [
        "is_now_dangerous",
        "money_market_deep_dive",
        "world_markets_briefing",
        "cross_market_cash_pressure",
    ]
    assert mcp["resourceTemplates"] == []
    assert mcp["metadata"]["schemaProfile"] == (
        "MCP Registry 2025-12-11 server metadata"
    )
    assert mcp["metadata"]["experimentalServerCardConformance"] is False
    assert mcp["metadata"]["publicToolCount"] == len(mcp["capabilities"])
    assert mcp["metadata"]["publicPromptCount"] == len(mcp["prompts"])
    assert mcp["metadata"]["publicResourceCount"] == len(mcp["resourceTemplates"])
    assert mcp["metadata"]["mcpDiscovery"] == (
        "https://api.seiche.info/.well-known/mcp.json"
    )
    assert "latest_article" in mcp["capabilities"]
    assert "money_market_context" in mcp["capabilities"]
    assert "world_markets_context" in mcp["capabilities"]
    corpus_mcp = next(
        entry
        for entry in catalog["entries"]
        if entry["identifier"] == "urn:air:seiche.info:mcp:market-corpus"
    )
    assert corpus_mcp["data"]["remotes"] == [
        {
            "type": "streamable-http",
            "url": "https://api.seiche.info/api/v2/corpus/mcp",
        }
    ]
    assert "repository" not in corpus_mcp["data"]
    assert "bis_records" in corpus_mcp["capabilities"]
    assert "bis_flow_manifest" in corpus_mcp["capabilities"]
    assert "inspect_dataset" in corpus_mcp["capabilities"]
    assert "corpus_health" in corpus_mcp["capabilities"]
    assert len(corpus_mcp["capabilities"]) == 9
    assert corpus_mcp["metadata"]["requestTimeMonolithScan"] is False
    assert corpus_mcp["metadata"]["availabilityClaim"] == (
        "declared_endpoint_verify_with_corpus_health"
    )
    corpus_claims = json.dumps(corpus_mcp).lower()
    assert corpus_mcp.get("status") not in {"active", "live"}
    assert corpus_mcp["metadata"].get("status") not in {"active", "live"}
    assert "live gateway" not in corpus_claims
    assert corpus_mcp["metadata"]["publicToolCount"] == len(
        corpus_mcp["capabilities"]
    )
    world = next(
        entry
        for entry in catalog["entries"]
        if entry["identifier"].endswith(":world-markets-context")
    )
    assert world["url"] == "https://api.seiche.info/api/v2/world-markets"
    assert world["metadata"]["humanPage"] == "https://seiche.info/markets/"
    assert world["metadata"]["forexReferenceSeries"] == 22
    dataset = next(
        entry
        for entry in catalog["entries"]
        if entry["identifier"] == "urn:air:seiche.info:dataset:direct-ofr"
    )
    assert dataset["type"] == "application/ld+json"
    assert dataset["url"] == ("https://seiche.info/datasets/direct-ofr/catalog.jsonld")
    assert dataset["metadata"] == {
        "authentication": "none",
        "access": "public-read-only",
        "humanPage": "https://seiche.info/datasets/direct-ofr/",
        "publicationStatus": "draft_not_submitted",
        "doiAssigned": False,
        "seriesCount": 10,
        "recordCount": 11163,
        "distributionCount": 2,
    }
    assert catalog["host"]["documentationUrl"] == ("https://seiche.info/developers")
    assert all(".html" not in json.dumps(entry) for entry in catalog["entries"])


def test_security_txt_is_present_and_not_stale():
    text = (PUBLIC / ".well-known" / "security.txt").read_text()
    assert (
        "Contact: https://github.com/beepboop2025/seiche/security/advisories/new"
        in text
    )
    assert "Canonical: https://seiche.info/.well-known/security.txt" in text
    assert "Expires: 2027-08-19T00:00:00.000Z" in text
    assert "—" not in text
    assert "–" not in text
    headers = (PUBLIC / "_headers").read_text()
    assert "/.well-known/security.txt" in headers
    assert "text/plain" in headers


def test_security_txt_is_served_on_the_well_known_route():
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from fastapi.testclient import TestClient

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
    index = (ROOT / "frontend" / "index.html").read_text()
    assert (
        '<link rel="ai-catalog" type="application/json" '
        'href="/.well-known/ai-catalog.json" />'
    ) in index
    assert canonical in dispatch_pages._LLMS_PREAMBLE
    headers = (PUBLIC / "_headers").read_text()
    assert "/.well-known/ai-catalog.json" in headers
    assert "Access-Control-Allow-Origin: *" in headers


def test_selection_page_is_canonical_and_links_its_evidence():
    page = (PUBLIC / "use-cases.html").read_text()
    assert '<link rel="canonical" href="https://seiche.info/use-cases">' in page
    for required in (
        "/methodology",
        "/skeptic",
        "/developers",
        "/product-card.json",
        "https://api.seiche.info/mcp",
    ):
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
    assert ("/money-markets/", "weekly", "0.9") in dispatch_pages.BASE_URLS
    assert ("/datasets/direct-ofr/", "monthly", "0.8") in dispatch_pages.BASE_URLS
    for url in (
        "https://seiche.info/use-cases",
        "https://seiche.info/product-card.json",
        "https://seiche.info/money-markets/",
        "https://seiche.info/money-markets/catalog.json",
        "https://seiche.info/#corpus",
        "https://api.seiche.info/api/v2/corpus",
        "https://seiche.info/datasets/direct-ofr/",
        "https://seiche.info/datasets/direct-ofr/catalog.jsonld",
    ):
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
        assert (
            f'<link rel="canonical" href="{canonical}">' in (PUBLIC / name).read_text()
        )


def test_terminal_navigation_exposes_the_selection_surface():
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text()
    nav = app[
        app.index('<nav className="tabs">') : app.index(
            "</nav>", app.index('<nav className="tabs">')
        )
    ]
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
    for agent in (
        "OAI-SearchBot",
        "ChatGPT-User",
        "Claude-SearchBot",
        "Claude-User",
        "PerplexityBot",
        "Perplexity-User",
        "Googlebot",
        "Bingbot",
    ):
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
    text = "\n".join(
        [
            *(path.read_text() for path in paths),
            dispatch_pages._LLMS_PREAMBLE,
        ]
    )
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
    failure_branch = proof[
        proof.index("if (!bt.ok)") : proof.index(
            "const cap", proof.index("if (!bt.ok)")
        )
    ]
    assert "historical diagnostic withheld" in failure_branch
    assert "ENGINE DOWN" not in failure_branch


def test_financial_evidence_router_is_external_pinned_and_china_complete():
    revision = "34549a5bcc2a42c7760c04c95bd449f1d10a18fc"
    catalog = json.loads((PUBLIC / ".well-known" / "ai-catalog.json").read_text())
    router = next(
        entry
        for entry in catalog["entries"]
        if entry["identifier"] == "urn:air:seiche.info:workflow:financial-evidence"
    )
    assert router["version"] == revision
    assert router["url"] == (
        "https://raw.githubusercontent.com/beepboop2025/"
        f"financial-evidence-skills/{revision}/financial-evidence/SKILL.md"
    )
    assert router["metadata"]["skillSha256"] == (
        "sha256:091fc3c3bb4577e2481cfc52e9977e1c83fc114b085544c584b6ae5bf2b302ae"
    )
    assert router["metadata"]["fetcherSha256"] == (
        "sha256:79ad7a9c269ceefc86a77f98e3ed827a39e66a0d45a95535ce761fd4936a3ff2"
    )
    assert ".agents/skills/financial-evidence" not in json.dumps(catalog)

    card = json.loads((PUBLIC / "product-card.json").read_text())
    assert card["updated"] == "2026-08-24"
    assert "financial-evidence-skills" in card["access"]["financial_evidence_skill"]

    china = (PUBLIC / "use-cases" / "china-economy-evidence" / "index.html").read_text()
    assert "revision-safe public economic observations" in china
    assert "Far Basin model-entry gate" in china
    assert "never enters Seiche's market composite or model features" not in china
