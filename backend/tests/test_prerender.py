"""The no-JS home page: it must carry the board, and it must not cost the terminal.

seiche.info shipped about 400 characters of body text to anything that does not
run JavaScript, which is every AI crawler that reads raw HTML and every reader
with scripting off. These tests hold both halves of the fix: the page says
something real, and the interactive shell comes out the other side byte for
byte identical apart from the <noscript> block and the card meta.
"""

import json
import re
import shutil

import pytest

from seiche import config, prerender
from seiche.dispatch_daily import build_dispatch, write_dispatch
from seiche.dispatch_pages import build_all

# The shell the shipped index.html is built from. Reading the real file rather
# than a stub is the point: if the SPA template loses its <noscript> anchor,
# these tests fail here rather than at publish time.
SHELL = prerender.REPO_ROOT / "frontend" / "index.html"

WEEK_MD = """*Issue 9 · the week of 2026-07-06 · the sections run in the same order every week.*

## 1 · The week in one paragraph

Monday's reading: **41 out of 100, EROSION**, on 96% coverage.

## 5 · Pre-registered calls

Registered 2 calls for the week. Each carries a stable ID and the rule that decides it.

- **W9-1** · SRF take-up stays under $1B on every session of the week. Resolves 2026-07-13.
- **W9-2** · The composite reads between 38.0 and 44.0 on next week's board. Resolves 2026-07-13.

## 6 · Last week's calls, graded

Nothing to grade.
"""


@pytest.fixture
def site(tmp_path, fake_snap):
    """A built site directory, laid out the way vite leaves frontend/dist.

    The daily letter comes from the real generator; the weekly is a fixture so
    these tests do not pin dispatch_weekly's signature. The real committed
    issues are checked separately, in test_week_ahead_heading_still_matches.
    """
    repo = tmp_path / "repo"
    d = build_dispatch(fake_snap, prev_value=38.0)
    write_dispatch(d, repo_root=repo)

    disp = repo / "frontend" / "public" / "dispatches"
    week_slug = "2026-07-06-week-ahead"
    (disp / f"{week_slug}.md").write_text(WEEK_MD)
    index = json.loads((disp / "index.json").read_text())
    index.append({"slug": week_slug, "title": "The Week Ahead 9: 2 pre-registered calls",
                  "date": "2026-07-06", "tag": "WEEK AHEAD",
                  "summary": "Issue 9 of the Monday letter."})
    (disp / "index.json").write_text(json.dumps(index))
    build_all(repo_root=repo)          # llms.txt, feed, per-letter pages

    out = tmp_path / "dist"
    (out / "data").mkdir(parents=True)
    shutil.copytree(disp, out / "dispatches")
    # Vite injects the hashed application stylesheet into dist/index.html.
    # The source shell intentionally has no external font stylesheet anymore,
    # so model the post-build artifact this module actually consumes.
    built_shell = SHELL.read_text().replace(
        "</head>", '<link rel="stylesheet" href="/assets/index-test.css" />\n</head>', 1)
    (out / "index.html").write_text(built_shell)
    (out / "data" / "overview.json").write_text(json.dumps(fake_snap))
    return out, d, week_slug


def _shell_without_prerender(doc: str) -> str:
    """The document with everything this module is allowed to touch removed."""
    doc = re.sub(r"<noscript>.*?</noscript>", "<noscript/>", doc, flags=re.S)
    doc = re.sub(r"<!--prerender:meta-->.*?<!--/prerender:meta-->\n?", "", doc, flags=re.S)
    for key in ("og:title", "og:description", "twitter:title", "twitter:description"):
        attr = "property" if key.startswith("og:") else "name"
        doc = re.sub(r'(<meta\s+' + attr + r'="' + re.escape(key) + r'"\s+content=")[^"]*(")',
                     r"\1X\2", doc)
    return doc


# ---------------------------------------------------------------------------
# the page says something
# ---------------------------------------------------------------------------
def test_the_shell_alone_is_the_problem_this_fixes():
    """The unrendered shell is the ~400-character page. If this number ever
    climbs on its own, the SPA template started carrying content and this
    module's premise is worth rechecking."""
    assert len(prerender.body_text(SHELL.read_text())) < 800


def test_prerender_carries_the_board_and_the_letter(site):
    out, d, _ = site
    n = prerender.build(out)
    page = (out / "index.html").read_text()
    text = prerender.body_text(page)

    assert n == len(text)
    assert n > 5000, f"only {n} characters of no-JS body text"

    # the masthead and what Seiche is
    assert "SEI" in page and "funding-stress" in text
    assert "free open source software (AGPL-3.0-or-later)" in text

    # the composite reading with its plain-English gloss
    assert "The composite reads 41 out of 100, EROSION" in text
    assert "The margin for error is what is shrinking." in text   # the EROSION frame
    assert "96% coverage" in text

    # the letter, in full, not just its summary
    assert d["title"] in text
    assert f'href="/dispatches/{d["slug"]}"' in page

    # the Week Ahead's pre-registered calls
    assert "W9-1" in text and "W9-2" in text
    assert "Last week's calls, graded" not in text   # section 5 only, not the whole issue


def test_prerender_carries_the_same_argument_evidence_and_countercase(site):
    out, _, _ = site
    snap_path = out / "data" / "overview.json"
    snap = json.loads(snap_path.read_text())
    snap["editorial"] = {
        "thesis": "The balance sheet is tightening, but the tape has not confirmed it.",
        "standfirst": "The board reads 41 out of 100, EROSION; the calendar contributes 11 points.",
        "confidence": "guarded",
        "confidence_note": "One slow-moving structural signal is doing most of the work.",
        "evidence": [{
            "label": "Balance-sheet identity",
            "claim": "The Treasury General Account absorbed $80B.",
            "source": "Federal Reserve H.4.1",
            "asof": "2026-07-09",
        }],
        "countercase": [{
            "claim": "SOFR remains below IORB.",
            "source": "New York Fed",
            "asof": "2026-07-10",
        }],
    }
    snap_path.write_text(json.dumps(snap))

    prerender.build(out)
    page = (out / "index.html").read_text()
    text = prerender.body_text(page)
    assert "The argument" in text
    assert snap["editorial"]["thesis"] in text
    assert "Evidence ledger" in text and "Federal Reserve H.4.1" in text
    assert "The countercase" in text and "SOFR remains below IORB" in text
    assert "Conviction: GUARDED" in text
    assert (
        '<meta property="og:title" content="Seiche · world-market intelligence, '
        'argued and audited" />'
    ) in page
    assert "the balance sheet is tightening" not in re.search(
        r'<meta property="og:title" content="([^"]*)"', page
    ).group(1).lower()


def test_headline_numbers_carry_their_own_asof(site):
    """A funding desk reads the level and the lag together. The table must not
    print one without the other."""
    out, _, _ = site
    snap = json.loads((out / "data" / "overview.json").read_text())
    snap["headline"] = {"sofr_pct": {"value": 5.31, "asof": "2026-07-09"},
                        "reserves_b": {"value": 3120.4, "asof": "2026-07-08"},
                        "tga_b": {"value": None, "asof": "2026-07-08"}}
    (out / "data" / "overview.json").write_text(json.dumps(snap))
    prerender.build(out)
    text = prerender.body_text((out / "index.html").read_text())
    assert "SOFR" in text and "5.31" in text and "2026-07-09" in text
    assert "3,120" in text
    # a dark input is dropped, never printed as a zero or an empty cell
    assert "Treasury General Account" not in text


def test_proof_publishes_the_misses_next_to_the_rate(site):
    out, _, _ = site
    prerender.build(out)
    text = prerender.body_text((out / "index.html").read_text())
    assert "79%" in text                      # recall from the fixture
    assert "repo spike" in text               # the episode ledger
    assert "small event count" in text        # the caveat, carried not hidden


# ---------------------------------------------------------------------------
# the terminal is not the price
# ---------------------------------------------------------------------------
def test_interactive_shell_is_untouched(site):
    out, _, _ = site
    before = (out / "index.html").read_text()
    prerender.build(out)
    after = (out / "index.html").read_text()

    assert '<div id="root"></div>' in after
    assert '<script type="module" src="/src/main.tsx"></script>' in after
    assert _shell_without_prerender(before) == _shell_without_prerender(after)


def test_prerendered_content_stays_inside_noscript(site):
    """Not inside #root: React clears prerendered children on its first commit
    (a flash for every reader who has JavaScript), and text hidden by CSS is the
    shape search engines treat as hidden text. Everything lands in <noscript>."""
    out, _, _ = site
    prerender.build(out)
    body = (out / "index.html").read_text().split("<body>", 1)[1]
    outside = re.sub(r"<noscript>.*?</noscript>", "", body, flags=re.S)
    assert prerender.body_text("<body>" + outside + "</body>") == ""


def test_running_twice_changes_nothing(site):
    out, _, _ = site
    prerender.build(out)
    once = (out / "index.html").read_text()
    prerender.build(out)
    assert (out / "index.html").read_text() == once


# ---------------------------------------------------------------------------
# the card
# ---------------------------------------------------------------------------
def test_card_meta_keeps_broad_identity_while_body_carries_live_reading(site):
    out, _, _ = site
    prerender.build(out)
    page = (out / "index.html").read_text()
    assert (
        '<meta property="og:title" content="Seiche · world-market intelligence, '
        'argued and audited" />'
    ) in page
    for key in ("og:title", "og:description", "twitter:title", "twitter:description"):
        value = re.search(
            r'content="([^"]*)"',
            re.search(r'<meta [^>]*"' + re.escape(key) + r'"[^>]*>', page).group(0),
        ).group(1)
        assert "EROSION" not in value
    assert "Money, forex and capital markets" in page
    assert "The composite reads 41 out of 100, EROSION" in prerender.body_text(page)
    # the fleet's existing share card, not a new image pipeline
    assert '<meta property="og:image" content="https://seiche.info/og2.png" />' in page
    assert (
        '<meta property="og:image:alt" content="Seiche, the public money, forex '
        'and capital-market evidence terminal" />'
    ) in page


def test_meta_rewrite_survives_an_apostrophe_in_the_old_value():
    """Regression: the shipped description says "the Fed's plumbing". A
    non-greedy scan for either quote character ends the match on that
    apostrophe and staples half the old sentence onto the new one, which is
    exactly what shipped to the card on the first cut of this module."""
    doc = ('<meta property="og:description" content="Reads the Fed\'s plumbing '
           'so you don\'t have to: reserves, RRP." />')
    out, hit = prerender._set_meta(doc, "og:description", "NEW COPY")
    assert hit
    assert out == '<meta property="og:description" content="NEW COPY" />'
    assert "plumbing" not in out


def test_meta_rewrite_reports_a_missing_tag_instead_of_guessing(site):
    doc, hit = prerender._set_meta("<head></head>", "og:title", "x")
    assert not hit and doc == "<head></head>"


# ---------------------------------------------------------------------------
# consistency with the surfaces that already state this
# ---------------------------------------------------------------------------
def test_home_page_and_llms_txt_list_the_same_reading(site):
    """llms.txt tells an LLM where to go next. The no-JS home page is read by
    the same crawlers, so it must not offer a different set of doors."""
    out, _, _ = site
    prerender.build(out)
    page = (out / "index.html").read_text()
    urls = prerender.llms_doc_urls()
    assert urls, "llms.txt preamble has no Docs list to agree with"
    for url in urls:
        assert url in page, f"{url} is in llms.txt but not on the prerendered home page"


def test_home_page_carries_the_llms_txt_description(site):
    out, _, _ = site
    prerender.build(out)
    text = prerender.body_text((out / "index.html").read_text())
    llms = (out.parent / "repo" / "frontend" / "public" / "llms.txt").read_text()
    for phrase in ("free open source software (AGPL-3.0-or-later)",
                   "Liquidity intelligence sits on two shelves",
                   "may be read, quoted, indexed and used as AI input"):
        assert phrase in llms and phrase in text


def test_every_regime_has_a_plain_english_gloss():
    """The gloss is imported from the letter. A regime added to config without
    a frame would print the fallback sentence forever, silently."""
    for _, name in config.REGIMES:
        assert prerender._REGIME_FRAME.get(name), f"no gloss for regime {name}"


def test_no_em_or_en_dashes_reach_the_page(site):
    """House rule, and engine free text really does carry em dashes: two of the
    live backtest's published caveats are punctuated with them, as is the
    precision note. Every engine-supplied string on this page goes through the
    letter's own filter, so the dashes below are seeded deliberately rather
    than trusted to be absent from the fixture. The literals in this test are
    the test: they cannot be written any other way."""
    out, _, _ = site
    snap = json.loads((out / "data" / "overview.json").read_text())
    snap["deep"]["backtest"]["caveats"] = ["expanding-window only — no look-ahead",
                                           "small sample – wide intervals"]
    snap["deep"]["backtest"]["episodes"][0]["episode"] = "repo spike — Sep 2019"
    snap["deep"]["tell"]["reading"] = "plumbing leads price — widely"
    snap["engines"]["composite"]["decomposition"][0]["component"] = "an unnamed — engine"
    (out / "data" / "overview.json").write_text(json.dumps(snap))

    prerender.build(out)
    text = prerender.body_text((out / "index.html").read_text())
    assert "no look-ahead" in text and "wide intervals" in text   # the text survived
    assert "—" not in text and "–" not in text                    # the dashes did not


def test_generator_markers_never_print(site):
    out, _, _ = site
    prerender.build(out)
    page = (out / "index.html").read_text()
    assert "HAS-DESK" not in page and "HAS-PAID" not in page


def test_week_ahead_heading_still_matches_the_committed_issues():
    """The tripwire for a weekly-generator rename. The calls section is found
    by its words; if dispatch_weekly renames the heading the page silently
    falls back to the whole issue, so catch it here, in CI, where the publish
    gates on green."""
    issues = sorted((prerender.REPO_ROOT / "frontend" / "public" / "dispatches")
                    .glob("*-week-ahead.md"))
    if not issues:
        pytest.skip("no Week Ahead issue committed yet")
    for path in issues:
        md = path.read_text()
        body, matched = prerender.calls_md(md)
        assert matched, f"{path.name}: no 'pre-registered calls' heading found"
        assert body and body != md.strip()


# ---------------------------------------------------------------------------
# fail loud, never quietly back to the empty shell
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("missing", ["data/overview.json", "dispatches/index.json", "index.html"])
def test_missing_input_is_an_error(site, missing):
    out, _, _ = site
    (out / missing).unlink()
    with pytest.raises(SystemExit) as e:
        prerender.build(out)
    assert missing.split("/")[-1] in str(e.value)


def test_a_shell_without_the_anchor_is_an_error(site):
    out, _, _ = site
    (out / "index.html").write_text("<html><head></head><body><div id=root></div></body></html>")
    with pytest.raises(SystemExit) as e:
        prerender.build(out)
    assert "noscript" in str(e.value)
