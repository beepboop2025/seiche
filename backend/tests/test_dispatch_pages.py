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
    assert f'<link rel="canonical" href="https://seiche.info/dispatches/{d["slug"]}.html">' in page
    assert '"@type": "Article"' in page and d["date"] in page
    # the free reading and the desk's forward read are both in the static page
    assert "EROSION" in page
    assert "forward read" in page
    # the HAS-PAID marker never leaks into the rendered page
    assert "HAS-PAID" not in page


def test_archive_lists_every_letter(repo):
    root, d = repo
    build_all(repo_root=root)
    archive = (root / "frontend" / "public" / "dispatches" / "index.html").read_text()
    assert f'href="/dispatches/{d["slug"]}.html"' in archive
    assert '<link rel="canonical" href="https://seiche.info/dispatches/">' in archive


def test_sitemap_has_base_pages_and_letters(repo):
    root, d = repo
    build_all(repo_root=root)
    sm = (root / "frontend" / "public" / "sitemap.xml").read_text()
    for loc in ("https://seiche.info/", "https://seiche.info/guide.html",
                "https://seiche.info/dispatches/",
                f"https://seiche.info/dispatches/{d['slug']}.html"):
        assert f"<loc>{loc}</loc>" in sm
    assert f"<lastmod>{d['date']}</lastmod>" in sm


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
    assert f"https://seiche.info/dispatches/{d['slug']}.md" in llms
    assert "AI input or training material" in llms  # the affirmative grant


def test_llms_full_carries_complete_letters(repo):
    root, d = repo
    build_all(repo_root=root)
    full = (root / "frontend" / "public" / "llms-full.txt").read_text()
    assert d["title"] in full
    assert "EROSION" in full
    assert "forward read" in full          # the desk continuation is in the corpus
    assert "HAS-PAID" not in full          # the marker never leaks


def test_feed_is_valid_atom_with_full_content(repo):
    import xml.etree.ElementTree as ET

    root, d = repo
    build_all(repo_root=root)
    feed_path = root / "frontend" / "public" / "dispatches" / "feed.xml"
    tree = ET.fromstring(feed_path.read_text())  # parses = well-formed
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = tree.findall("a:entry", ns)
    assert len(entries) == 1
    assert entries[0].find("a:id", ns).text == f"https://seiche.info/dispatches/{d['slug']}.html"
    assert "EROSION" in entries[0].find("a:content", ns).text


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
