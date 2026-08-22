"""The long-form layer must stay discoverable, cross-linked and evidence-bounded."""

from __future__ import annotations

import json
import struct
import sys
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "frontend" / "public"
HUB = PUBLIC / "investigations" / "index.html"
STORY = PUBLIC / "investigations" / "the-282-billion-settlement-test" / "index.html"
sys.path.insert(0, str(ROOT / "backend"))

from seiche.dispatch_pages import render_llms_txt, render_sitemap


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.hrefs.extend(value for name, value in attrs if name == "href" and value)


def _hrefs(document: str) -> list[str]:
    parser = _LinkCollector()
    parser.feed(document)
    return parser.hrefs


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_investigation_has_article_metadata_and_evidence_sections():
    page = STORY.read_text()
    for required in (
        'rel="canonical"', 'property="og:image"', '"@type":"AnalysisNewsArticle"',
        "THE STRONGEST COUNTER-CASE", "WHAT CHANGES OUR MIND", "Sources and method",
        "This is a scenario, not a forecasted fact.", "not investment advice",
    ):
        assert required in page


def test_network_links_are_bidirectional_destinations():
    for page in (HUB.read_text(), STORY.read_text()):
        links = _hrefs(page)
        assert links.count(
            "https://liquilens.in/investigations/"
            "the-5-64x-private-credit-concentration/"
        ) == 1
        assert links.count(
            "https://liquilens-undertow.com/investigations/"
            "eight-blanks-are-not-eight-green-lights/"
        ) == 1


def test_articles_are_the_public_front_door_without_hiding_evidence():
    hub = HUB.read_text()
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text()
    assert "SEICHE / ARTICLES" in hub
    assert 'href="/dispatches/"' in hub
    assert _hrefs(hub).count("https://myquantdoesntspeakenglish.com/") == 2
    assert "The board checks new evidence six times a day" in hub
    assert "If coverage cannot support a verdict, Seiche abstains" in hub
    assert "ARTICLES" in app[app.index('<nav className="tabs">'):app.index("</nav>", app.index('<nav className="tabs">'))]


def test_manifest_and_share_assets_are_publication_ready():
    manifest = json.loads((PUBLIC / "investigations" / "index.json").read_text())
    assert manifest["publication_policy"] == "reviewed_longform"
    article = manifest["articles"][0]
    assert article["editorial_status"] == "reviewed"
    assert article["article_type"] == "investigation"
    assert article["publication_status"] == "PUBLISHED"
    assert article["canonical_url"].startswith("https://seiche.info/investigations/")
    assert article["dek"] and article["limitations"]
    event_time = datetime.fromisoformat(article["clocks"]["event_time"])
    knowledge_time = datetime.fromisoformat(article["clocks"]["knowledge_time"])
    published_at = datetime.fromisoformat(article["published_at"])
    assert event_time <= knowledge_time <= published_at
    assert article["corrections"][0]["fields"] == [
        "published_at", "modified_at", "clocks.knowledge_time"]
    assert article["original_contribution"]["kinds"] == [
        "dated_forward_test", "cross_signal_divergence"]
    asset_dir = STORY.parent
    assert _png_size(asset_dir / "reserve-path.png") == (1600, 900)
    assert _png_size(asset_dir / "share.png") == (1200, 630)


def test_generated_discovery_keeps_the_investigation_urls():
    entries = [{"slug": "example", "date": "2026-08-12", "title": "Example", "summary": "Example", "tag": "EROSION"}]
    sitemap = render_sitemap(entries)
    llms = render_llms_txt(entries)
    assert "https://seiche.info/investigations/" in sitemap
    assert "the-282-billion-settlement-test" in sitemap
    assert "Reviewed investigations" in llms


def test_static_fast_path_reaches_the_canonical_cloudflare_origin():
    workflow = (ROOT / ".github" / "workflows" / "publish-static.yml").read_text()
    assert "workflow_dispatch:" in workflow
    assert "--exclude data/" in workflow
    for generated in (
        "dispatches/",
        "sitemap.xml",
        "llms.txt",
        "llms-full.txt",
        "methodology.html",
        "skeptic.html",
        "ampleness.html",
        "referee.html",
    ):
        assert f"--exclude {generated}" in workflow
    assert "rsync -a +" not in workflow
    assert 'echo "nothing to update"; exit 0' not in workflow
    assert "rsync -a --delete --exclude .git /tmp/site/ /tmp/cloudflare-site/" in workflow
    assert "pages deploy /tmp/cloudflare-site --project-name=seiche --branch=main" in workflow
    assert "continue-on-error: true" not in workflow
    assert "verify_public_dataset.py" in workflow
    assert "--expected-root /tmp/cloudflare-site" in workflow
