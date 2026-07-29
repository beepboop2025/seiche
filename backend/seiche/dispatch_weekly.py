"""The Monday flagship, "The Week Ahead": the letter that prints its calls first.

The daily dispatch tells a reader where the board is. This one tells them what
the week holds and what the desk expects out of it, in numbers, before the week
runs. Every issue is numbered, every issue carries three to five PRE-REGISTERED
calls with stable IDs, a stated expected number and a stated grading rule, and
every issue opens the graded scorecard of the last one. That is the whole
franchise: a forward table anybody can check, and a public record of being
wrong. Wrightson ICAP built a business on the first half; the second half is
what a free board can add, because it costs nothing to publish a miss.

Why pre-registration and not commentary: a weekly note that reads the tape
after the fact is worth nothing to a money-market analyst who read the same
tape. A note that said last Monday what this Monday would print, and prints the
difference, is worth reading twice. The calls are generated deterministically
from live board state (the turn model's band, the supply desk's projected
rows, the SRF take-up series, the runway path, the composite fan), so the desk
cannot quietly pick easy ones, and each is gradable from public data the board
already holds.

Section order is invariant and numbered:
  1 the week in one paragraph      5 pre-registered calls
  2 the calendar                   6 last week's calls, graded
  3 supply                         7 what would change the desk's mind
  4 reserves

Outputs (relative to the repo root):
  frontend/public/dispatches/{slug}.md              the issue (+ HAS-DESK marker)
  backend/seiche/dispatches/{slug}.desk.md          the continuation (free, like
                                                    everything else; pre-rename
                                                    history is *.paid.md and
                                                    readers accept both)
  frontend/public/dispatches/index.json             prepended, deduped, newest first
  backend/seiche/dispatches/weekly_state.json       the call ledger: this issue's
                                                    calls, the set the issue graded,
                                                    and the running record. A separate
                                                    file from the daily letter's
                                                    state.json so the two cannot
                                                    collide on a Monday, when both run.

Run:  python -m seiche.dispatch_weekly [--api URL | --snapshot FILE] [--date YYYY-MM-DD] [--force]
Stdlib only, so CI can run it with PYTHONPATH=backend and no install. CI
passes --snapshot with a board it built itself (same as the daily): the
weekly grades last issue's calls, so it must not depend on the box being
current, and it refuses a stale board rather than grade against it.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from seiche.dispatch_daily import (
    INDEX,
    MARKER,
    PAID_DIR,
    REPO_ROOT,
    _clean,
    _falsifiers,
    _fmt,
    _get_json,
    _ordinal,
    _pick,
    _signed,
    lint_letter,
)

DEFAULT_API = "https://api.seiche.info"
TAG = "WEEK AHEAD"
WEEKLY_STATE = PAID_DIR / "weekly_state.json"

CALENDAR_HORIZON_D = 10   # "the next 7 to 10 days", the week plus its shoulder
CALL_MAX = 5              # at most five calls: a scorecard nobody reads is not a scorecard
CALL_MIN = 3
CARRY_LIMIT = 1           # an open call gets one more week, then it is dropped unresolved
SUPPLY_ROWS = 10          # rows printed in the forward cash table
EMPTY_RECORD = {"graded": 0, "hit": 0, "miss": 0}


# ---------------------------------------------------------------------------
# dates. The weekly letter is a calendar product, so every date it prints goes
# through here and every window is closed at both ends.
# ---------------------------------------------------------------------------
_DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _parse(s):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _shift(date: str, days: int) -> str:
    d = _parse(date)
    return (d + timedelta(days=days)).isoformat() if d else str(date)


def _stamp(s) -> str:
    """2026-07-30 -> '2026-07-30 (Thu)'. A week-ahead table is read by weekday."""
    d = _parse(s)
    return f"{d.isoformat()} ({_DOW[d.weekday()]})" if d else str(s)


def _within(s, start: str, days: int) -> bool:
    d, s0 = _parse(s), _parse(start)
    if d is None or s0 is None:
        return False
    return 0 <= (d - s0).days <= days


def _cell(s) -> str:
    """Free text bound for a markdown table cell: house copy rules plus the
    one character that would silently break the column."""
    return _clean(s).replace("|", "/").strip()


# ---------------------------------------------------------------------------
# 2 the calendar. Every dated event inside the window, one row each, with the
# funding impact spelled out rather than left to the reader's memory.
# ---------------------------------------------------------------------------
def _settlement_rows(snap: dict, date: str) -> list[dict]:
    sd = snap.get("engines", {}).get("supplydesk", {}) or {}
    rows: list[dict] = []
    if sd.get("ok") and sd.get("rows"):
        for r in sd["rows"]:
            if not _within(r.get("date"), date, CALENDAR_HORIZON_D):
                continue
            net = r.get("net_new_cash_b")
            try:
                drains = float(net) > 0
            except (TypeError, ValueError):
                drains = True
            status = ("the board's house projection, not yet announced" if r.get("projected")
                      else "announced, size taken from the tenor's last print" if r.get("amount_estimated")
                      else "announced")
            rows.append({
                "date": r.get("date"),
                "event": "Treasury settlement",
                "impact": (f"{_signed(net)}B net new cash, "
                           + ("drains reserves" if drains else "returns cash to the market")),
                "watch": (f"bills {_fmt(r.get('bills_gross_b'))}B and coupons "
                          f"{_fmt(r.get('coupons_gross_b'))}B against {_fmt(r.get('maturing_b'))}B "
                          f"maturing; {status}"),
            })
        return rows
    for s in (snap.get("calendar", {}) or {}).get("upcoming_settlements") or []:
        if not _within(s.get("date"), date, CALENDAR_HORIZON_D):
            continue
        rows.append({
            "date": s.get("date"),
            "event": "Auction settlement",
            "impact": f"${_fmt(s.get('amount_b'))}B settles, reserves fall as the TGA builds",
            "watch": "the supply desk is dark, so this is gross settlement and not net new cash",
        })
    return rows


def _calendar_rows(snap: dict, date: str) -> list[dict]:
    cal = snap.get("calendar", {}) or {}
    weather = snap.get("engines", {}).get("weather", {}) or {}
    rows = _settlement_rows(snap, date)
    settled_dates = {r["date"] for r in rows}

    for f in cal.get("fomc_next_90d") or []:
        if _within(f.get("date"), date, CALENDAR_HORIZON_D):
            rows.append({
                "date": f.get("date"),
                "event": "FOMC decision",
                "impact": "the corridor every spread on this board is priced against can move",
                "watch": "the IORB and ON RRP settings, the runoff pace, any change to the SRF",
            })
    for t in cal.get("corporate_tax_next_90d") or []:
        if _within(t.get("date"), date, CALENDAR_HORIZON_D):
            rows.append({
                "date": t.get("date"),
                "event": "Corporate tax date",
                "impact": "the TGA builds and reserves fall on a schedule everyone can read",
                "watch": "the daily TGA print against the drain the calendar implies",
            })

    turn = (snap.get("deep", {}) or {}).get("turn", {}) or {}
    nt = turn.get("next_turn") or {}
    if nt.get("date") and _within(nt["date"], date, CALENDAR_HORIZON_D):
        band = nt.get("band_bp") or [None, None]
        rows.append({
            "date": nt["date"],
            "event": f"{str(nt.get('mode', 'calendar turn')).replace('_', ' ')} turn",
            "impact": (f"turn model forecasts {_signed(nt.get('forecast_bp'), 1)}bp of slosh, band "
                       f"[{_signed(band[0], 1)}, {_signed(band[1], 1)}], severity "
                       f"{_fmt(nt.get('severity'))} of 5"),
            "watch": (f"SOFR minus IORB into and over the turn; the published number is the "
                      f"{nt.get('published', 'model')} leg"),
        })

    for c in (cal.get("crunch_windows") or weather.get("crunch_windows") or []):
        if not _within(c.get("date"), date, CALENDAR_HORIZON_D) or c.get("date") in settled_dates:
            continue
        wc = (f"worst case reserves near ${_fmt(c.get('worst_case_b'))}B after the drain"
              if c.get("worst_case_b") is not None else "size unpublished on this window")
        rows.append({
            "date": c.get("date"),
            "event": "Flagged crunch window",
            "impact": _cell(c.get("reason", "a flagged crunch window")),
            "watch": wc,
        })

    order = {"Treasury settlement": 0, "Auction settlement": 0, "FOMC decision": 1,
             "Corporate tax date": 2}
    rows.sort(key=lambda r: (str(r.get("date")), order.get(r.get("event"), 3)))
    return rows


def _calendar_section(snap: dict, date: str, rows: list[dict]) -> list[str]:
    if not rows:
        return ["Nothing dated lands inside the next ten days: no settlement the supply desk can "
                "see, no tax date, no FOMC, no turn. An empty calendar is a reading, and it is the "
                "reading that makes an unscheduled move worth more when it comes."]
    table = ["| date | event | expected funding impact | what the desk watches |",
             "|---|---|---|---|"]
    for r in rows:
        table.append(f"| {_stamp(r.get('date'))} | {_cell(r.get('event'))} | "
                     f"{_cell(r.get('impact'))} | {_cell(r.get('watch'))} |")
    out = ["\n".join(table)]
    heavy = max(rows, key=lambda r: _weight(r))
    out += [f"{_fmt(len(rows))} dated items inside ten days. The one to diary: "
            f"{_cell(heavy.get('event'))}, {_stamp(heavy.get('date'))}. Dates are the part of "
            "funding stress that is knowable in advance, which is why they lead this letter "
            "rather than close it."]
    return out


def _weight(row: dict) -> int:
    kind = str(row.get("event", ""))
    if "FOMC" in kind:
        return 3
    if "turn" in kind:
        return 2
    if "tax" in kind:
        return 2
    return 1


# ---------------------------------------------------------------------------
# 3 supply. The forward cash table, announced separated from projected.
# ---------------------------------------------------------------------------
def _supply_section(snap: dict) -> list[str]:
    sd = snap.get("engines", {}).get("supplydesk", {}) or {}
    if not sd.get("ok") or not sd.get("rows"):
        reason = _clean(sd.get("reason", "the engine reports no forward table"))
        return [f"The supply desk is dark this week ({reason}). When it is live this section "
                "carries the forward cash table: every settlement date with bills, coupons and "
                "maturing paper, and the net new cash that is the reserve drain the calendar "
                "forces, with announced rows kept visibly apart from the house projection."]
    table = ["| settles | bills $B | coupons $B | maturing $B | net new cash $B | status |",
             "|---|---|---|---|---|---|"]
    for r in sd["rows"][:SUPPLY_ROWS]:
        status = ("projected" if r.get("projected")
                  else "announced, size estimated" if r.get("amount_estimated")
                  else "announced")
        table.append(
            f"| {_stamp(r.get('date'))} | {_fmt(r.get('bills_gross_b'))} "
            f"| {_fmt(r.get('coupons_gross_b'))} | {_fmt(r.get('maturing_b'))} "
            f"| **{_signed(r.get('net_new_cash_b'))}** | {status} |"
        )
    out = ["\n".join(table)]
    totals = sd.get("totals") or {}
    heavy = sd.get("heaviest_day") or {}
    n_proj = sum(1 for r in sd["rows"] if r.get("projected"))
    horizon_end = sd.get("horizon_end") or "the end of the window"
    tail = (
        f"Across the whole {_fmt(len(sd['rows']))} row horizon to {horizon_end}, gross issuance "
        f"runs ${_fmt(totals.get('gross_b'))}B against ${_fmt(totals.get('maturing_b'))}B "
        f"maturing, {_signed(totals.get('net_new_cash_b'))}B of net new cash. "
        + (f"The heaviest single day is {_stamp(heavy.get('date'))} at "
           f"{_signed(heavy.get('net_new_cash_b'))}B. " if heavy.get("date") else "")
        + (f"Announcements run through {sd.get('announced_through')}; "
           f"{_fmt(n_proj)} row{'' if n_proj == 1 else 's'} past that "
           f"{'is' if n_proj == 1 else 'are'} the desk's own projection, carried at each tenor's "
           "last size and its observed cadence, and graded in this letter when Treasury "
           "announces." if sd.get("announced_through") else
           "Nothing in the table is announced yet, so every row is the desk's projection.")
    )
    out += [tail,
            "Net new cash is the number that drains reserves. Maturing includes SOMA rollovers, so "
            "the private-side drain runs smaller on SOMA-heavy dates, and Treasury buybacks are not "
            "netted out of the maturing stock. Both caveats are in the engine, not in a footnote "
            "nobody reads."]
    return out


# ---------------------------------------------------------------------------
# 4 reserves. Where the path goes, how far the kink is, and what the NY Fed
# says about the same curve.
# ---------------------------------------------------------------------------
_LEG_NAMES = [("base", "base"), ("fast_drain", "fast drain"), ("slow", "slow")]


def _reserves_section(snap: dict) -> list[str]:
    eng = snap.get("engines", {}) or {}
    rw = eng.get("runway", {}) or {}
    k = eng.get("kink", {}) or {}
    out: list[str] = []

    if rw.get("ok") and rw.get("scenarios"):
        scen = rw["scenarios"]
        table = ["| leg | 13w end level $B | kink crossing |", "|---|---|---|"]
        for key, label in _LEG_NAMES:
            s = scen.get(key) or {}
            cross = s.get("crossing_date") or "none inside thirteen weeks"
            table.append(f"| {label} | {_fmt(s.get('end_reserves_b'))} | {cross} |")
        out.append("\n".join(table))
        a = rw.get("assumptions") or {}
        out += [f"The legs share one arithmetic and differ on three stated assumptions: a "
                    f"trailing drift of {_signed(a.get('trailing_drift_b_per_week'), 1)}B a week, a "
                    f"runoff pace of ${_fmt(a.get('qt_pace_b_per_month'))}B a month, and a TGA now "
                    f"at ${_fmt(a.get('tga_now_b'))}B reverting to its trailing median of "
                    f"${_fmt(a.get('tga_median_b'))}B on the base leg or its p75 of "
                    f"${_fmt(a.get('tga_p75_b'))}B on the fast one. This is arithmetic on published "
                    "assumptions, not a forecast of policy, and the trailing drift already embeds "
                    "recent runoff, so the explicit terms can double count."]
    else:
        out.append("The runway projection is dark this week. When it is live this section carries "
                   "the three reserve legs, their thirteen week end levels and the date each one "
                   "crosses the fitted kink.")

    if k.get("ok"):
        dist = k.get("distance_b")
        below = dist is not None and float(dist) < 0
        out.append(
            f"The kink itself sits near **${_fmt(k.get('kink_reserves_b'))}B** of reserves against "
            f"${_fmt(k.get('current_reserves_b'))}B held, "
            f"${_fmt(abs(float(dist)) if dist is not None else None)}B "
            f"{'below' if below else 'above'} the estimate, on a fit with R² {_fmt(k.get('r2'), 2)} "
            f"and a model versus market consistency of {_fmt(k.get('consistency'), 2)}. "
            + ("Through the kink is where the spread starts answering to reserve changes, so the "
               "week's job is watching the slope, not the distance."
               if below else
               f"At the trailing drain of ${_fmt(k.get('drain_per_bday_b'), 1)}B a business day the "
               "straight line arithmetic still has room, and the legs above carry the bands behind "
               "that.")
        )
    else:
        out.append("The kink fit is dark this week, so the distance to scarcity is unpriced and "
                   "the runway legs above have no threshold to cross.")

    rde = eng.get("rdenowcast", {}) or {}
    if rde.get("ok"):
        band = rde.get("nyfed_band_68") or [None, None]
        inside = "inside" if rde.get("within_68_band") else "outside"
        agree = "agrees" if rde.get("direction_agree") else "disagrees"
        lead = rde.get("nowcast_lead_days")
        lead_txt = ""
        try:
            if lead is not None and int(lead) > 0:
                lead_txt = (f", which the desk publishes {_fmt(lead)} days ahead of their release "
                            "cycle")
        except (TypeError, ValueError):
            lead_txt = ""
        summ = rde.get("scorecard_summary") or {}
        record = ""
        if summ.get("n"):
            record = (f" Across {_fmt(summ.get('n'))} walk forward refits the desk landed inside "
                      f"their 68% band {_fmt(summ.get('within_band'))} times and agreed on "
                      f"direction {_fmt(summ.get('direction_agree'))} times, mean absolute "
                      f"difference {_fmt(summ.get('mean_abs_diff_bp'), 2)}bp.")
        out.append(
            f"External check on the same curve: the NY Fed's latest Reserve Demand Elasticity "
            f"print ({rde.get('nyfed_asof')}) reads {_fmt(rde.get('nyfed_bp_per_1pct'), 2)}bp per "
            f"one percent of reserves with a 68% band of [{_fmt(band[0], 2)}, {_fmt(band[1], 2)}]; "
            f"the desk's continuous fit implies {_fmt(rde.get('ours_bp_per_1pct'), 2)}bp, {inside} "
            f"that band, and the direction {agree}{lead_txt}." + record
        )
    else:
        out.append("The RDE nowcast is dark this week, so there is no official print to grade the "
                   "desk's fit against. Where the two diverge one of us is wrong, and that "
                   "comparison is worth more than either number alone.")
    return out


# ---------------------------------------------------------------------------
# 5 the calls. Generated from live board state in a fixed candidate order, so
# the desk cannot pick the easy ones week to week. Every call states the number
# it expects, the date it resolves, and the rule that decides it.
# ---------------------------------------------------------------------------
def _call_turn(snap: dict, date: str) -> dict | None:
    nt = ((snap.get("deep", {}) or {}).get("turn", {}) or {}).get("next_turn") or {}
    d = nt.get("date")
    band = nt.get("band_bp") or [None, None]
    if not d or band[0] is None or band[1] is None or not _within(d, date, 7):
        return None
    mode = str(nt.get("mode", "calendar")).replace("_", " ")
    return {
        "kind": "turn",
        "claim": (f"The {mode} turn on {d} prints a slosh inside the model's published band of "
                  f"{_signed(band[0], 1)} to {_signed(band[1], 1)}bp."),
        "expected": (f"{_signed(band[0], 1)} to {_signed(band[1], 1)}bp, around a point forecast of "
                     f"{_signed(nt.get('forecast_bp'), 1)}bp ({nt.get('published', 'model')} leg, "
                     f"severity {_fmt(nt.get('severity'))} of 5)"),
        "rule": ("hit if the realized slosh for that date on next week's turn record lands inside "
                 "the band, miss if it lands outside; open if the turn has not yet entered the "
                 "record"),
        "turn_date": d,
        "lo": float(band[0]),
        "hi": float(band[1]),
        "resolve_by": _shift(d, 3),
    }


def _call_supply(snap: dict, date: str) -> dict | None:
    sd = snap.get("engines", {}).get("supplydesk", {}) or {}
    if not sd.get("ok") or not sd.get("rows"):
        return None
    # Only dates that survive into next week's table can be graded next week:
    # a settlement inside the coming seven days has left the forward window by
    # the time the next issue is written.
    cands = []
    for r in sd["rows"]:
        d = _parse(r.get("date"))
        d0 = _parse(date)
        if d is None or d0 is None:
            continue
        if not (8 <= (d - d0).days <= 27):
            continue
        try:
            net = float(r.get("net_new_cash_b"))
        except (TypeError, ValueError):
            continue
        cands.append((0 if r.get("projected") or r.get("amount_estimated") else 1, -abs(net), r, net))
    if not cands:
        return None
    cands.sort(key=lambda c: (c[0], c[1]))
    _p, _n, row, net = cands[0]
    tol = round(max(5.0, 0.1 * abs(net)), 1)
    house = ("the board's own projection" if row.get("projected")
             else "an announced date sized from the tenor's last print" if row.get("amount_estimated")
             else "already announced")
    return {
        "kind": "supply",
        "claim": (f"The {row.get('date')} settlement, which the board carries at "
                  f"{_signed(net)}B of net new cash ({house}), lands within ${_fmt(tol, 1)}B of "
                  "that figure once Treasury has announced it."),
        "expected": f"{_signed(net)}B net new cash, tolerance ${_fmt(tol, 1)}B",
        "rule": ("hit if next week's supply table shows that date announced with Treasury's amount "
                 "and within tolerance, miss if it is announced and outside; open if the row is "
                 "still projected or its amount is still TBA (a TBA fill is the desk's own "
                 "estimate and is never graded as announced)"),
        "settle_date": row.get("date"),
        "target": net,
        "tol": tol,
        "resolve_by": _shift(date, 7),
    }


def _call_srf(snap: dict, date: str) -> dict | None:
    st = snap.get("engines", {}).get("stigma", {}) or {}
    tk = st.get("takeup") or {}
    if not st.get("ok") or tk.get("max20_b") is None:
        return None
    try:
        base = float(tk["max20_b"])
    except (TypeError, ValueError):
        return None
    thr = 1.0 if base < 1.0 else 25.0
    label = "de minimis" if thr == 1.0 else "material"
    return {
        "kind": "srf",
        "claim": (f"SRF take-up stays under ${_fmt(thr, 0)}B on every session of the week, which is "
                  f"to say it stays below the {label} line."),
        "expected": (f"under ${_fmt(thr, 0)}B; the trailing twenty session maximum today is "
                     f"${_fmt(base, 2)}B"),
        "rule": ("hit if next week's board shows a twenty session maximum take-up under the "
                 "threshold, miss if any session prints at or above it"),
        "threshold": thr,
        "baseline": round(base, 2),
        "resolve_by": _shift(date, 7),
    }


def _call_reserves(snap: dict, date: str) -> dict | None:
    rw = snap.get("engines", {}).get("runway", {}) or {}
    k = snap.get("engines", {}).get("kink", {}) or {}
    if not rw.get("ok") or not k.get("ok"):
        return None
    scen = rw.get("scenarios") or {}

    def wk1(name):
        path = (scen.get(name) or {}).get("path") or []
        try:
            return float(path[1][1])
        except (IndexError, TypeError, ValueError):
            return None

    target = wk1("base")
    if target is None:
        return None
    fast, slow = wk1("fast_drain"), wk1("slow")
    spread = abs(fast - slow) if fast is not None and slow is not None else 0.0
    tol = round(max(25.0, spread), 1)
    return {
        "kind": "reserves",
        "claim": (f"Reserves print near ${_fmt(target)}B on next week's H.4.1, the base leg's week "
                  "one level."),
        "expected": (f"${_fmt(target)}B, tolerance ${_fmt(tol)}B (the width of the desk's own fast "
                     "to slow bracket at week one, floored at $25B)"),
        "rule": ("hit if next week's board carries current reserves within tolerance of the "
                 "target, miss otherwise"),
        "target": round(target, 1),
        "tol": tol,
        "resolve_by": _shift(date, 7),
    }


def _call_composite(snap: dict, date: str) -> dict | None:
    comp = snap.get("engines", {}).get("composite", {}) or {}
    v = comp.get("value")
    if v is None:
        return None
    # The call resolves in a week, so only a fan horizon near five sessions is
    # the right band. A 21 session fan is the wrong width for a seven day claim
    # and the flat band is the honest fallback.
    fan = [f for f in ((snap.get("deep", {}) or {}).get("montecarlo", {}) or {}).get("fan") or []
           if f.get("p10") is not None and f.get("p90") is not None
           and f.get("h") is not None and 3 <= float(f["h"]) <= 10]
    src = ("a flat five point band either side of today's reading, stated because the board has no "
           "fan at a one week horizon")
    lo, hi = float(v) - 5.0, float(v) + 5.0
    if fan:
        pick = min(fan, key=lambda f: abs((f.get("h") or 0) - 5))
        lo, hi = float(pick["p10"]), float(pick["p90"])
        src = (f"the board's own Monte Carlo p10 to p90 at {_fmt(pick.get('h'))} sessions, "
               "seeded fixed so the band is reproducible")
    return {
        "kind": "composite",
        "claim": f"The composite reads between {_fmt(lo, 1)} and {_fmt(hi, 1)} on next week's board.",
        "expected": f"{_fmt(lo, 1)} to {_fmt(hi, 1)}, from {_fmt(v, 1)} today; the band is {src}",
        "rule": "hit if next week's composite prints inside the band, miss otherwise",
        "lo": round(lo, 1),
        "hi": round(hi, 1),
        "at_issue": round(float(v), 1),
        "resolve_by": _shift(date, 7),
    }


def _call_rde(snap: dict, date: str) -> dict | None:
    rde = snap.get("engines", {}).get("rdenowcast", {}) or {}
    if not rde.get("ok") or rde.get("within_68_band") is None:
        return None
    inside = bool(rde["within_68_band"])
    band = rde.get("nyfed_band_68") or [None, None]
    return {
        "kind": "rde",
        "claim": ("The desk's continuous reserve demand fit stays "
                  + ("inside" if inside else "outside")
                  + " the NY Fed's published 68% band."),
        "expected": (f"{'inside' if inside else 'outside'} [{_fmt(band[0], 2)}, "
                     f"{_fmt(band[1], 2)}]bp per one percent of reserves; the desk reads "
                     f"{_fmt(rde.get('ours_bp_per_1pct'), 2)}bp today"),
        "rule": ("hit if next week's nowcast still reports the same side of their band, miss if it "
                 "flips; open if either fit is dark"),
        "expect_within": inside,
        "resolve_by": _shift(date, 7),
    }


def _pooled_odds(snap: dict):
    deep = snap.get("deep", {}) or {}
    mc = deep.get("modelcourt", {}) or {}
    if mc.get("ok") and (mc.get("ensemble") or {}).get("p") is not None:
        return float(mc["ensemble"]["p"]), "the model court's pooled read"
    st = deep.get("stacker", {}) or {}
    if st.get("ok") and st.get("p_now") is not None:
        return float(st["p_now"]), "the stack's pooled read"
    return None, None


def _call_court(snap: dict, date: str) -> dict | None:
    p, src = _pooled_odds(snap)
    if p is None:
        return None
    lo, hi = max(0.0, p - 0.05), min(1.0, p + 0.05)
    return {
        "kind": "court",
        "claim": (f"The pooled five day event odds stay between {lo:.0%} and {hi:.0%}, that is "
                  f"within five points of today's {p:.0%}."),
        "expected": f"{lo:.0%} to {hi:.0%}, from {p:.0%} today, taken from {src}",
        "rule": "hit if next week's pooled odds sit inside the band, miss otherwise",
        "lo": round(lo, 4),
        "hi": round(hi, 4),
        "resolve_by": _shift(date, 7),
    }


def _call_tell(snap: dict, date: str) -> dict | None:
    tell = (snap.get("deep", {}) or {}).get("tell", {}) or {}
    if not tell.get("ok") or tell.get("tell") is None:
        return None
    t = float(tell["tell"])
    lo, hi = t - 20.0, t + 20.0
    return {
        "kind": "tell",
        "claim": (f"The Tell, the gap between what the pipes measure and what the screens price, "
                  f"stays between {_signed(lo)} and {_signed(hi)}."),
        "expected": f"{_signed(lo)} to {_signed(hi)}, from {_signed(t)} today",
        "rule": "hit if next week's Tell prints inside the band, miss otherwise",
        "lo": round(lo, 1),
        "hi": round(hi, 1),
        "resolve_by": _shift(date, 7),
    }


def _call_regime(snap: dict, date: str) -> dict | None:
    comp = snap.get("engines", {}).get("composite", {}) or {}
    regime = (comp.get("regime") or "").upper()
    if not regime:
        return None
    return {
        "kind": "regime",
        "claim": f"The board is still reading {regime} next Monday.",
        "expected": f"{regime}, unchanged",
        "rule": "hit if next week's regime label matches, miss if it has moved either way",
        "regime": regime,
        "resolve_by": _shift(date, 7),
    }


# Candidate order is editorial and fixed: dated events first, then the levels.
_CANDIDATES = (_call_turn, _call_supply, _call_srf, _call_reserves, _call_composite,
               _call_rde, _call_court, _call_tell, _call_regime)


def _make_calls(snap: dict, date: str, issue_no) -> list[dict]:
    issue = int(issue_no) if issue_no is not None else 0
    calls: list[dict] = []
    for fn in _CANDIDATES:
        if len(calls) >= CALL_MAX:
            break
        c = fn(snap, date)
        if c:
            c.update({"id": f"W{issue}-{len(calls) + 1}", "issue": issue, "issued": date,
                      "carried": 0})
            calls.append(c)
    return calls


# ---------------------------------------------------------------------------
# 6 grading. Every call resolves against today's snapshot by its own stated
# rule. A dark engine makes a call open, never a hit.
# ---------------------------------------------------------------------------
def _band(v, lo, hi) -> bool:
    return float(lo) <= float(v) <= float(hi)


def _grade(call: dict, snap: dict) -> tuple[str, str]:
    """(verdict, actual as printed). Verdict is hit, miss or open."""
    eng = snap.get("engines", {}) or {}
    deep = snap.get("deep", {}) or {}
    kind = call.get("kind")

    if kind == "turn":
        for r in (deep.get("turn", {}) or {}).get("recent_turns") or []:
            if str(r.get("date")) == str(call.get("turn_date")):
                a = r.get("slosh_bp")
                if a is None:
                    break
                ok = _band(a, call["lo"], call["hi"])
                return ("hit" if ok else "miss"), f"{_signed(a, 1)}bp realized"
        return "open", "the turn is not in the board's record yet"

    if kind == "supply":
        sd = eng.get("supplydesk", {}) or {}
        if not sd.get("ok"):
            return "open", "the supply desk is dark"
        for r in sd.get("rows") or []:
            if str(r.get("date")) == str(call.get("settle_date")):
                if r.get("projected"):
                    return "open", (f"still projected at {_signed(r.get('net_new_cash_b'))}B, not "
                                    "yet announced")
                if r.get("amount_estimated"):
                    # A TBA amount is filled with the tenor's last size by the
                    # supply desk itself. Grading that as announced would let
                    # the desk score a hit against its own fill, so the call
                    # stays open until Treasury's number is on the row.
                    return "open", (f"announced, but the amount is still TBA (the board's "
                                    f"{_signed(r.get('net_new_cash_b'))}B is the desk's own fill "
                                    "from the tenor's last size, and the desk does not grade "
                                    "itself against its own estimate)")
                a = r.get("net_new_cash_b")
                if a is None:
                    return "open", "the row carries no net new cash"
                ok = abs(float(a) - float(call["target"])) <= float(call["tol"])
                return ("hit" if ok else "miss"), f"{_signed(a)}B announced"
        return "open", "that date has left the forward window"

    if kind == "srf":
        st = eng.get("stigma", {}) or {}
        tk = st.get("takeup") or {}
        if not st.get("ok") or tk.get("max20_b") is None:
            return "open", "the stigma gauge is dark"
        a = float(tk["max20_b"])
        ok = a < float(call["threshold"])
        return ("hit" if ok else "miss"), f"${_fmt(a, 2)}B twenty session maximum"

    if kind == "reserves":
        k = eng.get("kink", {}) or {}
        if not k.get("ok") or k.get("current_reserves_b") is None:
            return "open", "the kink fit is dark, so reserves have no published level"
        a = float(k["current_reserves_b"])
        ok = abs(a - float(call["target"])) <= float(call["tol"])
        return ("hit" if ok else "miss"), f"${_fmt(a)}B"

    if kind == "composite":
        v = (eng.get("composite", {}) or {}).get("value")
        if v is None:
            return "open", "no composite on the board"
        return ("hit" if _band(v, call["lo"], call["hi"]) else "miss"), _fmt(v, 1)

    if kind == "rde":
        rde = eng.get("rdenowcast", {}) or {}
        if not rde.get("ok") or rde.get("within_68_band") is None:
            return "open", "the nowcast is dark"
        a = bool(rde["within_68_band"])
        ok = a == bool(call.get("expect_within"))
        return ("hit" if ok else "miss"), (
            f"{'inside' if a else 'outside'} the band at "
            f"{_fmt(rde.get('ours_bp_per_1pct'), 2)}bp")

    if kind == "court":
        p, _src = _pooled_odds(snap)
        if p is None:
            return "open", "no pooled odds on the board"
        return ("hit" if _band(p, call["lo"], call["hi"]) else "miss"), f"{p:.0%}"

    if kind == "tell":
        tell = deep.get("tell", {}) or {}
        if not tell.get("ok") or tell.get("tell") is None:
            return "open", "the Tell is dark"
        t = float(tell["tell"])
        return ("hit" if _band(t, call["lo"], call["hi"]) else "miss"), _signed(t)

    if kind == "regime":
        r = ((eng.get("composite", {}) or {}).get("regime") or "").upper()
        if not r:
            return "open", "no regime on the board"
        return ("hit" if r == call.get("regime") else "miss"), r

    return "open", "no grading rule for this call"


_VERDICT_ORDER = {"miss": 0, "hit": 1, "open": 2}


def _grade_all(calls: list[dict], snap: dict) -> list[dict]:
    out = []
    for c in calls or []:
        verdict, actual = _grade(c, snap)
        out.append({**c, "verdict": verdict, "actual": actual})
    # Misses print first. A scorecard that leads with its hits is marketing.
    out.sort(key=lambda c: (_VERDICT_ORDER.get(c["verdict"], 3), str(c.get("id"))))
    return out


def _graded_section(graded: list[dict], record_prev: dict) -> list[str]:
    if not graded:
        return ["There is no prior issue to grade. This is the first Week Ahead, so the calls in "
                "section 5 are the first entries in the ledger and the next issue opens with them "
                "marked hit or miss. Saying that plainly beats printing an empty table."]
    table = ["| id | verdict | expected | actual | the call |", "|---|---|---|---|---|"]
    for c in graded:
        table.append(
            f"| {c.get('id')} | **{str(c['verdict']).upper()}** | {_cell(c.get('expected'))} "
            f"| {_cell(c.get('actual'))} | {_cell(c.get('claim'))} |"
        )
    out = ["\n".join(table)]
    hits = sum(1 for c in graded if c["verdict"] == "hit")
    misses = sum(1 for c in graded if c["verdict"] == "miss")
    opens = [c for c in graded if c["verdict"] == "open"]
    tally = (f"Last week: {_fmt(misses)} miss, {_fmt(hits)} hit"
             + (f", {_fmt(len(opens))} still open." if opens else "."))
    lifetime = ""
    total = int(record_prev.get("graded") or 0) + hits + misses
    won = int(record_prev.get("hit") or 0) + hits
    if total:
        lifetime = (f" Lifetime the desk has resolved {_fmt(total)} calls and hit {_fmt(won)} of "
                    f"them, {won / total:.0%}.")
    carried = [c for c in opens if int(c.get("carried") or 0) < CARRY_LIMIT]
    dropped = [c for c in opens if int(c.get("carried") or 0) >= CARRY_LIMIT]
    tail = ""
    if carried:
        tail += (" Open calls carry one more week rather than quietly vanish: "
                 + ", ".join(str(c.get("id")) for c in carried) + ".")
    if dropped:
        tail += (" Dropped unresolved after a second week without the data to settle them: "
                 + ", ".join(str(c.get("id")) for c in dropped) + ".")
    out += [tally + lifetime + tail,
            "An open call is one the data could not settle, usually a dark engine or a settlement "
            "still carried as projected. It is never scored as a hit."]
    return out


def _calls_section(calls: list[dict], date: str) -> list[str]:
    if not calls:
        return ["The board is too thin this week to register a falsifiable call. That is a bad "
                "week for this letter and it gets said rather than padded with something "
                "unfalsifiable."]
    out = [f"Registered {_fmt(len(calls))} calls for the week of {date}. Each carries a stable ID, "
           "the number the desk expects, the date it resolves and the rule that decides it. Next "
           "Monday's issue opens by grading them, misses first."]
    out.append("\n".join(
        f"- **{c.get('id')}** · {_clean(c.get('claim'))} Expected: "
        f"{_clean(c.get('expected'))}. Resolves {c.get('resolve_by')}, "
        f"{_clean(c.get('rule'))}."
        for c in calls
    ))
    if len(calls) < CALL_MIN:
        out.append(f"Only {_fmt(len(calls))} calls cleared the bar this week; the rest of the "
                   "candidate list needed engines that are dark. Fewer honest calls beat a full "
                   "card of unfalsifiable ones.")
    return out


# ---------------------------------------------------------------------------
# 1 the week in one paragraph, and the week's single question
# ---------------------------------------------------------------------------
def _question(snap: dict, date: str, cal_rows: list[dict]) -> str:
    eng = snap.get("engines", {}) or {}
    nt = ((snap.get("deep", {}) or {}).get("turn", {}) or {}).get("next_turn") or {}
    fomc = [r for r in cal_rows if r.get("event") == "FOMC decision"]
    if fomc:
        return (f"what the FOMC does to the corridor on {fomc[0].get('date')}, and whether the "
                "runoff pace behind the reserve path changes with it")
    try:
        sev = int(nt.get("severity") or 0)
    except (TypeError, ValueError):
        sev = 0
    if nt.get("date") and _within(nt["date"], date, CALENDAR_HORIZON_D) and sev >= 3:
        band = nt.get("band_bp") or [None, None]
        return (f"whether the {str(nt.get('mode', 'calendar')).replace('_', ' ')} turn on "
                f"{nt['date']} prints inside {_signed(band[0], 1)} to {_signed(band[1], 1)}bp or "
                "goes through the top of the band")
    sd = eng.get("supplydesk", {}) or {}
    heavy = sd.get("heaviest_day") or {}
    try:
        heavy_net = float(heavy.get("net_new_cash_b"))
    except (TypeError, ValueError):
        heavy_net = 0.0
    if heavy.get("date") and heavy_net >= 50:
        return (f"whether the {_signed(heavy_net)}B of net new cash settling {heavy['date']} shows "
                "up in reserves or gets absorbed by the buffers")
    k = eng.get("kink", {}) or {}
    if k.get("ok") and k.get("distance_b") is not None and float(k["distance_b"]) < 0:
        return ("whether the spread starts answering to reserve changes now that the system is "
                f"${_fmt(abs(float(k['distance_b'])))}B through the fitted kink")
    rw = eng.get("runway", {}) or {}
    cross = ((rw.get("scenarios") or {}).get("base") or {}).get("crossing_date")
    if cross:
        return f"whether anything this week moves the base path's kink crossing off {cross}"
    comp = eng.get("composite", {}) or {}
    return (f"whether the composite holds its {(comp.get('regime') or 'current').lower()} reading "
            "with nothing dated standing in the way")


def _opening(snap: dict, date: str, cal_rows: list[dict], n_calls: int, question: str) -> list[str]:
    comp = snap.get("engines", {}).get("composite", {}) or {}
    v = comp.get("value")
    regime = (comp.get("regime") or "UNRATED").upper()
    tell = (snap.get("deep", {}) or {}).get("tell", {}) or {}
    tell_txt = ""
    if tell.get("ok") and tell.get("tell") is not None:
        p, m = tell.get("plumbing_pctl"), tell.get("market_pctl")
        tell_txt = f" The Tell reads {_signed(tell.get('tell'))}"
        if p is not None and m is not None:
            tell_txt += (f", plumbing at the {_ordinal(p)} percentile of its own history against "
                         f"the market's {_ordinal(m)}")
        tell_txt += "."
    cov = comp.get("coverage_pct")
    cov_txt = f", on {_fmt(cov)}% coverage" if cov is not None else ", coverage unpublished today"
    lead = _pick(date, "weekly-open", [
        "The week opens with the board at",
        "The desk starts the week with the composite at",
        "Monday's reading:",
    ])
    return [
        f"{lead} **{_fmt(v)} out of 100, {regime}**{cov_txt}.{tell_txt} There are "
        f"{_fmt(len(cal_rows))} dated items inside the next ten days and {_fmt(n_calls)} calls on "
        "the record below.",
        f"The single question this week is **{question}**. Everything under it is either a date, a "
        "number the desk expects, or a number the desk got wrong last week.",
    ]


# ---------------------------------------------------------------------------
# state. Two slots, exactly like the daily letter: `calls` is what the next
# issue will grade, `calls_prev` is what this issue graded, keyed by date, so a
# same-day rebuild grades the same set instead of finding its own morning's
# calls and marking them open.
# ---------------------------------------------------------------------------
def load_state(path: Path | None = None) -> dict:
    p = path or WEEKLY_STATE
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def _calls_baseline(state: dict, date: str) -> list[dict]:
    if state.get("date") == date:
        return state.get("calls_prev") or []
    return state.get("calls") or []


def _record_baseline(state: dict, date: str) -> dict:
    if state.get("date") == date:
        return state.get("record_prev") or dict(EMPTY_RECORD)
    return state.get("record") or dict(EMPTY_RECORD)


def _updated_state(date, issue_no, calls, graded, baseline, record_prev) -> dict:
    hits = sum(1 for c in graded if c["verdict"] == "hit")
    misses = sum(1 for c in graded if c["verdict"] == "miss")
    carried = [
        {k: v for k, v in c.items() if k not in ("verdict", "actual")}
        | {"carried": int(c.get("carried") or 0) + 1}
        for c in graded
        if c["verdict"] == "open" and int(c.get("carried") or 0) < CARRY_LIMIT
    ]
    return {
        "date": date,
        "issue": int(issue_no) if issue_no is not None else 0,
        "calls_prev": baseline,
        "calls": calls + carried,
        "record_prev": record_prev,
        "record": {
            "graded": int(record_prev.get("graded") or 0) + hits + misses,
            "hit": int(record_prev.get("hit") or 0) + hits,
            "miss": int(record_prev.get("miss") or 0) + misses,
        },
    }


# ---------------------------------------------------------------------------
# the issue
# ---------------------------------------------------------------------------
def _title_summary(snap, date, issue_no, calls, graded, cal_rows) -> tuple[str, str]:
    comp = snap.get("engines", {}).get("composite", {}) or {}
    v = comp.get("value")
    regime = (comp.get("regime") or "UNRATED").upper()
    n_ev, n_c = len(cal_rows), len(calls)
    hits = sum(1 for c in graded if c["verdict"] == "hit")
    resolved = sum(1 for c in graded if c["verdict"] in ("hit", "miss"))
    issue_txt = _fmt(issue_no) if issue_no is not None else "?"

    options = [
        f"The Week Ahead {issue_txt}: {_fmt(n_ev)} dated events and {_fmt(n_c)} calls on the record",
        f"The Week Ahead {issue_txt}: {regime.lower()} into a week with {_fmt(n_ev)} dated events",
        f"The Week Ahead {issue_txt}: {_fmt(n_c)} pre-registered calls for the week of {date}",
    ]
    if resolved:
        options.append(
            f"The Week Ahead {issue_txt}: last week {_fmt(hits)} of {_fmt(resolved)}, "
            f"{_fmt(n_c)} new calls"
        )
    title = _pick(date, "weekly-title", options)

    summary = (
        f"Issue {issue_txt} of the Monday letter. The composite reads {_fmt(v)}, regime {regime}. "
        f"{_fmt(n_c)} pre-registered calls for the week and {_fmt(n_ev)} dated items on the "
        "calendar."
    )
    if resolved:
        summary += f" Last week's calls graded {_fmt(hits)} of {_fmt(resolved)}, misses first."
    else:
        summary += " The first issue, so there is nothing to grade yet."
    return title, summary


def _continuation(snap: dict, date: str, calls: list[dict], graded: list[dict],
                  record: dict) -> str:
    parts = ["## The week ahead, continuation", ""]

    parts += ["### The call ledger", ""]
    if record.get("graded"):
        parts += [
            f"The desk has resolved {_fmt(record.get('graded'))} calls across the run of this "
            f"letter and hit {_fmt(record.get('hit'))} of them, missing {_fmt(record.get('miss'))}. "
            "The ledger only counts calls the data actually settled; open ones are carried, not "
            "quietly counted as wins.", "",
        ]
    else:
        parts += ["The ledger opens with this issue. Nothing has resolved yet, so there is no hit "
                  "rate to quote and the desk does not quote one.", ""]
    if calls:
        parts += ["| id | kind | resolves | grading rule |", "|---|---|---|---|"]
        for c in calls:
            parts.append(f"| {c.get('id')} | {c.get('kind')} | {c.get('resolve_by')} "
                         f"| {_cell(c.get('rule'))} |")
        parts.append("")

    rw = snap.get("engines", {}).get("runway", {}) or {}
    if rw.get("ok") and rw.get("assumptions"):
        a = rw["assumptions"]
        parts += ["### Reserve path assumptions, published beside the path", "",
                  f"Start {_fmt(a.get('start_reserves_b'))}B on {a.get('start_date')}, trailing "
                  f"drift {_signed(a.get('trailing_drift_b_per_week'), 1)}B a week over "
                  f"{_fmt(a.get('drift_window_weeks'))} weeks, runoff "
                  f"${_fmt(a.get('qt_pace_b_per_month'))}B a month, TGA "
                  f"${_fmt(a.get('tga_now_b'))}B now against a median of "
                  f"${_fmt(a.get('tga_median_b'))}B and a p75 of ${_fmt(a.get('tga_p75_b'))}B, ON "
                  f"RRP ${_fmt(a.get('rrp_now_b'), 1)}B, settlements "
                  f"${_fmt(a.get('settlements_gross_b'))}B gross counted at "
                  f"{float(a.get('settlement_passthrough') or 0):.0%} passthrough.", ""]
        for c in (rw.get("caveats") or [])[:4]:
            parts.append(f"- {_clean(c)}")
        parts.append("")

    sd = snap.get("engines", {}).get("supplydesk", {}) or {}
    if sd.get("ok") and sd.get("caveats"):
        parts += ["### What the supply table does not know", ""]
        for c in sd["caveats"][:5]:
            parts.append(f"- {_clean(c)}")
        parts.append("")

    if graded:
        opens = [c for c in graded if c["verdict"] == "open"]
        if opens:
            parts += ["### Still open", ""]
            for c in opens:
                parts.append(f"- **{c.get('id')}** · {_clean(c.get('claim'))} Status: "
                             f"{_clean(c.get('actual'))}.")
            parts.append("")

    parts += ["The calls above were written before the week ran and are stored in the letter's own "
              "state file, so next Monday's issue grades exactly this list and not a convenient "
              "subset of it. The board recomputes six times a day; this issue freezes one Monday "
              "reading of it. Free public data with native lags. Not investment advice."]
    return "\n".join(parts)


def build_weekly(snap: dict, date: str | None = None, state: dict | None = None,
                 issue_no: int | None = None) -> dict:
    comp = snap.get("engines", {}).get("composite", {}) or {}
    if comp.get("value") is None or not comp.get("regime"):
        raise SystemExit("refusing to write a weekly issue without a live composite "
                         "(no board, no letter)")
    date = date or (snap.get("generated_at") or datetime.now(timezone.utc).isoformat())[:10]
    state = state or {}
    baseline = _calls_baseline(state, date)
    record_prev = _record_baseline(state, date)

    graded = _grade_all(baseline, snap)
    calls = _make_calls(snap, date, issue_no)
    cal_rows = _calendar_rows(snap, date)
    question = _question(snap, date, cal_rows)

    title, summary = _title_summary(snap, date, issue_no, calls, graded, cal_rows)

    paras: list[str] = []
    if issue_no is not None:
        paras.append(f"*Issue {_fmt(issue_no)} · the week of {date} · the sections run in the same "
                     "order every week, and section 6 grades what section 5 said last time.*")
    paras += ["## 1 · The week in one paragraph"] + _opening(snap, date, cal_rows, len(calls), question)
    paras += ["## 2 · The calendar"] + _calendar_section(snap, date, cal_rows)
    paras += ["## 3 · Supply"] + _supply_section(snap)
    paras += ["## 4 · Reserves"] + _reserves_section(snap)
    paras += ["## 5 · Pre-registered calls"] + _calls_section(calls, date)
    paras += ["## 6 · Last week's calls, graded"] + _graded_section(graded, record_prev)
    paras += ["## 7 · What would change the desk's mind this week"]
    paras.append("The falsifier ledger travels with the regime and the IDs are stable, so a "
                 "regular can watch the distance close instead of rereading a static sentence.")
    paras.append("\n".join(f"- **{fid}** · {_clean(text)}" for fid, text in _falsifiers(snap)))

    free_md = "\n\n".join(p for p in paras if p)
    desk_md = _continuation(snap, date, calls, graded,
                            _updated_state(date, issue_no, calls, graded, baseline,
                                           record_prev)["record"])

    issues = lint_letter(title, summary, free_md, desk_md)
    if issues:
        raise SystemExit("weekly issue failed lint: " + "; ".join(issues))

    return {
        "slug": f"{date}-week-ahead",
        "title": title,
        "date": date,
        "tag": TAG,
        "summary": summary,
        "free_md": free_md,
        "desk_md": desk_md,
        "state": _updated_state(date, issue_no, calls, graded, baseline, record_prev),
    }


# ---------------------------------------------------------------------------
# filesystem + index. Same dirs and same index as the daily letter, so the
# weekly issue lands in the existing archive instead of a parallel one; only
# the state file is separate.
# ---------------------------------------------------------------------------
def write_weekly(d: dict, repo_root: Path | None = None) -> list[str]:
    root = repo_root or REPO_ROOT
    free_dir = root / "frontend" / "public" / "dispatches"
    paid_dir = root / "backend" / "seiche" / "dispatches"
    index = free_dir / "index.json"
    free_dir.mkdir(parents=True, exist_ok=True)
    paid_dir.mkdir(parents=True, exist_ok=True)

    free_path = free_dir / f"{d['slug']}.md"
    free_path.write_text(d["free_md"] + (f"\n\n{MARKER}\n" if d["desk_md"] else "\n"))
    written = [str(free_path)]

    if d["desk_md"]:
        desk_path = paid_dir / f"{d['slug']}.desk.md"
        desk_path.write_text(d["desk_md"] + "\n")
        written.append(str(desk_path))

    if d.get("state"):
        state_path = paid_dir / "weekly_state.json"
        state_path.write_text(json.dumps(d["state"], indent=2) + "\n")
        written.append(str(state_path))

    entries = []
    if index.exists():
        entries = json.loads(index.read_text())
    entries = [e for e in entries if e.get("slug") != d["slug"]]
    entries.insert(0, {k: d[k] for k in ("slug", "title", "date", "tag", "summary")})
    entries.sort(key=lambda e: e.get("date", ""), reverse=True)
    index.write_text(json.dumps(entries, indent=2) + "\n")
    written.append(str(index))
    return written


def issue_number(index_path: Path, slug: str) -> int:
    """The issue number counts weekly issues only: the daily letter has its own
    numbering and mixing the two would make both meaningless."""
    try:
        entries = json.loads(index_path.read_text())
    except (OSError, ValueError):
        return 1
    return 1 + sum(1 for e in entries
                   if str(e.get("slug", "")).endswith("-week-ahead") and e.get("slug") != slug)


# ---------------------------------------------------------------------------
# Telegram announcement
# ---------------------------------------------------------------------------
def build_telegram_digest(d: dict) -> str:
    calls = (d.get("state") or {}).get("calls") or []
    lines = [f"SEICHE · The Week Ahead · {d['date']}", "", d["title"], "", d["summary"]]
    if calls:
        lines += ["", "This week's calls:"]
        for c in calls[:CALL_MAX]:
            lines.append(f"{c.get('id')}: {_clean(c.get('claim'))}")
    lines += ["", f"Full issue: https://seiche.info/#dispatches/{d['slug']}"]
    return "\n".join(lines)


def announce_telegram(d: dict) -> None:
    """Send the digest. Fail loud: an explicit announce with missing or refused
    credentials is an error, not a silent skip."""
    import os
    import urllib.request

    token = os.environ.get("SEICHE_TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("SEICHE_TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise SystemExit("announce needs SEICHE_TELEGRAM_BOT_TOKEN and SEICHE_TELEGRAM_CHAT_ID")
    body = json.dumps({"chat_id": chat_id, "text": build_telegram_digest(d),
                       "disable_web_page_preview": False}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                 data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read().decode())
    if not resp.get("ok"):
        raise SystemExit(f"telegram refused the message: {resp}")
    print(f"announced on telegram (message_id {resp['result']['message_id']})")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _check_board_freshness(snap: dict, date: str, allow_stale: bool) -> None:
    """The weekly is the artifact that GRADES the calls: scoring last Monday's
    pre-registered numbers against a stale board grades them against the wrong
    week and corrupts the running record — the same box-staleness failure the
    daily letter already refuses (a61ea7e). A board more than one day older
    than the issue date (or with no generated_at at all) is refused unless
    --allow-stale explicitly overrides, and the override still shouts."""
    gen = str(snap.get("generated_at") or "")[:10]
    try:
        age_d = (datetime.strptime(date, "%Y-%m-%d")
                 - datetime.strptime(gen, "%Y-%m-%d")).days
    except ValueError:
        age_d = None
    if age_d is not None and age_d <= 1:
        return
    msg = (f"board generated_at={gen or 'missing'} is stale against issue date {date}; "
           "grading pre-registered calls on an old board corrupts the record")
    if not allow_stale:
        raise SystemExit(f"refusing to write: {msg} (pass --allow-stale to override)")
    print(f"WARNING: {msg} — proceeding because --allow-stale was passed")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write this week's Week Ahead from the live board.")
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--snapshot", default=None,
                    help="read the board from a JSON file instead of the API. The issue is a "
                         "pure function of a board snapshot, so CI builds one itself rather "
                         "than depending on the box being both up and current — the weekly "
                         "GRADES the previous issue's calls, so a stale box would grade them "
                         "against the wrong week.")
    ap.add_argument("--allow-stale", action="store_true",
                    help="write anyway from a board older than the issue date (loudly)")
    ap.add_argument("--date", default=None, help="override the issue date (YYYY-MM-DD)")
    ap.add_argument("--force", action="store_true", help="rewrite even if this week's issue exists")
    ap.add_argument("--announce", action="store_true",
                    help="after writing, send the Telegram digest "
                         "(needs SEICHE_TELEGRAM_BOT_TOKEN and SEICHE_TELEGRAM_CHAT_ID)")
    ap.add_argument("--announce-only", action="store_true",
                    help="skip writing files; just build the issue and send the digest")
    args = ap.parse_args(argv)

    # The issue date comes from the clock (or --date), never from the board,
    # so a stale box cannot misdate the issue — it can only fail freshness.
    date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = f"{date}-week-ahead"

    if args.snapshot:
        snap = json.loads(Path(args.snapshot).read_text())
        print(f"board read from {args.snapshot} (generated {snap.get('generated_at')})")
    else:
        snap = _get_json(f"{args.api}/api/overview")
    _check_board_freshness(snap, date, args.allow_stale)
    d = build_weekly(snap, date=date, state=load_state(),
                     issue_no=issue_number(INDEX, slug))

    if args.announce_only:
        announce_telegram(d)
        return 0

    if INDEX.exists() and not args.force:
        if any(e.get("slug") == slug for e in json.loads(INDEX.read_text())):
            print(f"weekly issue {slug} already published, nothing to do")
            return 0

    for p in write_weekly(d):
        print(f"wrote {p}")
    print(f"week ahead ready: {d['slug']}, {d['title']}")
    if args.announce:
        announce_telegram(d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
