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
    assert "group: publish-static" in workflow
    assert "cancel-in-progress: true" in workflow
    for build_input in (
        "backend/seiche/dispatch_daily.py",
        "backend/seiche/dispatch_pages.py",
        "backend/seiche/evidence_boundary.py",
        "backend/seiche/newsroom.py",
        "backend/seiche/prerender.py",
        "backend/seiche/public_view.py",
        "backend/seiche/social_cards.py",
        "frontend/index.html",
        "frontend/package.json",
        "frontend/package-lock.json",
        "frontend/src/**",
        "frontend/tsconfig.json",
        "frontend/vite.config.ts",
        "ops/requirements-social-cards.txt",
    ):
        assert f'- "{build_input}"' in workflow
    assert "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020" in workflow
    assert 'node-version: "20"' in workflow
    assert "cache-dependency-path: frontend/package-lock.json" in workflow
    assert "working-directory: frontend" in workflow
    assert "npm ci" in workflow
    assert "npm test" in workflow
    assert "npm run build" in workflow
    renderer_install = workflow.index("Install hash-locked root-card renderer")
    assert renderer_install < workflow.index("Push static files to the live site repo")
    assert "--only-binary=:all:" in workflow[renderer_install:]
    assert "--require-hashes" in workflow[renderer_install:]
    assert "ops/requirements-social-cards.txt" in workflow[renderer_install:]
    assert "pip install -e" not in workflow
    assert workflow.index("Test and build exact-head frontend") < workflow.index(
        "Push static files to the live site repo"
    )
    for generated in (
        "/data/",
        "/dispatches/",
        "/articles/",
        "/share/",
        "/views/",
        "/sitemap.xml",
        "/llms.txt",
        "/llms-full.txt",
        "/methodology.html",
        "/skeptic.html",
        "/ampleness.html",
        "/referee.html",
    ):
        assert f"--exclude {generated}" in workflow
    for alias in (
        "/markets/index.html",
        "/markets/forex/index.html",
        "/markets/capital-markets/index.html",
        "/markets/china-macro/index.html",
        "/money-markets/index.html",
    ):
        assert f"--exclude {alias}" in workflow
    workflow_lines = {line.strip() for line in workflow.splitlines()}
    assert "--exclude /markets/ \\" not in workflow_lines
    assert "--exclude /money-markets/ \\" not in workflow_lines
    assert "frontend/public frontend/dist /tmp/site" in workflow
    assert "path.is_symlink()" in workflow
    assert '".git" in path.relative_to(root).parts' in workflow
    dist_site_writes = [
        line.strip()
        for line in workflow.splitlines()
        if "frontend/dist" in line
        and "/tmp/site" in line
        and line.strip().startswith(("install ", "rsync "))
    ]
    assert dist_site_writes == [
        "install -m 0644 frontend/dist/index.html /tmp/site/index.html",
        "rsync -a --safe-links frontend/dist/assets/ /tmp/site/assets/",
    ]
    assert "from seiche import prerender" in workflow
    assert "prerender.build(Path(sys.argv[2]))" in workflow
    assert "from seiche import social_cards" in workflow
    assert "social_cards.refresh_root(Path(sys.argv[2]))" in workflow
    assert "social_cards.build(" not in workflow
    assert "rsync -a frontend/dist/ /tmp/site/" not in workflow
    assert "rsync -a --delete frontend/dist/ /tmp/site/" not in workflow
    assert "rm -rf /tmp/site" not in workflow
    assert "test ! -L /tmp/site/index.html" in workflow
    assert "test ! -L /tmp/site/assets" in workflow
    assert "rsync -a +" not in workflow
    assert "git pull" not in workflow
    assert "git rebase" not in workflow
    assert workflow.index("Stale static publish") < workflow.index(
        "git push -q origin main"
    )
    assert workflow.count('current_main" != "$GITHUB_SHA"') == 2
    assert 'echo "site-sha=$prepared_site_sha" >> "$GITHUB_OUTPUT"' in workflow
    assert "Site mirror compare-and-swap lost" in workflow
    assert "EXPECTED_SITE_SHA: ${{ steps.publish-site.outputs.site-sha }}" in workflow
    assert "Site mirror changed before deploy" in workflow
    assert workflow.index("Stale static deploy") < workflow.index(
        "Deploy static fast path to Cloudflare Pages"
    )
    assert 'echo "nothing to update"; exit 0' not in workflow
    assert (
        "rsync -a --delete --exclude .git /tmp/site/ /tmp/cloudflare-site/" in workflow
    )
    assert (
        "pages deploy /tmp/cloudflare-site --project-name=seiche --branch=main"
        in workflow
    )
    assert "continue-on-error: true" not in workflow
    assert "verify_public_dataset.py" in workflow
    assert "--expected-root /tmp/cloudflare-site" in workflow
