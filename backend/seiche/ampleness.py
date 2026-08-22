"""The Ampleness Check: the one argument this audience actually has, taken
apart indicator by indicator.

Everyone who watches dollar funding is having the same argument. Are reserves
still ample, or is the system drifting toward scarcity? The Fed's own framing
rests on a specific indicator set (the level of reserves and its share of the
economy, the shape of the reserve demand curve, where overnight rates print
against the administered rates, whether anyone is paying up at the facilities,
and how much drainable buffer is left). This page assembles exactly that set
from the board's live payload: today's reading, the history percentile where
the payload carries one, an explicit AMPLE / WATCH / SCARCE token with the
threshold that produced it stated on the same line, and a plain sentence on
what the 2018-19 runoff says that level meant.

The overall reading is a COUNT of those tokens and nothing else. The board
already publishes one composite; a second opaque index assembled here would
average away the disagreement between the quantity lines and the price lines,
and that disagreement is the whole content of the argument. So the page counts
and refuses to blend, and it says so in its own text.

Honesty rules, enforced by the generator rather than by good intentions:
an indicator whose input is dark renders as "not available today" with the
reason and takes no token, thresholds are printed next to every verdict, and
a threshold that is the desk's editorial judgement rather than an established
one is labelled as such on that line.

Inputs: the board snapshot the publish job bakes at
frontend/public/data/overview.json before the page steps run (the same file
the static site uses as its offline fallback). When that file is absent the
generator falls back to fetching /api/overview over the wire, stdlib only,
the same way the skeptic pack does. Given the same snapshot the output is
byte-identical: no clock reads, no random anything.

Build step (not a server duty): `PYTHONPATH=backend python -m seiche.ampleness`
writes frontend/public/ampleness.html, which the frontend build copies into
dist/. Run it in the same publish slot as methodology and skeptic.
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
DEFAULT_OUT = REPO_ROOT / "frontend" / "public" / "ampleness.html"
DEFAULT_SNAPSHOT = REPO_ROOT / "frontend" / "public" / "data" / "overview.json"
DEFAULT_API = "https://api.seiche.info"

AMPLENESS_URL = f"{SITE}/ampleness"
SKEPTIC_URL = f"{SITE}/skeptic"

# The page version is the date of the newest changelog entry, so the version
# string moves exactly when the record of changes does.
CHANGELOG: list[tuple[str, str]] = [
    ("2026-07-28",
     "first publication; every indicator here was already on the board, "
     "scattered across engines, with no thresholds attached and no place "
     "where the ampleness question was answered line by line."),
]
AMPLENESS_VERSION = CHANGELOG[0][0]

AMPLE, WATCH, SCARCE = "AMPLE", "WATCH", "SCARCE"
TOKENS = (AMPLE, WATCH, SCARCE)

# The indicator keys, in page order. `check_status` reports one of these per
# key, so a line going dark is visible in the build log instead of quietly
# turning into a paragraph of confident prose.
INDICATOR_KEYS = (
    "reserves", "bank_assets", "kink", "rde", "sofr_iorb",
    "above_ceiling", "effr_iorb", "takeup", "rrp", "runway",
)

FAMILIES = ("quantity", "price", "curve")
FAMILY_LABEL = {
    "quantity": "how much is left",
    "price": "what it costs",
    "curve": "the shape of demand",
}

# The desk's editorial cut points, gathered here so they are one edit away and
# so the page can print them next to the verdict they produced. Anything in
# this dict is an opinion; the page says so on the line that uses it.
EDITORIAL = {
    "res_gdp_floor": 0.08,      # reserves/GDP below this reads scarce
    "kink_cushion_b": 200.0,    # dollars of headroom above the fitted kink
    "effr_watch_bp": 5.0,       # EFFR over IORB before the token goes scarce
    "ceiling_days_20": 3,       # sessions above the ceiling in 20 before WATCH
    "dw_watch_b": 10.0,         # discount window take-up before WATCH
    "dw_scarce_b": 25.0,
    "rrp_ample_b": 100.0,       # a buffer that can still absorb a bad week
    "rrp_scarce_b": 25.0,
    "sofr_fallback_bp": 10.0,   # stand-in ceiling when the live one is dark
}

# The stigma engine's published take-up scale, reused rather than reinvented so
# the facility lines are graded on the board's own classification.
FACILITY_NOTABLE_B = 1.0
FACILITY_MATERIAL_B = 25.0

# The stigma engine names its ceiling by series key; the page names it in words.
_CEILING_SOURCE = {
    "srf_rate": "SRF offering rate",
    "iorb": "IORB, standing in for the SRF offering rate",
}

_EXTRA_CSS = """
.ind { border-top:1px solid var(--edge); padding-top:18px; margin:26px 0 0; }
.ind h3 { display:flex; justify-content:space-between; align-items:baseline;
          gap:12px; flex-wrap:wrap; font-size:15px; margin:0 0 2px; }
.tok { font-family:var(--mono); font-size:10.5px; letter-spacing:.14em;
       font-weight:600; padding:3px 9px; border-radius:6px; border:1px solid;
       white-space:nowrap; }
.tok-ample { color:#7fd6a2; border-color:#2c5a41; background:#0c1a13; }
.tok-watch { color:#e8c46a; border-color:#5c4a1e; background:#1a150a; }
.tok-scarce { color:#f08a7a; border-color:#5e2c26; background:#1a0c0a; }
.tok-dark { color:var(--faint); border-color:var(--edge); background:var(--panel); }
.gap { background:var(--panel); border:1px solid var(--edge); border-radius:10px;
       padding:14px 16px; margin:12px 0; }
.gap strong { color:var(--accent-soft); }
.thr { border-left:2px solid var(--edge); padding:2px 0 2px 14px; margin:12px 0;
       color:var(--dim); font-size:13px; }
.thr b { color:var(--faint); font-weight:600; text-transform:uppercase;
         letter-spacing:.08em; font-size:11px; }
.edit { font-family:var(--mono); font-size:10.5px; letter-spacing:.1em;
        color:var(--accent); border:1px solid var(--edge); border-radius:5px;
        padding:1px 6px; margin-left:6px; white-space:nowrap; }
.lesson { background:var(--panel); border:1px solid var(--edge);
          border-radius:10px; padding:12px 16px; margin:12px 0;
          font-size:13px; color:var(--dim); }
.lesson b { color:var(--accent-soft); font-weight:600; }
.scoreline { font-family:var(--mono); font-size:13px; }
.q { color:var(--accent-bright); font-weight:500; }
"""


# ---------------------------------------------------------------------------
# formatting: anything that cannot be rendered as a number comes back empty and
# the sentence around it is dropped, because the lint refuses placeholder copy
# ---------------------------------------------------------------------------
def _f(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def _num(x, d: int = 2) -> str | None:
    v = _f(x)
    return None if v is None else f"{v:,.{d}f}"


def _signed(x, d: int = 1) -> str | None:
    v = _f(x)
    if v is None:
        return None
    return f"+{v:,.{d}f}" if v >= 0 else f"{v:,.{d}f}"


def _usd_b(x, d: int = 1, sign: bool = False) -> str | None:
    """Dollars in billions, with the sign outside the dollar sign so a negative
    distance reads as a negative number rather than as a currency oddity."""
    v = _f(x)
    if v is None:
        return None
    lead = "-" if v < 0 else ("+" if sign else "")
    return f"{lead}${abs(v):,.{d}f}B"


def _bp(x, d: int = 1) -> str | None:
    s = _signed(x, d)
    return None if s is None else f"{s} bp"


def _pct_of(x, d: int = 2) -> str | None:
    """A 0-1 ratio rendered as a percentage of the thing it is a share of."""
    v = _f(x)
    return None if v is None else f"{v * 100.0:,.{d}f}%"


def _pctl_0_100(x) -> float | None:
    """Percentiles reach this page on two scales: the turn engine publishes an
    expanding percentile on 0-1, breakwater publishes one on 0-100. Normalize
    without guessing: a value at or below 1 is a fraction."""
    v = _f(x)
    if v is None or v < 0 or v > 100:
        return None
    return v * 100.0 if v <= 1.0 else v


def _txt(s) -> str:
    """Engine-supplied prose enters the page through here: the house copy rule
    applies to it too, and the lint would otherwise block the whole page over
    one engine's em dash."""
    return _no_dashes(" ".join(str(s).split()))


def _sentence(s) -> str:
    """Engine text as a finished sentence: cleaned, escaped, closed once."""
    t = _txt(s)
    return html.escape(t if t.endswith((".", "!", "?")) else t + ".")


def _hl(headline: dict, key: str) -> tuple[float | None, str | None]:
    """A headline entry is {'value': x, 'asof': d} or absent or null."""
    blk = headline.get(key) if isinstance(headline, dict) else None
    if not isinstance(blk, dict):
        return None, None
    v = _f(blk.get("value"))
    asof = blk.get("asof")
    return v, (str(asof) if asof else None)


def _ok(block) -> bool:
    return isinstance(block, dict) and bool(block.get("ok"))


# ---------------------------------------------------------------------------
# the indicator record
# ---------------------------------------------------------------------------
def _ind(key: str, title: str, family: str, *, verdict: str | None = None,
         dark: str | None = None, readings=(), threshold: str = "",
         editorial: bool = False, percentile: str | None = None,
         lesson: str = "", notes=()) -> dict:
    """One line of the checklist. `verdict` is a token or nothing; when it is
    nothing, `dark` carries the reason and the line takes no part in the
    count. Readings may still be present on a dark line: a number we have is
    printed even when the number we would grade on is missing."""
    return {
        "key": key,
        "title": title,
        "family": family,
        "verdict": verdict,
        "dark": dark,
        "readings": list(readings),
        "threshold": threshold,
        "editorial": editorial,
        "percentile": percentile,
        "lesson": lesson,
        "notes": list(notes),
    }


def _r(label: str, value: str | None, note: str | None = None) -> tuple | None:
    """A reading row, dropped entirely when the value would not format."""
    if value is None:
        return None
    return (label, value, note)


def _rows(*items) -> list:
    return [i for i in items if i is not None]


def _caveat_note(engine: str, caveats, limit: int = 2) -> list[str]:
    """The engine's own caveats, verbatim and attributed, as one note. The
    engines are better at stating their limits than this page would be, so the
    page quotes rather than paraphrases."""
    items = [_txt(c) for c in (caveats or [])][:limit]
    if not items:
        return []
    word = "caveat" if len(items) == 1 else "caveats"
    return [f"The {engine} engine's own {word}, verbatim: " + "; ".join(items)]


# ---------------------------------------------------------------------------
# 1. reserves: the level, and the share of the economy
# ---------------------------------------------------------------------------
def _implied_gdp_b(kink: dict) -> float | None:
    """The fit works in reserves/GDP, so the GDP it used is recoverable from
    the two numbers it publishes. Printed on the page so the reader can check
    the arithmetic instead of trusting it."""
    ratio = _f(kink.get("kink_ratio"))
    kink_b = _f(kink.get("kink_reserves_b"))
    if ratio is None or kink_b is None or ratio <= 0:
        return None
    return kink_b / ratio


def _reserves_line(headline: dict, kink: dict, turn: dict) -> dict:
    lvl, lvl_asof = _hl(headline, "reserves_b")
    gdp_b = _implied_gdp_b(kink) if _ok(kink) else None
    res_for_ratio = _f(kink.get("current_reserves_b")) if _ok(kink) else None
    if res_for_ratio is None:
        res_for_ratio = lvl
    ratio = (res_for_ratio / gdp_b) if (gdp_b and res_for_ratio is not None) else None
    kink_ratio = _f(kink.get("kink_ratio")) if _ok(kink) else None

    pctl = _pctl_0_100((turn.get("features") or {}).get("res_gdp_pctl"))
    pct_txt = (f"reserves/GDP sits at percentile {_num(pctl, 0)} of its own "
               f"history (expanding percentile, from the board's turn engine)"
               if pctl is not None else None)

    readings = _rows(
        _r("reserves", _usd_b(lvl, 1), f"as of {lvl_asof}" if lvl_asof else None),
        _r("reserves as a share of GDP", _pct_of(ratio, 2),
           (f"implied nominal GDP ${_num(gdp_b / 1000.0, 1)} trillion, "
            f"recovered from the fit") if gdp_b else None),
        _r("the fitted kink, same units", _pct_of(kink_ratio, 2),
           "the share of GDP at which the board's own fit says the curve bends"),
    )

    lesson = (
        "Reserves were about $1.4 trillion when overnight repo broke in "
        "September 2019, which was roughly 6 to 7 percent of GDP at the time. "
        "The dollar figure does not carry across seven years of growth in the "
        "payments system, the balance sheet and bank size, which is why the "
        "share is the comparable measure and the fitted kink is the live one. "
        "Reserve demand has plainly shifted up since 2019: the board's own fit "
        "puts the bend well above where 2019 bit, so treat the 2019 ratio as a "
        "floor marker in the record, not as today's threshold.")

    if lvl is None and ratio is None:
        return _ind("reserves", "Reserves: the level and the share of GDP",
                    "quantity",
                    dark="The snapshot carries no reserves print and no fitted "
                         "kink to build the ratio from, so there is nothing to "
                         "grade. The board's reserves series is the weekly "
                         "H.4.1 balance, so a dark line here means the fetch "
                         "failed, not that the number is zero.",
                    readings=readings, percentile=pct_txt, lesson=lesson)

    if ratio is None or kink_ratio is None:
        return _ind("reserves", "Reserves: the level and the share of GDP",
                    "quantity",
                    dark="The level is on the board but the reserves/GDP ratio "
                         "is not: the hinge fit that carries the GDP "
                         "denominator is dark on this snapshot, and a dollar "
                         "level on its own has no defensible threshold across "
                         "a decade of nominal growth. The level is printed "
                         "above and left ungraded.",
                    readings=readings, percentile=pct_txt, lesson=lesson)

    floor = EDITORIAL["res_gdp_floor"]
    if ratio >= kink_ratio:
        verdict = AMPLE
    elif ratio < floor:
        verdict = SCARCE
    else:
        verdict = WATCH

    threshold = (
        f"AMPLE at or above the board's fitted kink ratio "
        f"({_pct_of(kink_ratio, 2)} of GDP today), SCARCE below "
        f"{_pct_of(floor, 0)} of GDP, WATCH in between. The kink ratio is "
        f"fitted, not chosen. The {_pct_of(floor, 0)} floor is the desk's "
        f"judgement: reserves broke at roughly 6 to 7 percent of GDP in 2019 "
        f"and the demand curve has shifted up since, so the floor is set above "
        f"the 2019 mark rather than at it.")

    return _ind("reserves", "Reserves: the level and the share of GDP",
                "quantity", verdict=verdict, readings=readings,
                threshold=threshold, editorial=True, percentile=pct_txt,
                lesson=lesson)


# ---------------------------------------------------------------------------
# 2. reserves as a share of bank assets
# ---------------------------------------------------------------------------
def _bank_assets_b(snap: dict) -> tuple[float | None, str | None]:
    """The board does not carry an H.8 bank assets aggregate today. The lookup
    is written anyway so the line lights up the day the series is added
    instead of needing this page edited to notice."""
    headline = snap.get("headline") or {}
    v, asof = _hl(headline, "bank_assets_b")
    if v is not None:
        return v, asof
    engines = snap.get("engines") or {}
    for name in sorted(engines):
        blk = engines.get(name)
        if isinstance(blk, dict):
            v = _f(blk.get("bank_assets_b"))
            if v is not None:
                return v, (str(blk.get("asof")) if blk.get("asof") else None)
    return None, None


def _bank_assets_line(snap: dict, headline: dict) -> dict:
    assets, asof = _bank_assets_b(snap)
    lvl, lvl_asof = _hl(headline, "reserves_b")
    lesson = (
        "The Fed's ample-reserves discussion uses reserves against bank "
        "assets alongside reserves against GDP, because the demand for "
        "reserves is a demand by banks and it scales with their balance "
        "sheets rather than with the economy. It is the measure most likely "
        "to explain why the 2019 ratio does not transfer: banks are much "
        "larger now than they were then.")

    if assets is None or lvl is None:
        return _ind(
            "bank_assets", "Reserves as a share of bank assets", "quantity",
            dark="The snapshot carries no commercial bank total assets series "
                 "(the H.8 aggregate), so this ratio cannot be computed here. "
                 "It is not estimated and not carried over from memory: "
                 "printing a remembered number where a measured one belongs is "
                 "exactly the failure this page exists to avoid. When the "
                 "series joins the board this line grades itself.",
            lesson=lesson)

    ratio = lvl / assets if assets else None
    readings = _rows(
        _r("reserves", _usd_b(lvl, 1), f"as of {lvl_asof}" if lvl_asof else None),
        _r("commercial bank assets", _usd_b(assets, 1),
           f"as of {asof}" if asof else None),
        _r("reserves as a share of bank assets", _pct_of(ratio, 2), None),
    )
    return _ind(
        "bank_assets", "Reserves as a share of bank assets", "quantity",
        dark="The inputs are present but this line has no published threshold "
             "the desk is willing to stand behind yet, so it prints the ratio "
             "and takes no token.",
        readings=readings, lesson=lesson)


# ---------------------------------------------------------------------------
# 3. the fitted kink and the distance to it
# ---------------------------------------------------------------------------
def _kink_line(kink: dict) -> dict:
    lesson = (
        "The reserve demand curve is flat while reserves are abundant and "
        "bends steeply near scarcity, which is why the crossing in 2018-19 was "
        "so quiet. Nothing looked wrong on the price side for months, and then "
        "one morning in September 2019 the spread went from single basis "
        "points to hundreds. Distance to the bend is the early part of that "
        "story: it says how much of a shock the system can absorb before the "
        "price starts responding to the quantity at all.")

    if not _ok(kink):
        reason = _txt(kink.get("reason")) if isinstance(kink, dict) and kink.get("reason") else ""
        why = f" The engine's own reason: {reason}." if reason else ""
        return _ind("kink", "Distance to the fitted reserve demand kink",
                    "quantity",
                    dark="The hinge fit is dark on this snapshot, so there is "
                         "no fitted kink and no distance to it." + why,
                    lesson=lesson)

    kink_b = _f(kink.get("kink_reserves_b"))
    res_b = _f(kink.get("current_reserves_b"))
    dist = _f(kink.get("distance_b"))
    r2 = _f(kink.get("r2"))
    cons = _f(kink.get("consistency"))
    slope = _f(kink.get("slope_bp_per_ratio"))
    kink_ratio = _f(kink.get("kink_ratio"))
    gdp_b = _implied_gdp_b(kink)
    drain = _f(kink.get("drain_per_bday_b"))
    days = _f(kink.get("days_to_kink"))
    asof = kink.get("asof")

    # What the hinge is actually worth at today's reserve level, in the fit's
    # own units: the lift the sloped region adds over the flat region.
    lift = None
    if slope is not None and kink_ratio is not None and gdp_b and res_b is not None:
        lift = slope * max(0.0, kink_ratio - res_b / gdp_b)

    readings = _rows(
        _r("fitted kink, in today's dollars", _usd_b(kink_b, 1),
           f"as of {asof}" if asof else None),
        _r("reserves now", _usd_b(res_b, 1), None),
        _r("distance to the kink", _usd_b(dist, 1),
           "negative means reserves are already inside the sloped region"),
        _r("what the slope is worth here", _bp(lift, 1),
           "the spread the fit attributes to being this far inside the sloped "
           "region, against the flat region"),
        _r("trailing drift", _usd_b(drain, 2, sign=True),
           "per business day; a positive number is reserves building, not draining"),
        _r("days to the kink at that drift",
           _num(days, 0) if days is not None else "not published today",
           "the engine publishes this only while reserves are above the kink "
           "and falling toward it"),
        _r("fit quality, r2", _num(r2, 3),
           "the board discounts this engine below 0.35"),
        _r("model against market", _num(cons, 2),
           f"fit says {_bp(kink.get('predicted_spread_now_bp'), 1)}, "
           f"tape says {_bp(kink.get('observed_spread_now_bp'), 1)}"
           if _f(kink.get("predicted_spread_now_bp")) is not None
           and _f(kink.get("observed_spread_now_bp")) is not None else None),
    )

    if dist is None:
        return _ind("kink", "Distance to the fitted reserve demand kink",
                    "quantity",
                    dark="The fit ran but published no distance to the kink, "
                         "so there is nothing to grade.",
                    readings=readings, lesson=lesson)

    if r2 is not None and r2 < 0.35:
        return _ind(
            "kink", "Distance to the fitted reserve demand kink", "quantity",
            dark=f"The hinge fit is below the board's own confidence gate "
                 f"(r2 {_num(r2, 3)} against a gate of 0.35), so the distance "
                 f"is printed and left ungraded. A weak fit is allowed to say "
                 f"nothing; it is not allowed to say something quietly.",
            readings=readings, lesson=lesson)

    cushion = EDITORIAL["kink_cushion_b"]
    if dist > cushion:
        verdict = AMPLE
    elif dist > 0:
        verdict = WATCH
    else:
        verdict = SCARCE

    threshold = (
        f"AMPLE more than {_usd_b(cushion, 0)} above the fitted kink, WATCH "
        f"inside that cushion, SCARCE at or below the kink. The kink location "
        f"is fitted from the data. The {_usd_b(cushion, 0)} cushion is the "
        f"desk's judgement, about 6 percent of the current reserve balance: "
        f"the fitted breakpoint is a point estimate on a hinge regression, and "
        f"a band around it is more honest than treating it as a wire.")

    return _ind("kink", "Distance to the fitted reserve demand kink", "quantity",
                verdict=verdict, readings=readings, threshold=threshold,
                editorial=True,
                percentile="the payload carries no history percentile for the "
                           "distance itself; the fit quality and the model "
                           "against market check above are the gates that stand "
                           "in for one",
                lesson=lesson)


# ---------------------------------------------------------------------------
# 4. the NY Fed's own reserve demand elasticity print
# ---------------------------------------------------------------------------
def _rde_line(rde: dict) -> dict:
    lesson = (
        "The New York Fed built this measure so the ampleness argument would "
        "not have to be settled by anecdote, and its own reading of the "
        "2018-19 period is that the demand curve had already left the flat "
        "region before the September 2019 break. That is the useful property: "
        "the elasticity moved while the price still looked calm. Their "
        "definition of ample is a flat curve, so zero inside the band is the "
        "ample reading and a band that excludes zero is the curve saying "
        "reserve changes now move rates.")

    if not _ok(rde):
        return _ind("rde", "The NY Fed's reserve demand elasticity print",
                    "curve",
                    dark="The RDE comparison is dark on this snapshot, so the "
                         "official print is not on the board today. The measure "
                         "publishes monthly and the board mirrors it rather "
                         "than reconstructing it, so a dark line here means the "
                         "mirror is stale, not that the curve moved.",
                    lesson=lesson)

    nyfed = _f(rde.get("nyfed_bp_per_1pct"))
    b68 = rde.get("nyfed_band_68") or []
    b95 = rde.get("nyfed_band_95") or []
    lo68 = _f(b68[0]) if len(b68) > 0 else None
    hi68 = _f(b68[1]) if len(b68) > 1 else None
    lo95 = _f(b95[0]) if len(b95) > 0 else None
    hi95 = _f(b95[1]) if len(b95) > 1 else None
    ours = _f(rde.get("ours_bp_per_1pct"))
    summary = rde.get("scorecard_summary") or {}

    readings = _rows(
        _r("NY Fed RDE, bp per 1% change in reserves", _num(nyfed, 3),
           f"as of {rde.get('nyfed_asof')}" if rde.get("nyfed_asof") else None),
        _r("their 68% band", f"{_num(lo68, 3)} to {_num(hi68, 3)}"
           if lo68 is not None and hi68 is not None else None, None),
        _r("their 95% band", f"{_num(lo95, 3)} to {_num(hi95, 3)}"
           if lo95 is not None and hi95 is not None else None, None),
        _r("the board's own nowcast of the same quantity", _num(ours, 3),
           f"divergence {_num(rde.get('divergence_bp'), 3)}, "
           f"{'inside' if rde.get('within_68_band') else 'outside'} their 68% band"
           if _f(rde.get("divergence_bp")) is not None else None),
        _r("nowcast lead over their release", _num(rde.get("nowcast_lead_days"), 0),
           "days"),
        _r("the nowcast's own scorecard",
           (f"{_num(summary.get('within_band'), 0)} of "
            f"{_num(summary.get('n'), 0)} refits inside their band")
           if _f(summary.get("n")) else None,
           (f"direction agreed {_num(summary.get('direction_agree'), 0)} times, "
            f"mean absolute difference {_num(summary.get('mean_abs_diff_bp'), 3)}")
           if _f(summary.get("mean_abs_diff_bp")) is not None else None),
    )

    if nyfed is None or lo68 is None or hi68 is None:
        return _ind("rde", "The NY Fed's reserve demand elasticity print",
                    "curve",
                    dark="The official print is on the board without its "
                         "published bands, and the whole test here is whether "
                         "zero sits inside a band, so the line takes no token.",
                    readings=readings, lesson=lesson)

    notes = []
    if lo68 <= 0.0 <= hi68:
        verdict = AMPLE
    elif lo68 > 0.0:
        verdict = AMPLE
        notes.append(
            "The band sits entirely above zero, which is the wrong sign for a "
            "scarcity read: a positive elasticity is not the steep region of "
            "the demand curve and is not graded as one.")
    elif hi95 is not None and hi95 < 0.0:
        verdict = SCARCE
    else:
        verdict = WATCH

    threshold = (
        "AMPLE when zero sits inside their 68% band (the curve is flat, which "
        "is their own definition of ample), WATCH when zero is outside the 68% "
        "band but inside the 95%, SCARCE when zero is outside both. The cut "
        "points are the bands the New York Fed publishes with the measure, not "
        "numbers chosen here.")

    return _ind("rde", "The NY Fed's reserve demand elasticity print", "curve",
                verdict=verdict, readings=readings, threshold=threshold,
                editorial=False,
                percentile="the official series publishes a median and two "
                           "bands rather than a history percentile, and the "
                           "bands are the better test",
                lesson=lesson, notes=notes)


# ---------------------------------------------------------------------------
# 5. SOFR minus IORB
# ---------------------------------------------------------------------------
def _ceiling_gap_bp(stigma: dict, iorb: float | None) -> float | None:
    """The administered ceiling above IORB, in bp, when the payload carries the
    live offering rate. The SRF rate is set by the Fed, so when it is on the
    board the scarce cut is not the desk's opinion."""
    if not isinstance(stigma, dict) or iorb is None:
        return None
    ceiling = _f((stigma.get("ceiling") or {}).get("latest_pct"))
    if ceiling is None:
        return None
    return (ceiling - iorb) * 100.0


def _sofr_iorb_line(headline: dict, tails: dict, breakwater: dict,
                    stigma: dict) -> dict:
    lesson = (
        "Through 2018 and into 2019 SOFR moved from printing below IORB to "
        "printing above it, first only on settlement dates, then routinely, "
        "and finally by hundreds of basis points on 17 September 2019. A "
        "negative print is the ample-regime signature: cash lenders are content "
        "to leave money at the Fed rather than pay up in the market, which is "
        "only true when reserves are plentiful enough that nobody is bidding "
        "for them.")

    sofr, sofr_asof = _hl(headline, "sofr_pct")
    iorb, iorb_asof = _hl(headline, "iorb_pct")

    spread = _f((tails.get("spread") or {}).get("sofr_iorb_bp")) if _ok(tails) else None
    source = "the tails engine's SOFR minus IORB series"
    if spread is None and sofr is not None and iorb is not None:
        spread = round((sofr - iorb) * 100.0, 1)
        source = "the headline SOFR and IORB prints, differenced here"

    z = _f((tails.get("spread") or {}).get("sofr_iorb_z")) if _ok(tails) else None
    pctl = _pctl_0_100((breakwater.get("current") or {}).get("spread_pctl")) \
        if _ok(breakwater) else None
    pct_txt = (f"percentile {_num(pctl, 0)} of its own expanding history, from "
               f"the breakwater engine's replay of the same spread"
               if pctl is not None else None)

    readings = _rows(
        _r("SOFR minus IORB", _bp(spread, 1), source),
        _r("SOFR", f"{_num(sofr, 2)}%" if sofr is not None else None,
           f"as of {sofr_asof}" if sofr_asof else None),
        _r("IORB", f"{_num(iorb, 2)}%" if iorb is not None else None,
           f"as of {iorb_asof}" if iorb_asof else None),
        _r("250 day z score of the spread", _signed(z, 2), None),
    )

    if spread is None:
        return _ind("sofr_iorb", "SOFR minus IORB", "price",
                    dark="Neither the tails engine's spread nor a SOFR and "
                         "IORB pair is on this snapshot, so the spread cannot "
                         "be formed.",
                    readings=readings, percentile=pct_txt, lesson=lesson)

    gap = _ceiling_gap_bp(stigma, iorb)
    editorial = gap is None
    if gap is None:
        gap = EDITORIAL["sofr_fallback_bp"]
        ceiling_txt = (
            f"The live SRF offering rate is not on this snapshot, so the scarce "
            f"cut falls back to IORB plus {_num(gap, 0)} bp, which is the desk's "
            f"stand-in for the missing administered ceiling.")
    else:
        ceiling_txt = (
            f"The scarce cut is the SRF offering rate itself, currently IORB "
            f"plus {_num(gap, 0)} bp, so it is the Fed's administered ceiling "
            f"rather than a number chosen here.")

    if spread < 0:
        verdict = AMPLE
    elif spread <= gap:
        verdict = WATCH
    else:
        verdict = SCARCE

    threshold = (
        f"AMPLE below zero (SOFR under IORB), WATCH from zero to "
        f"{_num(gap, 0)} bp above it, SCARCE above that. {ceiling_txt}")

    return _ind("sofr_iorb", "SOFR minus IORB", "price", verdict=verdict,
                readings=readings, threshold=threshold, editorial=editorial,
                percentile=pct_txt, lesson=lesson)


# ---------------------------------------------------------------------------
# 6. how much repo volume prints above the ceiling
# ---------------------------------------------------------------------------
def _above_ceiling_line(stigma: dict, headline: dict) -> dict:
    lesson = (
        "In the run-up to September 2019 the upper percentiles went first. The "
        "99th percentile of repo cleared the administered rate for months while "
        "the median still looked perfectly calm, and only then did the whole "
        "distribution follow. Anyone watching the average alone would have seen "
        "nothing until the morning it was too late, which is the case for "
        "reading the distribution rather than the print.")

    if not _ok(stigma):
        return _ind(
            "above_ceiling", "The share of repo volume printing above the ceiling",
            "price",
            dark="The stigma engine is dark on this snapshot, so the repo "
                 "percentile frame (the P75 and P99 prints against the "
                 "administered ceiling) is not available. The headline SOFR "
                 "print is a volume weighted median and cannot answer a "
                 "question about the tail of the distribution, so nothing is "
                 "substituted for it.",
            lesson=lesson)

    band = stigma.get("bp_days_above_ceiling") or {}
    takeup = stigma.get("takeup") or {}
    ceiling = stigma.get("ceiling") or {}
    days = _f(band.get("days_above_20d"))
    p99_sum = _f(band.get("p99_sum20_bp_days"))
    p75_sum = _f(band.get("p75_sum20_bp_days"))
    p99_last = _f(band.get("p99_last_bp"))
    p75_last = _f(band.get("p75_last_bp"))
    iorb, _ = _hl(headline, "iorb_pct")
    gap = _ceiling_gap_bp(stigma, iorb)

    readings = _rows(
        _r("sessions in the last 20 with the 99th percentile above the ceiling",
           _num(days, 0),
           "each one proves at least 1% of volume paid above the ceiling"),
        _r("99th percentile leak, 20 day sum", _num(p99_sum, 1), "bp days"),
        _r("75th percentile leak, 20 day sum", _num(p75_sum, 1),
           "bp days; anything above zero proves at least 25% of volume paid up"),
        _r("99th percentile above the ceiling, latest session", _bp(p99_last, 1), None),
        _r("75th percentile above the ceiling, latest session", _bp(p75_last, 1), None),
        _r("the ceiling being measured against",
           f"{_num(ceiling.get('latest_pct'), 2)}%"
           if _f(ceiling.get("latest_pct")) is not None else None,
           (f"the {_CEILING_SOURCE.get(str(ceiling.get('source')), _txt(ceiling.get('source')))}, "
            f"which is IORB plus {_num(gap, 0)} bp today, so this is a stricter "
            f"test than printing above IORB") if gap is not None else None),
        _r("stigma score", _num(stigma.get("stigma_score"), 1), "0 to 100"),
    )
    notes = _caveat_note("stigma", stigma.get("caveats"))

    if days is None and p75_sum is None and p99_sum is None:
        return _ind(
            "above_ceiling", "The share of repo volume printing above the ceiling",
            "price",
            dark="The stigma engine ran but published no percentile frame on "
                 "this snapshot, so there is nothing to grade.",
            readings=readings, lesson=lesson, notes=notes)

    watch_days = EDITORIAL["ceiling_days_20"]
    if (p75_sum or 0.0) > 0 or (p75_last or 0.0) > 0:
        verdict = SCARCE
    elif days is None:
        return _ind(
            "above_ceiling", "The share of repo volume printing above the ceiling",
            "price",
            dark="The frame is on the board without its count of sessions above "
                 "the ceiling, and that count is the only thing separating an "
                 "isolated print from a pattern, so the line takes no token.",
            readings=readings, lesson=lesson, notes=notes)
    elif days >= watch_days:
        verdict = WATCH
    else:
        verdict = AMPLE

    threshold = (
        f"SCARCE on any 75th percentile breach at all, because that print "
        f"proves at least a quarter of repo volume paid above the Fed's own "
        f"ceiling. WATCH at {watch_days} or more sessions in the last 20 with "
        f"a 99th percentile breach. AMPLE below that. The bound logic is the "
        f"feed's, not the desk's: only the 1st, 25th, 75th and 99th "
        f"percentiles are published, so the share above the ceiling is bounded "
        f"rather than measured, and this line says bounded. The "
        f"{watch_days} session cut is the desk's judgement about when isolated "
        f"prints stop being isolated.")

    return _ind("above_ceiling",
                "The share of repo volume printing above the ceiling", "price",
                verdict=verdict, readings=readings, threshold=threshold,
                editorial=True,
                percentile="this line IS the percentile frame: the readings "
                           "above are the 75th and 99th percentiles of the repo "
                           "distribution against the administered ceiling",
                lesson=lesson, notes=notes)


# ---------------------------------------------------------------------------
# 7. EFFR minus IORB
# ---------------------------------------------------------------------------
def _effr_iorb_line(headline: dict) -> dict:
    lesson = (
        "EFFR climbed toward and then through IOER during the 2018-19 runoff, "
        "and the Fed answered with a sequence of 5 bp technical adjustments to "
        "the administered rate rather than a policy move. That sequence is the "
        "cleanest signal the last cycle gave: when the effective rate presses "
        "the rate the Fed pays on reserves, the plumbing is saying reserves are "
        "getting tight, and it says it in the one market the Fed targets.")

    effr, effr_asof = _hl(headline, "effr_pct")
    iorb, iorb_asof = _hl(headline, "iorb_pct")
    if effr is None or iorb is None:
        return _ind("effr_iorb", "EFFR minus IORB", "price",
                    dark="The snapshot is missing the EFFR print, the IORB "
                         "print, or both, so the spread cannot be formed. "
                         "Neither leg is inferred from the other.",
                    lesson=lesson)

    spread = round((effr - iorb) * 100.0, 1)
    readings = _rows(
        _r("EFFR minus IORB", _bp(spread, 1),
           "differenced from the two headline prints"),
        _r("EFFR", f"{_num(effr, 2)}%", f"as of {effr_asof}" if effr_asof else None),
        _r("IORB", f"{_num(iorb, 2)}%", f"as of {iorb_asof}" if iorb_asof else None),
    )

    watch = EDITORIAL["effr_watch_bp"]
    if spread < 0:
        verdict = AMPLE
    elif spread <= watch:
        verdict = WATCH
    else:
        verdict = SCARCE

    threshold = (
        f"AMPLE below zero (EFFR under IORB), WATCH from zero to "
        f"{_num(watch, 0)} bp above it, SCARCE above that. Zero is not the "
        f"desk's line: the Fed's own 2018-19 practice was to make a technical "
        f"adjustment when the effective rate pressed the administered rate. "
        f"The {_num(watch, 0)} bp width is the desk's judgement, taken from the "
        f"size of those adjustments, which were 5 bp each.")

    return _ind("effr_iorb", "EFFR minus IORB", "price", verdict=verdict,
                readings=readings, threshold=threshold, editorial=True,
                percentile="the payload carries no history percentile for this "
                           "spread; the sign against IORB is the test that "
                           "mattered in 2018-19 and it needs no percentile",
                lesson=lesson)


# ---------------------------------------------------------------------------
# 8. SRF and discount window take-up
# ---------------------------------------------------------------------------
def _facility_token(peak_b: float) -> str:
    """The stigma engine's own published scale: de minimis under $1B, notable
    to $25B, material at or above. Reused rather than reinvented."""
    if peak_b < FACILITY_NOTABLE_B:
        return AMPLE
    if peak_b < FACILITY_MATERIAL_B:
        return WATCH
    return SCARCE


def _takeup_line(stigma: dict, headline: dict) -> dict:
    lesson = (
        "There was no standing repo facility in 2019: it was created in July "
        "2021 precisely because of what happened. The discount window did "
        "exist, and it was barely touched through the squeeze, which is the "
        "stigma problem in a single fact. Take-up is therefore a confession "
        "indicator and the size matters less than the willingness to be seen. "
        "One thing has changed since: from 2023 the Fed has pushed banks to "
        "pre-position collateral and to test the window, so small steady use "
        "now reads as hygiene rather than distress, and this line is graded "
        "with that in mind.")

    srf_peak = None
    srf_latest = None
    srf_asof = None
    scale_txt = None
    classification = None
    if _ok(stigma):
        tk = stigma.get("takeup") or {}
        srf_latest = _f(tk.get("latest_b"))
        srf_peak = _f(tk.get("max20_b"))
        srf_asof = tk.get("asof")
        classification = tk.get("classification")
        scale = _f(tk.get("facility_scale_b"))
        share = _f(tk.get("share_of_facility_pct"))
        if scale is not None and share is not None:
            scale_txt = (f"{_num(share, 3)}% of a {_usd_b(scale, 0)} facility")
    if srf_latest is None:
        srf_latest, srf_asof = _hl(headline, "srf_accepted_b")
    dw, dw_asof = _hl(headline, "dw_b")

    readings = _rows(
        _r("SRF take-up, latest", _usd_b(srf_latest, 2),
           f"as of {srf_asof}" if srf_asof else None),
        _r("SRF take-up, 20 session peak", _usd_b(srf_peak, 2), scale_txt),
        _r("the engine's classification of that take-up",
           _txt(classification).replace("_", " ") if classification else None,
           "de minimis under $1B, notable to $25B, material at or above"),
        _r("discount window primary credit", _usd_b(dw, 2),
           f"weekly H.4.1 level as of {dw_asof}" if dw_asof else None),
    )

    srf_for_grade = srf_peak if srf_peak is not None else srf_latest
    parts = []
    if srf_for_grade is not None:
        parts.append(("SRF", _facility_token(srf_for_grade)))
    if dw is not None:
        if dw < EDITORIAL["dw_watch_b"]:
            parts.append(("the discount window", AMPLE))
        elif dw < EDITORIAL["dw_scarce_b"]:
            parts.append(("the discount window", WATCH))
        else:
            parts.append(("the discount window", SCARCE))

    if not parts:
        return _ind("takeup", "SRF and discount window take-up", "price",
                    dark="Neither the SRF take-up block nor the discount window "
                         "level is on this snapshot, so there is no confession "
                         "to read either way.",
                    readings=readings, lesson=lesson)

    order = {AMPLE: 0, WATCH: 1, SCARCE: 2}
    verdict = max((t for _, t in parts), key=lambda t: order[t])
    missing = ""
    if srf_for_grade is None:
        missing = (" The SRF take-up block is dark on this snapshot, so the "
                   "token comes from the discount window alone.")
    elif dw is None:
        missing = (" The discount window level is dark on this snapshot, so the "
                   "token comes from SRF take-up alone.")

    threshold = (
        f"One token for the pair, and it is the worse of the two. SRF take-up "
        f"is graded on the stigma engine's own published scale: AMPLE below "
        f"{_usd_b(FACILITY_NOTABLE_B, 0)} (de minimis), WATCH to "
        f"{_usd_b(FACILITY_MATERIAL_B, 0)}, SCARCE at or above. The discount "
        f"window is graded AMPLE below {_usd_b(EDITORIAL['dw_watch_b'], 0)}, "
        f"WATCH to {_usd_b(EDITORIAL['dw_scarce_b'], 0)}, SCARCE above, and "
        f"those cuts are the desk's judgement: they are set wide deliberately "
        f"because the post-2023 push to pre-position and test the window puts a "
        f"few billion of routine borrowing on the tape that has nothing to do "
        f"with stress.{missing}")

    notes = ["The pair grades as: "
             + ", ".join(f"{name} {tok}" for name, tok in parts)]

    return _ind("takeup", "SRF and discount window take-up", "price",
                verdict=verdict, readings=readings, threshold=threshold,
                editorial=True,
                percentile="the payload carries no history percentile for "
                           "take-up; the engine's de minimis, notable and "
                           "material bands are the frame it publishes instead",
                lesson=lesson, notes=notes)


# ---------------------------------------------------------------------------
# 9. the ON RRP buffer
# ---------------------------------------------------------------------------
def _rrp_line(headline: dict, runway: dict) -> dict:
    lesson = (
        "There was no meaningful ON RRP balance in 2018-19, so every dollar "
        "drained from the Fed's liabilities came straight out of reserves, and "
        "that is the configuration that produced September 2019. Between 2022 "
        "and 2024 the facility was a shock absorber measured in trillions, and "
        "balance sheet runoff could proceed for two years without touching "
        "reserves at all. When that absorber is empty the runoff arithmetic "
        "goes back to what it was in 2019.")

    rrp, asof = _hl(headline, "rrp_b")
    if rrp is None and _ok(runway):
        rrp = _f((runway.get("assumptions") or {}).get("rrp_now_b"))
        asof = runway.get("asof")
    if rrp is None:
        return _ind("rrp", "The ON RRP buffer left to drain", "quantity",
                    dark="The snapshot carries no ON RRP balance, so the size "
                         "of the remaining buffer is unknown rather than zero. "
                         "The distinction matters here more than anywhere else "
                         "on the page: an empty buffer and a missing print look "
                         "identical if you let them.",
                    lesson=lesson)

    readings = _rows(
        _r("ON RRP balance", _usd_b(rrp, 2), f"as of {asof}" if asof else None),
        _r("capacity to absorb the next drain",
           "effectively none" if rrp < EDITORIAL["rrp_scarce_b"]
           else "partial" if rrp <= EDITORIAL["rrp_ample_b"] else "intact",
           "the next dollar drained comes out of reserves once this is empty"),
    )

    if rrp > EDITORIAL["rrp_ample_b"]:
        verdict = AMPLE
    elif rrp >= EDITORIAL["rrp_scarce_b"]:
        verdict = WATCH
    else:
        verdict = SCARCE

    threshold = (
        f"AMPLE above {_usd_b(EDITORIAL['rrp_ample_b'], 0)}, WATCH from "
        f"{_usd_b(EDITORIAL['rrp_scarce_b'], 0)} to there, SCARCE below. Both "
        f"cuts are the desk's judgement. The reasoning is stated so it can be "
        f"argued with: the facility ran above $2 trillion through 2022 and "
        f"2023, and a balance under {_usd_b(EDITORIAL['rrp_scarce_b'], 0)} "
        f"cannot absorb one heavy settlement week, which makes the next drain "
        f"a reserve drain by arithmetic rather than by judgement.")

    return _ind("rrp", "The ON RRP buffer left to drain", "quantity",
                verdict=verdict, readings=readings, threshold=threshold,
                editorial=True,
                percentile="the payload carries no history percentile for the "
                           "RRP balance; near the floor a percentile would be "
                           "the least informative way to say it",
                lesson=lesson)


# ---------------------------------------------------------------------------
# 10. the runway's kink-crossing dates
# ---------------------------------------------------------------------------
_SCENARIO_LABEL = (("base", "base case"),
                   ("fast_drain", "fast drain"),
                   ("slow", "slow drain"))


def _runway_line(runway: dict) -> dict:
    lesson = (
        "The timing lesson from 2018-19 is that the crossing is not an event "
        "anyone feels. Reserves fell through the level that mattered months "
        "before the September break, and the calendar supplied the trigger when "
        "a tax date and a settlement date landed together. A projected crossing "
        "date is not a forecast of stress. It is the date after which the "
        "calendar starts to matter.")

    if not _ok(runway):
        return _ind("runway", "The runway's kink crossing dates", "quantity",
                    dark="The runway projection is dark on this snapshot, so "
                         "there are no crossing dates to read. The projection "
                         "is arithmetic on stated assumptions rather than a "
                         "forecast, so it is not reconstructed here by hand.",
                    lesson=lesson)

    scenarios = runway.get("scenarios") or {}
    assumptions = runway.get("assumptions") or {}
    rows = []
    crossed: dict[str, bool] = {}
    for key, label in _SCENARIO_LABEL:
        sc = scenarios.get(key)
        if not isinstance(sc, dict):
            continue
        date = sc.get("crossing_date")
        crossed[key] = bool(date)
        rows.append(_r(label,
                       str(date) if date else "no crossing inside the horizon",
                       _txt(sc.get("verdict")) if sc.get("verdict") else None))
    readings = _rows(
        *rows,
        _r("horizon", _num(assumptions.get("horizon_weeks"), 0), "weeks"),
        _r("trailing drift used", _usd_b(assumptions.get("trailing_drift_b_per_week"), 1),
           "per week"),
        _r("the kink it is projecting against",
           _usd_b(assumptions.get("kink_reserves_b"), 1), None),
    )

    if not crossed:
        return _ind("runway", "The runway's kink crossing dates", "quantity",
                    dark="The runway engine published no scenarios on this "
                         "snapshot, so there is nothing to grade.",
                    readings=readings, lesson=lesson)

    if crossed.get("base"):
        verdict = SCARCE
    elif any(crossed.values()):
        verdict = WATCH
    else:
        verdict = AMPLE

    threshold = (
        "SCARCE when the base case crosses the fitted kink inside the horizon "
        "(or starts below it), WATCH when only a stressed scenario crosses, "
        "AMPLE when none does. That ladder is the desk's judgement about how "
        "to read a scenario set, and the underlying projection is arithmetic on "
        "the engine's stated assumptions rather than a forecast of policy: the "
        "Fed can change the drift on any Wednesday and this line would not know "
        "until it did.")

    return _ind("runway", "The runway's kink crossing dates", "quantity",
                verdict=verdict, readings=readings, threshold=threshold,
                editorial=True,
                percentile="a projected date has no history percentile; the "
                           "three scenarios are the uncertainty statement",
                lesson=lesson,
                notes=_caveat_note("runway", runway.get("caveats")))


# ---------------------------------------------------------------------------
# the checklist, the count
# ---------------------------------------------------------------------------
def indicators(snap: dict) -> list[dict]:
    """The whole checklist as data. Pure function of the snapshot: no clock, no
    network, no filesystem."""
    snap = snap if isinstance(snap, dict) else {}
    headline = snap.get("headline") or {}
    engines = snap.get("engines") or {}
    deep = snap.get("deep") or {}
    kink = engines.get("kink") or {}
    turn = deep.get("turn") or {}
    stigma = engines.get("stigma") or {}
    tails = engines.get("tails") or {}
    breakwater = engines.get("breakwater") or {}

    return [
        _reserves_line(headline, kink, turn),
        _bank_assets_line(snap, headline),
        _kink_line(kink),
        _rde_line(engines.get("rdenowcast") or {}),
        _sofr_iorb_line(headline, tails, breakwater, stigma),
        _above_ceiling_line(stigma, headline),
        _effr_iorb_line(headline),
        _takeup_line(stigma, headline),
        _rrp_line(headline, engines.get("runway") or {}),
        _runway_line(engines.get("runway") or {}),
    ]


def tally(inds: list[dict]) -> dict:
    """The overall reading: a count of tokens, by token and by family. Not a
    blend, not a weighted average, not a new index."""
    counts = {t: sum(1 for i in inds if i["verdict"] == t) for t in TOKENS}
    graded = sum(counts.values())
    by_family = {
        fam: {t: sum(1 for i in inds
                     if i["family"] == fam and i["verdict"] == t) for t in TOKENS}
        for fam in FAMILIES
    }
    return {
        "n": len(inds),
        "graded": graded,
        "counts": counts,
        "not_available": [i["key"] for i in inds if i["verdict"] is None],
        "by_family": by_family,
    }


def check_status(snap: dict) -> dict[str, str]:
    """Which indicator graded and to what, keyed by indicator. The publish step
    prints it, so a line going dark shows up in the build log."""
    return {i["key"]: (i["verdict"] or "not available") for i in indicators(snap)}


def _split_sentence(inds: list[dict]) -> str:
    """The one observation the page draws from the count, computed rather than
    asserted: do the quantity lines and the price lines agree today?"""
    def pressure(fam: str) -> tuple[int, int]:
        fam_inds = [i for i in inds if i["family"] == fam and i["verdict"]]
        bad = sum(1 for i in fam_inds if i["verdict"] in (WATCH, SCARCE))
        return bad, len(fam_inds)

    def _of(bad: int, n: int) -> str:
        return f"all {n}" if bad == n else f"{bad} of the {n}"

    q_bad, q_n = pressure("quantity")
    p_bad, p_n = pressure("price")
    if q_n == 0 or p_n == 0:
        return ("Too few lines graded on this snapshot to say whether the "
                "quantity side and the price side agree, which is itself worth "
                "knowing before quoting anything above.")
    if q_bad and not p_bad:
        return (f"The split is the finding. {_of(q_bad, q_n)} gradable "
                f"quantity lines print WATCH or SCARCE while all {p_n} gradable "
                f"price lines print AMPLE. That ordering is exactly what "
                f"2018-19 looked like from a distance: the balance sheet said "
                f"tight for months before the tape agreed, and the tape then "
                f"agreed all at once.")
    if p_bad and not q_bad:
        return (f"The split runs the other way today. {_of(p_bad, p_n)} "
                f"gradable price lines print WATCH or SCARCE while all {q_n} "
                f"gradable quantity lines print AMPLE. Pressure in the price "
                f"of overnight money with room still on the balance sheet "
                f"usually points at something other than reserve scarcity: a "
                f"dealer balance sheet, a settlement date, a collateral shock.")
    if p_bad and q_bad:
        return (f"Both families are printing pressure: {_of(q_bad, q_n)} "
                f"gradable quantity lines and {_of(p_bad, p_n)} gradable price "
                f"lines are at WATCH or worse. That is the configuration the "
                f"2019 episode had in its final weeks, and it is the one case "
                f"where the count above deserves to be read quickly.")
    return (f"All {q_n + p_n} gradable lines across both families print AMPLE. "
            f"The quantity side and the price side agree, which is the least "
            f"interesting and most reassuring state this page can be in.")


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------
_TOK_CLASS = {AMPLE: "tok-ample", WATCH: "tok-watch", SCARCE: "tok-scarce"}


def _render_indicator(n: int, ind: dict) -> str:
    e = html.escape
    if ind["verdict"]:
        badge = (f"<span class='tok {_TOK_CLASS[ind['verdict']]}'>"
                 f"{ind['verdict']}</span>")
    else:
        badge = "<span class='tok tok-dark'>NOT AVAILABLE TODAY</span>"

    out = ["<div class='ind'>",
           f"<h3><span>{n}. {e(ind['title'])}</span>{badge}</h3>"]

    if ind["dark"]:
        out.append(f"<div class='gap'><strong>Not available today.</strong> "
                   f"{e(_txt(ind['dark']))}</div>")

    if ind["readings"]:
        rows = []
        for label, value, note in ind["readings"]:
            rows.append(f"<tr><td>{e(label)}</td>"
                        f"<td class='num'>{e(_txt(value))}</td>"
                        f"<td class='faint'>{e(_txt(note)) if note else ''}</td></tr>")
        out.append("<table>" + "".join(rows) + "</table>")

    if ind["percentile"]:
        out.append(f"<p class='faint'>History percentile: "
                   f"{e(_txt(ind['percentile']))}.</p>")

    if ind["threshold"]:
        tag = ("<span class='edit'>desk editorial</span>"
               if ind["editorial"] else "")
        out.append(f"<p class='thr'><b>Threshold</b>{tag}<br>"
                   f"{e(_txt(ind['threshold']))}</p>")

    for note in ind["notes"]:
        out.append(f"<p class='faint'>{_sentence(note)}</p>")

    if ind["lesson"]:
        out.append(f"<p class='lesson'><b>What 2018-19 says this level meant</b>"
                   f"<br>{e(_txt(ind['lesson']))}</p>")

    out.append("</div>")
    return "\n".join(out)


def _render_scoreboard(inds: list[dict]) -> str:
    e = html.escape
    rows = []
    for n, ind in enumerate(inds, start=1):
        if ind["verdict"]:
            cell = (f"<span class='tok {_TOK_CLASS[ind['verdict']]}'>"
                    f"{ind['verdict']}</span>")
        else:
            cell = "<span class='tok tok-dark'>NOT AVAILABLE</span>"
        rows.append(f"<tr><td class='num'>{n}</td><td>{e(ind['title'])}</td>"
                    f"<td class='dim'>{e(FAMILY_LABEL[ind['family']])}</td>"
                    f"<td>{cell}</td></tr>")
    return ("<table><tr><th></th><th>indicator</th><th>family</th>"
            "<th>verdict</th></tr>" + "".join(rows) + "</table>")


def _render_count(inds: list[dict], t: dict) -> str:
    c = t["counts"]
    dark = t["n"] - t["graded"]
    line = (f"{c[AMPLE]} AMPLE, {c[WATCH]} WATCH, {c[SCARCE]} SCARCE, "
            f"{dark} not available today, out of {t['n']} indicators.")
    fam_rows = []
    for fam in FAMILIES:
        f = t["by_family"][fam]
        fam_rows.append(
            f"<tr><td>{html.escape(FAMILY_LABEL[fam])}</td>"
            f"<td class='num'>{f[AMPLE]}</td><td class='num'>{f[WATCH]}</td>"
            f"<td class='num'>{f[SCARCE]}</td></tr>")
    fam_table = ("<table><tr><th>family</th><th>ample</th><th>watch</th>"
                 "<th>scarce</th></tr>" + "".join(fam_rows) + "</table>")
    return (f"<p class='scoreline'>{html.escape(line)}</p>\n"
            f"<p>{html.escape(_split_sentence(inds))}</p>\n{fam_table}")


def render_ampleness_html(snap: dict) -> str:
    """The page. Pure function of the snapshot: same board in, same bytes out.

    The rendered page goes through the letter lint before it is returned, so a
    dash, a leaked placeholder or a malformed ordinal blocks publication here
    exactly as it does in the daily letter."""
    e = html.escape
    snap = snap if isinstance(snap, dict) else {}
    inds = indicators(snap)
    t = tally(inds)

    board_version = str(snap.get("version") or "unknown build")
    asof = str(snap.get("generated_at") or "")[:10]
    version_line = f"ampleness check {AMPLENESS_VERSION} / board {board_version}"
    if asof:
        version_line += f" / board snapshot {asof}"

    body = "\n".join(_render_indicator(n, ind) for n, ind in enumerate(inds, start=1))
    changelog_html = "\n".join(
        f"<li><span class='mono'>{e(day)}</span>: {e(note)}</li>"
        for day, note in CHANGELOG)

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Seiche ampleness check</title>
<meta name="description" content="Are reserves still ample? The indicator set the Fed's own framing rests on, assembled from the live board: reserves and their share of GDP, the fitted reserve demand kink and the NY Fed's RDE print, SOFR and EFFR against IORB, the share of repo printing above the ceiling, SRF and discount window take-up, the ON RRP buffer, and the projected kink crossing dates. Each with a threshold, a verdict, and what 2018-19 says that level meant.">
<link rel="canonical" href="{AMPLENESS_URL}">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<style>{_CSS}{_EXTRA_CSS}</style>
</head>
<body>
<div class="top">
  <div class="wordmark">SEI<span>CHE</span></div>
  <div class="faint"><a href="/">back to the board</a> &middot; <a href="/methodology">methodology</a> &middot; <a href="/skeptic">skeptic pack</a> &middot; <a href="/guide">plain English guide</a></div>
</div>

<h1>The ampleness check</h1>
<p class="faint mono">{e(version_line)}</p>
<p class="dim">There is only one argument in this corner of the market:
<span class="q">are reserves still ample, or is the system drifting toward
scarcity?</span> It is usually conducted with one number and a strong opinion.
The Fed's own framing rests on a specific indicator set, so this page walks
that set line by line, prints today's reading with the history percentile where
the payload carries one, attaches an explicit verdict with the threshold that
produced it, and says what the 2018-19 runoff showed a level like that
meant.</p>
<p class="dim">Two rules make the page worth reading. Every threshold is
printed next to the verdict it produced, and the ones that are the desk's
judgement rather than an established number are labelled
<span class="edit">desk editorial</span> on that line. And an indicator whose
input is dark says <strong>not available today</strong> with the reason,
because a missing number and a comfortable number are not the same thing and
should never look the same.</p>

<h2>The verdicts, at a glance</h2>
{_render_scoreboard(inds)}

<h2>The overall reading, which is a count</h2>
{_render_count(inds, t)}
<p class="dim">That is a count of the lines below and nothing else. The board
already publishes one composite; a second index assembled here would blend
these ten readings into a single number and hide the one thing worth seeing,
which is where they disagree. So this page counts tokens and refuses to
average them. If you want a weighted number, the board's composite is on the
front page with its weights published; this page is the argument underneath
it, not another summary of it.</p>

<h2>The checklist</h2>
{body}

<h2>What this page is not</h2>
<ul>
<li>It is not a forecast. Every line is a reading of where things stand now,
plus a historical note; the only forward-looking item is the runway, which is
arithmetic on stated assumptions and says so.</li>
<li>It is not a new index. The overall reading is a count of tokens. Nothing
here is weighted, blended or scaled into a composite.</li>
<li>It is not the last word on ampleness. The thresholds marked
<span class="edit">desk editorial</span> are opinions, published so they can be
argued with rather than buried in code. The rest come from the fit, from the
engines' own published scales, or from the New York Fed's published bands.</li>
<li>It does not fill gaps. A dark input prints as not available today, with
the reason, and takes no part in the count.</li>
</ul>

<h2>Where the numbers come from</h2>
<p>Every reading on this page is lifted from the board's own payload: the
hinge fit and the kink distance from the kink engine, the official elasticity
comparison from the RDE nowcast, the repo percentile frame and facility
take-up from the stigma engine, the spread and its z score from the tails
engine, the spread's expanding percentile from the breakwater engine, the
reserves/GDP percentile from the turn engine, the crossing dates from the
runway engine, and the rate and balance prints from the board headline. The
method behind each of those lives on the versioned
<a href="{METHODOLOGY_URL}">methodology page</a>, and the code is at
<a href="{REPO_URL}">{e(REPO_URL)}</a> under AGPL-3.0-or-later. The two questions a
skeptic asks before any of this are answered in the
<a href="{SKEPTIC_URL}">skeptic pack</a>.</p>

<h2>Changelog</h2>
<ul>
{changelog_html}
</ul>

<p class="faint">Free public data with native lags. Not investment advice.
Seiche is free open source software (AGPL-3.0-or-later) and a public good.</p>
</body>
</html>
"""
    issues = lint_letter(page)
    if issues:
        raise SystemExit("ampleness page failed lint: " + "; ".join(issues))
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
                                 headers={"User-Agent": "seiche-ampleness"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def write_ampleness(snap: dict, out: Path | None = None) -> Path:
    path = out or DEFAULT_OUT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_ampleness_html(snap))
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Render the ampleness check from the live board.")
    ap.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT),
                    help="board snapshot JSON (falls back to fetching the API)")
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)

    snap = load_snapshot(Path(args.snapshot), args.api)
    path = write_ampleness(snap, Path(args.out))
    inds = indicators(snap)
    t = tally(inds)
    print(path)
    for n, ind in enumerate(inds, start=1):
        print(f"  {n:>2}. {ind['key']:<14} {ind['verdict'] or 'not available'}")
    c = t["counts"]
    print(f"count: {c[AMPLE]} AMPLE, {c[WATCH]} WATCH, {c[SCARCE]} SCARCE, "
          f"{t['n'] - t['graded']} not available (of {t['n']})")
    if t["not_available"]:
        print("not available today: " + ", ".join(t["not_available"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
