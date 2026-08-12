"""The long-form layer must stay discoverable, cross-linked and evidence-bounded."""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "frontend" / "public"
HUB = PUBLIC / "investigations" / "index.html"
STORY = PUBLIC / "investigations" / "the-282-billion-settlement-test" / "index.html"
sys.path.insert(0, str(ROOT / "backend"))

from seiche.dispatch_pages import render_llms_txt, render_sitemap  # noqa: E402


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
        assert "https://liquilens.in/investigations/" in page
        assert "https://liquilens-undertow.com/investigations/" in page


def test_manifest_and_share_assets_are_publication_ready():
    manifest = json.loads((PUBLIC / "investigations" / "index.json").read_text())
    assert manifest["publication_policy"] == "reviewed_longform"
    assert manifest["articles"][0]["editorial_status"] == "reviewed"
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
    assert "rsync -a --delete --exclude .git /tmp/site/ /tmp/cloudflare-site/" in workflow
    assert "pages deploy /tmp/cloudflare-site --project-name=seiche --branch=main" in workflow
