"""Prerender the home page, so seiche.info says something without JavaScript.

The terminal is a React SPA: index.html ships an empty ``<div id="root">`` and
a module script, so anything that does not execute JavaScript sees about 400
characters of body text. That is every AI crawler that reads raw HTML, every
link unfurler, and every reader with scripting turned off. The board is a
static artifact by publish time anyway (export_public.py bakes
``data/overview.json`` before the vite build), so the home page can carry its
own reading with no server and no SSR.

This runs AFTER ``npm run build``, against the built site directory, and
rewrites exactly two things in ``index.html``:

  * the ``<noscript>`` block: masthead, what Seiche is, today's composite
    reading with its plain-English gloss, the headline numbers, the PROOF
    record, the latest daily letter in full, and the Week Ahead's
    pre-registered calls;
  * the Open Graph and Twitter card meta, so a pasted link previews the
    current reading instead of a fixed sentence. The card image stays
    /og2.png, the share card the fleet already ships: no new image machinery.

``<noscript>`` rather than markup inside ``#root``, deliberately. React clears
prerendered children of ``#root`` on its first commit, which is a visible
flash on every load for every reader who does have JavaScript; and a block of
text that sits in the DOM but is hidden by CSS is the exact shape search
engines treat as hidden text. ``<noscript>`` is the sanctioned mechanism:
invisible to the terminal, plain text to everything that cannot run it.
Nothing on the interactive path is touched, and ``#root``, the module script
and the stylesheet link are asserted intact after the rewrite.

Copy is not authored here. The description, the positioning and the reading
list are lifted from the llms.txt preamble, the conclusion sentence from
public_view (the module that defines the free public surface), and the regime
gloss and component names from the daily letter, so the home page cannot drift
from the surfaces that already state them.

Run at publish time, after the frontend build:

    python -m seiche.prerender frontend/dist

Stdlib only, deterministic, fail-loud: a missing snapshot, a missing dispatch
index or a missing ``<noscript>`` anchor is an error, not a quiet fallback to
the empty shell. A home page that silently reverts to 400 characters is the
failure this module exists to prevent, so it must never be the failure mode.
"""

from __future__ import annotations

import html
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from seiche import public_view
# The house copy rules and the plain-language names live in the letter; the
# reading list and the site description live in the llms.txt preamble. Both
# are imported rather than restated so this page cannot contradict them, and
# a rename upstream breaks a test in CI (publish gates on green) before it can
# break a publish.
from seiche.dispatch_daily import DISPLAY_NAMES, _REGIME_FRAME, _clean, _ordinal
from seiche.dispatch_pages import (
    _CSS, _LLMS_PREAMBLE, _esc, _strip_markers, md_to_html,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SITE_DIR = REPO_ROOT / "frontend" / "dist"

# The headline strip, in the order the desk reads it: policy rates first, then
# the balance-sheet levels, then the two facilities, then the market check.
# Labels are spelled out once here because this table is the version an
# indexer quotes; the audience knows the acronyms, the machine does not.
HEADLINE_ROWS = [
    ("sofr_pct", "SOFR", "%", 2),
    ("effr_pct", "EFFR", "%", 2),
    ("iorb_pct", "IORB", "%", 2),
    ("reserves_b", "Reserve balances", "$B", 0),
    ("rrp_b", "ON RRP", "$B", 1),
    ("tga_b", "Treasury General Account", "$B", 0),
    ("srf_accepted_b", "SRF accepted", "$B", 2),
    ("dw_b", "Discount window", "$B", 1),
    ("vix", "VIX", "pts", 2),
    ("hy_oas_pct", "HY OAS", "%", 2),
]

# The Week Ahead's section 5. Matched on the words, not the section number, so
# a reordered issue still finds it; if the heading is renamed outright the
# whole issue is rendered instead and the reason is printed. Degrading loudly
# beats failing a publish over a heading, and test_prerender.py asserts the
# committed issues still match, so a rename is caught in CI first.
_CALLS_HEADING = re.compile(r"^##\s+.*pre-registered calls.*$", re.I | re.M)
_NEXT_HEADING = re.compile(r"^##\s+", re.M)

_NOSCRIPT = re.compile(r"<noscript>.*?</noscript>", re.S)
# Matches exactly what inject() writes, trailing newline included, so a second
# run restores the shell byte for byte before appending again.
_META_BLOCK = re.compile(r"<!--prerender:meta-->.*?<!--/prerender:meta-->\n?", re.S)


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------
def _num(v, nd: int = 1) -> str:
    """A number, or 'n/a'. A dark input is never imputed and never blank."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(f):
        return "n/a"
    return f"{f:,.{nd}f}"


def _pct(v, nd: int = 0) -> str:
    return "n/a" if _num(v, nd) == "n/a" else f"{_num(float(v) * 100, nd)}%"


def _signed(v, nd: int = 0) -> str:
    return "n/a" if _num(v, nd) == "n/a" else f"{float(v):+,.{nd}f}"


def _stamp(iso: str) -> str:
    """'2026-08-04T06:02:35+00:00' -> '2026-08-04 06:02 UTC'. The raw string on
    anything unparseable: a build time is provenance, never invented."""
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return str(iso)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _row(cells: list[str], tag: str = "td") -> str:
    return "<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>"


def _table(head: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return ""
    return (
        "<table><thead>" + _row([_esc(h) for h in head], "th") + "</thead><tbody>"
        + "".join(_row(r) for r in rows) + "</tbody></table>"
    )


# ---------------------------------------------------------------------------
# copy lifted from llms.txt, so the two surfaces cannot disagree
# ---------------------------------------------------------------------------
def llms_intro_md() -> str:
    """What Seiche is, as llms.txt already says it.

    Drops the '# Seiche' title (the page has its own h1), folds the wrapped
    blockquote into one line so it renders as a single quote rather than eight,
    and stops before the reading list, which lands at the foot of the page
    under its own heading: a reader who arrived from a search result wants the
    board before the index of other pages.
    """
    src = _LLMS_PREAMBLE.split("## Docs")[0]
    lines = src.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    quote: list[str] = []

    def flush() -> None:
        if quote:
            out.append("> " + " ".join(quote))
            quote.clear()

    for line in lines:
        if line.startswith("# Seiche"):
            continue
        if line.startswith(">"):
            quote.append(line.lstrip("> ").strip())
            continue
        flush()
        out.append(line)
    flush()
    return "\n".join(out).strip()


def llms_docs_md() -> str:
    """The llms.txt reading list, minus its own heading (the page supplies one).

    Rendered from the same source so the home page can never offer a crawler a
    different set of doors than llms.txt does.
    """
    docs = _LLMS_PREAMBLE.split("## Docs", 1)
    if len(docs) < 2:
        return ""
    return docs[1].split("## Daily dispatches")[0].strip()


def llms_doc_urls() -> list[str]:
    """Every URL in the llms.txt reading list. The consistency test's anchor."""
    return re.findall(r"\]\((https?://[^)]+)\)", llms_docs_md())


# ---------------------------------------------------------------------------
# the sections
# ---------------------------------------------------------------------------
def reading_block(snap: dict) -> tuple[str, dict]:
    """Today's reading: the conclusion sentence, the gloss, the decomposition.

    Returns the HTML and the facts the meta tags need, so the card and the page
    can never quote two different boards.
    """
    pub = public_view.public_payload(snap)
    con = pub["conclusion"]
    comp = snap.get("engines", {}).get("composite", {}) or {}
    regime = (con.get("regime") or "UNRATED").upper()
    value = con.get("value")
    cov = con.get("coverage_pct")

    # The letter's own sentence for the reading, so the page and the dispatch
    # state it identically. public_view's packaged one-liner is deliberately
    # not reused verbatim: it joins the Tell onto the regime with a bare
    # period ("The board reads STRAIN (46/100). the Tell is ..."), and the
    # Tell gets its own paragraph here anyway.
    cov_txt = f", on {_num(cov, 0)}% coverage" if cov is not None else ""
    reading = f"The composite reads {_num(value, 0)} out of 100, {regime}{cov_txt}."
    editorial = snap.get("editorial") or {}
    headline = editorial.get("thesis") or f"The board reads {regime}, {_num(value, 0)} out of 100"
    gloss = editorial.get("standfirst") or _REGIME_FRAME.get(
        regime, "The board publishes what it sees and nothing else."
    )

    parts = [f"<p>{_esc(reading)} {_esc(gloss)}</p>"]

    tell = (snap.get("deep", {}) or {}).get("tell", {}) or {}
    if tell.get("ok") and tell.get("tell") is not None:
        parts.append(
            "<p>The Tell reads <strong>{t}</strong>, {reading}: the plumbing sits at the "
            "{p} percentile of its own history against the market's {m}, as of {d}. "
            "The Tell is reported beside the index, never weighted into it: divergence is "
            "a signal about positioning, not evidence of stress.</p>".format(
                t=_esc(_signed(tell.get("tell"))),
                reading=_esc(_clean(tell.get("reading", "no reading"))),
                p=_esc(_ordinal(tell.get("plumbing_pctl"))),
                m=_esc(_ordinal(tell.get("market_pctl"))),
                d=_esc(str(tell.get("asof", "n/a"))),
            )
        )

    rows = []
    for d in comp.get("decomposition", []) or []:
        if d.get("contribution") is None:
            continue
        name = DISPLAY_NAMES.get(str(d.get("component")), str(d.get("component")))
        flag = " (pinned near its ceiling)" if d.get("saturated") else ""
        rows.append([
            _esc(_clean(name) + flag),
            _esc(_num(d.get("score"), 1)),
            _esc(_num(d.get("weight"), 2)),
            _esc(_num(d.get("contribution"), 1)),
        ])
    if rows:
        parts.append(_table(
            ["component", "score 0-100", "weight", "contribution to the index"], rows))
        parts.append(
            "<p>A dead input is never imputed. Its weight is renormalised away and the "
            f"published coverage falls to say so: coverage today is {_esc(_num(comp.get('coverage_pct'), 0))}%.</p>"
        )

    facts = {
        "regime": regime,
        "value": value,
        "headline": headline,
        "reading": reading,
        "gloss": gloss,
        "generated_at": snap.get("generated_at") or "",
        "proof": pub["proof"],
        "editorial": editorial,
    }
    return "".join(parts), facts


def editorial_block(snap: dict) -> str:
    """Render the argument as a claim, an evidence ledger and a countercase.

    This is intentionally a view over ``snap.editorial`` rather than a second
    prose generator.  The dispatch, React front page, public API and raw-HTML
    page therefore quote one point-in-time editorial object.
    """
    editorial = snap.get("editorial") or {}
    thesis = editorial.get("thesis")
    if not thesis:
        return ""

    parts = [f'<p class="lede"><strong>{_esc(_clean(str(thesis)))}</strong></p>']
    if editorial.get("standfirst"):
        parts.append(f"<p>{_esc(_clean(str(editorial['standfirst'])))}</p>")

    confidence = editorial.get("confidence")
    if confidence:
        note = _clean(str(editorial.get("confidence_note") or ""))
        suffix = f": {_esc(note)}" if note else ""
        parts.append(
            f"<p><strong>Conviction: {_esc(str(confidence).upper())}</strong>{suffix}</p>"
        )

    evidence = [row for row in (editorial.get("evidence") or []) if row.get("claim")]
    if evidence:
        parts.append("<h3>Evidence ledger</h3><ol>")
        for row in evidence:
            label = _clean(str(row.get("label") or "Evidence"))
            claim = _clean(str(row["claim"]))
            source = _clean(str(row.get("source") or "source not recorded"))
            asof = _clean(str(row.get("asof") or "as-of date not recorded"))
            parts.append(
                f"<li><strong>{_esc(label)}.</strong> {_esc(claim)} "
                f"<small>Source: {_esc(source)}; as of {_esc(asof)}.</small></li>"
            )
        parts.append("</ol>")

    countercase = [row for row in (editorial.get("countercase") or []) if row.get("claim")]
    if countercase:
        parts.append("<h3>The countercase</h3><ul>")
        for row in countercase:
            claim = _clean(str(row["claim"]))
            source = _clean(str(row.get("source") or "source not recorded"))
            asof = _clean(str(row.get("asof") or "as-of date not recorded"))
            parts.append(
                f"<li>{_esc(claim)} <small>Source: {_esc(source)}; as of {_esc(asof)}.</small></li>"
            )
        parts.append("</ul>")

    return "".join(parts)


def headline_block(snap: dict) -> str:
    hl = snap.get("headline", {}) or {}
    rows = []
    for key, label, unit, nd in HEADLINE_ROWS:
        blk = hl.get(key) or {}
        if blk.get("value") is None:
            continue
        rows.append([_esc(label), _esc(f"{_num(blk.get('value'), nd)} {unit}"),
                     _esc(str(blk.get("asof", "n/a")))])
    if not rows:
        return ""
    return (
        "<p>Each line carries its own as-of date, because these series publish on "
        "different lags and averaging that away is how a stale print gets read as "
        "today's.</p>"
        + _table(["series", "latest", "as of"], rows)
    )


def proof_block(proof: dict) -> str:
    ec = proof or {}
    if ec.get("recall") is None:
        return ""
    ci = ec.get("recall_ci95") or []
    ci_txt = (f" (95% interval {_num(ci[0] * 100, 0)}% to {_num(ci[1] * 100, 0)}%)"
              if len(ci) == 2 else "")
    parts = [
        "<p>The scoreboard and the misses are published together, because the record is "
        "the product. On {n} funding events the board recalled <strong>{r}</strong>{ci}, "
        "at a base rate of {b}, with run-level precision {p} and a median lead of "
        "{lead} days.</p>".format(
            n=_esc(_num(ec.get("n_events"), 0)),
            r=_esc(_pct(ec.get("recall"), 0)),
            ci=_esc(ci_txt),
            b=_esc(_pct(ec.get("base_rate"), 1)),
            p=_esc(_pct(ec.get("precision_runs"), 0)),
            lead=_esc(_num(ec.get("median_lead_d"), 0)),
        )
    ]
    caveats = [c for c in (ec.get("caveats") or []) if c]
    if caveats:
        parts.append("<p>What the board says against itself:</p><ul>"
                     + "".join(f"<li>{_esc(_clean(c))}</li>" for c in caveats)
                     + "</ul>")
    episodes = [e for e in (ec.get("episodes") or []) if e.get("episode")]
    if episodes:
        rows = [[_esc(str(e.get("date", ""))), _esc(_clean(str(e.get("episode", "")))),
                 "in sample" if e.get("in_sample") else "out of sample"]
                for e in episodes]
        parts.append(_table(["date", "episode", "sample"], rows))
    return "".join(parts)


def calls_md(week_md: str) -> tuple[str, bool]:
    """The pre-registered calls out of a Week Ahead issue.

    Returns (markdown, matched). On a renamed heading the whole issue comes
    back with matched False: the calls are still on the page, and the caller
    prints why the cheaper path missed.
    """
    m = _CALLS_HEADING.search(week_md)
    if not m:
        return week_md.strip(), False
    rest = week_md[m.end():]
    nxt = _NEXT_HEADING.search(rest)
    return (rest[:nxt.start()] if nxt else rest).strip(), True


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------
def build_block(snap: dict, entries: list[dict], letters: dict[str, str]) -> tuple[str, dict]:
    reading_html, facts = reading_block(snap)
    dailies = [e for e in entries if not str(e.get("slug", "")).endswith("-week-ahead")]
    weeklies = [e for e in entries if str(e.get("slug", "")).endswith("-week-ahead")]
    latest = dailies[0] if dailies else (entries[0] if entries else None)
    week = weeklies[0] if weeklies else None

    head = (
        '<div class="top"><a class="wordmark" href="/">SEI<span>CHE</span></a>'
        '<span class="crumb"><a href="/dispatches/">dispatches</a> &middot; '
        '<a href="/guide">guide</a> &middot; '
        '<a href="/methodology">methodology</a> &middot; '
        '<a href="/skeptic">skeptic pack</a></span></div>'
        f'<div class="date">funding-stress &amp; leveraged-positioning early warning '
        f'&middot; free public data only &middot; board built {_esc(_stamp(facts["generated_at"]))}</div>'
        f'<h1>{_esc(facts["headline"])}</h1>'
        f'<p class="lede">{_esc(facts["gloss"])} This page is the board written out for readers '
        'and machines that do not run JavaScript. The interactive terminal, with the full '
        'engine set, the charts and the Time Machine, is the same URL with scripting on.</p>'
    )

    body = ['<div class="body">', md_to_html(llms_intro_md())]

    argument = editorial_block(snap)
    if argument:
        body += ["<h2>The argument</h2>", argument]

    body += ["<h2>Today's reading</h2>", reading_html]

    hb = headline_block(snap)
    if hb:
        body += ["<h2>The headline numbers</h2>", hb]

    pb = proof_block(facts["proof"])
    if pb:
        body += ["<h2>PROOF: the record, misses included</h2>", pb]

    if latest and letters.get(latest["slug"]):
        body += [
            f"<h2>The latest dispatch: {_esc(latest['title'])}</h2>",
            f'<p class="lede">{_esc(latest["date"])}'
            f'{" &middot; " + _esc(latest["tag"]) if latest.get("tag") else ""} &middot; '
            f'<a href="/dispatches/{_esc(latest["slug"])}">this letter as its own page</a>, '
            f'<a href="/dispatches/{_esc(latest["slug"])}.md">as markdown</a>, or the '
            f'<a href="/dispatches/">whole archive</a>. The free reading is below in full; '
            "the desk's forward read, which is free too, continues on the letter's own page.</p>",
            md_to_html(letters[latest["slug"]]),
        ]

    if week and letters.get(week["slug"]):
        md, matched = calls_md(letters[week["slug"]])
        if not matched:
            print("prerender: no 'pre-registered calls' heading in "
                  f"{week['slug']}; rendering the whole issue instead", file=sys.stderr)
        body += [
            "<h2>The Week Ahead: the calls on the record</h2>",
            f'<p class="lede">{_esc(week["title"])}, {_esc(week["date"])}. Each call carries a '
            'stable ID, the number the desk expects, the date it resolves and the rule that '
            'decides it, printed before the week runs. The next issue opens by grading them, '
            f'misses first. <a href="/dispatches/{_esc(week["slug"])}">The full issue</a>.</p>',
            md_to_html(md),
        ]
        facts["week_slug"] = week["slug"]

    docs = llms_docs_md()
    if docs:
        body += ["<h2>Read next</h2>", md_to_html(docs)]

    body.append("</div>")

    foot = (
        '<div class="foot">Seiche is free open source software '
        '(<a href="https://github.com/beepboop2025/seiche">AGPL-3.0, source</a>) and a free '
        'public good: no sign-in or paywall for the public desk. The core USD board uses '
        'public APIs from the Fed, NY Fed, OFR, Treasury and CFTC; global market coverage '
        'labels credentialed, licensed, derived-only and unavailable inputs explicitly. '
        '<a href="/llms.txt">llms.txt</a> &middot; '
        '<a href="/llms-full.txt">full letter corpus</a> &middot; '
        '<a href="/dispatches/feed.xml">Atom feed</a> &middot; '
        '<a href="/sitemap.xml">sitemap</a> &middot; Not investment advice.</div>'
    )

    inner = f"<style>{_CSS}</style>{head}{''.join(body)}{foot}"
    return f"<noscript>{inner}</noscript>", facts


def build_meta(facts: dict, snap: dict) -> dict[str, str]:
    """Live card copy. The image stays the share card the fleet already ships."""
    hl = snap.get("headline", {}) or {}

    def v(key: str, nd: int) -> str:
        return _num((hl.get(key) or {}).get("value"), nd)

    head = _clean(str(facts["headline"]))
    editorial = facts.get("editorial") or {}
    title = f"Seiche · {head[0].lower() + head[1:]}" if head else "Seiche"
    if editorial.get("thesis"):
        conviction = str(editorial.get("confidence") or "unrated").upper()
        desc = (
            f"{_clean(str(editorial.get('standfirst') or facts['reading']))} "
            f"Conviction: {conviction}. Evidence, countercase and dated source clocks published."
        )
    else:
        desc = (
            f"{facts['reading']} {facts['gloss']} "
            f"Reserves ${v('reserves_b', 0)}B, ON RRP ${v('rrp_b', 1)}B, "
            f"TGA ${v('tga_b', 0)}B, SOFR {v('sofr_pct', 2)}%. "
            "Free public data, no sign-in, historical diagnostic status and misses published."
        )
    return {
        "og:title": title,
        "og:description": desc,
        "twitter:title": title,
        "twitter:description": desc,
        "og:updated_time": str(facts.get("generated_at") or ""),
        "og:image:alt": "Seiche, the funding stress board for US money markets",
        "twitter:image:alt": "Seiche, the funding stress board for US money markets",
        "og:locale": "en_US",
    }


# ---------------------------------------------------------------------------
# injection
# ---------------------------------------------------------------------------
_PROPERTY_KEYS = ("og:", "article:")


def _attr(key: str) -> str:
    return "property" if key.startswith(_PROPERTY_KEYS) else "name"


def _set_meta(doc: str, key: str, value: str) -> tuple[str, bool]:
    """Replace one meta tag's content, matching the quote that opened it.

    The delimiter is backreferenced, not a ["'] class: the shipped
    description contains "the Fed's plumbing", and a non-greedy scan for
    either quote ends the match on that apostrophe and leaves half the old
    sentence stapled to the new one.
    """
    attr = _attr(key)
    pat = re.compile(
        r'(<meta\s+' + attr + r'=(["\'])' + re.escape(key) + r'\2\s+content=(["\']))'
        r'(?:(?!\3).)*' + r'\3',
        re.S)
    m = pat.search(doc)
    if not m:
        return doc, False
    return doc[:m.start()] + m.group(1) + _esc(value) + m.group(3) + doc[m.end():], True


def inject(doc: str, block: str, meta: dict[str, str]) -> str:
    if "<noscript>" not in doc:
        raise SystemExit("prerender: no <noscript> block in index.html to replace; "
                         "the shell changed shape, look before rewriting it blind")
    if len(_NOSCRIPT.findall(doc)) != 1:
        raise SystemExit("prerender: expected exactly one <noscript> block in index.html")
    doc = _NOSCRIPT.sub(lambda _m: block, doc, count=1)

    # A stale run's appended tags are replaced, never stacked.
    doc = _META_BLOCK.sub("", doc)

    added: list[str] = []
    for key, value in meta.items():
        doc, hit = _set_meta(doc, key, value)
        if not hit:
            added.append(f'<meta {_attr(key)}="{key}" content="{_esc(value)}" />')
    if added:
        extra = "<!--prerender:meta-->\n" + "\n".join(added) + "\n<!--/prerender:meta-->\n"
        if "</head>" not in doc:
            raise SystemExit("prerender: no </head> in index.html")
        doc = doc.replace("</head>", extra + "</head>", 1)

    # The interactive path is not this module's to touch. Assert it, do not
    # hope: a regex that ate the script tag would ship a dead terminal.
    for anchor in ('<div id="root">', "<script type=\"module\"", "rel=\"stylesheet\""):
        if anchor not in doc:
            raise SystemExit(f"prerender: {anchor!r} missing after rewrite; refusing to ship")
    return doc


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
def load_site(site_dir: Path) -> tuple[str, dict, list[dict], dict[str, str]]:
    index = site_dir / "index.html"
    snap_path = site_dir / "data" / "overview.json"
    disp_index = site_dir / "dispatches" / "index.json"
    for p in (index, snap_path, disp_index):
        if not p.exists():
            raise SystemExit(
                f"prerender: {p} is missing. Run this after export_public.py, "
                "seiche.dispatch_pages and the vite build, against the built site "
                "directory. A home page that quietly falls back to the empty shell "
                "is the bug this module exists to prevent.")
    snap = json.loads(snap_path.read_text())
    entries = json.loads(disp_index.read_text())
    entries.sort(key=lambda e: (e.get("date", ""), e.get("slug", "")), reverse=True)

    letters: dict[str, str] = {}
    for e in entries[:12]:
        md = site_dir / "dispatches" / f"{e['slug']}.md"
        if md.exists():
            # The HAS-DESK / HAS-PAID markers are generator bookkeeping. Left
            # in, md_to_html escapes them and they print as literal text on the
            # page, exactly as they would in the letter's own HTML if
            # dispatch_pages did not strip them first.
            letters[e["slug"]] = _strip_markers(md.read_text()).strip()
    return index.read_text(), snap, entries, letters


def build(site_dir: Path) -> int:
    """Rewrite index.html in place. Returns the no-JS body-text length."""
    doc, snap, entries, letters = load_site(site_dir)
    block, facts = build_block(snap, entries, letters)
    out = inject(doc, block, build_meta(facts, snap))
    (site_dir / "index.html").write_text(out)
    return len(body_text(out))


# ---------------------------------------------------------------------------
# measurement: the number this module is judged on, printed on every run
# ---------------------------------------------------------------------------
def body_text(doc: str) -> str:
    """The body text a client with scripting off actually reads.

    Only <body>; script/style/template contents dropped; <noscript> unwrapped
    and kept, since that is precisely what renders; tags stripped, entities
    decoded, whitespace collapsed.
    """
    m = re.search(r"<body[^>]*>(.*?)</body\s*>", doc, re.S | re.I)
    inner = m.group(1) if m else doc
    for tag in ("script", "style", "template"):
        inner = re.sub(rf"<{tag}\b.*?</{tag}\s*>", " ", inner, flags=re.S | re.I)
    inner = re.sub(r"</?noscript\s*>", " ", inner, flags=re.I)
    inner = re.sub(r"<!--.*?-->", " ", inner, flags=re.S)
    inner = re.sub(r"<[^>]+>", " ", inner)
    return re.sub(r"\s+", " ", html.unescape(inner)).strip()


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    site_dir = Path(args[0]) if args else DEFAULT_SITE_DIR
    n = build(site_dir)
    print(f"prerendered {site_dir / 'index.html'}: {n:,} characters of no-JS body text")
    return 0


if __name__ == "__main__":
    sys.exit(main())
