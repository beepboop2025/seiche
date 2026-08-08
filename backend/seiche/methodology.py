"""Citability Kit: the methodology page, the CSV export surface, and the
series catalog.

A reading nobody can check is a vibe. This module makes the board quotable:
every series the board holds is downloadable as a CSV with its provenance in
the header, the catalog of those series is one JSON away, and the method
behind every number lives on a versioned page with real citations and a
changelog that admits what changed. The page is generated, not hand-kept:
the composite weights come from config and the engine subsections come from
the engine module docstrings, so the documentation cannot drift from the
code that produced the number.

Build step (not a server duty): `PYTHONPATH=backend python -m seiche.methodology`
writes frontend/public/methodology.html, which the frontend build copies into
dist/. Run it in the same publish slot as dispatch_pages.
"""

from __future__ import annotations

import ast
import html
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from seiche.config import ALL_SERIES, COMPOSITE_WEIGHTS, DB_PATH, REGIMES

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINES_DIR = Path(__file__).resolve().parent / "engines"
DEFAULT_OUT = REPO_ROOT / "frontend" / "public" / "methodology.html"

SITE = "https://seiche.info"
METHODOLOGY_URL = f"{SITE}/methodology.html"
REPO_URL = "https://github.com/beepboop2025/seiche"

# The methodology version is the date of the newest changelog entry, so the
# page's version string moves exactly when the record of changes does.
CHANGELOG: list[tuple[str, str]] = [
    ("2026-07-28",
     "letter hygiene overhaul; letters before this date could re-headline "
     "slow series as news and printed raw z-scores without magnitude context; "
     "the archive is preserved as published."),
]
METHODOLOGY_VERSION = CHANGELOG[0][0]

# Cadence -> plain words for the CSV header and the series catalog. The point
# is the native lag doctrine: an old observation on a lagged series is the
# current print, not a failure, and the reader deserves to know which.
FREQ_DESC = {
    "D": ("daily", "published same day to next business day"),
    "W": ("weekly", "published a few days after the reference week"),
    "M": ("monthly", "published weeks after the reference month"),
    "ML": ("monthly, lagged", "publishes about two months after the reference month by design"),
    "Q": ("quarterly", "published about a quarter after the reference period"),
    "QL": ("quarterly, lagged", "publishes about two quarters after the reference period by design"),
}

# Bulk export is opt-IN by upstream, not opt-out by exception.
#
# The board reads from eight upstreams and only some of them let us hand their
# raw history to a third party. US government and US public-agency data (FRED's
# own Federal Reserve and Treasury series, OFR, Treasury FiscalData) is public
# domain and exports freely. Everything else is somebody's licensed product:
# CFETS asserts rights over its China fixings, BIS restricts redistribution of
# its statistics, exchange market data carries redistribution terms, and FRED
# itself mirrors copyrighted index data from CBOE, ICE BofA and S&P DJI.
#
# For those the board publishes DERIVED readings, which is a different act from
# republishing the series, and points the reader upstream for the raw history.
# A deny-list would silently start exporting the next licensed source somebody
# adds; an allow-list fails closed instead, which is the right direction for a
# question nobody wants to answer to a lawyer after the fact.
CSV_ALLOWED_SOURCES = {"fred", "ofr", "fiscaldata"}

# Licensed series that ride in on an otherwise-exportable upstream: FRED hosts
# these but does not own them.
CSV_RESTRICTED = {"VIX", "HY_OAS", "IG_OAS", "SP500"}

# Why each restricted upstream is restricted, named in the refusal so the
# reader knows exactly whose permission they need rather than ours.
CSV_SOURCE_OWNER = {
    "chinamoney": "CFETS, the China Foreign Exchange Trade System",
    "bis": "the Bank for International Settlements",
    "boj": "the Bank of Japan",
    "ecb": "the European Central Bank",
    "crypto": "the exchanges the quotes come from",
    "palimpsest": "the upstream fixing publishers behind the China series",
}


def _plain(x: float) -> str:
    """Plain decimal, no scientific notation, no trailing zeros."""
    s = f"{float(x):.6f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-") else "0"


def _no_dashes(s: str) -> str:
    """Repo convention: no em or en dashes in generated prose. Engine
    docstrings predate the rule; normalize on the way out."""
    return (s.replace(" — ", " - ").replace("—", " - ")
             .replace(" – ", " - ").replace("–", "-"))


# ---------------------------------------------------------------------------
# CSV export + series catalog
# ---------------------------------------------------------------------------

def csv_restriction(mnemonic: str) -> str | None:
    """Reason string when a series cannot be bulk-exported; None when it can."""
    spec = ALL_SERIES.get(mnemonic)
    label = spec.label if spec else mnemonic
    if mnemonic in CSV_RESTRICTED:
        return (f"{label} is third-party licensed data mirrored on FRED; "
                f"bulk redistribution is not ours to grant. Pull the raw "
                f"history from the upstream source; the board publishes only "
                f"derived readings of it.")
    source = getattr(spec, "source", None)
    if spec is not None and source not in CSV_ALLOWED_SOURCES:
        owner = CSV_SOURCE_OWNER.get(str(source), f"the {source} upstream")
        return (f"{label} comes from {owner}, which licenses its data rather than "
                f"placing it in the public domain; bulk redistribution is not ours "
                f"to grant. The board publishes derived readings of it and points "
                f"you upstream for the raw history.")
    return None


def render_series_csv(s) -> str:
    """date,value CSV with a provenance comment header. `s` is a
    sources.base.Series; the spec (unit, cadence) comes from config."""
    spec = ALL_SERIES.get(s.mnemonic)
    unit = spec.unit if spec else (s.unit or "?")
    freq = spec.freq if spec else (s.freq or "D")
    cadence, lag = FREQ_DESC.get(freq, (freq, "cadence unlisted"))
    lines = [
        f"# Seiche series {s.mnemonic}: {s.label}",
        f"# source: {s.source} (remote id {s.remote_id}), unit: {unit}, "
        f"cadence: {cadence}, native lag: {lag}",
        f"# retrieved_at: {s.fetched_at}, last_observation: {s.asof}, "
        f"staleness: {s.staleness}",
        "# vintage: the upstream's current print, not a reconstructed "
        "as-of view; lightly revised series revise here too",
        f"# cite: Seiche ({METHODOLOGY_URL}); credit for the data belongs "
        f"to the source above",
        "date,value",
    ]
    for idx, v in s.points.dropna().items():
        lines.append(f"{idx.date().isoformat()},{_plain(v)}")
    return "\n".join(lines) + "\n"


def _fetched_mnemonics() -> set[str]:
    """Which registry series the store currently holds. Read-only peek at the
    fetch log; a missing database means a cold box, not an error."""
    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            rows = conn.execute("SELECT mnemonic FROM fetches").fetchall()
        finally:
            conn.close()
        return {r[0] for r in rows}
    except sqlite3.Error:
        return set()


def series_index() -> dict:
    """The citable-series catalog: every registry mnemonic with its config
    metadata, export URLs, and whether the store holds it right now."""
    have = _fetched_mnemonics()
    rows = []
    for m, spec in sorted(ALL_SERIES.items()):
        cadence, lag = FREQ_DESC.get(spec.freq, (spec.freq, "cadence unlisted"))
        # One licence answer for both formats: the CSV export and the JSON
        # twin enforce csv_restriction, so the catalog must not advertise a
        # link either route will 403. This also covers source-restricted
        # upstreams (BIS, CFETS, exchanges), which the old CSV field missed.
        restricted = csv_restriction(m) is not None
        rows.append({
            "mnemonic": m,
            "label": spec.label,
            "source": spec.source,
            "remote_id": spec.remote_id,
            "unit": spec.unit,
            "cadence": cadence,
            "native_lag": lag,
            "csv": None if restricted else f"/api/series/{m}.csv",
            "csv_restricted": restricted or None,
            "json": None if restricted else f"/api/series/{m}",
            "available": m in have,
        })
    return {
        "schema": "seiche.series-index.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "methodology": METHODOLOGY_URL,
        "cite_as": f'Seiche funding-stress terminal, {SITE} (methodology '
                   f'{METHODOLOGY_VERSION})',
        "note": "CSV headers carry source, unit, native lag and retrieved-at; "
                "restricted rows are third-party licensed series we display "
                "but do not redistribute in bulk.",
        "n_series": len(rows),
        "series": rows,
    }


# ---------------------------------------------------------------------------
# Methodology page
# ---------------------------------------------------------------------------

def engine_sections() -> list[dict]:
    """One subsection per engine module: name + first docstring paragraph
    (plus the second when the first is a bare title line). Parsed with ast so
    generating docs never imports or executes engine code."""
    out = []
    for path in sorted(ENGINES_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        try:
            doc = ast.get_docstring(ast.parse(path.read_text()))
        except SyntaxError:
            doc = None
        if not doc:
            continue
        paras = [p.strip() for p in doc.split("\n\n") if p.strip()]
        text = paras[0]
        if len(paras) > 1 and "\n" not in paras[0]:
            text = paras[0] + " " + paras[1]
        out.append({"module": path.stem,
                    "text": _no_dashes(" ".join(text.split()))})
    return out


# What each composite component measures, in one clause. Hand-written here
# rather than scraped so a weight retune shows up in the table automatically
# while the wording stays deliberate.
_WEIGHT_WHY = {
    "tails": "the tail seismograph on the SOFR complex, including the SOFR-IORB pressure gauge",
    "kink": "proximity to the reserve-scarcity kink",
    "weather": "forward crunch-window risk from the settlement calendar",
    "confession": "SRF take-up and discount window; paying up is an admission",
    "rvxray": "relative-value complex size and fragility",
    "resonance": "basin amplification, a louder ring to the same calendar forcing",
    "hydrophone": "plumbing connectivity, how far a shock travels through the pipes",
    "undertow": "damping loss; slower free decay is critical slowing down",
    "auctions": "Treasury supply digestion",
    "warehouse": "dealer balance-sheet saturation",
    "buffers": "RRP buffer emptiness; zero means no shock absorber left",
}

_CSS = """
:root { --bg:#000; --panel:#0b0d15; --edge:#1c1f2b; --text:#edeef4;
        --dim:#9aa0b6; --faint:#787f95; --accent:#9c8fe8;
        --accent-soft:#bbaffe; --accent-bright:#d9d4ff;
        --mono:ui-monospace,Menlo,Consolas,monospace;
        color-scheme:dark; }
* { box-sizing:border-box; margin:0; padding:0; }
body { background:var(--bg); color:var(--text);
       font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
       font-size:14px; line-height:1.7; max-width:820px; margin:0 auto;
       padding:40px 24px 90px; }
a { color:var(--accent-soft); text-decoration:none; }
a:hover { color:var(--accent-bright); text-decoration:underline; }
h1 { font-weight:500; font-size:clamp(26px,4.6vw,34px); letter-spacing:-.02em;
     margin:30px 0 6px; }
h2 { font-weight:500; font-size:11.5px; letter-spacing:.15em;
     text-transform:uppercase; color:var(--accent); margin:44px 0 14px;
     padding-top:20px; border-top:1px solid var(--edge); }
h3 { font-weight:600; font-size:13.5px; margin:18px 0 4px; }
p { margin:12px 0; }
.dim { color:var(--dim); }
.faint { color:var(--faint); font-size:12px; }
code, .mono { font-family:var(--mono); font-size:.9em; }
code { background:var(--panel); padding:1px 6px; border-radius:5px;
       color:var(--accent-bright); }
table { border-collapse:collapse; width:100%; margin:14px 0; font-size:13px; }
th, td { text-align:left; padding:7px 10px; border-bottom:1px solid var(--edge); }
th { color:var(--faint); font-weight:500; font-size:11px;
     letter-spacing:.08em; text-transform:uppercase; }
td.num { font-family:var(--mono); }
.cite { background:var(--panel); border:1px solid var(--edge); border-radius:10px;
        padding:14px 16px; font-family:var(--mono); font-size:12.5px;
        white-space:pre-wrap; word-break:break-word; margin:14px 0; }
ul { margin:12px 0 12px 20px; }
li { margin:6px 0; }
.top { display:flex; justify-content:space-between; align-items:baseline;
       gap:14px; flex-wrap:wrap; padding-bottom:16px;
       border-bottom:1px solid var(--edge); }
.wordmark { font-weight:500; letter-spacing:.30em; font-size:18px; }
.wordmark span { color:var(--accent); }
"""


def render_methodology_html(board_version: str | None = None) -> str:
    if board_version is None:
        from seiche import assemble  # lazy: keeps module import light
        board_version = assemble.VERSION_LABEL
    e = html.escape
    version_line = (f"methodology {METHODOLOGY_VERSION} / board "
                    f"{board_version}")

    weight_rows = "\n".join(
        f"<tr><td><code>{e(k)}</code></td><td class='num'>{_plain(w)}</td>"
        f"<td class='dim'>{e(_WEIGHT_WHY.get(k, ''))}</td></tr>"
        for k, w in COMPOSITE_WEIGHTS.items()
    )
    lo = 0.0
    regime_rows = []
    for cut, name in REGIMES:
        hi = "100" if cut > 100 else _plain(cut)
        regime_rows.append(
            f"<tr><td><code>{e(name)}</code></td>"
            f"<td class='num'>{_plain(lo)} to {hi}</td></tr>")
        lo = cut
    regime_rows = "\n".join(regime_rows)

    engines_html = "\n".join(
        f"<h3><code>{e(sec['module'])}</code></h3>\n<p class='dim'>{e(sec['text'])}</p>"
        for sec in engine_sections()
    )

    changelog_html = "\n".join(
        f"<li><span class='mono'>{e(day)}</span>: {e(note)}</li>"
        for day, note in CHANGELOG
    )

    cite_block = (
        f"Seiche funding-stress terminal ({version_line}).\n"
        f"{METHODOLOGY_URL}\n"
        f"Source code: {REPO_URL} (AGPL-3.0).\n"
        f"Data: free public sources with native lags; per-series provenance "
        f"in each CSV header."
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Seiche methodology</title>
<meta name="description" content="How Seiche computes its funding stress reading: composite weights, point-in-time rules, the PROOF scoreboard, the reserve-demand kink, and one subsection per engine. Versioned, with citations and a changelog.">
<link rel="canonical" href="{METHODOLOGY_URL}">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<style>{_CSS}</style>
</head>
<body>
<div class="top">
  <div class="wordmark">SEI<span>CHE</span></div>
  <div class="faint"><a href="/">back to the board</a> &middot; <a href="/guide.html">plain English guide</a></div>
</div>

<h1>Methodology</h1>
<p class="faint mono">{e(version_line)}</p>
<p class="dim">This page is generated from the code that computes the board:
the weights come from the config file, the engine subsections come from the
engine module docstrings. If the code changes, this page changes with it, and
the changelog at the bottom says when and why.</p>

<h2>The composite and its weights</h2>
<p>The headline number is a 0 to 100 nowcast of US dollar funding stress,
a weighted blend of {len(COMPOSITE_WEIGHTS)} sub-scores. Each sub-score is a pure function of
free public data; the weights are an editorial opinion, published here so the
opinion is checkable. Missing components renormalize the remaining weights
and the coverage percentage says how much of the blend was live.</p>
<table>
<tr><th>component</th><th>weight</th><th>what it measures</th></tr>
{weight_rows}
</table>
<p>The number maps to a regime word:</p>
<table>
<tr><th>regime</th><th>index band</th></tr>
{regime_rows}
</table>
<p class="dim">Context layers (the Tell, Echo, the physics engines, every
forecast view) are deliberately NOT weighted into the composite: resemblance
and forecasts are context, not evidence of stress now.</p>

<h2>Point-in-time rules</h2>
<ul>
<li>Every published reading is frozen into a forward-accruing as-published
record the moment it is computed. No later recomputation can revise it.</li>
<li>Each reading is hashed into a tamper-evident chain and anchored to
Bitcoin via OpenTimestamps; anyone can verify at <code>/api/notary</code>
that no past reading was altered or backdated.</li>
<li>Historical replays (the Time Machine) use final-vintage data and say so
in the payload. Weekly aggregates are lightly revised versus what was on
screens that day; the replay never pretends otherwise.</li>
<li>Native publication lags are shown, never hidden. A two-month-old
observation on a lagged monthly series is the current print, and the board
labels it that way instead of calling it news.</li>
</ul>

<h2>The PROOF scoreboard</h2>
<p>PROOF is the board's own report card: a construction-PIT diagnostic of the
composite against dated funding-stress events, with recall, false-alarm
runs, lead times, and the misses printed next to the hits. The evaluation
uses final/current-vintage history, so it is not validated-backtest evidence.
It follows the early-warning-system tradition of Kaminsky and Reinhart: signals
are judged on hits, false alarms and lead time against a dated event list,
not on in-sample fit.</p>
<p>The competence boundary is stated, not implied. Endogenous events build
up inside the plumbing (reserve scarcity, calendar settlement pressure) and
leave a trail this board can read weeks ahead. Exogenous events (a pandemic,
a single-bank run, a policy shock) arrive from outside the plumbing and are
not visible in it beforehand; PROOF reports recall split by class so the
tool never claims to see what it cannot. A leakage audit publishes the gains
the board refuses to claim from deliberately leaky variants.</p>
<ul>
<li>Kaminsky, G., Lizondo, S., and Reinhart, C. (1998). Leading Indicators
of Currency Crises. IMF Staff Papers 45(1).</li>
<li>Kaminsky, G. and Reinhart, C. (1999). The Twin Crises: The Causes of
Banking and Balance-of-Payments Problems. American Economic Review 89(3).</li>
</ul>

<h2>The reserve-demand kink</h2>
<p>The reserve demand curve is flat when reserves are abundant and turns
steeply negative near scarcity. The New York Fed estimates this elasticity
as periodic research; Seiche fits it continuously as a hockey-stick
regression of the SOFR-IORB spread on reserves/GDP, grid-searching the
breakpoint and reporting the kink in today's dollars, the distance from
current reserves, and days-to-kink at the trailing drain rate. Fit quality
gates the confidence, and the model-vs-market consistency check discounts
the sub-score when the fit and the tape disagree.</p>
<p>The lineage is the NY Fed's Reserve Demand Elasticity work of Afonso,
Giannone, La Spada and Williams; their estimate is the reference method and
this board's hinge fit is the always-on approximation of it.</p>
<ul>
<li>Afonso, G., Giannone, D., La Spada, G., and Williams, J. C. When Are
Central Bank Reserves Ample? Federal Reserve Bank of New York Staff Report
1019 (the time-varying reserve demand curve model).</li>
<li>Afonso, Giannone, La Spada and Williams. Tracking Reserve Demand
Elasticity in Real Time. Liberty Street Economics / the NY Fed's published
Reserve Demand Elasticity measure.</li>
</ul>

<h2>The engines</h2>
<p class="dim">One subsection per engine module, taken from the first
paragraph of each module's docstring. Every engine is a pure function
of its input series and degrades to an explicit reason string instead of a
silent gap.</p>
{engines_html}

<h2>Data, downloads and citing</h2>
<p>Every series the board holds can be downloaded as CSV from
<code>/api/series/&lt;MNEMONIC&gt;.csv</code> on
<code>api.seiche.info</code>, with source, unit, native lag and retrieved-at
in the header. The catalog of mnemonics is at
<code>/api/series/index.json</code>. A handful of third-party licensed
series (mirrored on FRED) are display-only on the board and excluded from
bulk export; the catalog marks them.</p>
<h3>Cite as</h3>
<div class="cite">{e(cite_block)}</div>

<h2>Changelog</h2>
<ul>
{changelog_html}
</ul>

<p class="faint">Free public data with native lags. Not investment advice.
Seiche is free open source software (AGPL-3.0) and a public good.</p>
</body>
</html>
"""


def write_methodology(out: Path | None = None) -> Path:
    path = out or DEFAULT_OUT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_methodology_html())
    return path


def main(argv: list[str] | None = None) -> int:
    import sys
    args = argv if argv is not None else sys.argv[1:]
    out = Path(args[0]) if args else DEFAULT_OUT
    print(write_methodology(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
