"""Static HTML pages for the dispatches — the SEO surface of the daily letter.

The SPA renders dispatches at #dispatches/{slug}, which search engines cannot
index as separate pages. This module renders every dispatch to a standalone
HTML page with its own URL, canonical tag and Article JSON-LD, plus an archive
index page, and regenerates sitemap.xml so each letter is a first-class URL:

  frontend/public/dispatches/{slug}.html     one page per letter
  frontend/public/dispatches/index.html      the archive
  frontend/public/dispatches/feed.xml        Atom feed of the letters
  frontend/public/sitemap.xml                base pages + archive + letters
  frontend/public/llms.txt                   llmstxt.org index for LLMs
  frontend/public/llms-full.txt              full text of every letter

Run at publish time (the generated pages are baked into the static build, not
committed):  PYTHONPATH=backend python -m seiche.dispatch_pages

Stdlib only, deterministic, fail-loud: a slug listed in index.json whose
markdown file is missing is an error, not a skipped page. The desk's forward
read (the .desk.md continuation — .paid.md on pre-rename history — free like
everything else) is rendered into the page so the full letter is crawlable.
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

SITE = "https://seiche.info"
# Old letters carry HAS-PAID (pre open-access naming, gated nothing), new
# ones HAS-DESK; this renderer reads the whole archive so it accepts both.
MARKERS = ("<!--HAS-DESK-->", "<!--HAS-PAID-->")


def _strip_markers(md: str) -> str:
    for m in MARKERS:
        md = md.replace(m, "")
    return md

REPO_ROOT = Path(__file__).resolve().parents[2]

# Base site URLs that always belong in the sitemap, with their cadence.
BASE_URLS = [
    ("/", "daily", "1.0"),
    ("/developers.html", "monthly", "0.9"),
    ("/use-cases.html", "monthly", "0.9"),
    ("/guide.html", "monthly", "0.8"),
    ("/methodology.html", "monthly", "0.8"),
    ("/skeptic.html", "monthly", "0.8"),
    ("/ampleness.html", "daily", "0.8"),
    ("/referee.html", "daily", "0.8"),
    ("/investigations/", "weekly", "0.9"),
    ("/investigations/the-282-billion-settlement-test/", "monthly", "0.8"),
    ("/dispatches/", "daily", "0.8"),
    ("/support.html", "monthly", "0.5"),
    ("/privacy.html", "yearly", "0.2"),
    ("/terms.html", "yearly", "0.2"),
]


# ---------------------------------------------------------------------------
# markdown -> HTML: the same subset the SPA's md.ts renders, plus tables
# (the desk's forward read carries an echoes table). Everything is escaped.
# ---------------------------------------------------------------------------
def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def _inline(s: str) -> str:
    s = _esc(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", s)

    def _link(m: re.Match) -> str:
        text, url = m.group(1), m.group(2).strip()
        safe = url if re.match(r"^(https?:|mailto:|#|/)", url, re.I) else "#"
        return f'<a href="{safe}">{text}</a>'

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _link, s)


def _is_table_sep(line: str) -> bool:
    return bool(re.match(r"^\|[\s\-:|]+\|\s*$", line))


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def md_to_html(src: str) -> str:
    lines = src.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            close_list()
            buf: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            out.append("<pre><code>" + _esc("\n".join(buf)) + "</code></pre>")
            continue
        if re.match(r"^\s*$", line):
            close_list()
            i += 1
            continue
        # a table: a |...| line whose next line is the |---| separator
        if line.lstrip().startswith("|") and i + 1 < len(lines) and _is_table_sep(lines[i + 1]):
            close_list()
            head = _cells(line)
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            out.append("<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead><tbody>")
            for r in rows:
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>")
            out.append("</tbody></table>")
            continue
        if re.match(r"^###\s+", line):
            close_list()
            out.append("<h3>" + _inline(re.sub(r"^###\s+", "", line)) + "</h3>")
            i += 1
            continue
        if re.match(r"^##\s+", line):
            close_list()
            out.append("<h2>" + _inline(re.sub(r"^##\s+", "", line)) + "</h2>")
            i += 1
            continue
        if re.match(r"^---\s*$", line):
            close_list()
            out.append("<hr />")
            i += 1
            continue
        if re.match(r"^>\s?", line):
            close_list()
            out.append("<blockquote>" + _inline(re.sub(r"^>\s?", "", line)) + "</blockquote>")
            i += 1
            continue
        if re.match(r"^[-*]\s+", line):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append("<li>" + _inline(re.sub(r"^[-*]\s+", "", line)) + "</li>")
            i += 1
            continue
        close_list()
        out.append("<p>" + _inline(line) + "</p>")
        i += 1
    close_list()
    return "\n".join(out)


# ---------------------------------------------------------------------------
# page chrome — Nocturne, self-contained, one <style> per page
# ---------------------------------------------------------------------------
_CSS = """
:root {
  --bg:#000000; --panel:#0b0d15; --panel-2:#11131d; --edge:#1c1f2b; --edge-2:#333748;
  --text:#edeef4; --dim:#9aa0b6; --faint:#787f95;
  --accent:#9c8fe8; --accent-soft:#bbaffe; --accent-deep:#453c72;
  --divider:rgba(237,238,244,0.16);
  --mono:"JetBrains Mono",ui-monospace,"SF Mono",Menlo,monospace;
  --display:"Inter",system-ui,-apple-system,sans-serif;
  color-scheme:dark;
}
* { box-sizing:border-box; margin:0; padding:0; }
body { background:var(--bg); color:var(--text); font-family:var(--display); font-size:15px;
       line-height:1.72; -webkit-font-smoothing:antialiased;
       max-width:720px; margin:0 auto; padding:36px 22px 80px; }
a { color:var(--accent-soft); text-decoration:none; }
a:hover { text-decoration:underline; }
.top { display:flex; align-items:baseline; justify-content:space-between; gap:14px; flex-wrap:wrap;
       padding-bottom:16px; border-bottom:1px solid var(--divider); }
.wordmark { font-weight:600; letter-spacing:.3em; font-size:17px; color:var(--text); }
.wordmark span { color:var(--accent-soft); }
.crumb { font-family:var(--mono); font-size:11px; color:var(--faint); }
.date { font-family:var(--mono); font-size:11px; letter-spacing:.1em; color:var(--accent-soft);
        text-transform:uppercase; margin-top:34px; }
h1 { font-size:clamp(26px,4.5vw,38px); font-weight:500; letter-spacing:-.02em; line-height:1.12;
     color:var(--text); margin:8px 0 10px; }
.lede { color:var(--dim); font-size:14.5px; max-width:64ch; margin-bottom:26px; }
.body h2 { font-size:19px; font-weight:500; color:var(--accent-soft); margin:30px 0 12px; }
.body h3 { font-size:15px; font-weight:500; color:var(--text); margin:22px 0 10px; }
.body p { margin:0 0 18px; }
.body strong { color:var(--text); font-weight:600; }
.body em { color:var(--dim); }
.body code { font-family:var(--mono); font-size:13px; background:var(--panel-2);
             padding:1px 5px; border-radius:5px; }
.body ul { margin:0 0 18px 20px; }
.body blockquote { border-left:2px solid var(--accent-deep); padding-left:14px; color:var(--dim); margin:0 0 18px; }
.body table { border-collapse:collapse; margin:0 0 18px; font-family:var(--mono); font-size:12.5px; }
.body th, .body td { border:1px solid var(--edge-2); padding:6px 12px; text-align:left; }
.body th { color:var(--accent-soft); font-weight:500; }
.body hr { border:0; border-top:1px solid var(--divider); margin:26px 0; }
.cards { display:grid; gap:14px; margin-top:26px; }
.card { display:block; background:var(--panel); border:1px solid var(--edge); border-radius:12px;
        padding:18px 20px; transition:border-color .2s; }
.card:hover { border-color:var(--accent-deep); text-decoration:none; }
.card-title { font-size:19px; font-weight:500; color:var(--text); margin:6px 0; line-height:1.25; }
.card-sum { color:var(--dim); font-size:13px; }
.read { margin-top:10px; font-size:11.5px; color:var(--accent-soft); letter-spacing:.05em; }
.foot { margin-top:52px; padding-top:18px; border-top:1px solid var(--divider);
        font-family:var(--mono); font-size:12px; color:var(--faint); line-height:2; }
"""

_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2'
    '?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap">'
)

_HEADER = (
    '<div class="top"><a class="wordmark" href="/">SEI<span>CHE</span></a>'
    '<span class="crumb"><a href="/dispatches/">dispatches</a> &middot; <a href="/">live board</a></span></div>'
)

# Cookieless aggregate page counts (Cloudflare Web Analytics); the site CSP in
# frontend/public/_headers whitelists exactly these two insights hosts.
_BEACON = (
    "<!-- Cloudflare Web Analytics -->"
    "<script type='module' src='https://static.cloudflareinsights.com/beacon.min.js' "
    "data-cf-beacon='{\"token\": \"534f08f7270a4f4f9a9a10f90d723ca7\"}'></script>"
    "<!-- End Cloudflare Web Analytics -->"
)

_FOOTER = (
    '<div class="foot">Written by the terminal from the live board, no model in the loop; '
    'every number is checkable on the <a href="/">free board</a>. '
    'Seiche is free open source software (<a href="https://github.com/beepboop2025/seiche">AGPL-3.0, source</a>). '
    '<a href="/guide.html">Plain English guide</a> &middot; <a href="/support.html">Support</a> &middot; '
    'Not investment advice.</div>'
)


def _page(title: str, description: str, canonical_path: str, jsonld: dict, body: str,
          og_type: str = "article", extra_head: str = "") -> str:
    t, d = _esc(title), _esc(description)
    url = SITE + canonical_path
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{t}</title>
<meta name="description" content="{d}">
<link rel="canonical" href="{url}">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<meta name="theme-color" content="#000000">
{_FONTS}
<meta property="og:type" content="{og_type}">
<meta property="og:site_name" content="Seiche">
<meta property="og:url" content="{url}">
<meta property="og:title" content="{t}">
<meta property="og:description" content="{d}">
<meta property="og:image" content="{SITE}/og.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{t}">
<meta name="twitter:description" content="{d}">
<meta name="twitter:image" content="{SITE}/og.png">
{extra_head}<script type="application/ld+json">
{json.dumps(jsonld, indent=1)}
</script>
<style>{_CSS}</style>
{_BEACON}
</head>
<body>
{_HEADER}
{body}
{_FOOTER}
</body>
</html>
"""


def render_letter_page(meta: dict, free_md: str, desk_md: str | None) -> str:
    slug = meta["slug"]
    date = meta["date"]
    tag = meta.get("tag", "")
    path = f"/dispatches/{slug}.html"
    body_md = _strip_markers(free_md).strip()
    body_html = md_to_html(body_md)
    if desk_md:
        body_html += "\n" + md_to_html(desk_md.strip())
    jsonld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "@id": f"{SITE}{path}#article",
        "headline": meta["title"],
        "description": meta["summary"],
        "datePublished": date,
        "dateModified": date,
        "articleSection": "Daily dispatch",
        "author": {"@type": "Organization", "name": "Seiche", "url": SITE},
        "publisher": {"@type": "Organization", "name": "Seiche", "url": SITE},
        "mainEntityOfPage": {"@type": "WebPage", "@id": f"{SITE}{path}"},
        "isPartOf": {"@type": "WebSite", "@id": f"{SITE}/#website"},
        "image": f"{SITE}/og.png",
    }
    # The archive carries two series now; the byline says which one.
    kind = "the Monday letter" if str(slug).endswith("-week-ahead") else "the daily letter"
    inner = (
        f'<div class="date">{_esc(date)}{" &middot; " + _esc(tag) if tag else ""} &middot; {kind}</div>'
        f"<h1>{_esc(meta['title'])}</h1>"
        f'<p class="lede">{_esc(meta["summary"])}</p>'
        f'<div class="body">{body_html}</div>'
    )
    return _page(
        title=f"{meta['title']} · Seiche dispatch {date}",
        description=meta["summary"],
        canonical_path=path,
        jsonld=jsonld,
        body=inner,
        extra_head=(
            f'<meta property="article:published_time" content="{_esc(date)}">\n'
            f'<link rel="alternate" type="text/markdown" href="/dispatches/{_esc(slug)}.md" title="This letter as markdown">\n'
            '<link rel="alternate" type="application/atom+xml" href="/dispatches/feed.xml" title="Seiche dispatches">\n'
        ),
    )


def render_archive(entries: list[dict]) -> str:
    cards = []
    for e in entries:
        cards.append(
            f'<a class="card" href="/dispatches/{_esc(e["slug"])}.html">'
            f'<div class="date" style="margin-top:0">{_esc(e["date"])}'
            f'{" &middot; " + _esc(e["tag"]) if e.get("tag") else ""}</div>'
            f'<div class="card-title">{_esc(e["title"])}</div>'
            f'<div class="card-sum">{_esc(e["summary"])}</div>'
            f'<div class="read">read the letter</div></a>'
        )
    jsonld = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "@id": f"{SITE}/dispatches/#archive",
        "name": "Seiche dispatches: the daily funding stress letter",
        "description": "Every daily letter the terminal has written, archived in full.",
        "isPartOf": {"@type": "WebSite", "@id": f"{SITE}/#website"},
        "hasPart": [
            {
                "@type": "Article",
                "headline": e["title"],
                "url": f"{SITE}/dispatches/{e['slug']}.html",
                "datePublished": e["date"],
            }
            for e in entries
        ],
    }
    intro = (
        "<h1>Dispatches</h1>"
        '<p class="lede">The daily letter on US money market plumbing, written by the terminal '
        "from the same free public data the board runs on. Every claim carries the number it "
        "stands on, and the misses stay published next to the hits.</p>"
    )
    return _page(
        title="Dispatches · the Seiche daily funding stress letter",
        description=(
            "The archive of Seiche's daily letters on US money market plumbing: "
            "funding stress readings, the dates that matter, and the desk's forward read, "
            "written from free public data with every number checkable."
        ),
        canonical_path="/dispatches/",
        jsonld=jsonld,
        body=intro + f'<div class="cards">{"".join(cards)}</div>',
        og_type="website",
        extra_head='<link rel="alternate" type="application/atom+xml" href="/dispatches/feed.xml" title="Seiche dispatches">\n',
    )


def render_feed(entries: list[dict], bodies: dict[str, str]) -> str:
    """Atom feed of the letters, full HTML content inline. AI search crawlers
    and aggregators consume feeds; the letters are small, so ship them whole."""
    newest = max((e.get("date", "") for e in entries), default="")
    items = []
    for e in entries[:50]:
        url = f"{SITE}/dispatches/{e['slug']}.html"
        items.append(
            "  <entry>\n"
            f"    <title>{_esc(e['title'])}</title>\n"
            f'    <link rel="alternate" type="text/html" href="{url}"/>\n'
            f"    <id>{url}</id>\n"
            f"    <published>{e['date']}T11:00:00Z</published>\n"
            f"    <updated>{e['date']}T11:00:00Z</updated>\n"
            f"    <summary>{_esc(e['summary'])}</summary>\n"
            f'    <content type="html">{_esc(bodies.get(e["slug"], ""))}</content>\n'
            "  </entry>"
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom">\n'
        "  <title>Seiche dispatches: the daily funding stress letter</title>\n"
        "  <subtitle>US money market plumbing, read daily from free public data. "
        "Every claim carries its number.</subtitle>\n"
        f'  <link rel="alternate" type="text/html" href="{SITE}/dispatches/"/>\n'
        f'  <link rel="self" type="application/atom+xml" href="{SITE}/dispatches/feed.xml"/>\n'
        f"  <id>{SITE}/dispatches/</id>\n"
        f"  <updated>{newest}T11:00:00Z</updated>\n"
        f"  <author><name>Seiche</name><uri>{SITE}</uri></author>\n"
        + "\n".join(items)
        + "\n</feed>\n"
    )


_LLMS_PREAMBLE = f"""# Seiche

> Seiche is free open source software (AGPL-3.0): a funding stress terminal for
> US money markets built from free public data (Fed H.4.1, NY Fed operations,
> OFR repo, Treasury cash, plus other public sources, each shown with its
> native publication lag). It publishes a live stress regime, forward event
> odds, historical analogs and a final-vintage construction-PIT diagnostic with the misses kept
> next to the hits. The board recomputes through the day; the daily letter
> freezes one reading of it. Cite it as "Seiche" and link {SITE}. Everything
> on this site may be read, quoted, indexed and used as AI input or training material.

Liquidity intelligence sits on two shelves. Official dashboards give you raw
series and no view. A terminal that has a view runs about $32k a seat each
year. Between them, nothing free that holds an opinion and grades itself.
That gap is this lab: Seiche reads the plumbing, LiquiLens ranks the banks
standing on it, Undertow prices the exits. All of it runs on free public
data. The misses are published beside the hits. Read those first. The
sibling boards: LiquiLens at https://liquilens.in and Undertow at
https://liquilens-undertow.com.

Key facts: the live board is at {SITE} (no sign-in). The plain English guide is
at {SITE}/guide.html; the versioned methodology page, with citations, a
changelog and a cite-as block, is at {SITE}/methodology.html. The source code
is at https://github.com/beepboop2025/seiche. Agents can query the live board
over MCP at https://api.seiche.info/mcp. Eight tools answer with no auth at all:
funding_stress_now, historical_analogs, proof_backtest, data_health,
crypto_stress_record, institutional_flows, oil_funding_context and
fx_materials_passage, metered per IP per day. Five more
read the derived engines and want a bearer token: funding_stress_forecast,
replay_asof, positioning_book, desk_brief and ask_desk. Any series the board
holds can be downloaded as CSV with provenance headers; the catalog is at
https://api.seiche.info/api/series/index.json. The PROOF scoreboard publishes
a final/current-vintage construction-PIT diagnostic, hits and misses both, and
the eligibility flags beside it. It is not validated-backtest evidence. Its
stated competence boundary is narrower: stress that builds inside the plumbing
can be investigated historically; shocks that arrive from outside it cannot be
claimed as early warnings.

Evidence receipt captured 2026-08-09 at 14:36 UTC: the v2 data plane has 10 market packs,
87,000 canonical observations across 28 role series, and 220 forward market-pack
validation records. The US pack is VALIDATING, nine packs are REFERENCE, no pack
is SUPPORTED or evidence-eligible, and Global Tide is UNAVAILABLE. The public
notary has 145 entries. Query the live coverage receipt at
https://api.seiche.info/api/v2/coverage because forward records continue to accrue.

Seiche currently serves zero validated backtests. Its four historical diagnostic
windows overlap and stay separate: Bathymetry has 1,537 scored days and 50 events;
ML has 1,583 out-of-sample days and 63 events, plus 6,930 pretraining rows and 425
events; Swell has 1,673 scored days and 68 events; Tide Tables has 1,893 scored days
and 96 events. Every window is FINAL_VINTAGE_CONSTRUCTION_PIT with
validated_backtest=false and real_money_eligible=false.

The independent LiquiLens Lab receipt has a different unit. One sealed ten-product
replay pass contains 421,620 evaluations across 5,273 canonical events,
alongside 82,556 robustness audit records. Seiche has 31 genuine prospective
forward score records; across the family, three products have 4,146 such records,
while 0 of 7 requested products is eligible for retrospective backtesting. Do not
call the 421,620 evaluations backtests.

The Telegram delivery surfaces are https://t.me/seiche_desk_bot for the bot
and https://t.me/LiquidityLabDesk for the public channel. They deliver the
same product; the callable contract remains the MCP server above.

## Docs

- [Plain English guide]({SITE}/guide.html): every engine and regime word explained without jargon
- [API + MCP quickstart]({SITE}/developers.html): connect an agent or make the first public API call in under a minute
- [OpenAPI 3.1 contract](https://api.seiche.info/api/openapi.json): import the intentionally public REST surface without exposing subscriber or operator routes
- [Selection guide]({SITE}/use-cases.html): when to use Seiche, when not to, how it differs from LiquiLens and Undertow, and how to cite it
- [Machine-readable product card]({SITE}/product-card.json): stable identity, use cases, limitations, evidence and public endpoints for retrieval systems and agents
- [Agentic Resource Discovery catalog]({SITE}/.well-known/ai-catalog.json): runtime-discoverable MCP and OpenAPI contracts, indexed with representative intent queries
- [Methodology]({SITE}/methodology.html): versioned methods page with citations, changelog and cite-as block
- [Skeptic pack]({SITE}/skeptic.html): the leakage audit, the orthogonal event-capture test, the construction-PIT replay and the notary, with the commands to check each one
- [Ampleness check]({SITE}/ampleness.html): are reserves still ample, walked indicator by indicator with the threshold behind every verdict
- [Live board]({SITE}/): the current funding stress reading
- [Reviewed investigations]({SITE}/investigations/): long-form reporting with reproducible charts, explicit counter-cases and falsifiers
- [The $282 billion settlement test]({SITE}/investigations/the-282-billion-settlement-test/): calm overnight cash versus a dated reserve-capacity test
- [Dispatch archive]({SITE}/dispatches/): every daily letter as an HTML page
- [Atom feed]({SITE}/dispatches/feed.xml): the letters as a feed
- [Full letter corpus]({SITE}/llms-full.txt): every letter's complete text in one file

## Daily dispatches (markdown)
"""


def render_llms_txt(entries: list[dict]) -> str:
    lines = [_LLMS_PREAMBLE]
    for e in entries:
        lines.append(
            f"- [{e['title']}]({SITE}/dispatches/{e['slug']}.md): {e['date']}, regime {e.get('tag', '?')}. {e['summary']}"
        )
    return "\n".join(lines) + "\n"


def render_llms_full(entries: list[dict], texts: dict[str, str]) -> str:
    parts = [_LLMS_PREAMBLE.split("## Docs")[0].strip(), ""]
    parts.append("Below is the complete text of every daily letter, newest first.")
    for e in entries:
        parts += [
            "",
            "---",
            "",
            f"# {e['title']}",
            f"Date: {e['date']} · Regime: {e.get('tag', '?')} · Canonical: {SITE}/dispatches/{e['slug']}.html",
            "",
            texts.get(e["slug"], "").strip(),
        ]
    return "\n".join(parts) + "\n"


def render_sitemap(entries: list[dict]) -> str:
    newest = max((e.get("date", "") for e in entries), default="")
    rows = []
    for path, freq, prio in BASE_URLS:
        lastmod = f"\n    <lastmod>{newest}</lastmod>" if newest and freq == "daily" else ""
        rows.append(
            f"  <url>\n    <loc>{SITE}{path}</loc>{lastmod}\n"
            f"    <changefreq>{freq}</changefreq>\n    <priority>{prio}</priority>\n  </url>"
        )
    for e in entries:
        rows.append(
            f"  <url>\n    <loc>{SITE}/dispatches/{e['slug']}.html</loc>\n"
            f"    <lastmod>{e['date']}</lastmod>\n"
            f"    <changefreq>monthly</changefreq>\n    <priority>0.6</priority>\n  </url>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(rows)
        + "\n</urlset>\n"
    )


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
def build_all(repo_root: Path | None = None) -> list[str]:
    root = repo_root or REPO_ROOT
    free_dir = root / "frontend" / "public" / "dispatches"
    paid_dir = root / "backend" / "seiche" / "dispatches"
    index = free_dir / "index.json"
    if not index.exists():
        raise SystemExit(f"no dispatch index at {index} (nothing to render is an error, not a no-op)")
    entries = json.loads(index.read_text())
    entries.sort(key=lambda e: e.get("date", ""), reverse=True)

    written: list[str] = []
    bodies: dict[str, str] = {}   # slug -> rendered letter HTML (for the feed)
    texts: dict[str, str] = {}    # slug -> full letter markdown (for llms-full)
    for e in entries:
        slug = e["slug"]
        md_path = free_dir / f"{slug}.md"
        if not md_path.exists():
            raise SystemExit(f"index lists {slug} but {md_path} is missing")
        free_md = md_path.read_text()
        desk_md = None
        # new letters write {slug}.desk.md; the box's history is {slug}.paid.md
        desk_path = paid_dir / f"{slug}.desk.md"
        if not desk_path.exists():
            desk_path = paid_dir / f"{slug}.paid.md"
        if any(m in free_md for m in MARKERS) and desk_path.exists():
            desk_md = desk_path.read_text()
        out = free_dir / f"{slug}.html"
        out.write_text(render_letter_page(e, free_md, desk_md))
        written.append(str(out))
        clean = _strip_markers(free_md).strip()
        bodies[slug] = md_to_html(clean) + ("\n" + md_to_html(desk_md.strip()) if desk_md else "")
        texts[slug] = clean + (("\n\n" + desk_md.strip()) if desk_md else "")

    archive = free_dir / "index.html"
    archive.write_text(render_archive(entries))
    written.append(str(archive))

    feed = free_dir / "feed.xml"
    feed.write_text(render_feed(entries, bodies))
    written.append(str(feed))

    # One stable machine feed for LiquiLens and the other lab desks. New
    # letters have typed story sidecars; older letters remain discoverable as
    # explicitly labelled pointers instead of vanishing from the wire.
    stories = []
    for e in entries:
        story_path = free_dir / f"{e['slug']}.json"
        if story_path.exists():
            story = json.loads(story_path.read_text())
            if story.get("slug") != e["slug"]:
                raise SystemExit(f"story sidecar slug mismatch at {story_path}")
            stories.append(story)
        else:
            stories.append({
                "schema": "seiche.legacy-dispatch-pointer.v1",
                "id": f"seiche:{e['slug']}",
                "product": "seiche",
                "slug": e["slug"],
                "canonical_url": f"{SITE}/dispatches/{e['slug']}.html",
                "headline": e.get("title"),
                "dek": e.get("summary"),
                "editorial_class": "legacy_dispatch",
                "publication_status": "PUBLISHED",
                "published_at": f"{e.get('date')}T00:00:00Z",
                "limitations": [
                    "Published before the structured analytical-story contract."
                ],
            })
    news = free_dir / "news.json"
    news.write_text(json.dumps({
        "schema": "seiche.analytical-feed.v1",
        "product": "seiche",
        "generated_from": "/dispatches/index.json",
        "entries": stories,
    }, indent=2, ensure_ascii=False) + "\n")
    written.append(str(news))

    public = root / "frontend" / "public"
    for name, content in (
        ("sitemap.xml", render_sitemap(entries)),
        ("llms.txt", render_llms_txt(entries)),
        ("llms-full.txt", render_llms_full(entries, texts)),
    ):
        p = public / name
        p.write_text(content)
        written.append(str(p))
    return written


def main(argv: list[str] | None = None) -> int:
    for p in build_all():
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
