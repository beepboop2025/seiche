"""Foreign Official Bid, rotation vs retreat in the official sector's dollars.

Foreign central banks hold Treasuries in custody at the New York Fed
(WMTSECL1) and park cash at the foreign RRP (WLRRAFOIAL). A falling custody
book alone is ambiguous: officials may be selling Treasuries to defend a
currency (the bid leaving the auction room) or merely shifting the same
dollars into cash at the Fed (a parking change, not a retreat). Reading the
two pools together disambiguates, and the FIMA repo line (officials borrowing
dollars against their custody collateral instead of selling it) is the stress
tell on top. Rules are stated, not vibes:

    custody down, foreign RRP up or flat  -> rotation  (parking shift)
    custody down, foreign RRP down        -> retreat   (the official bid leaving)
    custody up                            -> build     (classic build has both up)
    both inside the flat band             -> steady

All changes are read on the weekly H.4.1 grid over 13 weeks with a $5B flat
band; 4-week changes ride along for the impulse.
"""

from __future__ import annotations

import pandas as pd

FLAT_BAND_B = 5.0     # 13w change inside +/- this is flat, $B
MIN_WEEKS = 14        # need a 13w change on both pools
FIMA_DRAWN_B = 1.0    # FIMA repo above this counts as drawn, $B


def _wk(s: pd.Series) -> pd.Series:
    """$M weekly -> $B on the W-WED grid."""
    return (s.dropna() / 1000.0).resample("W-WED").last().dropna()


def _chg(s: pd.Series, weeks: int) -> float | None:
    if len(s) <= weeks:
        return None
    return round(float(s.iloc[-1] - s.iloc[-(weeks + 1)]), 1)


def _fmt(v: float) -> str:
    """Dollar amounts carry their sign in the surrounding words (cut, added,
    fell), so the magnitude is what prints, with the unit attached. A tenth
    of a billion matters on a weekly change and is noise on a stock of three
    trillion, so the precision follows the size."""
    a = abs(v)
    return f"${a:,.0f}B" if a >= 100 else f"${a:,.1f}B"


def analyze(
    custody_tsy_weekly: pd.Series,          # WMTSECL1, $M weekly
    foreign_rrp_weekly: pd.Series,          # WLRRAFOIAL, $M weekly
    fima_repo_weekly: pd.Series | None = None,  # H41RESPPALGTRFNWW, $M weekly
) -> dict:
    if custody_tsy_weekly is None or custody_tsy_weekly.dropna().empty:
        return {"ok": False, "reason": "no custody series"}
    if foreign_rrp_weekly is None or foreign_rrp_weekly.dropna().empty:
        return {"ok": False, "reason": "no foreign RRP series"}

    df = pd.concat(
        {"custody": _wk(custody_tsy_weekly), "rrp": _wk(foreign_rrp_weekly)}, axis=1
    ).dropna()
    if len(df) < MIN_WEEKS:
        return {"ok": False, "reason": f"insufficient overlap ({len(df)} weeks)"}

    cust, rrp = df["custody"], df["rrp"]
    footprint = cust + rrp

    c13, r13 = _chg(cust, 13), _chg(rrp, 13)
    f13 = _chg(footprint, 13)

    # Classification keys on the custody book first (the bid IS the custody
    # book); the RRP leg disambiguates a drawdown.
    if abs(c13) < FLAT_BAND_B:
        classification = "steady"
    elif c13 < 0 and r13 <= -FLAT_BAND_B:
        classification = "retreat"
    elif c13 < 0:
        classification = "rotation"
    else:
        classification = "build"

    # FIMA repo leg: optional until the board fetches it; an empty Series
    # (the assemble _pts default) counts as absent, same as None.
    fima = (
        _wk(fima_repo_weekly)
        if fima_repo_weekly is not None and not fima_repo_weekly.dropna().empty
        else pd.Series(dtype=float)
    )
    fima_now = round(float(fima.iloc[-1]), 1) if not fima.empty else None
    fima_drawn = fima_now is not None and fima_now > FIMA_DRAWN_B

    cust_now = round(float(cust.iloc[-1]), 1)
    rrp_now = round(float(rrp.iloc[-1]), 1)
    foot_now = round(float(footprint.iloc[-1]), 1)

    rrp_dir = "rose" if r13 >= FLAT_BAND_B else ("fell" if r13 <= -FLAT_BAND_B else "held flat")
    if classification == "rotation":
        letter_line = (
            f"Foreign officials cut their Fed custody Treasuries by {_fmt(c13)} over 13 weeks "
            f"while foreign RRP parking {rrp_dir}"
            + (f" by {_fmt(r13)}" if rrp_dir != "held flat" else "")
            + f", a parking rotation rather than the official bid leaving, footprint {_fmt(foot_now)}."
        )
    elif classification == "retreat":
        letter_line = (
            f"Foreign official custody Treasuries fell {_fmt(c13)} and foreign RRP cash fell "
            f"{_fmt(r13)} over 13 weeks, both pools shrinking together, the official bid is "
            f"leaving, footprint {_fmt(foot_now)}."
        )
    elif classification == "build":
        letter_line = (
            f"Foreign officials added {_fmt(c13)} of Fed custody Treasuries over 13 weeks "
            f"while foreign RRP parking {rrp_dir}"
            + (f" {_fmt(r13)}" if rrp_dir != "held flat" else "")
            + f", the official bid is on a build, footprint {_fmt(foot_now)}."
        )
    else:
        letter_line = (
            f"Foreign official custody Treasuries are near flat over 13 weeks at {_fmt(cust_now)} "
            f"with foreign RRP parking at {_fmt(rrp_now)}, no rotation and no retreat in the "
            f"official footprint of {_fmt(foot_now)}."
        )

    caveats = [
        "custody is par value held at FRBNY for foreign officials, not the whole official "
        "position; Euroclear and other offshore custody sit outside it",
        "weekly H.4.1 grid published Thursday for Wednesday, so the read lags the tape",
    ]
    if fima_now is None:
        caveats.append(
            "FIMA repo leg not supplied; officials raising dollars against custody collateral "
            "instead of selling would be invisible to this read"
        )
    elif fima_drawn:
        caveats.append(
            f"FIMA repo drawn at {fima_now}B: officials are borrowing dollars against "
            "collateral instead of selling, a stress tell that outranks the classification"
        )

    return {
        "ok": True,
        "asof": df.index[-1].date().isoformat(),
        "classification": classification,
        "custody_b": cust_now,
        "custody_chg_4w_b": _chg(cust, 4),
        "custody_chg_13w_b": c13,
        "foreign_rrp_b": rrp_now,
        "foreign_rrp_chg_4w_b": _chg(rrp, 4),
        "foreign_rrp_chg_13w_b": r13,
        "fima_repo_b": fima_now,
        "fima_repo_chg_4w_b": _chg(fima, 4) if not fima.empty else None,
        "fima_repo_chg_13w_b": _chg(fima, 13) if not fima.empty else None,
        "fima_drawn": bool(fima_drawn),
        "footprint_b": foot_now,
        "footprint_chg_4w_b": _chg(footprint, 4),
        "footprint_chg_13w_b": f13,
        "letter_line": letter_line,
        "series": [
            [
                d.date().isoformat(),
                round(float(row["custody"]), 1),
                round(float(row["rrp"]), 1),
                round(float(row["custody"] + row["rrp"]), 1),
            ]
            for d, row in df.tail(156).iterrows()
        ],
        "caveats": caveats,
        "method": (
            "13w change per pool on the W-WED grid, $B, with a 5B flat band: custody down "
            "with foreign RRP up or flat = rotation; both down = retreat; custody up = build "
            "(classic build has both pools rising); inside the band = steady; footprint = "
            "custody + foreign RRP; FIMA repo drawn > 1B flags officials borrowing instead "
            "of selling"
        ),
    }
