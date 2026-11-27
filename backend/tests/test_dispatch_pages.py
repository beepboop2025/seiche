"""The static dispatch pages: every letter becomes a real URL with escaped
content, correct canonicals and a sitemap entry. The generator must fail loud
on a broken index and never let markup or scripts leak through unescaped."""

import json

import pytest

from seiche.dispatch_daily import build_dispatch, write_dispatch
from seiche.dispatch_pages import build_all, md_to_html, render_letter_page, render_sitemap


@pytest.fixture
def repo(tmp_path, fake_snap):
    """A tmp repo with one real dispatch written by the real generator."""
    d = build_dispatch(fake_snap, prev_value=38.0)
    write_dispatch(d, repo_root=tmp_path)
    return tmp_path, d


def test_md_subset_and_tables():
    src = "## Head\n\nA **bold** *em* `code` [link](https://x.y).\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    out = md_to_html(src)
    assert "<h2>Head</h2>" in out
    assert "<strong>bold</strong>" in out and "<em>em</em>" in out
    assert '<a href="https://x.y">link</a>' in out
    assert "<table>" in out and "<th>a</th>" in out and "<td>2</td>" in out


def test_md_escapes_html_and_bad_schemes():
    out = md_to_html('<script>alert(1)</script> [x](javascript:evil())')
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert 'href="#"' in out  # javascript: scheme refused


def test_letter_page_carries_canonical_jsonld_and_both_halves(repo):
    root, d = repo
    pages = build_all(repo_root=root)
    html_path = root / "frontend" / "public" / "dispatches" / f"{d['slug']}.html"
    assert str(html_path) in pages
    page = html_path.read_text()
    assert f'<link rel="canonical" href="https://seiche.info/dispatches/{d["slug"]}">' in page
    assert '"@type": "Article"' in page and d["date"] in page
    # the free reading and the desk's forward read are both in the static page
    assert "EROSION" in page
    assert "forward read" in page
    # neither marker generation ever leaks into the rendered page
    assert "HAS-PAID" not in page and "HAS-DESK" not in page


def test_pre_rename_history_renders_both_halves(repo):
    """The box's published archive predates the desk rename: letters carrying
    the HAS-PAID marker with a .paid.md continuation must keep rendering whole.
    History is never rewritten to fit a rename; readers accept both names."""
    root, d = repo
    free_dir = root / "frontend" / "public" / "dispatches"
    desk_dir = root / "backend" / "seiche" / "dispatches"
    index = json.loads((free_dir / "index.json").read_text())
    index.append({"slug": "2026-07-01-daily", "title": "legacy letter",
                  "date": "2026-07-01", "tag": "EROSION", "summary": "old naming"})
    (free_dir / "index.json").write_text(json.dumps(index))
    (free_dir / "2026-07-01-daily.md").write_text(
        "## The reading\n\nlegacy free half\n\n<!--HAS-PAID-->\n")
    (desk_dir / "2026-07-01-daily.paid.md").write_text("## Desk\n\nlegacy desk half\n")
    build_all(repo_root=root)
    page = (free_dir / "2026-07-01-daily.html").read_text()
    assert "legacy free half" in page
    assert "legacy desk half" in page
    assert "HAS-PAID" not in page and "HAS-DESK" not in page


def test_archive_lists_every_letter(repo):
    root, d = repo
    build_all(repo_root=root)
    archive = (root / "frontend" / "public" / "dispatches" / "index.html").read_text()
    assert f'href="/dispatches/{d["slug"]}"' in archive
    assert '<link rel="canonical" href="https://seiche.info/dispatches/">' in archive


def test_sitemap_has_base_pages_and_letters(repo):
    root, d = repo
    build_all(repo_root=root)
    sm = (root / "frontend" / "public" / "sitemap.xml").read_text()
    for loc in ("https://seiche.info/", "https://seiche.info/guide",
                "https://seiche.info/gift-city/",
                "https://seiche.info/agents/quant/",
                "https://seiche.info/dispatches/",
                f"https://seiche.info/dispatches/{d['slug']}"):
        assert f"<loc>{loc}</loc>" in sm
    assert f"<lastmod>{d['date']}</lastmod>" in sm
    from xml.etree import ElementTree
    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    gift = next(node for node in ElementTree.fromstring(sm).findall("s:url", namespace)
                if node.findtext("s:loc", namespaces=namespace) == "https://seiche.info/gift-city/")
    # Nothing in this tree dates the GIFT City page, so the sitemap states no
    # date for it rather than borrowing the letter's.
    assert gift.findtext("s:lastmod", namespaces=namespace) is None
    assert gift.findtext("s:changefreq", namespaces=namespace) == "monthly"


SITEMAP_NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def _sitemap_dates(sitemap: str) -> dict:
    from xml.etree import ElementTree
    return {
        node.findtext("s:loc", namespaces=SITEMAP_NS).removeprefix("https://seiche.info"):
            node.findtext("s:lastmod", namespaces=SITEMAP_NS)
        for node in ElementTree.fromstring(sitemap).findall("s:url", SITEMAP_NS)
    }


def _jsonld_page(path, *entities):
    path.parent.mkdir(parents=True, exist_ok=True)
    blocks = "".join(
        f'<script type="application/ld+json">{entity}</script>' for entity in entities)
    path.write_text(f"<!doctype html><html><head>{blocks}</head><body>page</body></html>")


def test_sitemap_dates_come_from_each_page_artifact(tmp_path):
    """Every base-page date is one an artifact states: the snapshot for the
    board pages, the page's own JSON-LD for static pages, each archive's own
    newest row. No page borrows the newest letter's date."""
    public = tmp_path / "public"
    (public / "data").mkdir(parents=True)
    (public / "data" / "overview.json").write_text(
        json.dumps({"generated_at": "2026-11-26T15:17:02+00:00"}))
    _jsonld_page(
        public / "use-cases" / "money-market-research" / "index.html",
        json.dumps({"@context": "https://schema.org", "@graph": [
            {"@type": "Organization", "name": "Seiche"},
            {"@type": "WebPage", "datePublished": "2026-08-24", "dateModified": "2026-09-05"},
        ]}),
    )
    # datePublished stands in when a page declares no modification date.
    _jsonld_page(public / "use-cases.html",
                 json.dumps({"@type": "WebPage", "datePublished": "2026-11-19"}))
    # A date-time keeps the calendar date the page itself states.
    _jsonld_page(
        public / "investigations" / "the-282-billion-settlement-test" / "index.html",
        json.dumps({"@type": "AnalysisNewsArticle",
                    "datePublished": "2026-08-12T16:04:29+05:30",
                    "dateModified": "2026-08-14T00:58:39+05:30"}),
    )
    # A hub never takes a date from an entity nested inside its own.
    _jsonld_page(public / "markets" / "index.html", json.dumps({
        "@type": "DataCatalog", "dateModified": "2026-08-22",
        "dataset": [{"@type": "Dataset", "dateModified": "2026-11-01"}]}))
    # Unparsable structured data and invalid dates state nothing.
    _jsonld_page(public / "guide.html", "{not json")
    _jsonld_page(public / "gift-city" / "index.html",
                 json.dumps({"@type": "WebPage", "dateModified": "soon"}))
    entries = [{"slug": "2026-09-24-daily", "date": "2026-09-24"},
               {"slug": "2026-09-15-daily", "date": "2026-09-15"}]
    articles = [{"slug": "2026-09-13-article", "date": "2026-09-13"}]

    dates = _sitemap_dates(render_sitemap(entries, articles, public_dir=public))

    assert dates["/"] == "2026-11-26"
    assert dates["/ampleness"] == "2026-11-26"
    assert dates["/use-cases/money-market-research/"] == "2026-09-05"
    assert dates["/use-cases"] == "2026-11-19"
    assert dates["/investigations/the-282-billion-settlement-test/"] == "2026-08-14"
    assert dates["/markets/"] == "2026-08-22"
    assert dates["/guide"] is None
    assert dates["/gift-city/"] is None
    assert dates["/dispatches/"] == "2026-09-24"
    assert dates["/articles/"] == "2026-09-13"
    # Pages rendered later in the pipeline that declare nothing get no date.
    assert dates["/referee"] is None and dates["/methodology"] is None
    assert dates["/dispatches/2026-09-24-daily"] == "2026-09-24"


def test_board_pages_never_borrow_the_newest_letter_date(repo):
    """Without a sealed snapshot the homepage and the ampleness page carry no
    date: the archive's last letter says nothing about the live board."""
    root, d = repo
    build_all(repo_root=root)
    dates = _sitemap_dates((root / "frontend" / "public" / "sitemap.xml").read_text())
    assert dates["/dispatches/"] == d["date"]
    assert dates["/"] is None
    assert dates["/ampleness"] is None
    assert dates["/referee"] is None


def test_corrupt_snapshot_fails_loud(tmp_path):
    public = tmp_path / "public"
    (public / "data").mkdir(parents=True)
    (public / "data" / "overview.json").write_text("{truncated")
    with pytest.raises(ValueError):
        render_sitemap([], [], public_dir=public)


def test_committed_pages_and_rendered_sitemap_state_the_same_dates():
    """Property over every committed base page: the rendered sitemap repeats
    the newest dateModified (else datePublished) the page declares, or states
    no date when the page declares none. Read with a plain regex here, not
    the generator's parser, so the two must agree independently."""
    import re
    from seiche.dispatch_pages import BASE_URLS, REPO_ROOT, SNAPSHOT_ROUTES

    public = REPO_ROOT / "frontend" / "public"
    dates = _sitemap_dates(render_sitemap([], [], public_dir=public))
    checked = 0
    for route, _freq, _prio in BASE_URLS:
        if route in SNAPSHOT_ROUTES or route in ("/dispatches/", "/articles/"):
            continue
        relative = route.strip("/")
        page = (public / relative / "index.html" if route.endswith("/")
                else public / f"{relative}.html")
        if not page.is_file():
            assert dates[route] is None, route
            continue
        blocks = " ".join(re.findall(
            r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page.read_text(), re.S))
        modified = re.findall(r'"dateModified"\s*:\s*"(\d{4}-\d{2}-\d{2})', blocks)
        published = re.findall(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})', blocks)
        declared = max(modified or published, default=None)
        assert dates[route] == declared, (route, dates[route], declared)
        checked += 1
    assert checked >= 20


def test_question_pages_keep_their_reviewed_catalog_date():
    """The editorial catalog's reviewed_on reaches the sitemap through the
    pages it renders, for the index and for every answer."""
    from seiche.dispatch_pages import REPO_ROOT

    public = REPO_ROOT / "frontend" / "public"
    catalog = json.loads((public / "questions" / "pages.json").read_text())
    dates = _sitemap_dates(render_sitemap([], [], public_dir=public))
    routes = ["/questions/"] + [f"/questions/{p['slug']}/" for p in catalog["pages"]]
    assert {route: dates[route] for route in routes} == {
        route: catalog["reviewed_on"] for route in routes}


def _page_with_head(path, head):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"<!doctype html><html><head>{head}</head><body>page</body></html>")


def test_final_sitemap_pass_drops_every_page_that_declares_noindex(tmp_path):
    from seiche.dispatch_pages import declares_noindex, prune_unindexable

    site = tmp_path / "dist"
    site.mkdir()
    (site / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "  <url>\n    <loc>https://seiche.info/</loc>\n    <lastmod>2026-11-26</lastmod>\n  </url>\n"
        "  <url>\n    <loc>https://seiche.info/referee</loc>\n  </url>\n"
        "  <url>\n    <loc>https://seiche.info/dispatches/2026-09-24-daily</loc>\n  </url>\n"
        "  <url><loc>https://seiche.info/views/board/composite/</loc>"
        "<changefreq>daily</changefreq></url>\n"
        "</urlset>\n")
    _page_with_head(site / "index.html", '<meta name="robots" content="index,follow">')
    _page_with_head(site / "referee.html", '<meta name="robots" content="noindex">')
    _page_with_head(site / "dispatches" / "2026-09-24-daily.html", "<title>letter</title>")
    _page_with_head(site / "views" / "board" / "composite" / "index.html",
                    "<meta content='NOINDEX, follow' name='googlebot'>")
    # A noindex word in the body is not a robots tag.
    (site / "dispatches" / "2026-09-24-daily.html").write_text(
        "<html><head></head><body>meta name=robots content=noindex</body></html>")

    assert prune_unindexable(site) == ["/referee", "/views/board/composite/"]
    dates = _sitemap_dates((site / "sitemap.xml").read_text())
    assert dates == {"/": "2026-11-26", "/dispatches/2026-09-24-daily": None}
    assert prune_unindexable(site) == []
    assert not declares_noindex(site / "missing.html")


def test_sitemap_step_skips_a_static_page_that_declares_noindex(tmp_path):
    public = tmp_path / "public"
    _page_with_head(public / "guide.html", '<meta name="robots" content="noindex, nofollow">')
    dates = _sitemap_dates(render_sitemap([], [], public_dir=public))
    assert "/guide" not in dates
    assert "/support" in dates


def test_missing_markdown_fails_loud(repo):
    root, d = repo
    (root / "frontend" / "public" / "dispatches" / f"{d['slug']}.md").unlink()
    with pytest.raises(SystemExit):
        build_all(repo_root=root)


def test_no_index_fails_loud(tmp_path):
    with pytest.raises(SystemExit):
        build_all(repo_root=tmp_path)


def test_llms_txt_lists_letters_with_markdown_links(repo):
    root, d = repo
    build_all(repo_root=root)
    llms = (root / "frontend" / "public" / "llms.txt").read_text()
    assert llms.startswith("# Seiche")
    assert "https://seiche.info/gift-city/" in llms
    assert "https://seiche.info/agents/quant/" in llms
    assert f"https://seiche.info/dispatches/{d['slug']}.md" in llms
    assert "crawler policy permits search and AI input" in llms
    assert "terms#google-original-text" in llms
    assert "do not carry a model-training grant" in llms


def test_llms_full_carries_complete_letters(repo):
    root, d = repo
    build_all(repo_root=root)
    full = (root / "frontend" / "public" / "llms-full.txt").read_text()
    assert d["title"] in full
    assert "EROSION" in full
    assert "forward read" in full          # the desk continuation is in the corpus
    assert "HAS-PAID" not in full and "HAS-DESK" not in full   # markers never leak


def test_feed_is_valid_atom_with_full_content(repo):
    import xml.etree.ElementTree as ET

    root, d = repo
    build_all(repo_root=root)
    feed_path = root / "frontend" / "public" / "dispatches" / "feed.xml"
    tree = ET.fromstring(feed_path.read_text())  # parses = well-formed
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = tree.findall("a:entry", ns)
    assert len(entries) == 1
    assert entries[0].find("a:id", ns).text == f"https://seiche.info/dispatches/{d['slug']}"
    assert "EROSION" in entries[0].find("a:content", ns).text


def test_machine_news_feed_carries_typed_story_and_legacy_pointers(repo):
    root, d = repo
    free_dir = root / "frontend" / "public" / "dispatches"
    index = json.loads((free_dir / "index.json").read_text())
    index.append({"slug": "2026-06-01-daily", "title": "Older water",
                  "date": "2026-06-01", "tag": "CALM", "summary": "Archive."})
    (free_dir / "index.json").write_text(json.dumps(index))
    (free_dir / "2026-06-01-daily.md").write_text("## Reading\n\nQuiet.\n")

    build_all(repo_root=root)
    feed = json.loads((free_dir / "news.json").read_text())
    assert feed["schema"] == "seiche.analytical-feed.v1"
    by_slug = {row["slug"]: row for row in feed["entries"]}
    assert by_slug[d["slug"]]["newsworthiness"]["decision"] in {
        "full_story", "desk_brief", "watch_note"
    }
    assert by_slug["2026-06-01-daily"]["editorial_class"] == "legacy_dispatch"


def test_letter_page_links_its_markdown_twin(repo):
    root, d = repo
    build_all(repo_root=root)
    page = (root / "frontend" / "public" / "dispatches" / f"{d['slug']}.html").read_text()
    assert f'type="text/markdown" href="/dispatches/{d["slug"]}.md"' in page
    assert 'type="application/atom+xml" href="/dispatches/feed.xml"' in page


def test_page_chrome_has_no_dashes(repo):
    """House copy rule applies to the page furniture we author (the letter
    body is governed by its own generator test)."""
    meta = {"slug": "x", "title": "t", "date": "2026-07-13", "tag": "CALM", "summary": "s"}
    page = render_letter_page(meta, "body", None)
    chrome = page.replace("body", "")
    assert "—" not in chrome and "–" not in chrome
    sm = render_sitemap([meta])
    assert "—" not in sm and "–" not in sm


def test_package_block_names_this_release_and_no_other_products_version():
    """The block is rendered by the release it describes, so it cannot name an older one.

    Until 0.16.15 it was typed by hand ("Reviewed 18 October 2026 ... 0.16.5") and stayed
    there through nine releases. Other products' versions were typed in beside it and went
    stale the same way; the distribution hub states those, so this page no longer does.
    """
    import re

    from seiche import assemble, dispatch_pages

    assert dispatch_pages.RELEASE_VERSION == assemble.VERSION
    preamble = dispatch_pages._LLMS_PREAMBLE
    start = preamble.index("## Current packages and agent integrations")
    block = preamble[start:preamble.index("## Related products", start)]
    assert f"Seiche Python/MCP {assemble.VERSION} is the release version of the source this page was built from." in block
    assert "the signed tag for that release" in block and "this release was built from" not in block
    assert f"https://github.com/beepboop2025/seiche/tree/v{assemble.VERSION}" in block
    assert "https://pypi.org/project/seiche/)" in block
    assert "Reviewed 18 October 2026" not in block
    # The only version numbers in the block are this release's own.
    assert set(re.findall(r"\b\d+\.\d+\.\d+\b", block)) == {assemble.VERSION}
    assert "The hub states each current package version." in block
    # The dated usage record keeps its own date.
    assert "application windows end 17 October 2026" in block


def _folded(text: str) -> str:
    """The preamble as one line of prose: blockquote markers and wraps removed."""
    return " ".join(line.lstrip("> ").strip() for line in text.splitlines() if line.strip())


def test_llms_preamble_states_the_served_scope_not_the_withheld_board():
    """What the website and REST API serve, said as the source-rights policy allows it.

    Until 0.16.16 the live index said Seiche "publishes a live funding regime, forward
    event odds, historical analogs and a final-vintage construction-PIT diagnostic with
    the misses kept next to the hits". The first three are served (/api/gauge, and
    deep.markov, deep.swell and deep.tidetables in /api/overview); the diagnostic is not:
    deep.backtest is withheld, /api/public answers 403 and only the evidence status is
    served. The hosted MCP's legacy tools are the only surface that serves none of them.
    """
    from seiche import dispatch_pages, evidence_boundary, public_rights

    preamble = dispatch_pages._LLMS_PREAMBLE
    opening = _folded(preamble[: preamble.index("Liquidity intelligence sits on two shelves")])
    assert ("Its website and REST API serve a live funding regime, forward event odds and "
            "historical analogs as a source-rights projection") in opening
    assert "withheld, never reconstructed" in opening
    assert ("construction-PIT diagnostic reads sources that are not qualified, so its values "
            f"are withheld and only its evidence status, {evidence_boundary.DEFAULT_STATUS}, "
            "is served") in opening
    assert "The hosted MCP server's legacy forecast, backtest and derived-narrative tools" in opening
    assert "except Funding Pop, served research only with its skill" in opening
    assert "with the misses kept" not in opening and "Seiche publishes a live funding regime" not in opening
    assert "query the live board\nover MCP" not in preamble
    # The regime word is served; only the hosted MCP's legacy tools are unavailable.
    assert "withholds its regime" not in preamble.lower()

    # The claims follow the policy: every input of the served readings may be
    # public, and the diagnostic reads inputs that may not.
    assert public_rights.engine_can_be_public("composite")
    for name in ("markov", "swell", "tidetables", "funding_pop"):
        assert public_rights.engine_can_be_public(name, public_rights.DEEP_INPUTS), name
    for name in ("backtest", "ml"):
        assert not public_rights.engine_can_be_public(name, public_rights.DEEP_INPUTS), name
    assert "funding_stress_now" in public_rights.rights_status()["unavailable_tools"]

    # The PROOF scoreboard's route is a legacy path, so the index cannot call it published.
    assert "/api/public" in public_rights.LEGACY_PATHS
    assert "The PROOF scoreboard publishes" not in preamble
    assert "The misses are published beside the hits" not in preamble
    folded = _folded(preamble)
    assert ("The PROOF scoreboard of construction-PIT hits and misses is withheld pending "
            "source-rights qualification") in folded
    # Funding Pop's misses are public, research only, with its skill.
    assert "Funding Pop publishes its misses beside its hits: deep.funding_pop" in folded
    assert "the ones that did not stick included. Read those first." in folded
    assert "funding_pop" in public_rights.rights_status()["research_only_readings"]


def test_llms_preamble_types_no_diagnostic_counts_by_hand():
    """The diagnostic windows' counts move with every board, so the index points at
    the board that serves them. The typed counts had drifted (Bathymetry 1,537 scored
    days against 1,611 served on 24 Nov 2026) and named the withheld ML window."""
    import re

    from seiche import dispatch_pages, public_rights

    folded = _folded(dispatch_pages._LLMS_PREAMBLE)
    assert not re.search(r"\d[\d,]* (?:scored|out-of-sample) days", folded)
    assert ("Each public window carries its current scored-day and event counts beside its "
            "reading at https://api.seiche.info/api/overview") in folded
    assert "the ML window reads sources that are not qualified and is withheld" in folded
    for name in ("bathymetry", "swell", "tidetables"):
        assert public_rights.engine_can_be_public(name, public_rights.DEEP_INPUTS), name
    assert not public_rights.engine_can_be_public("ml", public_rights.DEEP_INPUTS)
