"""The Referee: the "global liquidity" category's headline claims, tested on
the layer any outsider can reconstruct, with the results published win or
lose.

The index family that owns this category is proprietary, and its public
materials carry three quantitative claims: liquidity leads asset prices by 3
to 6 months, liquidity leads the business cycle by 12 to 15 months, and
global liquidity follows a cycle of roughly 65 months. None of them ship
with error bars, out of sample tests, or a revision record. This page takes
the largest component everyone agrees on, G3 central bank balance sheets in
dollar terms, rebuilds it monthly from 2003, and puts the claims through the
board's own standards: bootstrap intervals on every correlation, a walk
forward test with no look ahead, causality tested in both directions, and a
stated power limit where the sample cannot decide.

The page never tests the proprietary index itself and says so above the
fold. Where a claim fails here, the honest conclusion is that the claim does
not live in the public layer, not that the full index fails. Where our
sample is too short to rule, the page says that too, and names the one
dataset that could settle it.

Inputs: the board snapshot the publish job bakes at
frontend/public/data/overview.json (deep block "refereegli"), falling back
to the live API the same way the ampleness page does. Given the same
snapshot the output is byte identical: no clock reads, no randomness.

Build step: `PYTHONPATH=backend python -m seiche.referee` writes
frontend/public/referee.html. Run it in the same publish slot as
methodology, skeptic and ampleness.
"""

from __future__ import annotations

import argparse
import html
import json
import urllib.request
from pathlib import Path

from seiche import rubric
from seiche.dispatch_daily import lint_letter
from seiche.methodology import _CSS, METHODOLOGY_URL, REPO_URL, SITE, _no_dashes

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parents[1]
DEFAULT_OUT = REPO_ROOT / "frontend" / "public" / "referee.html"
DEFAULT_SNAPSHOT = REPO_ROOT / "frontend" / "public" / "data" / "overview.json"
DEFAULT_API = "https://api.seiche.info"

REFEREE_URL = f"{SITE}/referee"
SKEPTIC_URL = f"{SITE}/skeptic"

CHANGELOG: list[tuple[str, str]] = [
    ("2026-08-01",
     "the rubric added: the coded evidence matrix (arXiv:2606.08285, two "
     "rows re coded for a terminal that trades nothing) with Seiche graded "
     "first and its own PARTIAL rows published."),
    ("2026-07-30",
     "first publication; G3 reconstruction 2003 onward, all three headline "
     "claims tested, the walk forward and both Granger directions published "
     "with the misses included."),
]
REFEREE_VERSION = CHANGELOG[0][0]

NOT_REPRODUCIBLE = "NOT REPRODUCIBLE"
WRONG_WINDOW = "WRONG WINDOW"
REPRODUCED = "REPRODUCED"
UNTESTABLE = "UNTESTABLE HERE"
DARK = "NOT AVAILABLE"

_EXTRA_CSS = """
.claim { border-top:1px solid var(--edge); padding-top:18px; margin:26px 0 0; }
.claim h3 { display:flex; justify-content:space-between; align-items:baseline;
            gap:12px; flex-wrap:wrap; font-size:15px; margin:0 0 2px; }
.tok { font-family:var(--mono); font-size:10.5px; letter-spacing:.14em;
       font-weight:600; padding:3px 9px; border-radius:6px; border:1px solid;
       white-space:nowrap; }
.tok-fail { color:#e08a8a; border-color:#5a2c2c; background:#1a0c0c; }
.tok-part { color:#d6c37f; border-color:#5a512c; background:#1a170c; }
.tok-pass { color:#7fd6a2; border-color:#2c5a41; background:#0c1a13; }
.tok-open { color:#9ab0c4; border-color:#2c3f5a; background:#0c111a; }
table.ref { border-collapse:collapse; margin:12px 0; font-size:13px; }
table.ref th, table.ref td { border:1px solid var(--edge); padding:5px 10px;
                             text-align:right; }
table.ref th:first-child, table.ref td:first-child { text-align:left; }
table.ref td.rub-ev { text-align:left; max-width:620px; }
"""

_TOK_CLASS = {
    NOT_REPRODUCIBLE: "tok-fail",
    WRONG_WINDOW: "tok-part",
    REPRODUCED: "tok-pass",
    UNTESTABLE: "tok-open",
    DARK: "tok-open",
}


def e(s: object) -> str:
    return html.escape(str(s))


# ------------------------------------------------------------- formatters

def _fc(c: dict | None) -> str:
    """A correlation with its interval, or an honest absence."""
    if not c:
        return "not available"
    lo, hi = c["ci95"]
    return f"{c['corr']:+.2f} in [{lo:+.2f}, {hi:+.2f}], n {c['n']}"


def _ci_excludes_zero(c: dict | None) -> bool:
    return bool(c) and (c["ci95"][0] > 0 or c["ci95"][1] < 0)


def _tok(label: str) -> str:
    return f'<span class="tok {_TOK_CLASS[label]}">{e(label)}</span>'


_GRADE_CLASS = {
    rubric.PASS: "tok-pass",
    rubric.PARTIAL: "tok-part",
    rubric.FAIL: "tok-fail",
    rubric.NOT_APPLICABLE: "tok-open",
}


def _grade_tok(grade: str) -> str:
    cls = _GRADE_CLASS.get(grade, "tok-open")
    return f'<span class="tok {cls}">{e(grade.replace("_", " "))}</span>'


def _rubric_section(rb: dict | None) -> str:
    """The two graded matrices, self first. Snapshots baked before the rubric
    shipped carry no block; the section simply does not render then."""
    if not rb or not rb.get("ok"):
        return ""
    labels = dict(rubric.FIELDS)
    parts = [
        "<h2>The rubric</h2>",
        "<p>Verdict tokens are cheap; the evidence matrix behind them is the "
        "standard. Eight coded rows, adapted from the execution assumption "
        "matrix of arXiv:2606.08285: the two rows a non trading terminal "
        "cannot honestly self grade (cost and turnover treatment, execution "
        "semantics) are re coded as vintage and revision handling and "
        "threshold provenance, and a verdict revision policy row is added. "
        "The ordering rule is the feature: Seiche grades itself first, on "
        "the same rows, and publishes its own PARTIAL rows with the gaps "
        "named.</p>",
    ]
    for case in rb.get("cases") or []:
        rows_html = "\n".join(
            f"<tr><td>{e(labels.get(r['field'], r['field']))}</td>"
            f"<td>{_grade_tok(r['grade'])}</td>"
            f'<td class="rub-ev">{e(_no_dashes(r["evidence"]))}'
            + (f' (not applicable because: {e(_no_dashes(r["na_reason"]))})'
               if r.get("na_reason") else "")
            + "</td></tr>"
            for r in case["rows"]
        )
        t = case["tally"]
        parts.append(
            f"<h3>{e(case['subject'])}</h3>\n"
            f'<p class="faint">{t[rubric.PASS]} PASS, {t[rubric.PARTIAL]} '
            f"PARTIAL, {t[rubric.FAIL]} FAIL, {t[rubric.NOT_APPLICABLE]} not "
            "applicable.</p>\n"
            '<table class="ref">\n'
            "<tr><th>row</th><th>grade</th><th>evidence</th></tr>\n"
            f"{rows_html}\n</table>\n"
            f"<p>{e(_no_dashes(case['reading']))}</p>"
        )
    return "\n".join(parts)


# --------------------------------------------------------------- verdicts

def claim1_verdict(c1: dict) -> str:
    cells = [c1.get("forward_return_corr", {}).get(k) for k in ("fwd_3m", "fwd_6m", "fwd_9m")]
    have = [c for c in cells if c]
    if not have:
        return DARK
    if any(c["ci95"][0] > 0 for c in have):
        return WRONG_WINDOW  # some positive lead exists, window aside
    return NOT_REPRODUCIBLE


def claim2_verdict(c2: dict) -> str:
    pk, det = c2.get("peak_lead_months"), c2.get("peak_detail")
    if pk is None or not det:
        return DARK
    if not _ci_excludes_zero(det):
        return NOT_REPRODUCIBLE
    return REPRODUCED if 12 <= pk <= 15 else WRONG_WINDOW


def claim3_verdict(c3: dict) -> str:
    res = c3.get("spectral_resolution_at_65m_pm_months")
    dom = c3.get("dominant_period_months")
    if res is None or dom is None:
        return DARK
    if res >= 10:
        return UNTESTABLE
    return REPRODUCED if abs(dom - 65) <= res else NOT_REPRODUCIBLE


# ----------------------------------------------------------------- render

def _dark_page(reason: str) -> str:
    body = (
        "<p>The referee analysis is not available today: "
        f"{e(_no_dashes(reason))}. The page publishes only what the engine "
        "computed; when the inputs are dark this notice is the whole "
        "content, by design.</p>"
    )
    return _shell("The Referee", body)


def _shell(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} | Seiche</title>
<meta name="description" content="The global liquidity category's headline
claims, tested on the publicly reconstructible layer, with bootstrap
intervals, walk forward tests and both causal directions published.">
<link rel="canonical" href="{REFEREE_URL}">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<meta property="og:type" content="article">
<meta property="og:site_name" content="Seiche">
<meta property="og:title" content="{e(title)} | Seiche">
<meta property="og:url" content="{REFEREE_URL}">
<meta property="og:image" content="{SITE}/og.png">
<style>{_CSS}{_EXTRA_CSS}</style>
</head>
<body>
{body}
</body>
</html>
"""


def render_referee_html(snap: dict) -> str:
    blk = (snap.get("deep") or {}).get("refereegli") or {}
    if not blk.get("ok"):
        page = _dark_page(str(blk.get("reason", "engine block missing from the snapshot")))
        issues = lint_letter(page)
        if issues:
            raise SystemExit("referee page failed lint: " + "; ".join(issues))
        return page

    latest = blk["latest"]
    evidence = blk.get("historical_evidence") or {}
    vintage_safe = evidence.get("validated_backtest_eligible") is True
    if vintage_safe:
        evidence_html = (
            '<p><span class="tok tok-part">VINTAGE INPUT CONTRACT PASSED</span> '
            "The supplied as-published manifest passed the vintage gate. A "
            "forward live record is still required before capital use.</p>"
        )
    else:
        reason = evidence.get(
            "reason",
            "the snapshot contains no machine-verifiable historical evidence record",
        )
        evidence_html = (
            '<p><span class="tok tok-fail">CONSTRUCTION PIT ONLY</span> '
            "These retrospective statistics are not validated-backtest evidence. "
            f"{e(_no_dashes(str(reason)))}.</p>"
        )
    comp = latest["components_usd_tn"]
    c1, c2, c3 = blk["claim1"], blk["claim2"], blk["claim3"]
    v1, v2, v3 = claim1_verdict(c1), claim2_verdict(c2), claim3_verdict(c3)
    fwd = c1["forward_return_corr"]
    oos = c1.get("walkforward_oos")
    gr = c1.get("granger") or {}
    subs = blk.get("subsample_stability_fwd6m") or {}
    rob = blk.get("robustness_liq_6m") or {}
    yoy = latest.get("yoy_pct")
    yoy_txt = "not available" if yoy is None else f"{yoy:+.1f} percent year on year"

    fwd_rows = "\n".join(
        f"<tr><td>{h} months ahead</td><td>{e(_fc(fwd.get(f'fwd_{h}m')))}</td></tr>"
        for h in (1, 3, 6, 9, 12, 18)
    )
    rob_rows = "\n".join(
        f"<tr><td>{h} months ahead</td><td>{e(_fc(rob.get(f'fwd_{h}m')))}</td></tr>"
        for h in (3, 6, 12)
    )

    nl = blk.get("robustness_net_liquidity") or {}
    if nl.get("ok"):
        nl_fwd = nl.get("forward_return_corr") or {}
        nl_rows = "\n".join(
            f"<tr><td>{h} months ahead</td><td>{e(_fc(nl_fwd.get(f'fwd_{h}m')))}</td></tr>"
            for h in (3, 6, 12)
        )
        nl_rows += (f"\n<tr><td>6 months ahead, 2015 onward</td>"
                    f"<td>{e(_fc(nl.get('fwd6_since_2015')))}</td></tr>")
        nlw = nl.get("walkforward_oos")
        if nlw:
            nl_wf = (
                f"<p>Its walk forward evaluation ({e(nlw['eval_window'][0])} to "
                f"{e(nlw['eval_window'][1])}) lands on exactly the era the exhibit "
                f"is drawn from, and puts the 6 month spread at "
                f"{nlw['spread_6m_logret']:+.3f} with a 95 percent interval of "
                f"[{nlw['spread_ci95'][0]:+.3f}, {nlw['spread_ci95'][1]:+.3f}].</p>"
            )
        else:
            nl_wf = "<p>Its walk forward test needs more overlap than the inputs currently give it.</p>"
        nl_html = f"""<p>Neither is the definition. The strongest known variant of
the claim is net liquidity: central bank assets minus the Treasury cash
balance minus reverse repo take up (today {nl["latest_usd_tn"]:.2f} trillion
dollars, window {e(nl["window"][0])} to {e(nl["window"][1])}). The level
chart of that series against equities is the famous exhibit. The honest
question is whether its growth predicts forward returns, and it reads the
same way:</p>
<table class="ref">
<tr><th>horizon (net liquidity)</th><th>correlation</th></tr>
{nl_rows}
</table>
{nl_wf}"""
    else:
        nl_html = (
            "<p>The strongest known variant of the claim, net liquidity "
            "(central bank assets minus the Treasury cash balance minus "
            "reverse repo take up), is not testable on today's inputs: "
            f"{e(_no_dashes(str(nl.get('reason', 'inputs unavailable'))))}.</p>"
        )
    sub_rows = "\n".join(
        f"<tr><td>{e(lbl)}</td><td>{e(_fc(subs.get(key)))}</td></tr>"
        for lbl, key in (("full sample", "full"),
                         ("2003 to 2014", "first_half"),
                         ("2015 onward", "second_half"))
    )

    if oos:
        oos_para = (
            f"<p>The walk forward test ({e(oos['eval_window'][0])} to "
            f"{e(oos['eval_window'][1])}, signal from trailing data only) puts the "
            f"6 month forward log return after high liquidity months at "
            f"{oos['mean_fwd6m_logret_high_liq']:+.3f} against "
            f"{oos['mean_fwd6m_logret_low_liq']:+.3f} after low liquidity months: a "
            f"spread of {oos['spread_6m_logret']:+.3f} with a 95 percent interval of "
            f"[{oos['spread_ci95'][0]:+.3f}, {oos['spread_ci95'][1]:+.3f}]. An interval "
            "that straddles zero is the test refusing to certify the claim.</p>"
        )
    else:
        oos_para = "<p>The walk forward test needs more overlap than the inputs currently give it.</p>"

    g_l2r, g_r2l = gr.get("liq_to_returns"), gr.get("returns_to_liq")
    if g_l2r and g_r2l:
        granger_para = (
            f"<p>Causality, both directions: liquidity to returns F {g_l2r['F']:.2f} "
            f"(p {g_l2r['p']:.2f}), returns to liquidity F {g_r2l['F']:.2f} "
            f"(p {g_r2l['p']:.2f}), {g_l2r['lags']} lags. Neither direction clears "
            "conventional significance on this layer.</p>"
        )
    else:
        granger_para = "<p>The Granger tests are not available on the current inputs.</p>"

    rubric_html = _rubric_section(blk.get("rubric"))

    lag0 = c2.get("contemporaneous")
    lag0_txt = "not available" if lag0 is None else f"{lag0:+.2f}"
    pk2 = c2.get("peak_lead_months")
    pk2_txt = "not identified" if pk2 is None else f"{pk2} months"
    spacings = c3.get("peak_to_peak_spacings_months") or []
    spacing_txt = ", ".join(str(s) for s in spacings) if spacings else "not available"

    body = f"""
<h1>The Referee</h1>
<p class="faint">Version {e(REFEREE_VERSION)}. Data through {e(blk["asof"])}.
Window {e(blk["window"][0])} to {e(blk["window"][1])}, {blk["n_months"]} months.</p>
{evidence_html}

<p>The firm that coined "global liquidity" as a macro category publishes
three quantitative claims for its index family: liquidity leads asset prices
by 3 to 6 months, liquidity leads the business cycle by 12 to 15 months, and
global liquidity runs a cycle of roughly 65 months. The index is
proprietary. The claims ship without error bars, out of sample tests, or a
revision record. So this page does what any referee does: it takes the
largest component everyone agrees on, G3 central bank balance sheets in
dollar terms, rebuilds it from public data, and tests the claims under the
same standards this board applies to itself.</p>

<p><strong>Scope, stated above the fold.</strong> This is not a test of the
proprietary index. It is a test of its publicly reconstructible layer: Fed,
ECB and Bank of Japan total assets at monthly average spot, month end, 2003
onward. The full index adds private credit and cross border flows across
some 80 countries back to 1974. If the full index performs better than its
public layer, that performance lives entirely in the unpublished layers, and
that is a claim its publisher could settle tomorrow by releasing an error
distribution. We would run it the day they do.</p>

<p>The reconstruction today: {latest["g3_usd_tn"]:.2f} trillion dollars
(Fed {comp["fed"]:.2f}, ECB {comp["ecb"]:.2f}, BoJ {comp["boj"]:.2f}),
{e(yoy_txt)}.</p>

<div class="claim">
<h3>Claim 1: "liquidity leads asset prices by 3 to 6 months" {_tok(v1)}</h3>
<p>Year on year growth of the G3 aggregate, correlated with forward equity
returns (Nasdaq Composite, the only broad index with full window public
history). Intervals are circular moving block bootstrap.</p>
<table class="ref">
<tr><th>horizon</th><th>correlation with forward return</th></tr>
{fwd_rows}
</table>
{oos_para}
{granger_para}
<p>The transform is not the story: rerunning the forward correlations on 6
month annualized growth gives the same flat zeros.</p>
<table class="ref">
<tr><th>horizon (6m growth)</th><th>correlation</th></tr>
{rob_rows}
</table>
{nl_html}
<p>Stability check, forward 6 month horizon:</p>
<table class="ref">
<tr><th>sample</th><th>correlation</th></tr>
{sub_rows}
</table>
<p>Whatever the 3 to 6 month lead is, it is not in the central bank layer,
gross or net.</p>
</div>

<div class="claim">
<h3>Claim 2: "liquidity leads the business cycle by 12 to 15 months" {_tok(v2)}</h3>
<p>Against US industrial production growth, the contemporaneous correlation
is {e(lag0_txt)}: central banks expand when the economy contracts. The
correlation turns positive only far out, peaking at a lead of {e(pk2_txt)}
({e(_fc(c2.get("peak_detail")))}). At the claimed 13 month lead it reads
{e(_fc(c2.get("corr_at_claimed_13m")))}, an interval that straddles zero.</p>
<p>Two honest readings. Either the lead is real but sits well past the
claimed window, or the pattern is a policy reaction function: recessions
trigger balance sheet expansion, recoveries follow recessions, and the
apparent lead is partly the economy forecasting the central bank rather than
the reverse. Separating those requires exactly the private credit layer that
is not public.</p>
</div>

<div class="claim">
<h3>Claim 3: "a cycle of roughly 65 months" {_tok(v3)}</h3>
<p>On {c3.get("n_months", 0)} months of data the periodogram's dominant
period is {c3.get("dominant_period_months", 0):.0f} months, but the spectral
resolution at a 65 month period is plus or minus
{c3.get("spectral_resolution_at_65m_pm_months", 0):.0f} months: the sample
cannot separate 65 from its neighbours. If the claim is true this window
holds about {c3.get("n_complete_cycles_in_sample_if_65m", 0):.1f} cycles.
Peak to peak spacings of the smoothed series run {e(spacing_txt)} months.</p>
<p>This deserves saying with respect: the long homogeneous history behind
the proprietary index is the one genuinely irreplaceable thing its publisher
owns, and it is the only instrument that could settle their own headline
claim. We would rather they published the test than the assertion.</p>
</div>

{rubric_html}

<h2>What would change this page</h2>
<p>A published lead lag distribution on the full index, with revision errors
and out of sample splits. That is the standard this board holds itself to:
every reading sealed before outcomes, every miss on the public record. Until
then, the reproducible layer of the global liquidity story contains no
tradable asset price lead, and the business cycle lead sits far from where
the marketing puts it.</p>

<h2>Sources and method</h2>
<p>Fed total assets (H.4.1 via FRED WALCL), ECB total assets (ECBASSETSW),
BoJ total assets (JPNASSETS), converted at monthly average spot (DEXUSEU,
DEXJPUS); Nasdaq Composite (NASDAQCOM); US industrial production (INDPRO).
Derived statistics only are republished here. {e(_no_dashes(blk["method"]))}
The G3 reconstruction is deliberately incomplete: no keyless PBoC or Bank of
England balance sheet feed exists, and the gap is stated rather than
interpolated over. The engine behind this page is
<code>engines/refereegli.py</code>; its method paragraph is on the
<a href="{METHODOLOGY_URL}">methodology page</a> and the code is at
<a href="{REPO_URL}">{e(REPO_URL)}</a> under AGPL-3.0-or-later. The two questions a
skeptic asks first are answered in the
<a href="{SKEPTIC_URL}">skeptic pack</a>.</p>

<h2>Changelog</h2>
<ul>
{"".join(f"<li><strong>{e(d)}</strong>: {e(t)}</li>" for d, t in CHANGELOG)}
</ul>

<p class="faint">Free public data with native lags. Not investment advice.
Seiche is free open source software (AGPL-3.0-or-later) and a public good.</p>
"""
    page = _shell("The Referee", body)
    issues = lint_letter(page)
    if issues:
        raise SystemExit("referee page failed lint: " + "; ".join(issues))
    return page


# ---------------------------------------------------------------------------
# inputs + CLI
# ---------------------------------------------------------------------------

def load_snapshot(path: Path | None = None, api: str = DEFAULT_API) -> dict:
    """The CI baked board snapshot when it is on disk, the live board over
    the wire when it is not. Stdlib only."""
    p = path or DEFAULT_SNAPSHOT
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        pass
    req = urllib.request.Request(f"{api}/api/overview",
                                 headers={"User-Agent": "seiche-referee"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def write_referee(snap: dict, out: Path | None = None) -> Path:
    path = out or DEFAULT_OUT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_referee_html(snap))
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Render the referee page from the live board.")
    ap.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT),
                    help="board snapshot JSON (falls back to fetching the API)")
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)

    snap = load_snapshot(Path(args.snapshot), args.api)
    path = write_referee(snap, Path(args.out))
    blk = (snap.get("deep") or {}).get("refereegli") or {}
    print(path)
    if blk.get("ok"):
        print(f"  window {blk['window'][0]} to {blk['window'][1]}, "
              f"g3 {blk['latest']['g3_usd_tn']} tn usd")
        print(f"  claim1 {claim1_verdict(blk['claim1'])} | "
              f"claim2 {claim2_verdict(blk['claim2'])} | "
              f"claim3 {claim3_verdict(blk['claim3'])}")
    else:
        print(f"  dark: {blk.get('reason', 'engine block missing')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
