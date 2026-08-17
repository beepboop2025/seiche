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
  frontend/public/dispatches/{slug}.md        the free reading (+ HAS-DESK marker)
  backend/seiche/dispatches/{slug}.desk.md    the desk's forward read (free, like
                                              everything else; pre-rename history
                                              is *.paid.md with a HAS-PAID marker
                                              and every reader accepts both)
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
import math
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from seiche.newsroom import build_story

DEFAULT_API = "https://api.seiche.info"
HISTORY_URL = "https://seiche.info/data/book_history.json"
# The continuation marker. Everything on Seiche is free: ".paid.md" and
# "HAS-PAID" were pre-open-access names that gated nothing but invited
# misreading of a free public good, so new letters ship *.desk.md with a
# HAS-DESK marker. History on the box and in the archive still carries the
# legacy names, so every READER accepts both; only the writers moved.
MARKER = "<!--HAS-DESK-->"
LEGACY_MARKER = "<!--HAS-PAID-->"

# repo root = backend/seiche/dispatch_daily.py -> three parents up
REPO_ROOT = Path(__file__).resolve().parents[2]
FREE_DIR = REPO_ROOT / "frontend" / "public" / "dispatches"
PAID_DIR = REPO_ROOT / "backend" / "seiche" / "dispatches"
INDEX = FREE_DIR / "index.json"
ARTICLE_INDEX = REPO_ROOT / "frontend" / "public" / "articles" / "index.json"
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
    "buffers": "the cash buffers, RRP and TGA (buffers)",
    "undertow": "market microstructure (undertow)",
    "confession": "the swap-line confession (confession)",
    "warehouse": "the dealer warehouse (warehouse)",
    "tails": "the tail law (tails)",
    "auctions": "auction digestion (auctions)",
    "hydrophone": "repo microstructure (hydrophone)",
}


def _display(key) -> str:
    return DISPLAY_NAMES.get(str(key), str(key))


def _f(x):
    """float(x) or None. Every guard below has to distinguish 'zero' from
    'absent', and `x or 0` silently turns the second into the first: that is
    exactly how a print with no age became an overnight move."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v   # NaN is not a number the letter can use


# ---------------------------------------------------------------------------
# cadence: how fast a series is ALLOWED to move.
#
# A monthly series cannot move overnight, and the readers this letter is
# written for are precisely the people who notice when it is said to have.
# The frequency is therefore taken from the series' own metadata that SONAR
# ships with every mover (the registry's declared code, plus the spacing
# observed on its own index) rather than from a list of series names: OECD
# MEI publishes call rates for Japan, India and Korea on one cadence and the
# Korean one is LABELLED "overnight call rate". A name list covers the series
# that already embarrassed you; metadata covers its siblings too.
# ---------------------------------------------------------------------------
# Registry cadence codes as the number of days one print covers.
# D=daily, W=weekly, M=monthly, ML=lagged monthly, Q/QL=quarterly.
_FREQ_DAYS = {"D": 1.0, "W": 7.0, "M": 31.0, "ML": 31.0, "Q": 92.0, "QL": 92.0}

# Business-daily series have a median gap of one day and a Friday to Monday
# gap of three, so the daily bucket has to reach three and stop well short of
# a weekly seven.
_DAILY_CADENCE_MAX_D = 3.5

# Vocabulary that asserts the move happened inside a single session. Every
# phrase here is a claim about ARRIVAL, which is the claim a slow series
# cannot support.
_INTRADAY_RE = re.compile(
    r"\b(overnight|over ?night|intraday|last night|this morning|"
    r"since the close|in the session|on the session|on the day|"
    r"day ?over ?day|hourly|by the hour|tick by tick)\b",
    re.IGNORECASE,
)


def _cadence_days(m: dict) -> float | None:
    """How many days one print of this series covers, from its metadata.

    The SLOWER of the declared code and the observed spacing wins. A registry
    entry that says daily while the index says monthly is a registry that has
    drifted, and the safe reading of a disagreement about speed is the slow
    one: overstating cadence is what puts "overnight" on a monthly print.
    """
    cands = []
    code = str(m.get("freq") or "").strip().upper()
    if code in _FREQ_DAYS:
        cands.append(_FREQ_DAYS[code])
    observed = _f(m.get("cadence_d"))
    if observed is not None and observed > 0:
        cands.append(observed)
    return max(cands) if cands else None


def _moves_overnight(m: dict) -> bool:
    """True only when the series' own cadence PROVES a print can be an
    overnight event AND the print is in fact from last night.

    Unknown cadence returns False. The letter cannot prove the claim, so it
    does not make it: silence costs a shade of colour, a wrong "overnight"
    costs the reader.
    """
    cad = _cadence_days(m)
    if cad is None or cad > _DAILY_CADENCE_MAX_D:
        return False
    age = _f(m.get("age_d"))
    return age is not None and age <= 1


def _cadence_word(m: dict) -> str:
    cad = _cadence_days(m)
    if cad is None:
        return "latest"
    if cad >= 60:
        return "latest quarterly"
    if cad >= 20:
        return "latest monthly"
    if cad >= 4:
        return "latest weekly"
    return "latest"


def _arrival(m: dict) -> str:
    """How this print arrived, said in the series' own cadence."""
    if _moves_overnight(m):
        return "overnight"
    return f"on its {_cadence_word(m)} print"


def cadence_issues(title: str, movers: list[dict] | None) -> list[str]:
    """Publish guard: intraday language must never attach to a slow series.

    Structurally the titles below cannot produce this, because they take their
    arrival phrase from `_arrival`. This is the backstop for the next template
    somebody writes, and it is the thing the tests break on purpose.

    A series NAME may legitimately contain "overnight" (Korea's overnight call
    rate, the secured overnight financing rate). Narrowing the check to dodge
    that false positive by dropping the word would re-open the whole family,
    so the name is REMOVED from the string first and the remaining CLAIM is
    what gets scanned.
    """
    text = title or ""
    named = [m for m in (movers or [])
             if str(m.get("label") or "") and str(m.get("label")) in text]
    claim = text
    for m in named:
        claim = claim.replace(str(m.get("label")), " ")
    if not _INTRADAY_RE.search(claim):
        return []
    issues = {f"intraday language over a {_cadence_word(m)}-cadence series "
              f"('{m.get('label')!s}')"
              for m in named if not _moves_overnight(m)}
    return sorted(issues)


def _all_movers(snap: dict) -> list[dict]:
    return (snap.get("engines", {}).get("sonar", {}) or {}).get("movers") or []


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


_LEAK_RE = re.compile(r"(?<![\w.])(?:None|nan|NaN)(?![\w.])")


def sanitize_leaks(text: str) -> tuple[str, int]:
    """Turn a stray None or nan into the letter's own missing-value mark.

    The lint below is publish-blocking, and a letter that does not publish is
    worse than a letter with a question mark in it: the reader loses the day's
    reading over one absent upstream field. So a format leak is repaired and
    then CONFESSED in the honesty section rather than being fatal. Everything
    else the lint catches is the desk's own copy and stays fatal, because
    those are fixed by editing, not by degrading gracefully.
    """
    out, n = _LEAK_RE.subn("?", text or "")
    # "None of those printed" is English, not a leak; the negative lookahead in
    # the pattern misses it, so restore the word when it led a sentence.
    out = out.replace("? of those", "None of those")
    return out, n


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
    tell = snap.get("deep", {}).get("tell", {}) or {}
    return {
        "regime": (comp.get("regime") or "UNRATED").upper(),
        "value": comp.get("value"),
        "decomp": {str(d.get("component")): d.get("contribution")
                   for d in comp.get("decomposition", [])
                   if d.get("contribution") is not None},
        "crunch": crunch,
        # The Tell is carried so tomorrow's headline can say what it DID.
        # A headline that reports the same standing level three days running
        # is how "plumbing leads price at +32" ran on 2026-08-01, 08-02 and
        # 08-03: the number was true every day and news on none of them.
        "tell": tell.get("tell") if tell.get("ok") else None,
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


# How many recent dispatches a headline has to differ from. Two weeks: long
# enough that nobody scanning the archive sees the same line twice on one
# screen, short enough that a condition which genuinely recurs may be
# described the same way again once it has been out of sight.
TITLE_MEMORY_N = 14


def _title_key(t: str) -> str:
    """Normalised form for the repeat check. Case and spacing only: two
    headlines that differ by a number are DIFFERENT headlines, and folding
    the numbers away would defeat the guard it is here to enforce."""
    return " ".join(str(t or "").split()).casefold()


def _is_deminimis(m: dict) -> bool:
    share = _f(m.get("share_of_peak"))
    return share is not None and share < 0.02


def _mover_titles(m: dict, regime: str, v) -> list[str]:
    """Headlines for one novel print: the series, what it printed, and how
    far out that is. The regime label is the tape's mood, not the news."""
    label = _clean(m.get("label") or "one gauge")
    unit = _clean(m.get("unit") or "").strip()
    qty = f"{_fmt(m.get('last'), 2)} {unit}".strip()
    arrival = _arrival(m)
    absz = m.get("max_abs_z")
    lz, cz, chg = m.get("level_z"), m.get("change_z"), m.get("chg_1d")
    out: list[str] = []

    if _is_deminimis(m):
        # A 16 sigma z on a near-zero baseline is a series waking up, not a
        # large flow. The body already says so; a headline that implies size
        # the dollars do not support would be undone by its own paragraph.
        out.append(f"{label} wakes to {qty} {arrival}: "
                   f"{_fmt(absz, 1)} sigma on a near-zero base, not a large flow")
    czf = _f(cz)
    if czf is not None and abs(czf) < 1.0:
        # Flagged on LEVEL alone. Calling that a move is the category error
        # the "what moved" section exists to avoid, so the headline says which
        # kind of flag it is holding.
        out.append(f"{label} sits at {qty}, {_signed(lz, 1)} sigma from its own median: "
                   "a level, not a move")
    elif _f(chg) is not None:
        move = f"{_signed(chg, 2)} {unit}".strip()
        out.append(f"{label} moves {move} to {qty} {arrival}: "
                   f"{_fmt(absz, 1)} sigma against its own history")
    out.append(f"{label} prints {qty} {arrival}: "
               f"{_fmt(absz, 1)} sigma against its own history")
    out.append(f"{label} at {qty}, {_fmt(absz, 1)} sigma out: "
               f"{regime.lower()} reads {_fmt(v)}")
    return out


def _title_candidates(snap: dict, date: str, prev_value, baseline: dict,
                      letter_prev: dict) -> list[str]:
    """Every true headline the day supports, best first.

    Each one names what actually CHANGED and carries the number that makes it
    checkable. The last entry carries the date, so the ladder can always end
    somewhere no earlier letter has been.
    """
    comp = snap.get("engines", {}).get("composite", {})
    tell = snap.get("deep", {}).get("tell", {}) or {}
    v = comp.get("value")
    regime = (comp.get("regime") or "UNRATED").upper()
    delta = None
    if prev_value is not None and v is not None:
        vf, pf = _f(v), _f(prev_value)
        delta = None if (vf is None or pf is None) else vf - pf

    novel, held = _split_flagged(snap, baseline)
    tell_v = _f(tell.get("tell")) if tell.get("ok") else None
    p_pctl, m_pctl = _f(tell.get("plumbing_pctl")), _f(tell.get("market_pctl"))
    cands: list[str] = []

    # The argument outranks the tape. A notable print is evidence; it is not
    # automatically the story. The recent-title guard below prevents a stable
    # thesis from becoming the same headline every morning, after which the
    # existing change-led ladder takes over.
    # ``_clean(None)`` deliberately renders a visible "?" elsewhere in the
    # letter.  Here, however, a missing editorial object means an older
    # snapshot and must fall through to the established headline ladder.
    raw_editorial_thesis = (snap.get("editorial") or {}).get("thesis")
    if raw_editorial_thesis:
        cands.append(_clean(raw_editorial_thesis).rstrip("."))

    # 1. The board itself moved. Name the component that did the work.
    if delta is not None and abs(delta) >= 5:
        direction = "climbs" if delta > 0 else "eases"
        deltas, _dropped = _component_deltas(snap, letter_prev)
        moved = sorted(deltas.items(), key=lambda kv: -abs(kv[1]))
        if moved and abs(moved[0][1]) >= 0.1:
            k, dv = moved[0]
            cands.append(f"The board {direction} {abs(delta):.0f} points to {_fmt(v)}: "
                         f"{_display(k)} {_signed(dv, 1)} of it")
        cands.append(f"The board {direction} {abs(delta):.0f} points to {_fmt(v)}, "
                     f"regime {regime.lower()}")

    # 2. A print the reader has not been told about yet.
    for m in novel[:2]:
        cands += _mover_titles(m, regime, v)

    # 3. The Tell. What it DID, not the standing level: a wide gap is true
    #    every day it persists and news on the day it changes.
    if tell_v is not None and abs(tell_v) >= 30:
        side = "plumbing leads price" if tell_v > 0 else "price leads plumbing"
        prev_tell = _f((letter_prev or {}).get("tell"))
        d_t = None if prev_tell is None else tell_v - prev_tell
        # A gap that MOVED outranks a gap that is merely wide.
        if d_t is not None and abs(d_t) >= 1:
            verb = "widens" if abs(tell_v) > abs(prev_tell) else "narrows"
            cands.append(f"The Tell {verb} {abs(d_t):.0f} to {_signed(tell_v, 1)}: "
                         f"{side}, {regime.lower()} at {_fmt(v)}")
        if p_pctl is not None and m_pctl is not None:
            cands.append(f"The Tell reads {_signed(tell_v, 1)}: plumbing at the "
                         f"{_ordinal(p_pctl)} percentile of its own history, screens at the "
                         f"{_ordinal(m_pctl)}")
        if d_t is not None and abs(d_t) < 1:
            held_txt = ("unchanged against the last letter" if abs(d_t) < 0.005
                        else f"{_signed(d_t, 2)} against the last letter")
            cands.append(f"The Tell holds at {_signed(tell_v, 1)}, {held_txt}: {side}")
        cands.append(f"{regime.title()} with a loud tell: {side} at {_signed(tell_v, 1)}")

    # 4. Nothing new printed, but something is still flagged. The only fact
    #    that changed is the age of the flag, so the headline prints the age
    #    and NOT the series: a standing flag was already headlined on the day
    #    it printed, and putting its name back up top is the re-headline this
    #    letter refuses to do. The series stays named in the body, where it is
    #    labelled as persistence.
    if held:
        n = len(held)
        ages = [a for a in (_f(x.get("age_d")) for x in held) if a is not None]
        if ages:
            cands.append(f"No print cleared the 2.5 sigma bar: the freshest standing flag is "
                         f"{int(min(ages))} days old, {regime.lower()} at {_fmt(v)}")
        cands.append(f"{regime.title()} at {_fmt(v)}: "
                     f"{n} standing flag{'s' if n != 1 else ''}, none of them new today")

    # 5. A genuinely quiet tape. The forward read recomputes even when the
    #    tape does not, and the calendar counts down, so both are news of a
    #    kind and both carry a number that moves.
    p5 = _f(((snap.get("deep", {}).get("bathymetry") or {}).get("p_by_horizon") or {}).get("h5"))
    if p5 is None:
        p5 = _f((snap.get("deep", {}).get("ml") or {}).get("p_event_5bd"))
    if p5 is not None:
        cands.append(f"{regime.title()} at {_fmt(v)}, nothing over the 2.5 sigma bar: "
                     f"five-day event odds {_fmt(p5 * 100)}%")
    cal = snap.get("calendar", {}) or {}
    weather = snap.get("engines", {}).get("weather", {}) or {}
    crunches = cal.get("crunch_windows") or weather.get("crunch_windows") or []
    if crunches and crunches[0].get("date"):
        days = _days_until(crunches[0].get("date"), date)
        if days is not None:
            cands.append(f"{regime.title()} at {_fmt(v)}, {days} days to "
                         f"{crunches[0].get('date')}: the tape stays quiet into it")

    # 6. The floor. Carries the date, so the ladder cannot run out.
    cands.append(f"{regime.title()} at {_fmt(v)}: the desk letter for {date}")
    return cands


def _choose_title(cands: list[str], recent_titles, movers) -> str:
    """First candidate that is neither a repeat nor a cadence overstatement.

    Two guards, one pass. The repeat guard is why the same line cannot run
    three days together; the cadence guard is why no line here can say a
    monthly series moved overnight even if a future template tries to.
    """
    seen = {_title_key(t) for t in (recent_titles or [])}
    first_clean = ""
    first_any = ""
    for c in cands:
        c = " ".join(str(c or "").split())
        if not c:
            continue
        first_any = first_any or c
        if cadence_issues(c, movers):
            continue
        first_clean = first_clean or c
        if _title_key(c) not in seen:
            return c
    # Nothing survived. Hand back the best candidate there was rather than an
    # empty headline: a letter with no title publishes silently, and the
    # caller's guard is waiting to refuse this one out loud.
    return first_clean or first_any


def _title_summary_tag(snap: dict, date: str, prev_value, baseline: dict,
                       letter_prev: dict | None = None,
                       recent_titles: list[str] | None = None) -> tuple[str, str, str]:
    comp = snap.get("engines", {}).get("composite", {})
    tell = snap.get("deep", {}).get("tell", {}) or {}
    v = comp.get("value")
    regime = (comp.get("regime") or "UNRATED").upper()
    delta = None
    if prev_value is not None and v is not None:
        vf, pf = _f(v), _f(prev_value)
        delta = None if (vf is None or pf is None) else vf - pf
    tell_v = tell.get("tell") if tell.get("ok") else None

    cands = _title_candidates(snap, date, prev_value, baseline, letter_prev or {})
    title = _choose_title(cands, recent_titles, _all_movers(snap))
    if not title:
        raise SystemExit("refusing to publish a letter with no headline")

    # Fail visible. The ladder ends in a candidate that names no series, so a
    # violation here means the guard above stopped working rather than that
    # today's board was awkward, and a wrong claim about arrival is the desk's
    # own copy: fixed by editing, not by degrading gracefully.
    bad = cadence_issues(title, _all_movers(snap))
    if bad:
        raise SystemExit("title failed the cadence guard: " + "; ".join(bad))

    editorial = snap.get("editorial") or {}
    if editorial.get("standfirst"):
        summary = _clean(editorial["standfirst"])
        if delta is not None:
            summary += f" The index is {_signed(delta, 1)} against the last published letter."
    else:
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


def _editorial_argument(snap: dict) -> list[str]:
    """The letter's argument before its fixed diagnostic skeleton.

    The section consumes the same versioned editorial object as the website's
    TODAY page.  That keeps the front page and the frozen letter from making
    subtly different claims from identical numbers.
    """
    editorial = snap.get("editorial") or {}
    thesis = editorial.get("thesis")
    if not thesis:
        return []

    out = [f"**{_clean(thesis)}**"]
    if editorial.get("standfirst"):
        out.append(_clean(editorial["standfirst"]))

    evidence = editorial.get("evidence") or []
    if evidence:
        lines = []
        for row in evidence[:3]:
            stamp = f", as of {row.get('asof')}" if row.get("asof") else ""
            source = f" Source: {row.get('source')}." if row.get("source") else ""
            lines.append(
                f"- **{_clean(row.get('label') or 'Evidence')}**{stamp}: "
                f"{_clean(row.get('claim') or '')}{source}"
            )
        out.append("\n".join(lines))

    counter = editorial.get("countercase") or []
    if counter:
        facts = " ".join(_clean(row.get("claim") or "") for row in counter[:2])
        out.append(f"**The countercase:** {facts}")

    confidence = editorial.get("confidence")
    note = editorial.get("confidence_note")
    if confidence and note:
        out.append(f"**Conviction: {str(confidence).upper()}.** {_clean(note)}")
    return out


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
    cov_txt = f", on {_fmt(cov)}% coverage" if cov is not None else ""
    out.append(
        f"The composite reads **{_fmt(v)} out of 100, {regime}**{delta_txt}{cov_txt}. "
        + _REGIME_FRAME.get(regime, "The board publishes what it sees and nothing else.")
    )
    decomp = [d for d in comp.get("decomposition", []) if d.get("contribution")]
    if decomp:
        top = decomp[0]
        # Saturation disclosure applies to EVERY pinned component, not just the
        # largest. Naming one while a second sits at its ceiling is the letter
        # breaking its own stated rule inside the paragraph that states it.
        def _pinned(d: dict) -> bool:
            if d.get("saturated") is not None:
                return bool(d["saturated"])
            try:
                return float(d.get("score") or 0) >= 99.5
            except (TypeError, ValueError):
                return False

        all_pinned = [d.get("component") for d in comp.get("decomposition", []) if _pinned(d)]
        pinned = ""
        if all_pinned:
            names = ", ".join(_display(c) for c in all_pinned)
            body = ("Those components are carrying no new information from day to day"
                    if len(all_pinned) > 1
                    else "That component is carrying no new information from day to day")
            pinned = (f" Pinned at the ceiling today: {names}. {body}, and the letter names "
                      "every one of them rather than only the loudest.")
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


def _component_deltas(snap: dict, letter_prev: dict) -> tuple[dict, list[str]]:
    """Per-component change against the letter's memory of yesterday.

    Shared by the attribution paragraph and by the headline, which needs to
    name the component that did the work rather than restate the regime.
    """
    prev = (letter_prev or {}).get("decomp") or {}
    if not prev:
        return {}, []
    comp = snap.get("engines", {}).get("composite", {}) or {}
    cur = {str(d.get("component")): d.get("contribution")
           for d in comp.get("decomposition", []) if d.get("contribution") is not None}
    # A component that went DEAD publishes no contribution today, so iterating
    # over today's keys alone drops it and the stated net silently excludes a
    # component the letter's own dead-inputs line is about to name. Yesterday's
    # keys are in the union, and a vanished contribution is a fall to zero.
    dropped: list[str] = []
    deltas = {}
    for k in set(cur) | set(prev):
        if k not in prev:
            continue                    # brand new component: no baseline to difference
        try:
            before = float(prev[k])
        except (TypeError, ValueError):
            continue
        if k in cur:
            try:
                deltas[k] = round(float(cur[k]) - before, 2)
            except (TypeError, ValueError):
                continue
        else:
            deltas[k] = round(-before, 2)
            dropped.append(k)
    return deltas, dropped


def _attribution(snap: dict, letter_prev: dict) -> list[str]:
    """Two-way attribution: the day's change decomposed by component, from
    the letter's own memory of yesterday's decomposition. Level attribution
    is the table on the board; change attribution is what a reader diffing
    consecutive letters actually wants."""
    if not ((letter_prev or {}).get("decomp") or {}):
        return []
    deltas, dropped = _component_deltas(snap, letter_prev)
    moved = [(k, dv) for k, dv in sorted(deltas.items(), key=lambda kv: -abs(kv[1]))
             if abs(dv) >= 0.1]
    if not moved:
        return ["Component by component, the reading is flat against the last letter: "
                "no contribution moved a tenth of a point. Flat gets said too."]
    total = round(sum(deltas.values()), 1)
    parts = ", ".join(f"{_display(k)} {_signed(dv, 1)}" for k, dv in moved[:4])
    drop_txt = ""
    if dropped:
        drop_txt = (f" {', '.join(_display(k) for k in dropped)} stopped contributing entirely today, "
                    "which is counted above as a fall to zero rather than quietly left out of the sum.")
    return [f"Change gets attributed here, not just level: against the last letter, "
            f"{parts}, {_signed(total, 1)} points net across the components." + drop_txt]


def _tell_para(snap: dict, date: str) -> list[str]:
    tell = snap.get("deep", {}).get("tell", {}) or {}
    if not tell.get("ok"):
        return []
    t = tell.get("tell")
    p, m = tell.get("plumbing_pctl"), tell.get("market_pctl")
    reading = _clean(tell.get("reading", ""))
    # The headline is the unrounded difference; the components print as
    # integers. A reader who subtracts them gets a number one off the bold
    # one, so the letter shows the arithmetic to one decimal instead of
    # inviting the subtraction and then failing it.
    detail = "."
    if p is not None and m is not None:
        try:
            # No ordinal suffix on a one-decimal figure: "81.2th" is what the
            # publish lint calls a malformed ordinal, and it would kill the
            # letter outright on any percentile whose first decimal is 1, 2 or 3.
            detail = (f": plumbing indicators at the {_fmt(p, 1)} percentile of their own history, "
                      f"market indicators at the {_fmt(m, 1)}, a gap of {_signed(float(p) - float(m), 1)}.")
        except (TypeError, ValueError):
            detail = (f": plumbing indicators at the {_ordinal(p)} percentile of their own history, "
                      f"market indicators at the {_ordinal(m)}.")
    lines = [
        f"The Tell, the gap between what the pipes measure and what the screens price, reads "
        f"**{_signed(t, 1)}**" + detail
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
    unit = _clean(m.get("unit") or "")
    return (f"For scale: that {_clean(m.get('label'))} print is {_fmt(m.get('last'), 2)} {unit} "
            f"against a historical peak near {_fmt(peak)} {unit}. The z score marks a "
            f"series waking up on a quiet baseline, not a large flow; the dollar amount is de minimis "
            f"and the letter says so next to the sigma.{woke}")


def _movers_para(snap: dict, date: str, baseline: dict) -> list[str]:
    sonar = snap.get("engines", {}).get("sonar", {})
    novel, held = _split_flagged(snap, baseline)
    out: list[str] = []

    if novel:
        bits = []
        level_only = []
        for m in novel[:3]:
            # A flag can come from the LEVEL alone. Under a heading called
            # "what moved", presenting a series whose change statistic is
            # unremarkable as a mover is a category error, so the letter
            # says which kind of flag it is holding.
            try:
                cz = abs(float(m.get("change_z")))
            except (TypeError, ValueError):
                cz = None
            if cz is not None and cz < 1.0:
                level_only.append(_clean(m.get("label")))
            bits.append(
                f"**{_clean(m.get('label'))}** printed {_fmt(m.get('last'), 2)} {_clean(m.get('unit') or '')} "
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
        if level_only:
            plural = "flags are" if len(level_only) > 1 else "flag is"
            out.append(
                f"One qualification on that list: the {', '.join(level_only)} {plural} on LEVEL, not on "
                "change. The series sits somewhere unusual in its own history and did not travel to get "
                "there today, which is worth knowing and is not the same claim as a move."
            )
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
                f"One reading is extreme but not fresh: **{_clean(s.get('label'))}** sits at "
                f"{_fmt(s.get('max_abs_z'), 1)} robust z on a print from {s.get('asof')}, "
                f"{s.get('age_d')} days behind the board. That is a level worth knowing and "
                "not a move, so it is reported here rather than in the movers line."
            )

    if held:
        # A standing flag carries its sigma into every letter until the series
        # reprints, so the scale caveat has to travel with it. A 16-sigma flag
        # on $378 million read as an alarm for five days running.
        bits = []
        trivial = []
        for m in held[:3]:
            share = m.get("share_of_peak")
            bit = f"**{_clean(m.get('label'))}** (|z| {_fmt(m.get('max_abs_z'), 1)}, as of {m.get('asof')})"
            try:
                if share is not None and float(share) < 0.02:
                    bit += " [de minimis]"
                    trivial.append(_clean(m.get("label")))
            except (TypeError, ValueError):
                pass
            bits.append(bit)
        tail = ""
        if trivial:
            tail = (f" The flags marked de minimis ({', '.join(trivial)}) sit at large sigma on series "
                    "whose levels are a rounding error against their own history: the standardizer is "
                    "reacting to a near-zero baseline, not to a large flow, and the letter would rather "
                    "say that every day than let a big number do work the dollars do not support.")
        out.append(
            f"Still flagged from prints already covered in an earlier letter: {'; '.join(bits)}. "
            "A standing flag is context, not news; when a fresh print lands it goes back in the movers line."
            + tail
        )
    return out


def _tripwire_para(snap: dict) -> list[str]:
    """Facility take-up watch (SRF, discount window, FIMA) with the stigma
    gauge's judgment on top, when the engine is live."""
    st = snap.get("engines", {}).get("stigma", {}) or {}
    if st.get("ok") and st.get("letter_line"):
        return [_clean(st["letter_line"])]
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
        "The desk fits the reserve demand curve continuously, a public-data approximation of the "
        "NY Fed's monthly Reserve Demand Elasticity (the Afonso, Giannone, La Spada and Williams "
        "lineage). Theirs is estimated on confidential bank-level fed funds transactions; this one "
        "is a hinge fit on SOFR minus IORB against reserves over GDP, which is a different and "
        "coarser instrument reaching for the same quantity, and the comparison below is the check "
        f"on whether it gets there. Today's fit puts the kink near **${_fmt(k.get('kink_reserves_b'))}B** "
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
        line = ("Reserves are through the estimated kink, which is exactly where the spread should start "
                "answering to reserve changes. From here the board watches the slope, not the distance.")
        # The tape gets the last word over the fit. A system priced below the
        # administered floor is not behaving like a scarce one, and a letter
        # that asserts scarcity while its own spread says abundance has told
        # the reader to stop reading.
        obs = k.get("observed_spread_now_bp")
        try:
            if obs is not None and float(obs) < 0:
                line += (f" Against that, the tape disagrees: SOFR is printing {_fmt(abs(float(obs)), 1)}bp "
                         "BELOW IORB, which is the abundance signature, not the scarce one. The honest "
                         "reading is that the fitted kink is an estimate with a wide band and the observed "
                         "spread is a fact, so scarcity here is a hypothesis the tape has not yet confirmed.")
        except (TypeError, ValueError):
            pass
        out.append(line)
    else:
        if k.get("days_to_kink") is not None:
            out.append(
                f"At the trailing drain of ${_fmt(k.get('drain_per_bday_b'), 1)}B a business day, the path "
                f"reaches the kink in roughly {_fmt(k.get('days_to_kink'))} business days. That is straight-line "
                "arithmetic; the runway view carries the scenario bands behind it."
            )
        rw = snap.get("engines", {}).get("runway", {}) or {}
        if rw.get("ok"):
            scen = rw.get("scenarios") or {}
            cross = (scen.get("base") or {}).get("crossing_date")
            others = sorted(c for name, s in scen.items() if name != "base"
                            and isinstance(s, dict) and (c := s.get("crossing_date")))
            if cross:
                span = (f"; the fast and slow legs bracket it between {others[0]} and {others[-1]}"
                        if others else "")
                out.append(
                    f"The runway projection puts the base path through the kink on **{cross}**{span}. "
                    "Every assumption behind that date is published beside it, not implied: the "
                    "trailing drift, the stated runoff pace, the TGA path and the settlement drains."
                )
            else:
                end_b = (scen.get("base") or {}).get("end_reserves_b")
                out.append(
                    "The runway projection finds no kink crossing inside thirteen weeks on any of its "
                    f"three legs, ending near ${_fmt(end_b)}B on the base path. A projection that "
                    "reaches no threshold is still a reading, and it gets published on the days it "
                    "says nothing is coming."
                )
    # Where the reserve change came from, before the reader has to ask. The
    # kink says whether the level matters; the ledger says which liability
    # moved it, which is the question every desk asks next.
    led = snap.get("engines", {}).get("ledger", {}) or {}
    if led.get("ok") and led.get("letter_line"):
        out.append(_clean(led["letter_line"]))

    rde = snap.get("engines", {}).get("rdenowcast", {}) or {}
    if rde.get("ok"):
        # None means the official release shipped no band this month, which is
        # not the same as our fit sitting outside one. Publishing "outside"
        # for an unknown would be inventing a disagreement with the Fed.
        wb = rde.get("within_68_band")
        band_txt = (f", {'inside' if wb else 'outside'} their 68% band" if wb is not None
                    else ". Their release carries no band this month, so this is a level "
                         "comparison only and the letter will not claim agreement it cannot test")
        agree = "agrees" if rde.get("direction_agree") else "disagrees"
        lead = rde.get("nowcast_lead_days")
        lead_txt = ""
        try:
            if lead is not None and int(lead) > 0:
                lead_txt = (f" The desk's fit runs {_fmt(lead)} days ahead of their release cycle, "
                            "which is the whole point of fitting it continuously.")
        except (TypeError, ValueError):
            pass
        out.append(
            f"External check: the NY Fed's latest official RDE print ({rde.get('nyfed_asof')}) reads "
            f"{_fmt(rde.get('nyfed_bp_per_1pct'), 2)}bp per one percent of reserves; the desk's continuous "
            f"fit implies {_fmt(rde.get('ours_bp_per_1pct'), 2)}bp{band_txt}, direction "
            f"{agree}.{lead_txt} Where the two diverge, one of us is wrong, and the scorecard "
            "keeps count."
        )
        # Today's comparison without the running record is cherry-picking by
        # omission, and a good day is exactly when the record is easiest to
        # leave out. It prints in the same paragraph, every day, either way.
        summ = rde.get("scorecard_summary") or rde.get("summary") or {}
        n = summ.get("n")
        try:
            if n:
                wb, da = summ.get("within_band"), summ.get("direction_agree")
                verdict = ("better than their band alone implies"
                           if wb is not None and wb / float(n) >= 0.68
                           else "short of the two-in-three a calibrated nowcast should manage, "
                                "so the fit is directionally useful and not yet precise")
                out.append(
                    f"The running record, not just today: across {_fmt(n)} of their releases the desk's "
                    f"walk-forward fit landed inside their 68% band {_fmt(wb)} times and agreed on "
                    f"direction {_fmt(da)} times, mean absolute gap {_fmt(summ.get('mean_abs_diff_bp'), 2)}bp. "
                    f"That is {verdict}. One matching print is an anecdote; this line is the claim, and it "
                    "is graded against their current-vintage history rather than the print that stood on "
                    "each cutoff date, which flatters neither side in a way the desk can measure."
                )
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    return out


def _official_para(snap: dict) -> list[str]:
    """The official sector: custody, the foreign RRP pool, FIMA repo. The
    rotation-versus-retreat read comes from the officialbid engine."""
    ob = snap.get("engines", {}).get("officialbid", {}) or {}
    if ob.get("ok") and ob.get("letter_line"):
        return [_clean(ob["letter_line"])]
    return ["The official sector engine is dark today. When it is live this section reads the "
            "foreign official footprint in one place: Treasuries in Fed custody, the foreign "
            "RRP pool, and FIMA repo, decomposed into rotation versus retreat."]


def _days_until(event_date, letter_date: str, fallback=None):
    """Days from the LETTER's dateline to the event, not from the snapshot's.

    The snapshot computes days_until against its own generated_at, and the
    letter publishes some hours later, sometimes across midnight. Reusing the
    snapshot's number printed 'FOMC decides tomorrow' on the morning of the
    meeting. The letter recomputes against the date it is stamped with, and
    falls back to the snapshot only when the event date is unparseable.
    """
    try:
        a = datetime.strptime(str(event_date)[:10], "%Y-%m-%d").date()
        b = datetime.strptime(str(letter_date)[:10], "%Y-%m-%d").date()
        return (a - b).days
    except (TypeError, ValueError):
        return fallback


def _calendar_para(snap: dict, letter_prev: dict | None = None, date: str = "") -> list[str]:
    cal = snap.get("calendar", {}) or {}
    turn = (snap.get("deep", {}).get("turn") or {}).get("next_turn")
    weather = snap.get("engines", {}).get("weather", {}) or {}
    out = []
    crunches = [c for c in (cal.get("crunch_windows") or weather.get("crunch_windows") or [])
                if c.get("date")]
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
    if turn and turn.get("date"):
        band = turn.get("band_bp") or [None, None]
        out.append(
            f"The turn model puts {turn.get('date')} ({_clean(turn.get('mode'))}) at "
            f"{_signed(turn.get('forecast_bp'), 1)}bp with a band of "
            f"[{_signed(band[0], 1)}, {_signed(band[1], 1)}], severity {_fmt(turn.get('severity'))}/5."
        )
    # A calendar entry with no date is not a date that matters. Skipping it is
    # both the honest read and the thing that stops a missing upstream field
    # from rendering "None" and tripping the publish-blocking lint.
    fomc = [f for f in (cal.get("fomc_next_90d") or []) if f.get("date")]
    if fomc:
        f = fomc[0]
        days = _days_until(f.get("date"), date, f.get("days_until"))
        if days is not None and int(days) <= 1:
            when = "today" if int(days) <= 0 else "tomorrow"
            out.append(
                f"FOMC decides {when}, and the letter cares in three named places: the IORB and ON RRP "
                "settings, which are the corridor every spread on this board is priced against; the runoff "
                "pace, which sets the drain rate behind the reserve path; and any change to the SRF, the "
                "ceiling whose quiet take-up the tripwires watch. The next issue grades this stanza against "
                "the statement."
            )
        else:
            out.append(f"FOMC decides {f.get('date')}, {_fmt(days)} days out.")
    tax = [t for t in (cal.get("corporate_tax_next_90d") or []) if t.get("date")]
    if tax:
        t = tax[0]
        tdays = _days_until(t.get("date"), date, t.get("days_until"))
        out.append(f"The corporate tax date lands {t.get('date')}, {_fmt(tdays)} days out; tax dates drain reserves on a schedule everyone can read.")
    return [" ".join(out)] if out else []


def _honesty_coda(snap: dict) -> list[str]:
    bt = (snap.get("deep", {}) or {}).get("backtest", {}) or {}
    ec = bt.get("event_capture") or {}
    proof = ""
    try:
        if ec.get("recall") is not None:
            ci = ec.get("recall_ci95") or []
            ci_txt = (f" (95% interval {float(ci[0]):.0%} to {float(ci[1]):.0%}, which is wide because the "
                      f"sample is {_fmt(ec.get('n_alert_runs'))} alert runs, not thousands)"
                      if len(ci) == 2 else "")
            # A lead time pinned at the evaluation horizon is a censoring
            # boundary, not an observation. Reporting it as a median would be
            # the letter's own worst habit, applied to its own scoreboard.
            leads = [float(x) for x in (ec.get("lead_times_d") or []) if x is not None]
            med = ec.get("median_lead_d")
            lead_txt = ""
            if leads and med is not None:
                capped = sum(1 for x in leads if x >= float(med))
                if capped >= max(2, 0.5 * len(leads)) and len(set(leads)) < len(leads):
                    lead_txt = (f", and the median lead of {_fmt(med)} trading days is censored: "
                                f"{capped} of {len(leads)} episodes were still flagged at the edge of the "
                                "evaluation window, so the true lead is at least that and the number is a "
                                "floor rather than a central estimate")
                else:
                    lead_txt = f", median lead {_fmt(med)} trading days"
            proof = (
                f" The record is in PROOF: event recall {float(ec['recall']):.0%}{ci_txt} against a base rate of "
                f"{float(ec.get('base_rate') or 0):.0%}, run precision {float(ec.get('precision_runs') or 0):.0%}"
                f"{lead_txt}. The misses sit next to the hits; read those before weighting today's letter."
            )
    except (TypeError, ValueError, IndexError):
        proof = ""
    if not proof:
        proof = (" The misses this board has made sit in PROOF next to the hits; read those "
                 "before weighting today's letter.")

    # An engine missing from the payload never raises a fault, so "all engines
    # report live" could sit two sections below "the official sector engine is
    # dark today". The coda counts the sections the letter itself found dark.
    eng = snap.get("engines", {}) or {}
    dark = [name for name, label in (("kink", "reserve scarcity"),
                                     ("officialbid", "the official sector"),
                                     ("stigma", "the facility tripwires"),
                                     ("runway", "the reserve runway"))
            if not (eng.get(name) or {}).get("ok")]
    dark_txt = ""
    if dark:
        dark_txt = (f" {len(dark)} of the letter's named sections are dark today "
                    f"({', '.join(dark)}); each says so in its own place rather than closing the gap "
                    "with a sentence about something else.")

    faults = snap.get("faults") or []
    if faults:
        # One source failing twice is one broken gauge, not two; the letter
        # reports the gauge, not the retry count.
        seen: list[str] = []
        for f in faults:
            s = str(f.get("source"))
            if s not in seen:
                seen.append(s)
        srcs = ", ".join(seen[:4])
        return [
            f"Faults on the board today: {srcs}. The affected inputs are degraded or dead and the "
            "composite's coverage says so. A dashboard that hides its broken gauges is lying with a straight face."
            + dark_txt + proof
        ]
    lead = ("All sources report live, and no engine raised a fault."
            if dark else "All sources and engines report live.")
    return [lead + dark_txt + proof]


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
            # The stacker's verdict turns self-critical after the first
            # parenthesis ("does NOT beat the best single member"), which is
            # exactly the half a credible letter must keep. Print it whole.
            v_txt = _clean(st.get("verdict")).rstrip()
            if v_txt and not v_txt.endswith("."):
                v_txt += "."
            verdict = f". Its own verdict, in full: {v_txt}" if v_txt else ""
            # The stack pools its OWN member set, which is not the three
            # sentences above it. Printing 14 percent, 5 percent and a pooled
            # 6 percent in one paragraph without saying that invites a reader
            # to try the arithmetic and conclude the letter cannot add up.
            n_members = len(st.get("members_now") or {})
            scope = (f", pooled over its own {n_members} members rather than the views quoted above"
                     if n_members else "")
            out.append(
                f"The stack pools the members at **{float(st['p_now']):.0%}** for the five-day window, "
                f"dispersion {_fmt(st.get('dispersion_now'), 2)}{scope}{verdict or '.'}"
            )
        except (TypeError, ValueError):
            pass
    mc = deep.get("modelcourt", {}) or {}
    if mc.get("ok"):
        if mc.get("adjudication"):
            out.append(_clean(mc["adjudication"]))
        if isinstance(mc.get("ledger_status"), str) and mc["ledger_status"]:
            out.append("Ledger status: " + _clean(mc["ledger_status"]) + ".")
        return out
    # Raw Brier is not comparable across members: each is scored on its own
    # sample with its own base rate, so a low Brier can mean an easy sample
    # rather than a good model. Skill against each member's OWN climatology is
    # the only ranking that survives contact with a statistician, and a member
    # that never published a climatology cannot be ranked at all.
    skills: list[tuple[str, float, float, float]] = []
    unranked: list[str] = []

    def _consider(label: str, brier, clim) -> None:
        if brier is None:
            return
        try:
            b = float(brier)
        except (TypeError, ValueError):
            return
        if clim is None:
            unranked.append(label)
            return
        try:
            c = float(clim)
        except (TypeError, ValueError):
            unranked.append(label)
            return
        if c <= 0:
            unranked.append(label)
            return
        skills.append((label, 1.0 - b / c, b, c))

    sk = (deep.get("tidetables", {}) or {}).get("skill") or {}
    _consider("the analog tables (Tide Tables)", sk.get("brier"), sk.get("brier_climatology"))
    sw = (deep.get("swell", {}) or {}).get("validation") or {}
    _consider("the dated term structure (Swell)", sw.get("brier"), sw.get("brier_climatology"))
    mlv = (deep.get("ml", {}) or {}).get("validation") or {}
    _consider("the learned model", mlv.get("brier"), mlv.get("brier_climatology"))

    if skills:
        best = max(skills, key=lambda s: s[1])
        tail = (f" {', '.join(unranked)} published no climatology, so it cannot be ranked here and is not."
                if unranked else "")
        if best[1] <= 0.01:
            out.append(
                "Adjudication, not averaging: on their own scored samples, no member clears its own "
                f"climatology by more than a hair (best is {best[0]} at {best[1]:+.1%} Brier skill, "
                f"{_fmt(best[2], 3)} against {_fmt(best[3], 3)}). That is the honest read of the forward "
                "odds today: they are a view, not an edge, and the letter will not dress them up as one."
                + tail
            )
        else:
            out.append(
                f"Adjudication, not averaging: ranked on skill against each member's own climatology, "
                f"{best[0]} leads at {best[1]:+.1%} ({_fmt(best[2], 3)} Brier against a climatology of "
                f"{_fmt(best[3], 3)}). Raw Brier is not comparable across members because each is scored "
                "on its own sample, so the ranking uses skill or it says nothing. The disagreement between "
                "members is model risk, and the court publishes it instead of hiding it in a mean. Every "
                "model's daily odds go to the public ledger, so this paragraph gets harder to argue with "
                "every month." + tail
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

    # A tripwire that names two facilities has to read both. The discount
    # window sits in the headline block, not in SONAR, and reading only SRF
    # published S1 as untouched on days the window was already through it.
    dw = (snap.get("headline") or {}).get("dw_b") or {}
    dw_v = dw.get("value")
    facility_bits = []
    if srf and srf.get("last") is not None:
        facility_bits.append(f"SRF {_fmt(srf.get('last'), 2)} {srf.get('unit') or '$B'} as of {srf.get('asof')}")
    if dw_v is not None:
        facility_bits.append(f"discount window ${_fmt(dw_v, 1)}B as of {dw.get('asof')}")
    srf_now = ("; " + ", ".join(facility_bits)) if facility_bits else ""
    breached = False
    try:
        breached = (dw_v is not None and float(dw_v) >= 1.0) or (
            srf is not None and srf.get("last") is not None
            and float(srf.get("last")) >= 1.0 and str(srf.get("unit") or "").strip() == "$B")
    except (TypeError, ValueError):
        breached = False
    if breached:
        srf_now += (". That is through the $1B line already, so this item reads BREACHED and the "
                    "standing read is that discount window borrowing is elevated against its own "
                    "recent history, not that nothing has happened")
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

    sd = eng.get("supplydesk", {}) or {}
    # Forward table means forward: a settlement that already happened is not
    # "ahead", and the board's table is built from its own build date, which
    # can trail the letter's dateline by a day.
    sd_rows = [r for r in (sd.get("rows") or []) if str(r.get("date", "")) >= date]
    if sd.get("ok") and sd_rows:
        parts += ["### The supply desk", ""]
        parts += ["| settles | bills $B | coupons $B | maturing $B | net new cash $B | status |",
                  "|---|---|---|---|---|---|"]
        incomplete = 0
        for r in sd_rows[:8]:
            status = ("projected" if r.get("projected")
                      else "est. size" if r.get("amount_estimated") else "announced")
            if r.get("issuance_incomplete"):
                incomplete += 1
                status = "refunding not yet announced"
                net = "n/a"
            else:
                net = f"**{_signed(r.get('net_new_cash_b'))}**"
            parts.append(
                f"| {r.get('date')} | {_fmt(r.get('bills_gross_b'))} | {_fmt(r.get('coupons_gross_b'))} "
                f"| {_fmt(r.get('maturing_b'))} | {net} | {status} |"
            )
        # Recomputed over the forward rows only, so "ahead" is true of the
        # date it names rather than of the board's build date.
        try:
            hv = max((r for r in sd_rows if not r.get("issuance_incomplete")),
                     key=lambda r: float(r.get("net_new_cash_b") or 0))
        except (TypeError, ValueError):
            hv = {}
        tail = (f"The heaviest settlement ahead is {hv.get('date')} at ${_signed(hv.get('net_new_cash_b'))}B net. "
                if hv.get("date") else "")
        inc_txt = ""
        if incomplete:
            inc_txt = (f" {incomplete} row"
                       + ("s carry" if incomplete > 1 else " carries")
                       + " no net: the maturities on those dates are known but the refunding that funds "
                         "them is not announced yet, and the desk will not print a drain it knows Treasury "
                         "is about to offset.")
        parts += ["", tail +
                  "Net new cash is the number that drains reserves. Maturing includes SOMA rollovers, so the "
                  "private-side drain runs smaller on SOMA-heavy dates; projected rows are the desk's house "
                  "estimate and get graded when Treasury announces." + inc_txt, ""]

    rc = eng.get("reportcard", {}) or {}
    if rc.get("ok") and rc.get("letter_line"):
        # The auction grade is the market's verdict; the event study is what
        # the plumbing did about it over the following week. A window that has
        # not closed says so instead of scoring an absence as calm.
        parts += [_clean(rc["letter_line"]), ""]

    pos = []
    rv = eng.get("rvxray", {}) or {}
    if rv.get("ok") and rv.get("gross_short_b") is not None:
        # The gross short is the number that gets quoted; net is what tells you
        # whether the book is two-sided. Neither is a directional view on its
        # own, because the offsetting cash leg is not in this dataset, and
        # saying that plainly is the difference between a number and a claim.
        net_b = rv.get("net_b")
        gross = rv.get("gross_short_b")
        side = "net short" if (net_b or 0) < 0 else "net long"
        line = (f"Leveraged funds hold **${_fmt(gross)}B gross short** in Treasury futures, "
                f"**${_fmt(abs(float(net_b)) if net_b is not None else None)}B {side}** of it after longs.")
        try:
            ratio = abs(float(net_b)) / float(gross) if gross else None
        except (TypeError, ValueError, ZeroDivisionError):
            ratio = None
        if ratio is not None and ratio >= 0.7:
            line += (f" That is {ratio:.0%} of the gross standing one way, the signature of the "
                     "cash-futures basis trade rather than a two-sided book.")
        elif ratio is not None:
            line += (f" Only {ratio:.0%} of the gross stands one way, so most of the position "
                     "offsets itself and the headline short overstates the exposure.")
        line += (" Neither number is a directional view by itself: the long cash leg that would "
                 "offset a basis position is not in this dataset, and the letter will not price "
                 "a bet it cannot see.")
        pos.append(line)
    crowd = eng.get("crowding", {}) or {}
    if crowd.get("ok") and crowd.get("rows"):
        # The engine ranks by |z|, which puts unusually LIGHT positioning at the
        # top just as readily as heavy: a contract at the 3rd percentile has a
        # big negative z and is the emptiest seat on the board, not the most
        # crowded. Crowding is the high percentile; say whichever one it is.
        rows = [r for r in crowd["rows"] if r.get("pctl") is not None]
        if rows:
            top = max(rows, key=lambda r: float(r["pctl"]))
            light = min(rows, key=lambda r: float(r["pctl"]))
            pos.append(
                f"The most crowded seat is **{top.get('contract')}** at the {_ordinal(top.get('pctl'))} percentile "
                f"of its own history, leveraged net {_signed(top.get('lev_net_share_oi'), 2)} of open interest "
                f"(z {_signed(top.get('z'), 1)}). The emptiest is {light.get('contract')} at the "
                f"{_ordinal(light.get('pctl'))}, which is worth naming because an unusually light seat is a "
                "different risk from a heavy one and the ranking statistic alone does not distinguish them."
            )
        else:
            r = crowd["rows"][0]
            pos.append(
                f"The loudest positioning reading is **{r.get('contract')}**, leveraged net "
                f"{_signed(r.get('lev_net_share_oi'), 2)} of open interest (z {_signed(r.get('z'), 1)}); "
                "no percentile is published today, so the letter will not call it crowded or empty."
            )
    wh = eng.get("warehouse", {}) or {}
    if wh.get("ok"):
        # A missing share must not render "?%": the lint reads that as an
        # unformatted placeholder and refuses the letter. An absent number is
        # a clause the letter drops, not a hole it prints.
        share = wh.get("long_end_share_pct")
        share_txt = f", {_fmt(share)}% of it long end" if share is not None else ""
        pos.append(
            f"Dealer warehouse holds ${_fmt(wh.get('total_net_b'))}B, the {_ordinal(wh.get('total_pctl'))} percentile "
            f"of its history{share_txt}"
            + (f", as of {wh.get('asof')}" if wh.get("asof") else "") + "."
        )
    if pos:
        # Provenance, precisely: the futures book is CFTC COT (T+3), the dealer
        # warehouse is the NY Fed primary dealer survey (T+9). Calling both COT
        # was wrong, and a wrong lag disclosure is worse than none.
        stamp_txt = (f" Futures positioning is dated {crowd['asof']}." if crowd.get("asof")
                     else " The futures rows carry no as-of date on the board today, which is itself "
                          "a gap and is reported rather than papered over.")
        parts += ["### Positioning", "",
                  " ".join(pos)
                  + " Futures positioning is CFTC Commitments of Traders and carries its native T+3 lag; "
                    "the dealer warehouse is the NY Fed primary dealer survey, published with a longer lag "
                    "again." + stamp_txt
                  + " Every figure here is weeks old by construction and is a stock, not this morning's flow.", ""]

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
        # "Close enough to matter" is unfalsifiable without a stated bar and a
        # null to read it against. The 0.60 line is the desk's own convention,
        # not a validated threshold, and the letter says which it is.
        if close:
            judgment = (f"The top rhyme is {top.get('episode')} at {_fmt(sim, 2)}, above the desk's 0.60 "
                        "attention line. That line is an editorial convention, not a validated threshold: "
                        "the engine publishes no null distribution for this distance, so nothing here "
                        "says how often a 0.60 rhyme is a coincidence. Similarity is not destiny, and "
                        "PROOF's outcome tables, not this one, carry what actually followed.")
        else:
            judgment = (f"No match clears the desk's 0.60 attention line today (top: {_fmt(sim, 2)}). "
                        "That line is an editorial convention rather than a validated threshold, and the "
                        "desk reads this table as texture, not signal, rather than letting the echo do "
                        "the alarming.")
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


def _spread_row(snap: dict, date: str) -> dict | None:
    """Today's SOFR minus IORB in bp, stored beside the odds.

    The ledger has to be able to grade itself, and grading needs the outcome
    series, not just the forecasts. Recording the spread daily makes the file
    self-contained: the same public number the odds are about, on the same
    line-per-day basis, with no second source to go stale.

    Stamping convention, disclosed: the letter runs at 10:45 UTC, before that
    day's SOFR publication, so the row stamped date D carries the PRIOR
    business day's print. Both sides of the grade share the offset — the
    odds stamped D and the spreads that resolve them are written by the same
    run — so a 5bd window is still five published prints, shifted one
    business day later in calendar terms. Restamping now would fork the
    meaning of every row already in the file, so the convention stays and is
    stated here and in resolve_ledger instead.
    """
    hl = snap.get("headline") or {}
    try:
        sofr = float((hl.get("sofr_pct") or {}).get("value"))
        iorb = float((hl.get("iorb_pct") or {}).get("value"))
    except (TypeError, ValueError):
        return None
    return {"date": date, "kind": "spread", "spread_bp": round((sofr - iorb) * 100.0, 2)}


def resolve_ledger(rows: list[dict], horizon_bd: int = 5, spike_bp: float = 10.0) -> tuple[list[dict], int]:
    """Fill in `realized` on odds rows whose horizon has closed.

    The event is the board's own published definition, the one PROOF scores
    against: SOFR minus IORB jumping at least `spike_bp` against its trailing
    median inside the horizon. Only null-to-bool transitions are written; a
    row's `p` is never touched, because editing a published forecast would
    forge the record the ledger exists to keep.

    Dating: spread rows follow the stamping convention stated on _spread_row
    (a row stamped D carries the prior business day's print, because the
    letter runs before that day's SOFR publication). Odds and spreads share
    the offset, so the horizon window is five published prints either way;
    read the calendar dates as one business day later than the label.

    A window date with no pop yardstick (fewer than 2 prior spread rows, the
    first days of ledger history) is not evidence of quiet: rows whose window
    contains such a date stay open unless some measurable date in the window
    already shows a spike, because resolving False against a yardstick that
    never existed would score a real early spike as a non-event.
    """
    spreads = {}
    for r in rows:
        if r.get("kind") == "spread" and r.get("spread_bp") is not None:
            spreads[str(r["date"])] = float(r["spread_bp"])
    if len(spreads) < 3:
        return rows, 0
    dates = sorted(spreads)
    # Trailing median over the prior 20 observations is the yardstick, matching
    # the backtest's pop statistic closely enough to score the same events.
    pops: dict[str, float] = {}
    for i, d in enumerate(dates):
        prior = [spreads[x] for x in dates[max(0, i - 20):i]]
        if len(prior) >= 2:
            med = sorted(prior)[len(prior) // 2]
            pops[d] = spreads[d] - med
    resolved = 0
    for r in rows:
        if r.get("kind") == "spread" or r.get("realized") is not None:
            continue
        d0 = str(r.get("date"))
        window = [x for x in dates if x > d0][:horizon_bd]
        if len(window) < horizon_bd:
            continue        # horizon still open; leave it null and say so
        if any(pops.get(x, 0.0) >= spike_bp for x in window):
            r["realized"] = True        # a measured spike is a spike
        elif all(x in pops for x in window):
            r["realized"] = False       # every window day measured, none popped
        else:
            continue        # unmeasurable day in the window: stays open, not False
        resolved += 1
    return rows, resolved


def build_dispatch(snap: dict, prev_value=None, date: str | None = None,
                   state: dict | None = None, issue_no: int | None = None,
                   recent_titles: list[str] | None = None) -> dict:
    comp = snap.get("engines", {}).get("composite", {})
    if comp.get("value") is None or not comp.get("regime"):
        raise SystemExit("refusing to write a dispatch without a live composite (no board, no letter)")
    date = date or (snap.get("generated_at") or datetime.now(timezone.utc).isoformat())[:10]
    baseline = _novelty_baseline(state or {}, date)
    letter_prev = _letter_baseline(state or {}, date)
    title, summary, tag = _title_summary_tag(snap, date, prev_value, baseline,
                                             letter_prev, recent_titles)

    novel, _held = _split_flagged(snap, baseline)

    # Section-level fault isolation. Every section reads engine payloads whose
    # shape the letter does not control, and a type that drifts upstream should
    # cost one section, never the day's letter. A section that raises says so
    # in its own place, and the honesty coda counts how many did.
    broken: list[str] = []

    def section(name: str, fn, fallback: str):
        try:
            out = fn()
            return out if out else [fallback]
        except Exception as e:  # noqa: BLE001 - the letter must publish
            broken.append(name)
            return [f"The {name} section could not be built from today's board "
                    f"({type(e).__name__}). The letter publishes the gap rather than the day, "
                    "and the fault is on the record here."]

    s1 = section("reading",
                 lambda: _opening(snap, date, prev_value) + _attribution(snap, letter_prev),
                 "The composite is live but its decomposition did not render.")

    def _moved():
        out = _movers_para(snap, date, baseline)
        if not novel:
            out += _forward_pulse(snap, date)
        return out + _tripwire_para(snap) + _press_para(snap)

    s2 = section("what moved", _moved, "Nothing cleared the bar and nothing is flagged.")
    s3 = section("Tell", lambda: _tell_para(snap, date),
                 "The Tell is dark today. When both sides of it are live, this section prints "
                 "the gap between what the pipes measure and what the screens price.")
    s4 = section("reserve scarcity", lambda: _kink_para(snap),
                 "The kink engine is dark today.")
    s5 = section("official sector", lambda: _official_para(snap),
                 "The official sector engine is dark today.")
    s6 = section("dates that matter", lambda: _calendar_para(snap, letter_prev, date),
                 "No flagged windows inside the calendar's horizon. A clear calendar is a "
                 "reading too.")
    s7 = section("honesty", lambda: _honesty_coda(snap),
                 "The board's record is in PROOF.")
    if broken:
        s7 = s7 + [f"Section faults today: {', '.join(broken)}. A section that cannot be built "
                   "prints its failure in place rather than being dropped silently, because a "
                   "letter with a visible hole is honest and a letter with an invisible one is not."]

    paras: list[str] = []
    if issue_no is not None:
        paras.append(f"*Issue {issue_no} · {date} · the sections run in the same order every day, "
                     "so the delta takes a minute to extract.*")
    argument = _editorial_argument(snap)
    if argument:
        paras += ["## The argument"] + argument
    paras += ["## 1 · The reading"] + s1
    paras += ["## 2 · What moved"] + s2
    paras += ["## 3 · The Tell"] + s3
    paras += ["## 4 · Reserve scarcity"] + s4
    paras += ["## 5 · The official sector"] + s5
    paras += ["## 6 · The dates that matter"] + s6
    paras += ["## 7 · What the board is honest about"] + s7

    free_md = "\n\n".join(p for p in paras if p)
    desk_md = _desk_read(snap, date, letter_prev)

    # Repair format leaks, then confess them; everything else the lint finds is
    # the desk's own copy and stops the presses.
    title, n1 = sanitize_leaks(title)
    summary, n2 = sanitize_leaks(summary)
    free_md, n3 = sanitize_leaks(free_md)
    desk_md, n4 = sanitize_leaks(desk_md)
    leaks = n1 + n2 + n3 + n4
    if leaks:
        free_md += (
            f"\n\nOne more piece of honesty: {leaks} value"
            + ("s" if leaks > 1 else "")
            + " the letter expected to print did not arrive from the board today and shows as a "
            "question mark above. The letter publishes with the gap visible rather than holding "
            "the issue or quietly rounding the hole away."
        )

    issues = lint_letter(title, summary, free_md, desk_md)
    if issues:
        raise SystemExit("letter failed lint: " + "; ".join(issues))

    dispatch = {
        "slug": f"{date}-daily",
        "title": title,
        "date": date,
        "tag": tag,
        "summary": summary,
        "free_md": free_md,
        "desk_md": desk_md,
        "state": _updated_state(snap, baseline, letter_prev, date),
        "odds": _current_odds(snap, date) + [r for r in [_spread_row(snap, date)] if r],
    }
    dispatch["story"] = build_story(
        dispatch,
        snap,
        novel_movers=novel,
        previous_value=prev_value,
        letter_previous=letter_prev,
    )
    return dispatch


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
    if d.get("story"):
        story_path = free_dir / f"{d['slug']}.json"
        story_path.write_text(json.dumps(d["story"], indent=2, ensure_ascii=False) + "\n")
        written.append(str(story_path))
    if d["desk_md"]:
        desk_path = paid_dir / f"{d['slug']}.desk.md"
        desk_path.write_text(d["desk_md"] + "\n")
        written.append(str(desk_path))

    if d.get("state"):
        state_path = paid_dir / "state.json"
        state_path.write_text(json.dumps(d["state"], indent=2) + "\n")
        written.append(str(state_path))

    # The odds ledger is append-only and deduped by date: the same letter
    # rebuilt for the announce step must not double-count the day.
    if d.get("odds"):
        ledger = paid_dir / "odds_ledger.jsonl"
        rows: list[dict] = []
        if ledger.exists():
            for line in ledger.read_text().splitlines():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        if d["date"] not in {str(r.get("date")) for r in rows}:
            rows.extend(d["odds"])
        # Grade whatever has come due. The file is rewritten rather than
        # appended to because resolution flips nulls in place; `p` is never
        # rewritten, so the published forecast stays exactly as published.
        rows, n_resolved = resolve_ledger(rows)
        ledger.write_text("".join(json.dumps(r) + "\n" for r in rows))
        written.append(str(ledger))
        if n_resolved:
            print(f"resolved {n_resolved} ledger rows")

    entries = []
    if index.exists():
        entries = json.loads(index.read_text())
    entries = [e for e in entries if e.get("slug") != d["slug"]]
    entry = {k: d[k] for k in ("slug", "title", "date", "tag", "summary")}
    if d.get("story"):
        entry["story_url"] = f"/dispatches/{d['slug']}.json"
        entry["editorial_class"] = d["story"].get("editorial_class")
    entries.insert(0, entry)
    entries.sort(key=lambda e: e.get("date", ""), reverse=True)
    index.write_text(json.dumps(entries, indent=2) + "\n")
    written.append(str(index))
    return written


# ---------------------------------------------------------------------------
# Telegram announcement — a digest with the numbers, then the link
# ---------------------------------------------------------------------------
def _published_article(date: str) -> dict | None:
    try:
        rows = json.loads(ARTICLE_INDEX.read_text())
    except (OSError, ValueError):
        return None
    return next((row for row in rows if row.get("date") == date), None)


def build_telegram_digest(d: dict, snap: dict | None = None,
                          article: dict | None = None) -> str:
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
    if article:
        kind = str(article.get("article_type") or "analysis").replace("_", " ")
        lines += [
            "",
            f"TODAY'S EDITORIAL · {kind}",
            str(article.get("headline") or ""),
            str(article.get("dek") or ""),
            str(article.get("canonical_url") or "https://seiche.info/articles/"),
        ]
    else:
        lines += ["", "Today's editorial did not clear the published article index; nothing was reconstructed here."]
    lines += ["", f"Fixed-order letter: https://seiche.info/#dispatches/{d['slug']}"]
    return "\n".join(lines)


def announce_telegram(d: dict, snap: dict | None = None) -> None:
    """Send the digest. Fail loud: an explicit announce with missing or
    refused credentials is an error, not a silent skip."""
    import os

    if os.environ.get("SEICHE_DISPATCH_TELEGRAM", "").strip().lower() not in (
            "1", "true", "on", "yes"):
        print("telegram announce skipped: the Seiche bot letter owns the "
              "lab channel")
        return
    token = os.environ.get("SEICHE_TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("SEICHE_TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise SystemExit("announce needs SEICHE_TELEGRAM_BOT_TOKEN and SEICHE_TELEGRAM_CHAT_ID")
    body = json.dumps({
        "chat_id": chat_id,
        "text": build_telegram_digest(d, snap, _published_article(d["date"])),
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
    """Yesterday's as-published composite from the hash-chained Book history.

    The record writes the composite under "index" (see scripts/append_book_record.py);
    reading "value" here silently returned None on every run, which meant the
    letter never printed a day change. Both keys are accepted so a future
    rename cannot re-break it quietly.
    """
    try:
        hist = _get_json(history_url, timeout=30)
        if isinstance(hist, list) and hist:
            last = hist[-1]
            for key in ("index", "value", "composite"):
                if last.get(key) is not None:
                    return last[key]
    except Exception:
        return None
    return None


def _published_entry(index_path: Path, slug: str) -> dict | None:
    """The archive entry for a slug, as published. None when absent."""
    try:
        for e in json.loads(index_path.read_text()):
            if e.get("slug") == slug:
                return e
    except (OSError, ValueError):
        return None
    return None


def _recent_titles(index_path: Path, slug: str, n: int = TITLE_MEMORY_N) -> list[str]:
    """The last N published headlines, today's own entry excluded.

    Excluding the slug is what lets a same-day rebuild reproduce the letter.
    CI runs the generator again for the announce step the better part of an
    hour after the write, and a run that found its own morning headline in the
    archive would reject it and announce a different one under the same link.
    The `--force` reissue depends on the same exclusion.
    """
    try:
        entries = json.loads(index_path.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(entries, list):
        return []
    entries = [e for e in entries
               if isinstance(e, dict) and e.get("slug") != slug and e.get("title")]
    entries.sort(key=lambda e: str(e.get("date", "")), reverse=True)
    return [str(e["title"]) for e in entries[:n]]


def _issue_number(index_path: Path, slug: str) -> int | None:
    """The issue number is the letter's position in the DAILY archive.

    The index is shared with the Monday Week Ahead, so counting every entry
    would step the daily's number by one extra every week. Daily slugs all
    end in "-daily", which is the series this number belongs to.
    """
    try:
        entries = json.loads(index_path.read_text())
        return 1 + sum(1 for e in entries
                       if str(e.get("slug", "")).endswith("-daily") and e.get("slug") != slug)
    except (OSError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write today's dispatch from the live board.")
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--snapshot", default=None,
                    help="read the board from a JSON file instead of the API. The letter is a "
                         "pure function of a board snapshot, so CI can build one itself rather "
                         "than depending on the box being both up and current: a stale box "
                         "would publish a letter whose live sections all print their dark-engine "
                         "placeholder, which is honest and useless.")
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

    if args.snapshot:
        snap = json.loads(Path(args.snapshot).read_text())
        print(f"board read from {args.snapshot} (generated {snap.get('generated_at')})")
    else:
        snap = _get_json(f"{args.api}/api/overview")
    prev = _prev_published_value(args.history_url)
    d = build_dispatch(snap, prev_value=prev, date=date, state=load_state(),
                       issue_no=_issue_number(INDEX, slug),
                       recent_titles=_recent_titles(INDEX, slug))

    if args.announce_only:
        # Announce what was PUBLISHED, not what a fresh snapshot would say.
        # CI runs this step the better part of an hour after the write, and
        # the board recomputes six times a day, so rebuilding here can send a
        # digest whose headline contradicts the letter it links to.
        published = _published_entry(INDEX, slug)
        if published:
            d = {**d, "title": published.get("title", d["title"]),
                 "summary": published.get("summary", d["summary"]),
                 "date": published.get("date", d["date"]),
                 "slug": published.get("slug", d["slug"])}
        else:
            print(f"warning: {slug} is not in the index; announcing the rebuilt letter")
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
