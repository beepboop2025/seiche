"""The Skeptic Pack: the two questions an economist opens with, answered first.

Every serious reader of a board like this asks the same two things before they
ask anything else. Is it just autocorrelation, one series predicting itself?
And is there look-ahead in the backtest? Seiche already computes both answers
on every cycle (the leak audit runs the one-switch leakage protocol against
our own pipeline; the orthogonal run rebuilds the same index with the target's
own variable family deleted) and then buries them inside a payload nobody
reads. This page pulls them to the front, prints the numbers, and hands the
reader the commands to reconstruct any past board and to prove no past reading
was edited after the fact.

Method: the page is assembled, never asserted. Sections 1 and 2 render live
board fields and degrade to an explicit "not yet published" block when the
field is missing or the engine is dark. Sections 3 and 4 describe machinery
that lives in this repo, so the generator checks the source for the route and
the verifier before claiming them; delete the endpoint and the section flips
to "not yet published" instead of lying. Every section states its own limit,
including the ones that make the board look worse.

Inputs: the board snapshot the publish job bakes at
frontend/public/data/overview.json before the page steps run (the same file
the static site uses as its offline fallback). When that file is absent the
generator falls back to fetching /api/overview over the wire, stdlib only,
the same way dispatch_daily does. Given the same snapshot the output is
byte-identical: no clock reads, no random anything.

Build step (not a server duty): `PYTHONPATH=backend python -m seiche.skeptic`
writes frontend/public/skeptic.html, which the frontend build copies into
dist/. Run it in the same publish slot as methodology and dispatch_pages.
"""

from __future__ import annotations

import argparse
import html
import json
import urllib.request
from pathlib import Path

from seiche.dispatch_daily import lint_letter
from seiche.methodology import _CSS, METHODOLOGY_URL, REPO_URL, SITE, _no_dashes

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parents[1]
DEFAULT_OUT = REPO_ROOT / "frontend" / "public" / "skeptic.html"
DEFAULT_SNAPSHOT = REPO_ROOT / "frontend" / "public" / "data" / "overview.json"
DEFAULT_API = "https://api.seiche.info"

SKEPTIC_URL = f"{SITE}/skeptic"

# The page version is the date of the newest changelog entry, so the version
# string moves exactly when the record of changes does.
CHANGELOG: list[tuple[str, str]] = [
    ("2026-07-28",
     "first publication; the leak audit and the orthogonal run were computed "
     "on the board for months before this page surfaced them."),
]
SKEPTIC_VERSION = CHANGELOG[0][0]

# A date to replay in the worked example. The generator prefers a dated
# endogenous episode from the board's own event list, so the command the
# reader copies rebuilds a board this page has just made a claim about.
FALLBACK_REPLAY_DATE = "2025-09-15"

# The section keys, in page order. `section_status` reports one of these per
# key so a build log (and the tests) can see which sections were real.
SECTION_KEYS = ("leak_audit", "orthogonal", "point_in_time", "notary", "falsifiers")

_EXTRA_CSS = """
.gap { background:var(--panel); border:1px solid var(--edge); border-radius:10px;
       padding:14px 16px; margin:14px 0; }
.gap strong { color:var(--accent-soft); }
.cmd { background:var(--panel); border:1px solid var(--edge); border-radius:10px;
       padding:14px 16px; font-family:var(--mono); font-size:12.5px;
       line-height:1.6; white-space:pre; overflow-x:auto; margin:14px 0; }
.limit { border-left:2px solid var(--edge); padding:2px 0 2px 14px;
         margin:14px 0; color:var(--dim); font-size:13px; }
.limit b { color:var(--faint); font-weight:600; text-transform:uppercase;
           letter-spacing:.08em; font-size:11px; }
.q { color:var(--accent-bright); font-weight:500; }
"""


# ---------------------------------------------------------------------------
# formatting: anything that cannot be rendered as a number returns None and the
# sentence around it is dropped, because the lint refuses placeholder copy
# ---------------------------------------------------------------------------
def _num(x, d: int = 2) -> str | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return f"{v:,.{d}f}"


def _signed(x, d: int = 3) -> str | None:
    s = _num(x, d)
    if s is None:
        return None
    try:
        return f"+{s}" if float(x) >= 0 else s
    except (TypeError, ValueError):
        return None


def _pct(x, d: int = 0) -> str | None:
    try:
        v = float(x) * 100.0
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    return f"{v:.{d}f}%"


def _txt(s) -> str:
    """Engine-supplied prose enters the page through here: the house copy rule
    applies to it too, and the lint would otherwise block the whole page over
    one engine docstring's em dash."""
    return _no_dashes(" ".join(str(s).split()))


def _sentence(s) -> str:
    """Engine text as a finished sentence: cleaned, escaped, closed once."""
    t = _txt(s)
    return html.escape(t if t.endswith((".", "!", "?")) else t + ".")


def _source_has(filename: str, *needles: str) -> bool:
    """Does a backend module still carry the machinery a section claims? Read,
    never imported: generating a page must not execute the server."""
    try:
        text = (BACKEND_DIR / filename).read_text()
    except OSError:
        return False
    return all(n in text for n in needles)


def _survives(ortho_recall, full_recall, base_rate) -> bool:
    """Did the orthogonal index actually keep the result? The bar: comfortably
    above the event base rate, and not a collapse against the full index. Set
    here so the page's verdict sentence is a computation, not an opinion."""
    try:
        r = float(ortho_recall)
    except (TypeError, ValueError):
        return False
    try:
        if r < 3.0 * float(base_rate):
            return False
    except (TypeError, ValueError):
        pass
    try:
        if r < 0.6 * float(full_recall):
            return False
    except (TypeError, ValueError):
        pass
    return True


def _not_yet(what: str, needed: str) -> str:
    return (f"<div class='gap'><strong>Not yet published.</strong> {html.escape(what)} "
            f"{html.escape(needed)}</div>")


def _limit(text: str) -> str:
    return f"<p class='limit'><b>Limit</b><br>{html.escape(text)}</p>"


# ---------------------------------------------------------------------------
# 1. the leak audit
# ---------------------------------------------------------------------------
def _leak_section(deep: dict) -> tuple[str, bool]:
    la = (deep.get("leakaudit") or {})
    rows = la.get("rows") or []
    if not la.get("ok") or not rows:
        return _not_yet(
            "The leak audit is dark on this snapshot.",
            "It needs the deep layer live with enough scored history to rebuild the "
            "index three times and score every variant against the same event list; "
            "when that runs, this section prints what each deliberate leak would "
            "have bought.",
        ), False

    head = [
        "<p>The one-switch protocol, run against ourselves. The audit rebuilds the "
        "same backtestable index with exactly ONE discipline deliberately broken, "
        "scores every variant against the SAME events, and publishes the gain that "
        "break would have bought. A clean pipeline is one whose published number "
        "sits at the bottom of its own audit table: everything above it is skill "
        "the board refuses to claim.</p>"
    ]

    by = {str(r.get("toggle")): r for r in rows}
    clean = by.get("clean") or {}
    gains = [(r.get("lg_auroc"), r) for r in rows
             if str(r.get("toggle")) != "clean" and _num(r.get("lg_auroc")) is not None]
    if gains:
        best_lg, best = max(gains, key=lambda g: float(g[0]))
        c_auroc, b_auroc = _num(clean.get("auroc"), 3), _num(best.get("auroc"), 3)
        lg_txt = _signed(best_lg, 3)
        if c_auroc and b_auroc and lg_txt:
            if float(best_lg) > 0:
                head.append(
                    f"<p>The largest gain on offer today is <code>{html.escape(str(best.get('toggle')))}</code>: "
                    f"event AUROC {b_auroc} against the honest {c_auroc}, a leakage gain of "
                    f"{lg_txt}. That is the number a leaky version of this board would print "
                    f"instead, and it is exactly the number this board declines to print.</p>")
            else:
                head.append(
                    f"<p>No break on the table buys skill today. The best any leaky variant "
                    f"manages is {b_auroc} event AUROC against the honest {c_auroc} "
                    f"({lg_txt}), which is the pleasant version of this result: the "
                    f"chronological-transform discipline is also the better instrument.</p>")

    body = ["<table>",
            "<tr><th>toggle</th><th>what breaks</th><th>AUROC</th><th>recall</th>"
            "<th>run precision</th><th>LG AUROC</th><th>LG recall</th></tr>"]
    for r in rows:
        cells = [
            f"<code>{html.escape(str(r.get('toggle')))}</code>",
            f"<span class='dim'>{html.escape(_txt(r.get('what_breaks', '')))}</span>",
            _num(r.get("auroc"), 3) or "not scored",
            _num(r.get("recall"), 3) or "not scored",
            _num(r.get("precision_runs"), 3) or "not scored",
            _signed(r.get("lg_auroc"), 3) or "not scored",
            _signed(r.get("lg_recall"), 3) or "not scored",
        ]
        num = " class='num'"
        body.append(
            f"<tr><td>{cells[0]}</td><td>{cells[1]}</td>"
            + "".join(f"<td{num}>{c}</td>" for c in cells[2:])
            + "</tr>")
    body.append("</table>")

    tail = []
    if la.get("reading"):
        tail.append(f"<p>{_sentence(la['reading'])}</p>")
    sha = la.get("clean_index_sha256")
    if sha:
        repro = ("hashed identically both times" if la.get("bit_reproducible")
                 else "did NOT hash identically, which is a bug and is printed rather than hidden")
        tail.append(
            f"<p>Determinism check: the clean build ran twice on the same inputs and "
            f"{repro} (sha256 prefix <code>{html.escape(str(sha))}</code>, over the dated "
            f"index values). Pin that hash and you can tell whether tomorrow's audit "
            f"scored the same index you read about today.</p>")
    if la.get("asof"):
        tail.append(f"<p class='faint mono'>audit as of {html.escape(str(la['asof']))}</p>")

    caveats = [_txt(c) for c in (la.get("caveats") or [])]
    if caveats:
        tail.append("<p class='dim'>The audit's own caveats, verbatim from the engine "
                    "that computed the table:</p><ul class='dim'>"
                    + "".join(f"<li>{html.escape(c)}</li>" for c in caveats) + "</ul>")
    tail.append(_limit(
        "A near zero gain on one toggle certifies immunity to that leak class and "
        "nothing wider. The audit is run on final-vintage data, so it measures "
        "pipeline discipline, not vendor revisions. And note the awkward direction: "
        "if the real pipeline were already peeking forward, the forward-peeking "
        "toggle would buy almost nothing, so read this table next to the "
        "forward as-published record in section 3, not instead of it."))

    return "\n".join(head + body + tail), True


# ---------------------------------------------------------------------------
# 2. the orthogonal test
# ---------------------------------------------------------------------------
def _orthogonal_section(deep: dict) -> tuple[str, bool]:
    bt = (deep.get("backtest") or {})
    orth = (bt.get("orthogonal") or {})
    oc = (orth.get("event_capture") or {})
    if not orth.get("ok") or not oc:
        return _not_yet(
            "The orthogonal run is not on this snapshot.",
            "It needs the PROOF backtest live, because it is the same capture test "
            "rerun on an index rebuilt without the target's own variable family; "
            "when that runs, this section prints both scores side by side.",
        ), False

    ec = (bt.get("event_capture") or {})
    out = [
        "<p>The event this board is scored against is a spike in the SOFR minus IORB "
        "spread. The published index contains spread and tail terms. So the objection "
        "is fair and obvious: is the signal just the target wearing a costume? The "
        "orthogonal run answers it by deleting the whole tails family from the index "
        "and rerunning the identical capture test, same events, same threshold, same "
        "warmup slice.</p>"
    ]
    if orth.get("why"):
        out.append(f"<p class='dim'>{_sentence(orth['why'])}</p>")

    rows = []

    def _row(label: str, full, ortho, fmt) -> None:
        a, b = fmt(full), fmt(ortho)
        if a is None and b is None:
            return
        rows.append(f"<tr><td>{html.escape(label)}</td><td class='num'>{a or 'not scored'}</td>"
                    f"<td class='num'>{b or 'not scored'}</td></tr>")

    _row("event recall", ec.get("recall"), oc.get("recall"), lambda v: _pct(v, 0))
    _row("run precision", ec.get("precision_runs"), oc.get("precision_runs"), lambda v: _pct(v, 0))
    _row("events in sample", ec.get("n_events"), oc.get("n_events"), lambda v: _num(v, 0))
    _row("alert runs", ec.get("n_alert_runs"), oc.get("n_alert_runs"), lambda v: _num(v, 0))
    _row("median lead, trading days", ec.get("median_lead_d"), oc.get("median_lead_d"),
         lambda v: _num(v, 0))
    if rows:
        out.append("<table>"
                   "<tr><th>metric</th><th>published index</th><th>tails removed</th></tr>"
                   + "".join(rows) + "</table>")

    # The verdict sentence is computed, never assumed. A run that collapses to
    # the base rate is the objection winning, and this page prints that reading
    # instead of the flattering one it was built to carry.
    r_full, r_orth = _pct(ec.get("recall")), _pct(oc.get("recall"))
    if r_orth:
        survives = _survives(oc.get("recall"), ec.get("recall"), ec.get("base_rate"))
        base_txt = _pct(ec.get("base_rate"))
        against = f", against {r_full} with everything in" if r_full else ""
        if survives:
            out.append(
                f"<p>Reading it in one line: strip out the variable family the event is "
                f"defined on and the board still captures {r_orth} of events{against}. "
                f"The capture does not live in the spread term. Whatever this board is "
                f"doing, it is not one series predicting itself.</p>")
        else:
            out.append(
                f"<p>Reading it in one line, and it is not the flattering one: strip out "
                f"the variable family the event is defined on and capture falls to "
                f"{r_orth}{against}"
                + (f", against a base rate of {base_txt}" if base_txt else "")
                + ". On this sample the objection wins, and the honest response is to "
                  "weight the headline capture number accordingly rather than to "
                  "explain the drop away.</p>")

    ci = oc.get("recall_ci95")
    if isinstance(ci, list) and len(ci) == 2 and _pct(ci[0]) and _pct(ci[1]):
        out.append(f"<p class='dim'>Wilson 95% interval on that recall: "
                   f"{_pct(ci[0])} to {_pct(ci[1])}. The interval is wide because the "
                   f"event count is small, and it is printed rather than rounded away.</p>")

    weights = orth.get("weights") or {}
    if weights:
        wr = "".join(
            f"<tr><td><code>{html.escape(str(k))}</code></td><td class='num'>{_num(v, 3) or 'not set'}</td></tr>"
            for k, v in weights.items())
        out.append("<p>What is left carrying the signal, with weights renormalized:</p>"
                   "<table><tr><th>surviving component</th><th>weight</th></tr>"
                   + wr + "</table>")
    excluded = orth.get("excluded_components") or []
    if excluded:
        out.append("<p class='dim'>Out of the run: "
                   + ", ".join(f"<code>{html.escape(str(c))}</code>" for c in excluded)
                   + ". Some are excluded because they are the target's own family, "
                     "the rest because they are live only and cannot be reconstructed "
                     "point in time at all.</p>")

    sig = ((bt.get("rigor") or {}).get("significance") or {})
    if sig.get("ok"):
        p = _num(sig.get("p_value"), 3)
        verdict = _txt(sig.get("verdict", ""))
        nperm = _num(sig.get("n_permutations"), 0)
        if p and verdict:
            out.append(
                f"<p>The harder test, on the same page as the flattering one. Permute "
                f"the alert runs at random and ask how often chance placement captures "
                f"as many events: p is {p}"
                + (f" over {nperm} permutations" if nperm else "")
                + f", verdict <em>{html.escape(verdict)}</em>. That is the board's own "
                  f"rigor block, and it belongs here more than anywhere else on the "
                  f"site.</p>")

    out.append(_limit(
        "State plainly what this does not prove. The surviving components are not "
        "independent of the funding tape: reserves, facility take-up and dealer "
        "pairs all respond to the same conditions the spread responds to, so this "
        "is a costume test, not an independence proof. The event is still defined "
        "on the spread. The index under test is the reconstructable lite index, "
        "not the live composite, which carries more information and cannot be "
        "backtested honestly. And the event count is small enough that the "
        "confidence interval, not the point estimate, is the number to quote."))
    return "\n".join(out), True


# ---------------------------------------------------------------------------
# 3. the construction-PIT replay
# ---------------------------------------------------------------------------
_REPLAY_CMD = """# 1. from the hosted board: run a construction-PIT reconstruction
# (a date nobody has replayed lately is rebuilt from scratch, so give it room)
curl -s --max-time 600 https://api.seiche.info/api/asof/REPLAY_DATE \\
  | python3 -c 'import json,sys; d=json.load(sys.stdin); \\
print(d["asof"], d["engines"]["composite"]["value"], d["engines"]["composite"]["regime"])'

# 2. or trust nothing of ours: run the same replay from the public source
git clone https://github.com/beepboop2025/seiche && cd seiche
pip install -e ./backend
python3 -c 'import asyncio, json; from seiche import assemble; \\
print(json.dumps(asyncio.run(assemble.snapshot_asof("REPLAY_DATE"))["engines"]["composite"]))'"""


def _replay_example(deep: dict) -> tuple[str, str]:
    """A date to replay, and the honest clause describing it. The generator
    prefers an episode the board actually flagged early, because the point of
    the example is to let the reader check a claim this page just made; if no
    such episode is on the board it picks any dated one and drops the claim
    rather than dressing a miss as a catch."""
    episodes = ((deep.get("backtest") or {}).get("episodes") or [])
    caught = [ep for ep in episodes
              if str(ep.get("class")) == "endogenous" and ep.get("date")
              and _num(ep.get("first_alert_lead_d"), 0) is not None]
    if caught:
        ep = sorted(caught, key=lambda r: str(r.get("date")))[-1]
        lead = _num(ep.get("first_alert_lead_d"), 0)
        return (str(ep["date"]),
                f"a squeeze the board first flagged {lead} trading days ahead")
    dated = [ep for ep in episodes if ep.get("date")]
    if dated:
        ep = sorted(dated, key=lambda r: str(r.get("date")))[-1]
        return str(ep["date"]), "an episode from the board's own event list"
    return FALLBACK_REPLAY_DATE, "a dated funding squeeze"


def _pit_section(deep: dict) -> tuple[str, bool]:
    if not (_source_has("api.py", '@app.get("/api/asof/{date}")')
            and _source_has("assemble.py", "def snapshot_asof")):
        return _not_yet(
            "The replay endpoint is not in this build.",
            "This section claims a reader can reconstruct any past board; it prints "
            "only while the Time Machine route and the truncating snapshot both exist "
            "in the source it is generated from.",
        ), False

    date, clause = _replay_example(deep)
    cmd = _REPLAY_CMD.replace("REPLAY_DATE", date)
    out = [
        "<p>Every engine on this board is a pure function of its input series. No "
        "engine fetches, no engine remembers. That is not a style preference, it is "
        "what makes the next paragraph possible: truncate every series to "
        "observations dated on or before a past date and rerun the same code. What "
        "comes out is a construction-PIT reconstruction, not the publication vintage "
        "that was visible on screens that day. Nothing dated later is carried back.</p>",
        f"<p>So do not take the sections above on trust. Rebuild the board yourself "
        f"for {html.escape(date)}, {html.escape(clause)}, and read the composite "
        f"reconstructed for that date:</p>",
        f"<div class='cmd'>{html.escape(cmd)}</div>",
        "<p>The whole payload comes back, every engine, so any claim about any past "
        "day is checkable against the same code that made it. Coverage starts around "
        "June 2018, which is where the free public series start. Be patient with the "
        "hosted route: a date nobody has asked for lately is rebuilt from the sources "
        "on demand, so the first call can run for minutes on a busy box before the "
        "replay is cached per date and served with a day-long cache header. The "
        "second route needs nothing from us but the code, which is the version a "
        "skeptic should prefer anyway.</p>",
        "<p>The stronger artifact sits next to it. From the day it was switched on, "
        "every published reading is appended to a forward-accruing as-published "
        "notary at <code>/api/notary</code>, whose hash-linked entries and proofs can be "
        "checked without a private route. A replay is honest reconstruction; the "
        "as-published record is not reconstruction at all.</p>",
    ]
    out.append(_limit(
        "Replays run on final/current-vintage data and are not validated-backtest "
        "evidence. Daily market prints are effectively "
        "unrevised, but weekly H.4.1 aggregates are lightly revised against what "
        "was on screens that day, and the payload says so in its own vintage note. "
        "The deep analytics layer is excluded from replays on purpose, because its "
        "percentile bases are defined against the live sample and replaying them "
        "would be the look-ahead this page exists to refuse. Replays are cached per "
        "date, and the operator can put the route behind sign-in with "
        "SEICHE_ASOF_AUTH=1, so a 401 means gated, not missing."))
    return "\n".join(out), True


# ---------------------------------------------------------------------------
# 4. the notary
# ---------------------------------------------------------------------------
_NOTARY_CMD = """# 1. pull the ledger and recompute every link yourself
curl -s 'https://api.seiche.info/api/notary?n=500' > notary.json
python3 -c '
import hashlib, json
d = json.load(open("notary.json"))
rows = sorted(d["entries"], key=lambda r: r["seq"])
prev = rows[0]["prev_hash"]
print("window starts at genesis:", prev == d["genesis"])
for e in rows:
    link = "%s|%s|%s|%s" % (prev, e["record_sha256"], e["utc"], e["pit_date"])
    assert hashlib.sha256(link.encode()).hexdigest() == e["chain_hash"], e["seq"]
    prev = e["chain_hash"]
print("chain intact through", len(rows), "links, head", prev)
'

# 2. prove a reading was not backdated (Bitcoin, via OpenTimestamps)
curl -s https://api.seiche.info/api/notary/proof/RECORD_SHA256 -o reading.ots
ots verify -d RECORD_SHA256 reading.ots

# 3. the signed record: the operator's key, then the per-day commitments
curl -s https://api.seiche.info/api/attest/pubkey"""


def _notary_section() -> tuple[str, bool]:
    if not (_source_has("notary.py", "def verify_chain", "def chain_hash", "GENESIS")
            and _source_has("api.py", '@app.get("/api/notary")')):
        return _not_yet(
            "The notary is not in this build.",
            "This section claims a reader can prove a past published reading was not "
            "edited later; it prints only while the hash chain and its public "
            "endpoint both exist in the source it is generated from.",
        ), False

    out = [
        "<p>A record that can be quietly improved is not a record. Every as-published "
        "reading is canonicalised to JSON with fixed key order, hashed with SHA-256, "
        "and chained to the one before it as "
        "<code>sha256(prev_hash|digest|utc|pit_date)</code>, from a fixed published "
        "root, <code>seiche-notary-genesis-v1</code>. Change any earlier reading, "
        "reorder two links, delete one, and every later link stops reproducing.</p>",
        "<p>The chain is served with no authentication on purpose, and the check runs "
        "on your machine, not ours:</p>",
        f"<div class='cmd'>{html.escape(_NOTARY_CMD)}</div>",
        "<p>Step one proves internal consistency: the server cannot hand you a ledger "
        "with an edited past that still recomputes. Step two is the part that does "
        "not depend on trusting the operator at all: each digest is submitted to the "
        "OpenTimestamps calendars and settles into the Bitcoin chain, so a reading "
        "can be shown to have existed by a given block time and cannot be backdated. "
        "Step three is the signature layer, an Ed25519 key over "
        "<code>domain:stream:day:record_hash</code>, with per-day commitments at "
        "<code>/api/attest/stream/{stream}</code>. The digest in step two is the "
        "<code>record_sha256</code> of the ledger entry you are checking, and what "
        "comes back is a standard detached OpenTimestamps proof that any OTS verifier "
        "will take. A digest whose anchor has not settled yet answers 404 saying "
        "exactly that, which is better than a proof that proves nothing.</p>",
        f"<p>The board's own as-published composite history, hash chained the same way "
        f"and versioned in a public git repository, is a plain file: "
        f"<code>{html.escape(SITE)}/data/book_history.json</code>. Keep a copy today "
        f"and you hold a copy of the past nobody can edit out from under you.</p>",
    ]
    out.append(_limit(
        "The chain proves integrity and ordering, and the Bitcoin anchor proves a "
        "reading is not backdated. Neither proves the reading was computed from "
        "honest inputs; that is what sections 1 to 3 are for. Anchoring is a "
        "separate step from committing, so a very recent digest can still be "
        "pending, in which case it is only as good as the operator's word until it "
        "settles. And the ledger stores commitments, not payloads, so pinning a "
        "specific past number means keeping the record you were shown, or reading "
        "it out of the published history file above."))
    return "\n".join(out), True


# ---------------------------------------------------------------------------
# 5. what would falsify the whole board
# ---------------------------------------------------------------------------
def _falsifier_items(deep: dict) -> list[str]:
    bt = (deep.get("backtest") or {})
    ec = (bt.get("event_capture") or {})
    split = (bt.get("class_split") or {})
    endo = (split.get("endogenous") or {})
    sig = ((bt.get("rigor") or {}).get("significance") or {})
    items: list[str] = []

    n_endo = _num(endo.get("n"), 0)
    if n_endo and float(endo.get("n") or 0) > 0:
        items.append(
            f"<strong>The endogenous claim dies on its own sample.</strong> The "
            f"headline competence claim rests on {n_endo} endogenous episodes. If the "
            f"next two calendar or reserve squeezes arrive with the composite below "
            f"its alert threshold, the claim is dead and no amount of engine count "
            f"saves it.")
    else:
        items.append(
            "<strong>The endogenous claim dies on its own sample.</strong> The "
            "competence claim rests on a handful of dated endogenous episodes. If "
            "the next two arrive with the composite below its alert threshold, the "
            "claim is dead.")

    p = _num(sig.get("p_value"), 3)
    if p:
        items.append(
            f"<strong>The permutation test never improves.</strong> Today the alert "
            f"placement carries p {p} against chance placement of the same runs. If "
            f"that does not fall as the sample grows, this board is decoration with "
            f"good typography, and the honest move is to say so here.")
    else:
        items.append(
            "<strong>The permutation test never improves.</strong> If alert placement "
            "stays indistinguishable from chance as the sample grows, this board is "
            "decoration with good typography.")

    pr = _pct(ec.get("precision_runs"))
    if pr:
        items.append(
            f"<strong>Run precision stays where it is.</strong> {pr} of alert runs are "
            f"followed by an event, which means most alerts are not. If that does not "
            f"improve with sample, the board is a smoke alarm that rings at toast, "
            f"and the number stays printed next to every claim until it does.")
    else:
        items.append(
            "<strong>Run precision stays where it is.</strong> Most alert runs are not "
            "followed by an event. If that does not improve with sample, the board is "
            "a smoke alarm that rings at toast.")

    items += [
        "<strong>The orthogonal run collapses.</strong> If capture without the tails "
        "family falls to the base rate, the board is one series wearing a costume "
        "and this page will print that instead of the current table.",
        "<strong>The leak audit inverts.</strong> If the forward-peeking toggle "
        "stops buying anything while published skill rises, the honest reading is "
        "not that we got better; it is that the real pipeline started peeking.",
        "<strong>The as-published record diverges from the reconstruction.</strong> "
        "The forward-accruing record and historical diagnostic cover the same dates from "
        "different directions. If the reconstruction is systematically kinder to the "
        "board than the record it accrued live, the reconstruction is flattering and "
        "the diagnostic number should be retired.",
        "<strong>A replay cannot be reproduced.</strong> The code is AGPL and public. "
        "If someone runs it against the same free public series and cannot reproduce "
        "the same construction-PIT result within revision noise, the reconstruction claim is "
        "false and everything above it is worth less.",
    ]
    return items


def _falsifier_section(deep: dict) -> tuple[str, bool]:
    items = _falsifier_items(deep)
    out = [
        "<p>Not marketing. These are the conditions under which the honest thing to "
        "do is take the board down, or at least stop quoting it. They are listed "
        "here because a tool that cannot say what would refute it is not a "
        "measurement, it is a mood.</p>",
        "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>",
    ]
    out.append(_limit(
        "This list is written by the same desk that built the board, which is a "
        "conflict of interest and not a small one. The mitigation is that every item "
        "is checkable against a published artifact rather than against our opinion: "
        "the audit table, the rigor block, the ledger, the replay endpoint and the "
        "source. Bring your own falsifier if this list is missing one."))
    return "\n".join(out), True


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------
def section_status(snap: dict) -> dict[str, bool]:
    """Which sections rendered against a real artifact on this snapshot. The
    publish step prints it, so a section going dark is visible in the build
    log instead of quietly turning into a paragraph of confident prose."""
    deep = (snap.get("deep") or {})
    return {
        "leak_audit": _leak_section(deep)[1],
        "orthogonal": _orthogonal_section(deep)[1],
        "point_in_time": _pit_section(deep)[1],
        "notary": _notary_section()[1],
        "falsifiers": _falsifier_section(deep)[1],
    }


def render_skeptic_html(snap: dict) -> str:
    """The page. Pure function of the snapshot: same board in, same bytes out.

    The rendered page goes through the letter lint before it is returned, so a
    dash, a leaked placeholder or a malformed ordinal blocks publication here
    exactly as it does in the daily letter."""
    e = html.escape
    deep = (snap.get("deep") or {})
    board_version = str(snap.get("version") or "unknown build")
    asof = str(snap.get("generated_at") or "")[:10]
    version_line = f"skeptic pack {SKEPTIC_VERSION} / board {board_version}"
    if asof:
        version_line += f" / board snapshot {asof}"

    leak_html, _ = _leak_section(deep)
    orth_html, _ = _orthogonal_section(deep)
    pit_html, _ = _pit_section(deep)
    notary_html, _ = _notary_section()
    fals_html, _ = _falsifier_section(deep)

    changelog_html = "\n".join(
        f"<li><span class='mono'>{e(day)}</span>: {e(note)}</li>"
        for day, note in CHANGELOG)

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Seiche skeptic pack</title>
<meta name="description" content="The two questions a skeptical economist asks first, answered with the board's own numbers: the leak audit that prices what look-ahead would have bought, the orthogonal test that reruns event capture with the target's own variables removed, the final-vintage construction-PIT replay, the hash-chained notary, and what would falsify the whole board.">
<link rel="canonical" href="{SKEPTIC_URL}">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<style>{_CSS}{_EXTRA_CSS}</style>
</head>
<body>
<div class="top">
  <div class="wordmark">SEI<span>CHE</span></div>
  <div class="faint"><a href="/">back to the board</a> &middot; <a href="/methodology">methodology</a> &middot; <a href="/guide">plain English guide</a></div>
</div>

<h1>The skeptic pack</h1>
<p class="faint mono">{e(version_line)}</p>
<p class="dim">Two questions open every serious conversation about a board like
this one. <span class="q">Isn't this just autocorrelation?</span> and
<span class="q">isn't there look-ahead in your backtest?</span> The board has
computed both answers on every cycle for months and buried them in a payload
nobody reads. This page brings them to the front, with the numbers, the
commands to check them yourself, and the limit of each test stated next to the
result instead of in a footnote.</p>
<p class="dim">Every section here is assembled from the live board or from the
source that serves it. A section whose artifact is missing says so rather than
filling the space with confident prose.</p>

<h2>1. The leak audit: what cheating would have bought</h2>
{leak_html}

<h2>2. The orthogonal test: the board without its own headline input</h2>
{orth_html}

<h2>3. The construction-PIT reconstruction: rerun a past date yourself</h2>
{pit_html}

<h2>4. The notary: proof a past reading was not edited later</h2>
{notary_html}

<h2>5. What would falsify the whole board</h2>
{fals_html}

<h2>Where to go next</h2>
<p>The method behind every number is on the versioned
<a href="{METHODOLOGY_URL}">methodology page</a>, with citations and a
changelog. The code that produced all of it is at
<a href="{REPO_URL}">{e(REPO_URL)}</a> under AGPL-3.0, including the leak audit
and the orthogonal run, so the fastest way to disprove this page is to read
the two engines behind it.</p>

<h2>Changelog</h2>
<ul>
{changelog_html}
</ul>

<p class="faint">Free public data with native lags. Not investment advice.
Seiche is free open source software (AGPL-3.0) and a public good.</p>
</body>
</html>
"""
    issues = lint_letter(page)
    if issues:
        raise SystemExit("skeptic page failed lint: " + "; ".join(issues))
    return page


# ---------------------------------------------------------------------------
# inputs + CLI
# ---------------------------------------------------------------------------
def load_snapshot(path: Path | None = None, api: str = DEFAULT_API) -> dict:
    """The CI-baked board snapshot when it is on disk, the live board over the
    wire when it is not. Stdlib only, so the publish job needs no extra
    install to render this page."""
    p = path or DEFAULT_SNAPSHOT
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        pass
    req = urllib.request.Request(f"{api}/api/overview",
                                 headers={"User-Agent": "seiche-skeptic"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def write_skeptic(snap: dict, out: Path | None = None) -> Path:
    path = out or DEFAULT_OUT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_skeptic_html(snap))
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Render the skeptic pack from the live board.")
    ap.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT),
                    help="board snapshot JSON (falls back to fetching the API)")
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)

    snap = load_snapshot(Path(args.snapshot), args.api)
    path = write_skeptic(snap, Path(args.out))
    status = section_status(snap)
    dark = [k for k, live in status.items() if not live]
    print(path)
    print("sections published: " + ", ".join(k for k, live in status.items() if live))
    if dark:
        print("sections not yet published: " + ", ".join(dark))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
