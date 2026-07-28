"""The daily dispatch — the desk's morning letter, written by the terminal.

Deterministic prose over the live snapshot (no LLM, no surprises): every
sentence carries the number it stands on, and phrasing varies day to day by a
date-seeded pick so the letter does not read like a form. Same ethos as
brief.py, different register: the brief is a checklist for the desk, the
dispatch is a letter to the reader.

Skeleton v2 (2026-07-28): the free letter runs a fixed, numbered section
order every day — 1 the reading, 2 what moved, 3 the Tell, 4 reserve
scarcity, 5 the official sector, 6 the dates that matter, 7 what the board
is honest about — so a regular can extract the delta in a minute. A section
whose engine is dark says so in one line instead of vanishing. The desk read
carries the forward court (odds plus adjudication), positioning, echoes, and
a live falsifier ledger with stable IDs. Every letter passes a publish-
blocking lint (no em or en dashes, no miscased SRF, no malformed ordinals,
no paywall language, no format leaks).

Outputs (relative to the repo root):
  frontend/public/dispatches/{slug}.md        the free reading (+ HAS-PAID marker)
  backend/seiche/dispatches/{slug}.paid.md    the desk's forward read (also free;
                                              filename is the historical contract)
  frontend/public/dispatches/index.json       prepended, deduped, newest first
  backend/seiche/dispatches/state.json        the letter's memory: which prints it
                                              has already reported (a slow series
                                              at an extreme is news once, not daily)
                                              plus yesterday's regime, decomposition
                                              and calendar claims, so today's letter
                                              can attribute its own day change and
                                              acknowledge its own revisions
  backend/seiche/dispatches/odds_ledger.jsonl the as-published forward odds, one
                                              line per model per day, so the court's
                                              Brier ledger accrues in public

Run:  python -m seiche.dispatch_daily [--api URL] [--date YYYY-MM-DD] [--force]
Stdlib only, so CI can run it with PYTHONPATH=backend and no install.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_API = "https://api.seiche.info"
HISTORY_URL = "https://seiche.info/data/book_history.json"
MARKER = "<!--HAS-PAID-->"

# repo root = backend/seiche/dispatch_daily.py -> three parents up
REPO_ROOT = Path(__file__).resolve().parents[2]
FREE_DIR = REPO_ROOT / "frontend" / "public" / "dispatches"
PAID_DIR = REPO_ROOT / "backend" / "seiche" / "dispatches"
INDEX = FREE_DIR / "index.json"
STATE = PAID_DIR / "state.json"


# ---------------------------------------------------------------------------
# small formatting helpers — every number in the letter goes through these
# ---------------------------------------------------------------------------
def _fmt(x, d: int = 0) -> str:
    if x is None:
        return "?"
    try:
        return f"{float(x):,.{d}f}"
    except (TypeError, ValueError):
        return str(x)


def _signed(x, d: int = 0) -> str:
    if x is None:
        return "?"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    return f"{'+' if v >= 0 else ''}{v:,.{d}f}"


def _ordinal(x) -> str:
    """53 -> '53rd'. The letter printed '53th percentile' for weeks; every
    ordinal now goes through here and the lint rejects any that do not."""
    try:
        n = int(round(float(x)))
    except (TypeError, ValueError):
        return "?"
    if 10 <= n % 100 <= 13:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _pick(date: str, salt: str, options: list[str]) -> str:
    """Date-seeded deterministic choice: varies day to day, reproducible."""
    h = int(hashlib.sha256(f"{date}:{salt}".encode()).hexdigest(), 16)
    return options[h % len(options)]


def _clean(s) -> str:
    """Engine-supplied free text (verdicts, readings, reasons) enters the
    letter through here: the house copy rules apply to it too, and the lint
    would otherwise block the whole letter over one engine's em dash."""
    return (str(s).replace(" — ", ", ").replace("—", ", ")
            .replace(" – ", ", ").replace("–", "-"))


# Composite components are engine keys; the reader is owed plain language.
# The key stays in parentheses so the board cross-reference still works.
DISPLAY_NAMES = {
    "weather": "the calendar squeeze (weather)",
    "resonance": "calendar amplification (resonance)",
    "kink": "reserve scarcity (kink)",
    "rvxray": "futures-basis plumbing (rvxray)",
    "buffers": "the buffers, RRP and TGA (buffers)",
    "undertow": "market microstructure (undertow)",
    "confession": "the swap-line confession (confession)",
    "warehouse": "the dealer warehouse (warehouse)",
    "tails": "the tail law (tails)",
    "auctions": "auction digestion (auctions)",
    "hydrophone": "repo microstructure (hydrophone)",
}


def _display(key) -> str:
    return DISPLAY_NAMES.get(str(key), str(key))


# ---------------------------------------------------------------------------
# lint — the tone codex, publish-blocking. A precision product that prints
# '53th' or 'Srf' loses the reader it is trying to win; the letter refuses
# to publish rather than publish carelessly.
# ---------------------------------------------------------------------------
_ORDINAL_RE = re.compile(r"\b(\d+)(st|nd|rd|th)\b")


def _suffix_for(n: int) -> str:
    if 10 <= n % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def lint_letter(*texts: str) -> list[str]:
    issues: set[str] = set()
    for t in texts:
        if not t:
            continue
        if "—" in t:
            issues.add("em dash in copy")
        if "–" in t:
            issues.add("en dash in copy")
        if re.search(r"\bSrf\b", t):
            issues.add("miscased SRF ('Srf')")
        if re.search(r"\bsubscriber\b", t, re.IGNORECASE):
            issues.add("paywall language ('subscriber')")
        if re.search(r"\bNone\b(?!\s+of\b)", t):
            issues.add("format leak ('None' in copy)")
        if re.search(r"\bnan\b", t):
            issues.add("format leak ('nan' in copy)")
        if "? out of 100" in t or "?%" in t:
            issues.add("unformatted placeholder")
        for m in _ORDINAL_RE.finditer(t):
            if m.group(2) != _suffix_for(int(m.group(1))):
                issues.add(f"malformed ordinal ('{m.group(0)}')")
    return sorted(issues)


# ---------------------------------------------------------------------------
# novelty state — the letter's memory of what it has already told the reader.
# A mover is news on the first letter after its print date; after that it is a
# standing flag, and re-headlining it would be the letter dressing persistence
# as news. The state keeps two maps: `reported` (as of after today's letter)
# and `reported_prev` (the baseline today's letter was built from), keyed by
# `date`, so a same-day rebuild — the CI announce step runs the generator
# again the better part of an hour after the write — reproduces the same
# novelty decisions instead of finding its own morning's letter in the state
# and calling everything old news. The same two-slot pattern carries the
# letter memory (`letter` / `letter_prev`): yesterday's regime, decomposition
# and calendar claims, so today's letter attributes its own day change and
# owns its own revisions.
# ---------------------------------------------------------------------------
def load_state(path: Path | None = None) -> dict:
    p = path or STATE
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def _novelty_baseline(state: dict, date: str) -> dict:
    """label -> asof of the last print the letter reported for that series."""
    if state.get("date") == date:
        return state.get("reported_prev") or {}
    return state.get("reported") or {}


def _letter_baseline(state: dict, date: str) -> dict:
    """Yesterday's letter memory (regime, decomposition, calendar claims)."""
    if state.get("date") == date:
        return state.get("letter_prev") or {}
    return state.get("letter") or {}


def _is_novel(m: dict, baseline: dict) -> bool:
    prev = baseline.get(str(m.get("label")))
    asof = m.get("asof")
    # ISO dates compare lexicographically; an unknown asof is treated as news
    # because the letter cannot prove it already told the reader.
    return asof is None or prev is None or str(asof) > str(prev)


def _split_flagged(snap: dict, baseline: dict) -> tuple[list[dict], list[dict]]:
    """Flagged movers split into (novel, held). Novel sorts freshest print
    first, loudest second: the headline goes to what is actually new."""
    flagged = [m for m in snap.get("engines", {}).get("sonar", {}).get("movers", [])
               if m.get("flag")]
    novel = sorted((m for m in flagged if _is_novel(m, baseline)),
                   key=lambda m: ((m.get("age_d") or 0), -(m.get("max_abs_z") or 0)))
    held = [m for m in flagged if not _is_novel(m, baseline)]
    return novel, held


def _letter_memory(snap: dict) -> dict:
    """What today's letter claimed, kept so tomorrow's can attribute and
    acknowledge rather than silently restate."""
    comp = snap.get("engines", {}).get("composite", {}) or {}
    cal = snap.get("calendar", {}) or {}
    weather = snap.get("engines", {}).get("weather", {}) or {}
    crunches = (cal.get("crunch_windows") or weather.get("crunch_windows") or [])
    crunch = {}
    if crunches:
        c = crunches[0]
        crunch = {"date": c.get("date"), "worst_case_b": c.get("worst_case_b"),
                  "settlement_b": c.get("settlement_b")}
    return {
        "regime": (comp.get("regime") or "UNRATED").upper(),
        "value": comp.get("value"),
        "decomp": {str(d.get("component")): d.get("contribution")
                   for d in comp.get("decomposition", [])
                   if d.get("contribution") is not None},
        "crunch": crunch,
    }


def _updated_state(snap: dict, baseline: dict, letter_baseline: dict, date: str) -> dict:
    """Every currently flagged print is on the record after today's letter
    (novel ones in the movers line, held ones in the standing-flags line).
    Labels that stop flagging fall out, which keeps the file bounded; a
    series can only re-flag on a newer print, which is novel again anyway."""
    reported = {str(m.get("label")): str(m.get("asof"))
                for m in snap.get("engines", {}).get("sonar", {}).get("movers", [])
                if m.get("flag") and m.get("asof") is not None}
    return {"date": date, "reported_prev": baseline, "reported": reported,
            "letter_prev": letter_baseline, "letter": _letter_memory(snap)}


# ---------------------------------------------------------------------------
# the letter
# ---------------------------------------------------------------------------
_REGIME_FRAME = {
    "CALM": "The basin is flat. That is a reading, not a promise.",
    "EROSION": "Nothing is breaking. The margin for error is what is shrinking.",
    "STRAIN": "The pipes are working harder for the same result. This is the regime where surprises stop being cheap.",
    "STRESS": "The basin is sloshing. From here the board stops being early and starts being current.",
    "CRISIS": "The wave is over the edge. The board's job now is measurement, not warning.",
}


def _title_summary_tag(snap: dict, date: str, prev_value, baseline: dict) -> tuple[str, str, str]:
    comp = snap.get("engines", {}).get("composite", {})
    tell = snap.get("deep", {}).get("tell", {}) or {}
    v = comp.get("value")
    regime = (comp.get("regime") or "UNRATED").upper()
    delta = None
    if prev_value is not None and v is not None:
        try:
            delta = float(v) - float(prev_value)
        except (TypeError, ValueError):
            delta = None

    novel, held = _split_flagged(snap, baseline)
    tell_v = tell.get("tell") if tell.get("ok") else None

    if delta is not None and abs(delta) >= 5:
        direction = "climbs" if delta > 0 else "eases"
        title = f"The board {direction} {abs(delta):.0f} points: {regime.lower()} at {_fmt(v)}"
    elif tell_v is not None and abs(tell_v) >= 30:
        side = "plumbing leads price" if tell_v > 0 else "price leads plumbing"
        title = f"{regime.title()} with a loud tell: {side} at {_signed(tell_v)}"
    elif novel:
        m = novel[0]
        # "Overnight" has to be earned. SONAR only flags prints within
        # SONAR_FRESH_D of the board, but a weekly release is still days old
        # when it is the freshest thing on the tape, so say which it is.
        when = "moved overnight" if (m.get("age_d") or 0) <= 1 else "moved on the latest print"
        title = f"{m.get('label', 'One gauge')} {when}: the {regime.lower()} tape gets a data point"
    elif held:
        # Everything flagged was already reported on the day it printed.
        # Persistence is worth a title, but a different one each day, not
        # the same headline until the next release cycle.
        title = _pick(date, "title-held", [
            f"{regime.title()} at {_fmt(v)}: the flags hold, no new print to report",
            f"No new prints, standing flags: the {regime.lower()} reading for {date}",
            f"{regime.title()}, carried: the same flags, one day older",
        ])
    else:
        quiet = _pick(date, "title", [
            f"{regime.title()} at {_fmt(v)}: what the pipes say while nothing moves",
            f"A quiet tape, a {regime.lower()} board: the reading for {date}",
            f"{regime.title()}, held: the desk letter for {date}",
        ])
        title = quiet

    hook = ""
    if tell_v is not None:
        hook = f" The Tell reads {_signed(tell_v)}."
    summary = (
        f"The composite reads {_fmt(v)}, regime {regime}."
        + (f" That is {_signed(delta, 1)} on the day." if delta is not None else "")
        + hook
        + " Every number below is checkable on the board."
    )
    return title, summary, regime


def _opening(snap: dict, date: str, prev_value) -> list[str]:
    comp = snap.get("engines", {}).get("composite", {})
    v = comp.get("value")
    regime = (comp.get("regime") or "UNRATED").upper()
    cov = comp.get("coverage_pct")
    out = []
    delta_txt = ""
    if prev_value is not None and v is not None:
        try:
            d = float(v) - float(prev_value)
            delta_txt = f", {_signed(d, 1)} against the last published reading"
        except (TypeError, ValueError):
            pass
    out.append(
        f"The composite reads **{_fmt(v)} out of 100, {regime}**{delta_txt}, "
        f"on {_fmt(cov)}% coverage. "
        + _REGIME_FRAME.get(regime, "The board publishes what it sees and nothing else.")
    )
    decomp = [d for d in comp.get("decomposition", []) if d.get("contribution")]
    if decomp:
        top = decomp[0]
        pinned = ""
        try:
            if float(top.get("score") or 0) >= 99.5:
                pinned = (" That gauge is pinned at its ceiling; a saturated component "
                          "gets said out loud instead of left for the reader to wonder about.")
        except (TypeError, ValueError):
            pass
        out.append(
            f"The heaviest hand on the dial is **{_display(top.get('component'))}** at a score of "
            f"{_fmt(top.get('score'))}, worth {_signed(top.get('contribution'), 1)} points of the total."
            + pinned + " "
            + _pick(date, "driver", [
                "When one component carries the reading, watch that component, not the headline.",
                "A composite is only as honest as its decomposition, so here it is.",
                "That is where the reading comes from. The rest is arithmetic.",
            ])
        )
    dead = [d.get("component") for d in comp.get("decomposition", []) if d.get("status") == "DEAD"]
    if dead:
        out.append(
            f"Dead inputs today: {', '.join(_display(c) for c in dead)}. Coverage is reduced accordingly and "
            "the composite says so rather than filling the gap with yesterday."
        )
    return out


def _attribution(snap: dict, letter_prev: dict) -> list[str]:
    """Two-way attribution: the day's change decomposed by component, from
    the letter's own memory of yesterday's decomposition. Level attribution
    is the table on the board; change attribution is what a reader diffing
    consecutive letters actually wants."""
    prev = (letter_prev or {}).get("decomp") or {}
    if not prev:
        return []
    comp = snap.get("engines", {}).get("composite", {}) or {}
    cur = {str(d.get("component")): d.get("contribution")
           for d in comp.get("decomposition", []) if d.get("contribution") is not None}
    deltas = {}
    for k, cv in cur.items():
        if k in prev:
            try:
                deltas[k] = round(float(cv) - float(prev[k]), 2)
            except (TypeError, ValueError):
                continue
    moved = [(k, dv) for k, dv in sorted(deltas.items(), key=lambda kv: -abs(kv[1]))
             if abs(dv) >= 0.1]
    if not moved:
        return ["Component by component, the reading is flat against the last letter: "
                "no contribution moved a tenth of a point. Flat gets said too."]
    total = round(sum(deltas.values()), 1)
    parts = ", ".join(f"{_display(k)} {_signed(dv, 1)}" for k, dv in moved[:4])
    return [f"Change gets attributed here, not just level: against the last letter, "
            f"{parts}, {_signed(total, 1)} points net across the components."]


def _tell_para(snap: dict, date: str) -> list[str]:
    tell = snap.get("deep", {}).get("tell", {}) or {}
    if not tell.get("ok"):
        return []
    t = tell.get("tell")
    p, m = tell.get("plumbing_pctl"), tell.get("market_pctl")
    reading = _clean(tell.get("reading", ""))
    lines = [
        f"The Tell, the gap between what the pipes measure and what the screens price, reads "
        f"**{_signed(t)}**"
        + (
            f": plumbing indicators at the {_ordinal(p)} percentile of their own history, "
            f"market indicators at the {_ordinal(m)}."
            if p is not None and m is not None else "."
        )
    ]
    if t is not None and abs(float(t)) >= 30:
        lines.append(
            _pick(date, "tell", [
                f"That is a wide disagreement, and it resolves one of two ways: the screens catch up to the pipes, or the pipes calm down to meet the screens. The reading is *{reading}*.",
                f"A gap this wide has a short shelf life. The board's read is *{reading}*, and the record of what happened after past gaps sits in PROOF.",
            ])
        )
    elif t is not None:
        lines.append("The gap is modest. Modest gaps are what most days look like, and saying so is part of the record.")
    return lines


def _deminimis_note(m: dict) -> str | None:
    """A 16-sigma z on a near-zero baseline is a wake-up call, not a size
    claim; the letter says which before the reader has to work it out."""
    peak = m.get("hist_peak_abs")
    share = m.get("share_of_peak")
    if peak is None or share is None or share >= 0.02:
        return None
    woke = (" It is the first nonzero print after a stretch at zero, which is the "
            "actually interesting fact here." if m.get("woke_from_zero") else "")
    unit = m.get("unit") or ""
    return (f"For scale: that {m.get('label')} print is {_fmt(m.get('last'), 2)} {unit} "
            f"against a historical peak near {_fmt(peak)} {unit}. The z score marks a "
            f"series waking up on a quiet baseline, not a large flow; the dollar amount is de minimis "
            f"and the letter says so next to the sigma.{woke}")


def _movers_para(snap: dict, date: str, baseline: dict) -> list[str]:
    sonar = snap.get("engines", {}).get("sonar", {})
    novel, held = _split_flagged(snap, baseline)
    out: list[str] = []

    if novel:
        bits = []
        for m in novel[:3]:
            bits.append(
                f"**{m.get('label')}** printed {_fmt(m.get('last'), 2)} {m.get('unit') or ''} "
                f"(level z {_signed(m.get('level_z'), 1)}, change z {_signed(m.get('change_z'), 1)}, as of {m.get('asof')})"
            )
        # The lead describes the whole list, so it is bound by the OLDEST print in
        # it, not the newest. Claiming "overnight" because one of three gauges
        # printed last night is the same overstatement this gate exists to stop.
        oldest = max((m.get("age_d") or 0) for m in novel[:3])
        if oldest <= 1:
            lead = "Overnight, the tape did move: " if len(bits) > 1 else "One gauge moved overnight: "
        else:
            lead = ("The tape moved on its latest prints, none of them from last night: "
                    if len(bits) > 1 else "One gauge moved on its latest print, not overnight: ")
        out.append(lead + "; ".join(bits) + ".")
        for m in novel[:3]:
            note = _deminimis_note(m)
            if note:
                out.append(note)
    elif held:
        out.append(_pick(date, "held-quiet", [
            "No new print cleared the ±2.5 robust z bar since the last letter. What is flagged today was flagged when it printed, and re-breaking old news is not this letter's trade.",
            "Nothing new crossed the ±2.5 robust z bar overnight. The standing flags below are persistence, not news, and the letter labels them as such.",
        ]))
    else:
        out.append(_pick(date, "quiet", [
            "Overnight, nothing cleared the ±2.5 robust z bar. A quiet tape is a data point too; it is what erosion looks like from the inside.",
            "No gauge moved beyond ±2.5 robust z overnight. The letter reports the silence rather than decorating it.",
        ]))
        # A slow series sitting at an extreme level is worth knowing about,
        # but it is not news and the letter must not dress it as news.
        stale = [m for m in sonar.get("movers", []) if m.get("stale")
                 and (m.get("max_abs_z") or 0) >= 2.5]
        if stale:
            s = stale[0]
            out.append(
                f"One reading is extreme but not fresh: **{s.get('label')}** sits at "
                f"{_fmt(s.get('max_abs_z'), 1)} robust z on a print from {s.get('asof')}, "
                f"{s.get('age_d')} days behind the board. That is a level worth knowing and "
                "not a move, so it is reported here rather than in the movers line."
            )

    if held:
        bits = "; ".join(
            f"**{m.get('label')}** (|z| {_fmt(m.get('max_abs_z'), 1)}, as of {m.get('asof')})"
            for m in held[:3]
        )
        out.append(
            f"Still flagged from prints already covered in an earlier letter: {bits}. "
            "A standing flag is context, not news; when a fresh print lands it goes back in the movers line."
        )
    return out


def _tripwire_para(snap: dict) -> list[str]:
    """Facility take-up watch (SRF, discount window, FIMA) with the stigma
    gauge's judgment on top, when the engine is live."""
    st = snap.get("engines", {}).get("stigma", {}) or {}
    if st.get("ok") and st.get("letter_line"):
        return [str(st["letter_line"])]
    return []


def _press_para(snap: dict) -> list[str]:
    """Scuttlebutt, only when a topic actually flags: press attention on the
    plumbing, display only, never a score input. One sentence, no dashes."""
    flags = (snap.get("engines", {}).get("scuttlebutt", {}) or {}).get("flags") or []
    if not flags:
        return []
    shown = "; ".join(_clean(f) for f in flags[:2])
    return [f"The scuttlebutt, display only and feeding no score: {shown}."]


def _kink_para(snap: dict) -> list[str]:
    """The reserve-scarcity section: the desk's continuous estimate of the
    reserve demand curve, the object the NY Fed publishes monthly as its
    Reserve Demand Elasticity. The board computed this all along; the letter
    now says it out loud, with the fit quality attached."""
    k = snap.get("engines", {}).get("kink", {}) or {}
    if not k.get("ok"):
        return ["The kink engine is dark today. When the fit is live this section carries "
                "the desk's continuous estimate of the reserve demand curve, the same object "
                "the NY Fed publishes monthly as its Reserve Demand Elasticity."]
    dist = k.get("distance_b")
    below = dist is not None and float(dist) < 0
    out = [
        "The desk fits the reserve demand curve continuously from the same public series the "
        "NY Fed uses for its monthly Reserve Demand Elasticity (the Afonso, Giannone, La Spada "
        f"and Williams lineage). Today's fit puts the kink near **${_fmt(k.get('kink_reserves_b'))}B** "
        f"of reserves; the system holds ${_fmt(k.get('current_reserves_b'))}B, "
        f"${_fmt(abs(float(dist)) if dist is not None else None)}B {'below' if below else 'above'} the estimate."
    ]
    r2 = k.get("r2")
    fitword = "modest"
    try:
        if float(r2) >= 0.7:
            fitword = "solid"
    except (TypeError, ValueError):
        pass
    out.append(
        f"Fit honesty: R² {_fmt(r2, 2)} against a flat curve; the observed SOFR minus IORB spread of "
        f"{_fmt(k.get('observed_spread_now_bp'), 1)}bp versus the fit's {_fmt(k.get('predicted_spread_now_bp'), 1)}bp "
        f"gives a consistency of {_fmt(k.get('consistency'), 2)}. A {fitword} fit is a lens, not a verdict, "
        "and the number is printed so you can discount it yourself."
    )
    if below:
        out.append(
            "Reserves are through the estimated kink, which is exactly where the spread should start "
            "answering to reserve changes. From here the board watches the slope, not the distance."
        )
    elif k.get("days_to_kink") is not None:
        out.append(
            f"At the trailing drain of ${_fmt(k.get('drain_per_bday_b'), 1)}B a business day, the path "
            f"reaches the kink in roughly {_fmt(k.get('days_to_kink'))} business days. The runway view "
            "on the board carries the scenario bands behind that arithmetic."
        )
    return out


def _official_para(snap: dict) -> list[str]:
    """The official sector: custody, the foreign RRP pool, FIMA repo. The
    rotation-versus-retreat read comes from the officialbid engine."""
    ob = snap.get("engines", {}).get("officialbid", {}) or {}
    if ob.get("ok") and ob.get("letter_line"):
        return [str(ob["letter_line"])]
    return ["The official sector engine is dark today. When it is live this section reads the "
            "foreign official footprint in one place: Treasuries in Fed custody, the foreign "
            "RRP pool, and FIMA repo, decomposed into rotation versus retreat."]


def _calendar_para(snap: dict, letter_prev: dict | None = None) -> list[str]:
    cal = snap.get("calendar", {}) or {}
    turn = (snap.get("deep", {}).get("turn") or {}).get("next_turn")
    weather = snap.get("engines", {}).get("weather", {}) or {}
    out = []
    crunches = (cal.get("crunch_windows") or weather.get("crunch_windows") or [])
    if crunches:
        c = crunches[0]
        wc = ""
        if c.get("worst_case_b") is not None:
            wc = f", worst case reserves near ${_fmt(c.get('worst_case_b'))}B after the drain"
        out.append(
            f"The next date that matters is **{c.get('date')}**: {_clean(c.get('reason', 'a flagged crunch window'))}{wc}."
        )
        # The letter's own prior claim about this window, checked and, when it
        # moved, said. Experts diff consecutive issues; a silent 64% revision
        # in a flagged settlement reads as either a bug or carelessness.
        pc = (letter_prev or {}).get("crunch") or {}
        if pc.get("date") == c.get("date"):
            for key, label in (("settlement_b", "settlement"), ("worst_case_b", "worst-case reserves")):
                a, b = pc.get(key), c.get(key)
                try:
                    if a is not None and b is not None and abs(float(b) - float(a)) > max(5.0, 0.1 * abs(float(a))):
                        out.append(
                            f"The last letter put that window's {label} at ${_fmt(a)}B; the schedule now reads "
                            f"${_fmt(b)}B. Revisions get said, not slipped."
                        )
                        break
                except (TypeError, ValueError):
                    continue
    if turn:
        band = turn.get("band_bp") or [None, None]
        out.append(
            f"The turn model puts {turn.get('date')} ({turn.get('mode')}) at "
            f"{_signed(turn.get('forecast_bp'), 1)}bp with a band of "
            f"[{_signed(band[0], 1)}, {_signed(band[1], 1)}], severity {turn.get('severity')}/5."
        )
    fomc = (cal.get("fomc_next_90d") or [])
    if fomc:
        f = fomc[0]
        days = f.get("days_until")
        if days is not None and int(days) <= 1:
            when = "today" if int(days) == 0 else "tomorrow"
            out.append(
                f"FOMC decides {when}, and the letter cares in three named places: the IORB and ON RRP "
                "settings, which are the corridor every spread on this board is priced against; the runoff "
                "pace, which sets the drain rate behind the reserve path; and any change to the SRF, the "
                "ceiling whose quiet take-up the tripwires watch. The next issue grades this stanza against "
                "the statement."
            )
        else:
            out.append(f"FOMC decides {f.get('date')}, {days} days out.")
    tax = (cal.get("corporate_tax_next_90d") or [])
    if tax:
        t = tax[0]
        out.append(f"The corporate tax date lands {t.get('date')}, {t.get('days_until')} days out; tax dates drain reserves on a schedule everyone can read.")
    return [" ".join(out)] if out else []


def _honesty_coda(snap: dict) -> list[str]:
    bt = (snap.get("deep", {}) or {}).get("backtest", {}) or {}
    ec = bt.get("event_capture") or {}
    proof = ""
    try:
        if ec.get("recall") is not None:
            proof = (
                f" The record is in PROOF: event recall {float(ec['recall']):.0%} against a base rate of "
                f"{float(ec.get('base_rate') or 0):.0%}, run precision {float(ec.get('precision_runs') or 0):.0%}, "
                f"median lead {_fmt(ec.get('median_lead_d'))} trading days. The misses sit next to the hits; "
                "read those before weighting today's letter."
            )
    except (TypeError, ValueError):
        proof = ""
    if not proof:
        proof = (" The misses this board has made sit in PROOF next to the hits; read those "
                 "before weighting today's letter.")

    faults = snap.get("faults") or []
    if faults:
        srcs = ", ".join(str(f.get("source")) for f in faults[:4])
        return [
            f"Faults on the board today: {srcs}. The affected inputs are degraded or dead and the "
            "composite's coverage says so. A dashboard that hides its broken gauges is lying with a straight face."
            + proof
        ]
    return ["All sources and engines report live." + proof]


def _forward_sentences(snap: dict) -> list[str]:
    """The forward-odds sentences, one per live model. The desk read always
    carries them; the free letter borrows them on days with no new print."""
    eng = snap.get("engines", {})
    deep = snap.get("deep", {})
    fwd = []
    bath = deep.get("bathymetry", {}) or {}
    if bath.get("ok"):
        p5 = (bath.get("p_by_horizon") or {}).get("h5", bath.get("p_event_5bd"))
        if p5 is not None:
            mfpt = f", mean first-passage roughly {_fmt(bath.get('mfpt_bd'))} business days" if bath.get("mfpt_bd") is not None else ""
            fwd.append(f"Bathymetry puts the odds of an event inside five business days at **{float(p5):.0%}**{mfpt}.")
    ml = deep.get("ml", {}) or {}
    if ml.get("ok") and ml.get("p_event_5bd") is not None:
        verdict = _clean(str(ml.get("verdict", "")).split(";")[0].split("(")[0].strip())
        fwd.append(f"The learned model reads {float(ml['p_event_5bd']):.0%} for the same window" + (f" and calls it *{verdict}*." if verdict else "."))
    markov = deep.get("markov", {}) or {}
    if markov.get("ok"):
        reach = (markov.get("p_reach_stress") or {}).get("h21")
        if reach is not None:
            fwd.append(
                f"The regime chain gives {float(reach):.0%} odds of touching STRESS inside 21 business days, "
                f"with an expected dwell of {_fmt(markov.get('expected_dwell_bd'))} business days in the current state."
            )
    res = eng.get("resonance", {}) or {}
    if res.get("ok"):
        wm = res.get("worst_mode", {}) or {}
        if wm.get("label"):
            fwd.append(
                f"Resonance reads {_fmt(res.get('score'))}: the {wm.get('label')} mode is amplifying at "
                f"{_fmt(wm.get('amplification'), 1)}x, which is the basin ringing louder to the same calendar."
            )
    return fwd


def _court_paras(snap: dict) -> list[str]:
    """The forward court: the members' odds, the stack's pooled read, and an
    adjudication by scored record. Three numbers that disagree by an order
    of magnitude are not a forecast; which one has the better Brier is."""
    deep = snap.get("deep", {}) or {}
    out = []
    fwd = _forward_sentences(snap)
    if fwd:
        out.append(" ".join(fwd))
    st = deep.get("stacker", {}) or {}
    if st.get("ok") and st.get("p_now") is not None:
        try:
            verdict = f"; its own verdict: {_clean(str(st.get('verdict')).split(' (')[0])}" if st.get("verdict") else ""
            out.append(
                f"The stack pools the members at **{float(st['p_now']):.0%}** for the five-day window, "
                f"dispersion {_fmt(st.get('dispersion_now'), 2)}{verdict}."
            )
        except (TypeError, ValueError):
            pass
    skills: list[tuple[str, float, object]] = []
    sk = (deep.get("tidetables", {}) or {}).get("skill") or {}
    if sk.get("brier") is not None:
        skills.append(("the analog tables (Tide Tables)", float(sk["brier"]), sk.get("brier_climatology")))
    sw = (deep.get("swell", {}) or {}).get("validation") or {}
    if sw.get("brier") is not None:
        skills.append(("the dated term structure (Swell)", float(sw["brier"]), sw.get("brier_climatology")))
    mlv = (deep.get("ml", {}) or {}).get("validation") or {}
    if mlv.get("brier") is not None:
        skills.append(("the learned model", float(mlv["brier"]), None))
    if skills:
        best = min(skills, key=lambda s: s[1])
        clim = f" against a climatology of {_fmt(best[2], 2)}" if best[2] is not None else ""
        out.append(
            f"Adjudication, not averaging: of the members with a scored record, {best[0]} carries the best "
            f"out-of-sample Brier at {_fmt(best[1], 2)}{clim}. When the members disagree, that is the record "
            "to weight; the disagreement itself is model risk, and the court publishes it instead of hiding "
            "it in a mean. Every model's daily odds go to the public ledger, so this paragraph gets harder "
            "to argue with every month."
        )
    return out


def _find_mover(snap: dict, needle: str) -> dict | None:
    for m in (snap.get("engines", {}).get("sonar", {}) or {}).get("movers", []) or []:
        if needle.lower() in str(m.get("label", "")).lower():
            return m
    return None


def _falsifiers(snap: dict) -> list[tuple[str, str]]:
    """The live falsifier ledger. Each mind-changer carries a stable ID and
    today's value of the thing it watches, so a recurring reader can watch
    the distance close instead of rereading a static sentence."""
    eng = snap.get("engines", {}) or {}
    comp = eng.get("composite", {}) or {}
    regime = (comp.get("regime") or "").upper()
    v = comp.get("value")
    tell = (snap.get("deep", {}) or {}).get("tell", {}) or {}
    tv = tell.get("tell") if tell.get("ok") else None
    kink = eng.get("kink", {}) or {}
    res = eng.get("resonance", {}) or {}
    srf = _find_mover(snap, "SRF")

    comp_now = f"; today it reads {_fmt(v)}" if v is not None else ""
    tell_now = f"; today it reads {_signed(tv)}" if tv is not None else ""
    srf_now = (f"; the latest print is {_fmt(srf.get('last'), 2)} {srf.get('unit') or ''} as of {srf.get('asof')}"
               if srf and srf.get("last") is not None else "")
    drain = kink.get("drain_per_bday_b") if kink.get("ok") else None
    drain_now = f"; the current drain runs ${_fmt(drain, 1)}B a business day" if drain is not None else ""
    amp = ((res.get("worst_mode") or {}).get("amplification")) if res.get("ok") else None
    amp_now = f"; today it runs {_fmt(amp, 1)}x" if amp is not None else ""

    table = {
        "CALM": [
            ("C1", f"The Tell holding above +30{tell_now}."),
            ("C2", f"SRF take-up above $1B on a day with no calendar excuse{srf_now}."),
            ("C3", "Two consecutive sessions with fresh funding-side movers clearing the ±2.5 bar."),
        ],
        "EROSION": [
            ("E1", f"The Tell closing back under +15{tell_now}."),
            ("E2", f"Reserves stabilising for two straight weeks{drain_now}."),
            ("E3", f"Calendar amplification easing below 1x{amp_now}."),
        ],
        "STRAIN": [
            ("S1", f"SRF or discount window take-up above $1B on a day with no calendar excuse{srf_now}."),
            ("S2", "A funding-side mover breaching ±3 robust z on a fresh print."),
            ("S3", f"The composite crossing 60{comp_now}."),
        ],
        "STRESS": [
            ("T1", f"The composite easing below 55 for three straight sessions{comp_now}."),
            ("T2", "The crunch calendar clearing without a facility print."),
        ],
        "CRISIS": [
            ("X1", f"Facility usage normalising while the composite holds under 70 for a week{comp_now}."),
        ],
    }
    return table.get(regime, [("U1", "The numbers above moving against the read.")])


def _desk_read(snap: dict, date: str, letter_prev: dict | None = None) -> str:
    """The continuation: the forward read. Free, like everything else."""
    eng = snap.get("engines", {})
    comp = eng.get("composite", {}) or {}
    regime = (comp.get("regime") or "").upper()
    parts: list[str] = ["## The desk's forward read", ""]

    court = _court_paras(snap)
    if court:
        parts += ["### The court", ""]
        for c in court:
            parts += [c, ""]

    pos = []
    crowd = eng.get("crowding", {}) or {}
    if crowd.get("ok") and crowd.get("rows"):
        r = crowd["rows"][0]
        pos.append(
            f"The most crowded seat is **{r.get('contract')}**, leveraged net {_signed(r.get('lev_net_share_oi'), 2)} "
            f"of open interest (z {_signed(r.get('z'), 1)})."
        )
    wh = eng.get("warehouse", {}) or {}
    if wh.get("ok"):
        pos.append(
            f"Dealer warehouse holds ${_fmt(wh.get('total_net_b'))}B, the {_ordinal(wh.get('total_pctl'))} percentile "
            f"of its history, {_fmt(wh.get('long_end_share_pct'))}% of it long end."
        )
    if pos:
        parts += ["### Positioning", "",
                  " ".join(pos) + " Positioning data is COT and carries its native T+3 lag; the lag is shown, never hidden.", ""]

    echo = eng.get("echo", {}) or {}
    if echo.get("ok") and echo.get("matches"):
        parts += ["### Echoes", ""]
        parts += ["| episode | window | similarity |", "|---|---|---|"]
        for m in echo["matches"][:4]:
            parts.append(f"| {_clean(m.get('episode'))} | T−{m.get('lead_days')}d | {_fmt(m.get('similarity'), 2)} |")
        top = echo["matches"][0]
        sim = top.get("similarity")
        try:
            close = sim is not None and float(sim) >= 0.6
        except (TypeError, ValueError):
            close = False
        if close:
            judgment = (f"Similarity is not destiny, but the top rhyme is close enough to matter: "
                        f"{top.get('episode')} at {_fmt(sim, 2)}. PROOF's outcome tables say what followed "
                        "matches at this distance; that table, not this one, is the judgment.")
        else:
            judgment = (f"Similarity is not destiny, and no match clears 0.60 today (top: {_fmt(sim, 2)}). "
                        "The desk reads this table as texture, not signal, and says so rather than "
                        "letting the echo do the alarming.")
        parts += ["", judgment, ""]

    parts += ["### The ledger: what would change the desk's mind", ""]
    for fid, text in _falsifiers(snap):
        parts.append(f"- **{fid}** · {text}")
    parts.append("")
    prev_regime = (letter_prev or {}).get("regime")
    if prev_regime and regime and prev_regime != regime and prev_regime != "UNRATED":
        parts.append(
            f"Resolution: the last letter's ledger was written for {prev_regime}; the regime moved to "
            f"{regime}, so the items above are rewritten for the new state. The move itself is on the "
            "record in the composite history."
        )
    else:
        parts.append(
            "When one of those prints, the letter will say so, in this same place, with the number. "
            "The IDs are stable, so hold the desk to them."
        )
    parts.append("")

    parts += ["The board recomputes six times a day; this letter freezes one reading of it. "
              "Free public data with native lags. Not investment advice."]
    return "\n".join(parts)


def _current_odds(snap: dict, date: str) -> list[dict]:
    """Today's forward odds, one row per model, bound for the public ledger.
    The ledger is what turns 'three numbers that disagree' into a scoreboard."""
    deep = snap.get("deep", {}) or {}
    rows: list[dict] = []

    def add(model: str, p) -> None:
        if p is None:
            return
        try:
            rows.append({"date": date, "model": model, "horizon_bd": 5, "p": round(float(p), 4)})
        except (TypeError, ValueError):
            return

    bath = deep.get("bathymetry", {}) or {}
    if bath.get("ok"):
        add("bathymetry", (bath.get("p_by_horizon") or {}).get("h5", bath.get("p_event_5bd")))
    ml = deep.get("ml", {}) or {}
    if ml.get("ok"):
        add("ml", ml.get("p_event_5bd"))
    sw = deep.get("swell", {}) or {}
    if sw.get("ok"):
        add("swell", (sw.get("event_by_horizon") or {}).get("h5"))
    tt = deep.get("tidetables", {}) or {}
    if tt.get("ok"):
        add("tidetables", (tt.get("event_odds") or {}).get("p"))
    st = deep.get("stacker", {}) or {}
    if st.get("ok"):
        add("stacker", st.get("p_now"))
    return rows


def build_dispatch(snap: dict, prev_value=None, date: str | None = None,
                   state: dict | None = None, issue_no: int | None = None) -> dict:
    comp = snap.get("engines", {}).get("composite", {})
    if comp.get("value") is None or not comp.get("regime"):
        raise SystemExit("refusing to write a dispatch without a live composite (no board, no letter)")
    date = date or (snap.get("generated_at") or datetime.now(timezone.utc).isoformat())[:10]
    baseline = _novelty_baseline(state or {}, date)
    letter_prev = _letter_baseline(state or {}, date)
    title, summary, tag = _title_summary_tag(snap, date, prev_value, baseline)

    novel, _held = _split_flagged(snap, baseline)

    s1 = _opening(snap, date, prev_value) + _attribution(snap, letter_prev)
    s2 = _movers_para(snap, date, baseline)
    if not novel:
        s2 += _forward_pulse(snap, date)
    s2 += _tripwire_para(snap)
    s2 += _press_para(snap)
    s3 = _tell_para(snap, date) or [
        "The Tell is dark today. When both sides of it are live, this section prints the gap "
        "between what the pipes measure and what the screens price."
    ]
    s4 = _kink_para(snap)
    s5 = _official_para(snap)
    s6 = _calendar_para(snap, letter_prev) or [
        "No flagged windows inside the calendar's horizon. A clear calendar is a reading too."
    ]
    s7 = _honesty_coda(snap)

    paras: list[str] = []
    if issue_no is not None:
        paras.append(f"*Issue {issue_no} · {date} · the sections run in the same order every day, "
                     "so the delta takes a minute to extract.*")
    paras += ["## 1 · The reading"] + s1
    paras += ["## 2 · What moved"] + s2
    paras += ["## 3 · The Tell"] + s3
    paras += ["## 4 · Reserve scarcity"] + s4
    paras += ["## 5 · The official sector"] + s5
    paras += ["## 6 · The dates that matter"] + s6
    paras += ["## 7 · What the board is honest about"] + s7

    free_md = "\n\n".join(p for p in paras if p)
    desk_md = _desk_read(snap, date, letter_prev)

    issues = lint_letter(title, summary, free_md, desk_md)
    if issues:
        raise SystemExit("letter failed lint: " + "; ".join(issues))

    return {
        "slug": f"{date}-daily",
        "title": title,
        "date": date,
        "tag": tag,
        "summary": summary,
        "free_md": free_md,
        "desk_md": desk_md,
        "state": _updated_state(snap, baseline, letter_prev, date),
        "odds": _current_odds(snap, date),
    }


def _forward_pulse(snap: dict, date: str) -> list[str]:
    """On a day with no new print, the free letter takes its spine from the
    forward read: the odds recompute daily even when the tape does not, so
    this paragraph is the part of a quiet letter that is genuinely new."""
    fwd = _forward_sentences(snap)
    if not fwd:
        return []
    lead = _pick(date, "pulse", [
        "With nothing new on the tape, the forward read carries the letter. ",
        "No fresh print does not mean no information; the odds recompute either way. ",
        "The tape is quiet, so the forward odds do the talking. ",
    ])
    return [lead + " ".join(fwd[:3])]


# ---------------------------------------------------------------------------
# filesystem + index
# ---------------------------------------------------------------------------
def write_dispatch(d: dict, repo_root: Path | None = None) -> list[str]:
    root = repo_root or REPO_ROOT
    free_dir = root / "frontend" / "public" / "dispatches"
    paid_dir = root / "backend" / "seiche" / "dispatches"
    index = free_dir / "index.json"
    free_dir.mkdir(parents=True, exist_ok=True)
    paid_dir.mkdir(parents=True, exist_ok=True)

    free_path = free_dir / f"{d['slug']}.md"
    body = d["free_md"] + (f"\n\n{MARKER}\n" if d["desk_md"] else "\n")
    free_path.write_text(body)

    written = [str(free_path)]
    if d["desk_md"]:
        paid_path = paid_dir / f"{d['slug']}.paid.md"
        paid_path.write_text(d["desk_md"] + "\n")
        written.append(str(paid_path))

    if d.get("state"):
        state_path = paid_dir / "state.json"
        state_path.write_text(json.dumps(d["state"], indent=2) + "\n")
        written.append(str(state_path))

    # The odds ledger is append-only and deduped by date: the same letter
    # rebuilt for the announce step must not double-count the day.
    if d.get("odds"):
        ledger = paid_dir / "odds_ledger.jsonl"
        seen_dates: set[str] = set()
        if ledger.exists():
            for line in ledger.read_text().splitlines():
                try:
                    seen_dates.add(str(json.loads(line).get("date")))
                except ValueError:
                    continue
        if d["date"] not in seen_dates:
            with ledger.open("a") as fh:
                for row in d["odds"]:
                    fh.write(json.dumps(row) + "\n")
            written.append(str(ledger))

    entries = []
    if index.exists():
        entries = json.loads(index.read_text())
    entries = [e for e in entries if e.get("slug") != d["slug"]]
    entries.insert(0, {k: d[k] for k in ("slug", "title", "date", "tag", "summary")})
    entries.sort(key=lambda e: e.get("date", ""), reverse=True)
    index.write_text(json.dumps(entries, indent=2) + "\n")
    written.append(str(index))
    return written


# ---------------------------------------------------------------------------
# Telegram announcement — a digest with the numbers, then the link
# ---------------------------------------------------------------------------
def build_telegram_digest(d: dict, snap: dict | None = None) -> str:
    """A Telegram-sized digest of the letter: the reading, the hook numbers,
    the next date, the link. Plain text, no markup surprises, no dashes."""
    lines = [
        f"SEICHE · the daily letter · {d['date']}",
        "",
        d["title"],
        "",
        d["summary"],
    ]
    if snap:
        cal = snap.get("calendar", {}) or {}
        weather = snap.get("engines", {}).get("weather", {}) or {}
        crunches = cal.get("crunch_windows") or weather.get("crunch_windows") or []
        if crunches:
            c = crunches[0]
            lines += ["", f"Next date that matters: {c.get('date')} ({c.get('reason', 'flagged window')})."]
    lines += ["", f"Full letter: https://seiche.info/#dispatches/{d['slug']}"]
    return "\n".join(lines)


def announce_telegram(d: dict, snap: dict | None = None) -> None:
    """Send the digest. Fail loud: an explicit announce with missing or
    refused credentials is an error, not a silent skip."""
    import os

    token = os.environ.get("SEICHE_TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("SEICHE_TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise SystemExit("announce needs SEICHE_TELEGRAM_BOT_TOKEN and SEICHE_TELEGRAM_CHAT_ID")
    body = json.dumps({
        "chat_id": chat_id,
        "text": build_telegram_digest(d, snap),
        "disable_web_page_preview": False,
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body, headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read().decode())
    if not resp.get("ok"):
        raise SystemExit(f"telegram refused the message: {resp}")
    print(f"announced on telegram (message_id {resp['result']['message_id']})")


# ---------------------------------------------------------------------------
# CLI — stdlib fetch so CI needs no install
# ---------------------------------------------------------------------------
def _get_json(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": "seiche-dispatch-daily"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _prev_published_value(history_url: str):
    """Yesterday's as-published composite from the hash-chained Book history."""
    try:
        hist = _get_json(history_url, timeout=30)
        if isinstance(hist, list) and hist:
            return hist[-1].get("value")
    except Exception:
        return None
    return None


def _issue_number(index_path: Path, slug: str) -> int | None:
    """The issue number is the letter's position in the archive: entries
    already published (excluding a same-day rewrite of this slug) plus one."""
    try:
        entries = json.loads(index_path.read_text())
        return 1 + sum(1 for e in entries if e.get("slug") != slug)
    except (OSError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write today's dispatch from the live board.")
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--history-url", default=HISTORY_URL)
    ap.add_argument("--date", default=None, help="override the dispatch date (YYYY-MM-DD)")
    ap.add_argument("--force", action="store_true", help="rewrite even if today's dispatch exists")
    ap.add_argument("--announce", action="store_true",
                    help="after writing, send the Telegram digest "
                         "(needs SEICHE_TELEGRAM_BOT_TOKEN and SEICHE_TELEGRAM_CHAT_ID)")
    ap.add_argument("--announce-only", action="store_true",
                    help="skip writing files; just build today's letter and send the digest")
    args = ap.parse_args(argv)

    date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = f"{date}-daily"

    snap = _get_json(f"{args.api}/api/overview")
    prev = _prev_published_value(args.history_url)
    d = build_dispatch(snap, prev_value=prev, date=date, state=load_state(),
                       issue_no=_issue_number(INDEX, slug))

    if args.announce_only:
        announce_telegram(d, snap)
        return 0

    if INDEX.exists() and not args.force:
        if any(e.get("slug") == slug for e in json.loads(INDEX.read_text())):
            print(f"dispatch {slug} already published — nothing to do")
            return 0

    for p in write_dispatch(d):
        print(f"wrote {p}")
    print(f"dispatch ready: {d['slug']} — {d['title']}")
    if args.announce:
        announce_telegram(d, snap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
