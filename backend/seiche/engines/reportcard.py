"""Auction Report Card: the same day event study behind every auction grade.

auctions.py already scores each auction against its own tenor's history, bid
to cover, the primary dealer takedown, the indirect share, and carries an EWMA
digestion index across them. That answers "was the demand normal". It does not
answer the question a funding desk asks next, which is what the plumbing did
about it. This engine is that second layer, and it is deliberately not a
re-implementation of the first.

For each recent auction it writes one card: the identity (auction date, tenor,
CUSIP when the frame carries it, and a stable slug so the card has a
permalink), the grades auctions.py already computed restated as a plain letter
A to F off the composite z, and an event study of the funding window that
follows. The window is measured on the SOFR minus IORB spread at T+0, T+1, T+3
and T+5 prints of the funding tape against the last print before the auction,
on any SRF take-up inside that window, and on the reserve change across the
settlement week. A window the tape has not finished reports status "open" with
null marks, never a zero it did not observe. An observed zero and a missing
mark are different facts and the card keeps them different.

The point is the divergence. Free sources publish the auction statistics on
the afternoon of the auction and then move on. Nobody publishes, auction by
auction and on the record, what funding did over the five days that followed:
the market said the auction was fine, here is what the plumbing did. Because
each card is dated and slugged it is citable, and the cards compound into a
demand fatigue dataset, the trailing indirect share by tenor, so soft demand
that repeats is visible instead of re-argued every quarter.

Narrative and evidence only. Nothing here feeds the composite; the auction
signal already reaches the score through the digestion index.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from seiche.dispatch_daily import lint_letter

HORIZONS = (0, 1, 3, 5)      # business days after the auction, counted on the funding tape
DEFAULT_N = 12               # cards per run, newest first
PRESSURE_BP = 3.0            # widening inside the window that counts as funding pressure
SRF_NOTABLE_B = 1.0          # take-up above this inside the window is worth the letter
FATIGUE_MIN = 4              # same tenor auctions needed before a trend is stated
FATIGUE_WINDOW = 8           # auctions in the trailing indirect share trend
FATIGUE_SLOPE_PP = 0.25      # pp per auction that turns "steady" into "softening"
UNIT_THRESHOLD = 50_000.0    # reserves near 3,200 in $B and 3,200,000 in $M; this separates cleanly

# auctions.py's own weights: score = 0.5*(-btc_z) + 0.3*(pd_share_z) + 0.2*(-indirect_z).
# It publishes btc_z and pd_share_z on every recent row but folds the indirect
# leg into the composite, so the card recovers that third grade from the stated
# identity rather than recomputing a z that engine already owns.
W_BTC, W_PD, W_IND = 0.5, 0.3, 0.2

# Composite z bands, low is strong demand because the composite is an
# indigestion score. Stated here and restated in `method`.
GRADE_BANDS = ((-1.0, "A"), (-0.35, "B"), (0.35, "C"), (1.0, "D"))

_TERM_UNITS = ((r"-?\s*year", "y"), (r"-?\s*month", "m"),
               (r"-?\s*week", "w"), (r"-?\s*day", "d"))


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _cell(row, key: str):
    """One frame cell, with NaN read as absent. A missing CUSIP must reach the
    card as null, never as the string 'nan'."""
    if row is None:
        return None
    v = row.get(key)
    if v is None:
        return None
    if not isinstance(v, str) and pd.isna(v):
        return None
    return v


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


def _to_b(x) -> float | None:
    """Treasury reports accepted amounts in dollars; the board talks in $B."""
    v = _num(x)
    if v is None:
        return None
    return round(v / 1e9, 1) if abs(v) > 1e6 else round(v, 1)


# The two characters the house rule bans, written as escapes so this file
# contains neither of them even as data.
_EM, _EN = "\u2014", "\u2013"


def _scrub(s) -> str:
    """House copy rules applied to anything that reaches the letter. The lint
    below is the gate; this is the thing that keeps the gate from firing."""
    return (str(s).replace(f" {_EM} ", ", ").replace(_EM, ", ")
            .replace(f" {_EN} ", ", ").replace(_EN, "-"))


def _short_term(term) -> str:
    """'10-Year' -> '10y', '9-Year 10-Month' -> '9y10m', '42-Day' -> '42d'."""
    t = str(term or "").strip()
    if not t:
        return ""
    for pattern, sym in _TERM_UNITS:
        t = re.sub(pattern, sym, t, flags=re.IGNORECASE)
    return re.sub(r"[^0-9A-Za-z]+", "", t).lower()


def _kind(security_type) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(security_type or "").strip().lower())


def _split_tenor(tenor) -> tuple[str, str]:
    """auctions.py joins type and term with a space; take it back apart."""
    parts = str(tenor or "").strip().split(" ", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return (parts[0] if parts else ""), ""


def _is_coupon(tenor) -> int:
    """1 for notes, bonds, TIPS and FRNs; 0 for bills."""
    return 0 if re.search(r"bill", str(tenor or ""), flags=re.IGNORECASE) else 1


def _slug(date_iso: str, security_type, security_term) -> str:
    term, kind = _short_term(security_term), _kind(security_type) or "auction"
    return f"{date_iso}-{term}-{kind}" if term else f"{date_iso}-{kind}"


def _display(security_type, security_term, tenor) -> str:
    """The way a desk says it out loud: '10y note', '4w bill'."""
    term, kind = _short_term(security_term), _kind(security_type)
    if term and kind:
        return f"{term} {kind}"
    return _scrub(tenor or "auction").strip().lower() or "auction"


def _grade(z: float | None) -> str | None:
    if z is None:
        return None
    for edge, letter in GRADE_BANDS:
        if z <= edge:
            return letter
    return "F"


def _series(obj) -> pd.Series:
    """Any caller series to a clean, sorted, datetime indexed float series."""
    if obj is None:
        return pd.Series(dtype=float)
    s = pd.to_numeric(pd.Series(obj), errors="coerce").dropna()
    if s.empty:
        return pd.Series(dtype=float)
    idx = pd.to_datetime(s.index, errors="coerce")
    s = s[pd.notna(idx)]
    s.index = pd.DatetimeIndex(idx[pd.notna(idx)])
    return s.sort_index()


def _srf_series(srf_daily) -> pd.Series:
    """Accepts the NY Fed ops frame or the accepted column already extracted."""
    if isinstance(srf_daily, pd.DataFrame):
        if srf_daily.empty or "accepted" not in srf_daily.columns:
            return pd.Series(dtype=float)
        return _series(srf_daily["accepted"])
    return _series(srf_daily)


def _reserves_b(reserves_weekly) -> pd.Series:
    s = _series(reserves_weekly)
    if s.empty:
        return s
    return s / 1000.0 if float(s.abs().max()) > UNIT_THRESHOLD else s


def _bp(v: float) -> str:
    return f"{abs(v):.1f}bp"


# ---------------------------------------------------------------------------
# the frame side: identity, shares and the fatigue series
# ---------------------------------------------------------------------------
def _prep_frame(frame) -> pd.DataFrame | None:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    need = ("auction_date", "security_type", "security_term")
    if any(c not in frame.columns for c in need):
        return None
    df = frame.copy()
    df["auction_date"] = pd.to_datetime(df["auction_date"], errors="coerce")
    df = df.dropna(subset=["auction_date"])
    if df.empty:
        return None
    for col in ("total_accepted", "offering_amt", "indirect_bidder_accepted",
                "primary_dealer_accepted", "direct_bidder_accepted",
                "bid_to_cover_ratio", "high_yield"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["security_type"] = df["security_type"].astype(str).str.strip()
    df["security_term"] = df["security_term"].astype(str).str.strip()
    df["tenor"] = df["security_type"] + " " + df["security_term"]
    if "total_accepted" in df.columns and "indirect_bidder_accepted" in df.columns:
        df["indirect_share"] = (df["indirect_bidder_accepted"]
                                / df["total_accepted"].replace(0, np.nan))
    return df.sort_values("auction_date")


def _fatigue_rows(fr: pd.DataFrame | None, tenors: list[str]) -> list[dict]:
    """Trailing indirect share by tenor. One soft auction is a story; the same
    tenor softening across a run of auctions is the dataset."""
    rows: list[dict] = []
    if fr is None or "indirect_share" not in fr.columns:
        return rows
    for tenor in sorted(set(tenors)):
        g = fr[fr["tenor"] == tenor].dropna(subset=["indirect_share"]).tail(FATIGUE_WINDOW)
        if len(g) < FATIGUE_MIN:
            continue
        y = g["indirect_share"].to_numpy(dtype=float) * 100.0
        slope = float(np.polyfit(np.arange(len(y), dtype=float), y, 1)[0])
        latest, prior_mean = float(y[-1]), float(y[:-1].mean())
        if slope <= -FATIGUE_SLOPE_PP and latest < prior_mean:
            verdict = "softening"
        elif slope >= FATIGUE_SLOPE_PP and latest > prior_mean:
            verdict = "firming"
        else:
            verdict = "steady"
        stype, term = _split_tenor(tenor)
        rows.append({
            "tenor": tenor,
            "display": _display(stype, term, tenor),
            "auctions": int(len(g)),
            "latest_indirect_pct": round(latest, 1),
            "prior_mean_indirect_pct": round(prior_mean, 1),
            "trend_pp_per_auction": round(slope, 2),
            "verdict": verdict,
            "series": [[d.date().isoformat(), round(float(v), 1)]
                       for d, v in zip(g["auction_date"], y)],
        })
    return rows


# ---------------------------------------------------------------------------
# the event study
# ---------------------------------------------------------------------------
def _spread_window(spread: pd.Series, auction_ts: pd.Timestamp) -> dict:
    idx = spread.index
    pos = int(idx.searchsorted(auction_ts, side="left"))
    if pos >= len(idx):
        return {"status": "open", "reason": "the funding tape has not reached this auction",
                "baseline_bp": None, "baseline_date": None,
                "horizons": [{"h": f"T+{h}", "date": None, "spread_bp": None,
                              "change_bp": None, "status": "open"} for h in HORIZONS],
                "marks_in": 0, "peak_change_bp": None, "change_t5_bp": None}
    if pos == 0:
        return {"status": "no_data", "reason": "no funding print before this auction",
                "baseline_bp": None, "baseline_date": None,
                "horizons": [{"h": f"T+{h}", "date": None, "spread_bp": None,
                              "change_bp": None, "status": "no_data"} for h in HORIZONS],
                "marks_in": 0, "peak_change_bp": None, "change_t5_bp": None}

    base = float(spread.iloc[pos - 1])
    rows, changes = [], []
    for h in HORIZONS:
        j = pos + h
        if j < len(idx):
            v = float(spread.iloc[j])
            chg = round(v - base, 2)
            changes.append(chg)
            rows.append({"h": f"T+{h}", "date": idx[j].date().isoformat(),
                         "spread_bp": round(v, 2), "change_bp": chg, "status": "printed"})
        else:
            rows.append({"h": f"T+{h}", "date": None, "spread_bp": None,
                         "change_bp": None, "status": "open"})
    complete = all(r["status"] == "printed" for r in rows)
    peak = max(changes, key=abs) if changes else None
    return {
        "status": "complete" if complete else "open",
        "baseline_bp": round(base, 2),
        "baseline_date": idx[pos - 1].date().isoformat(),
        "horizons": rows,
        "marks_in": len(changes),
        "peak_change_bp": peak,
        # the widest the spread got inside the window, which is the number the
        # pressure read keys on: a late reversal must not erase what happened
        "max_widening_bp": max(changes) if changes else None,
        "change_t5_bp": rows[-1]["change_bp"],
    }


def _srf_window(srf: pd.Series, spread: pd.Series, auction_ts: pd.Timestamp) -> dict:
    if srf.empty:
        return {"status": "absent", "max_b": None, "sum_b": None, "days_with_takeup": None}
    idx = spread.index
    pos = int(idx.searchsorted(auction_ts, side="left"))
    if pos >= len(idx):
        return {"status": "open", "max_b": None, "sum_b": None, "days_with_takeup": None}
    start = idx[pos]
    end_pos = pos + HORIZONS[-1]
    end = idx[end_pos] if end_pos < len(idx) else None
    covered = end is not None and srf.index[-1] >= end
    stop = end if end is not None else srf.index[-1]
    w = srf[(srf.index >= start) & (srf.index <= stop)]
    if w.empty:
        # No operation rows inside the window is a fact only if the feed has
        # already run past it; otherwise the window is simply still open.
        return {"status": "complete" if covered else "open",
                "max_b": 0.0 if covered else None,
                "sum_b": 0.0 if covered else None,
                "days_with_takeup": 0 if covered else None,
                "window": [start.date().isoformat(),
                           (end or srf.index[-1]).date().isoformat()]}
    return {
        "status": "complete" if covered else "open",
        "max_b": round(float(w.max()), 2),
        "sum_b": round(float(w.sum()), 2),
        "days_with_takeup": int((w > SRF_NOTABLE_B).sum()),
        "window": [start.date().isoformat(), stop.date().isoformat()],
    }


def _reserve_week(res_b: pd.Series, settle_ts: pd.Timestamp) -> dict:
    if res_b.empty:
        return {"status": "absent", "week_ending": None, "level_b": None, "change_b": None}
    pos = int(res_b.index.searchsorted(settle_ts, side="left"))
    if pos >= len(res_b):
        return {"status": "open", "week_ending": None, "level_b": None, "change_b": None}
    if pos == 0:
        return {"status": "no_data", "week_ending": res_b.index[0].date().isoformat(),
                "level_b": round(float(res_b.iloc[0]), 1), "change_b": None}
    return {
        "status": "complete",
        "week_ending": res_b.index[pos].date().isoformat(),
        "level_b": round(float(res_b.iloc[pos]), 1),
        "change_b": round(float(res_b.iloc[pos] - res_b.iloc[pos - 1]), 1),
    }


# ---------------------------------------------------------------------------
# prose
# ---------------------------------------------------------------------------
def _grade_clause(disp: str, date_iso: str, grade: str | None,
                  btc: float | None, pd_share: float | None) -> str:
    head = f"The {disp} on {date_iso} graded {grade}" if grade else f"The {disp} on {date_iso}"
    bits = []
    if btc is not None:
        bits.append(f"bid to cover {btc:.2f}")
    if pd_share is not None:
        bits.append(f"dealers taking {pd_share * 100:.0f} percent")
    return head + (", " + " with ".join(bits) if bits else "")


def _plumbing_clause(es: dict, srf: dict, res: dict, grade: str | None) -> str:
    if es.get("status") != "complete":
        marks = es.get("marks_in") or 0
        if es.get("status") == "no_data":
            return (", and the funding tape does not reach back far enough to score "
                    "the window behind it")
        return (f", and the funding window is still open at {marks} of {len(HORIZONS)} marks, "
                "so the plumbing is not scored yet")

    t5 = es.get("change_t5_bp")
    widest = es.get("max_widening_bp")
    srf_max = srf.get("max_b") if srf.get("status") == "complete" else None
    res_chg = res.get("change_b") if res.get("status") == "complete" else None

    pressure = (
        (widest is not None and widest >= PRESSURE_BP)
        or (srf_max is not None and srf_max >= SRF_NOTABLE_B)
    )
    soft = grade in ("D", "F")
    if soft and pressure:
        lead = ", and the plumbing confirmed it"
    elif soft:
        lead = ", and the plumbing shrugged"
    elif pressure:
        lead = ", the screens called it fine and the plumbing disagreed"
    else:
        lead = ", and the plumbing agreed"

    if t5 is None:
        move = "SOFR minus IORB left no mark at T plus 5"
    elif abs(t5) < 0.05:
        move = "SOFR minus IORB sat flat through T plus 5"
    else:
        move = (f"SOFR minus IORB {'widened' if t5 > 0 else 'narrowed'} "
                f"{_bp(t5)} by T plus 5")
    out = f"{lead}: {move}"

    if srf_max is not None:
        out += (f", SRF take-up peaked at ${srf_max:.1f}B inside the window"
                if srf_max >= SRF_NOTABLE_B else ", with no SRF take-up inside the window")
    if res_chg is not None:
        out += (f" and reserves {'fell' if res_chg < 0 else 'rose'} "
                f"${abs(res_chg):,.0f}B across the settlement week")
    return out


def _card_verdict(card: dict) -> str:
    es = card["event_study"]
    g = card["grades"]
    text = _grade_clause(card["display"], card["auction_date"], g.get("grade"),
                         g.get("bid_to_cover"), g.get("pd_share"))
    text += _plumbing_clause(es, es.get("srf") or {}, es.get("reserves") or {}, g.get("grade"))
    return _scrub(text + ".")


def letter_line(cards) -> str:
    """One sentence about the most recent auction, for section 2 of the letter.

    Takes the cards list or the whole report_cards payload. Returns an empty
    string when there is nothing on the record, so the letter prints its own
    dark-section line rather than a sentence about nothing.
    """
    if isinstance(cards, dict):
        cards = cards.get("cards") or []
    if not cards:
        return ""
    c = cards[0]
    g = c.get("grades") or {}
    es = c.get("event_study") or {}
    grade, z = g.get("grade"), g.get("composite_z")
    line = f"The last auction on the board is the {c.get('display')} of {c.get('auction_date')}"
    if grade:
        line += f", graded {grade}"
        if z is not None:
            line += f" on a composite z of {z:+.2f}"
    if es.get("status") == "complete":
        t5 = es.get("change_t5_bp")
        if t5 is None or abs(t5) < 0.05:
            line += ", and SOFR minus IORB was flat over the five business days that followed"
        else:
            line += (f", and over the five business days that followed SOFR minus IORB "
                     f"{'widened' if t5 > 0 else 'narrowed'} {_bp(t5)}")
        srf = es.get("srf") or {}
        srf_max = srf.get("max_b") if srf.get("status") == "complete" else None
        if srf_max is not None:
            line += (f" with SRF take-up peaking at ${srf_max:.1f}B"
                     if srf_max >= SRF_NOTABLE_B else " with no SRF take-up")
    elif es.get("status") == "open":
        line += (f", and its funding window is still open at {es.get('marks_in') or 0} of "
                 f"{len(HORIZONS)} marks, so the board is not scoring the plumbing yet")
    else:
        line += ", and the funding tape does not cover the window behind it"
    fat = c.get("fatigue") or {}
    if fat.get("verdict") in ("softening", "firming"):
        line += f", and the trailing indirect share for that tenor is {fat['verdict']}"
    return _scrub(line + ".")


# ---------------------------------------------------------------------------
# the engine
# ---------------------------------------------------------------------------
def report_cards(
    auctions_payload: dict,                       # the auctions.py result (it owns the grades)
    spread_bp_daily: pd.Series,                   # SOFR - IORB, bp, daily
    srf_daily,                                    # SRF accepted $B daily, Series or the ops frame
    reserves_weekly: pd.Series,                   # WRESBAL, $M (or $B), weekly Wed
    n: int = DEFAULT_N,
    auctions_frame: pd.DataFrame | None = None,   # the raw auction frame: CUSIP, size, shares
) -> dict:
    if not isinstance(auctions_payload, dict) or not auctions_payload.get("ok"):
        reason = (auctions_payload or {}).get("reason") if isinstance(auctions_payload, dict) else None
        return {"ok": False, "reason": f"auction digestion unavailable: {reason or 'no payload'}"}
    recent = auctions_payload.get("recent_auctions") or []
    if not recent:
        return {"ok": False, "reason": "no scored auctions to grade"}

    spread = _series(spread_bp_daily)
    if spread.empty:
        return {"ok": False, "reason": "no SOFR-IORB history for the event study"}
    srf = _srf_series(srf_daily)
    res_b = _reserves_b(reserves_weekly)

    fr = _prep_frame(auctions_frame)
    lookup: dict[tuple[str, str], pd.Series] = {}
    if fr is not None:
        for _, r in fr.iterrows():
            lookup[(r["auction_date"].date().isoformat(), str(r["tenor"]).strip())] = r

    # Newest first, and on a day with several auctions the coupon outranks the
    # bill: the coupon is the one carrying duration into dealer balance sheets,
    # which is the same reason the digestion engine weights bills lighter.
    ordered = sorted(
        (r for r in recent if r.get("date")),
        key=lambda r: (str(r.get("date")), _is_coupon(r.get("tenor"))),
        reverse=True,
    )[: max(1, int(n))]

    cards: list[dict] = []
    slugs: set[str] = set()
    for row in ordered:
        date_iso = str(row.get("date"))
        tenor = str(row.get("tenor") or "").strip()
        src = lookup.get((date_iso, tenor))
        stype = str(_cell(src, "security_type") or "").strip()
        term = str(_cell(src, "security_term") or "").strip()
        if not stype and not term:
            stype, term = _split_tenor(tenor)

        btc_z, pd_z = _num(row.get("btc_z")), _num(row.get("pd_share_z"))
        score = _num(row.get("score"))
        ind_z = None
        if None not in (btc_z, pd_z, score):
            # score = W_BTC*(-btc_z) + W_PD*pd_z + W_IND*(-ind_z), solved for ind_z
            ind_z = round((W_PD * pd_z - W_BTC * btc_z - score) / W_IND, 2)

        cusip = _cell(src, "cusip")
        cusip = str(cusip).strip() if cusip is not None else None
        slug = _slug(date_iso, stype, term)
        if slug in slugs:
            slug = f"{slug}-{cusip[-4:].lower()}" if cusip else f"{slug}-{len(slugs) + 1}"
        slugs.add(slug)

        auction_ts = pd.Timestamp(date_iso)
        settle_ts, issue_iso = auction_ts, None
        issue = pd.to_datetime(_cell(src, "issue_date"), errors="coerce")
        if pd.notna(issue):
            settle_ts, issue_iso = issue, issue.date().isoformat()

        es = _spread_window(spread, auction_ts)
        es["srf"] = _srf_window(srf, spread, auction_ts)
        es["reserves"] = _reserve_week(res_b, settle_ts)

        ind_share = _num(_cell(src, "indirect_share"))
        card = {
            "slug": slug,
            "permalink": f"/auction/{slug}",
            "auction_date": date_iso,
            "issue_date": issue_iso,
            "tenor": tenor,
            "display": _display(stype, term, tenor),
            "security_type": stype or None,
            "security_term": term or None,
            "cusip": cusip,
            "size_b": _to_b(_cell(src, "total_accepted")),
            "grades": {
                "bid_to_cover": _num(row.get("btc")),
                "btc_z": btc_z,
                "pd_share": _num(row.get("pd_share")),
                "pd_share_z": pd_z,
                "indirect_share": round(ind_share, 4) if ind_share is not None else None,
                "indirect_z": ind_z,
                "composite_z": score,
                "grade": _grade(score),
            },
            "event_study": es,
        }
        cards.append(card)

    fatigue = _fatigue_rows(fr, [c["tenor"] for c in cards])
    by_tenor = {r["tenor"]: r for r in fatigue}
    for c in cards:
        f = by_tenor.get(c["tenor"])
        c["fatigue"] = ({"verdict": f["verdict"],
                         "trend_pp_per_auction": f["trend_pp_per_auction"],
                         "latest_indirect_pct": f["latest_indirect_pct"]} if f else None)
        c["verdict"] = _card_verdict(c)

    line = letter_line(cards)

    caveats = [
        "T+0 is the first funding print on or after the auction date, and the horizons are "
        "counted on the funding tape's own trading days, so a holiday inside the window "
        "moves T+5 in calendar time",
        "coupon settlement lands after the auction, so the spread leg reads the auction date "
        "and the reserve leg reads the settlement week instead of pretending they are the same day",
        "an event study is association, not causation: a five day window around an auction also "
        "contains month end, tax dates, bill paydowns and the Fed's own operations",
        "the indirect z is recovered from the digestion engine's published composite and its "
        "stated weights rather than recomputed, so it carries that engine's two decimal rounding",
        "nothing here feeds the composite; the auction signal already reaches the score through "
        "the digestion index",
    ]
    if fr is None:
        caveats.append(
            "no auction frame supplied: CUSIPs, sizes, indirect shares and the demand fatigue "
            "series are unavailable and the cards carry the grades and the event study only"
        )

    # The lint is the same publish-blocking gate the daily letter runs. An
    # engine must not take the board down over a sentence, so anything that
    # fails is withheld and the withholding is stated instead of shipped.
    blocked = lint_letter(line, *(c["verdict"] for c in cards))
    if blocked:
        line = ""
        for c in cards:
            c["verdict"] = ""
        caveats.append(
            "generated prose failed the house copy lint and was withheld: " + "; ".join(blocked)
        )

    return {
        "ok": True,
        "asof": cards[0]["auction_date"],
        "funding_asof": spread.index[-1].date().isoformat(),
        "n_cards": len(cards),
        "window_bd": list(HORIZONS),
        "cards": cards,
        "demand_fatigue": fatigue,
        "letter_line": line,
        "caveats": caveats,
        "method": (
            "one card per recent auction from the digestion engine's own scored rows; letter "
            "grade off its composite z (A at -1.0 and below, B to -0.35, C inside plus or minus "
            "0.35, D to +1.0, F above +1.0), where composite z = "
            f"{W_BTC}(-btc z) + {W_PD}(pd share z) + {W_IND}(-indirect z) so a lower number is "
            "stronger demand; cards run newest first and a coupon outranks a bill auctioned the "
            "same day; event study = SOFR minus IORB in bp at T+0, T+1, T+3 and T+5 prints "
            "of the funding tape against the last print before the auction, SRF take-up inside "
            "the same window, and the WRESBAL change across the settlement week; an unfinished "
            "window reports status open with null marks and never a zero; demand fatigue = the "
            f"trailing indirect share by tenor over the last {FATIGUE_WINDOW} auctions with a "
            "straight line slope in percentage points per auction"
        ),
    }
