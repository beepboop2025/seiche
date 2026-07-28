"""Where the Dollars Sit: the weekly stock-flow ledger of the dollar funding system.

Every desk asks the same question on H.4.1 day and almost nobody publishes the
answer: reserves moved, so which liability took the other side. The Fed's own
balance sheet answers it as an accounting identity, because total assets have
to equal everything holding them, and every piece of it is free weekly public
data. The ledger reconciles to the dollar and prints its own residual, which
is the part that makes it worth trusting. The identity it closes runs total
assets against currency, reserves, the Treasury's account, the two reverse
repo pools and a residual:

    WALCL = currency + reserves + TGA + ON RRP + foreign RRP + residual

The residual is not slack and it is not an error term. It is other deposits
held at the Reserve Banks, Treasury currency outstanding, other liabilities
and capital, and the timing basis between a Wednesday level and a week
average. It is real balance sheet that this ledger does not name, so the
ledger names it as unnamed, prints it as a level and as a share of assets, and
sizes its own week to week movement. A ledger that balances is table stakes,
since the residual is defined as the plug and the identity closes by
construction. A ledger that holds its residual still is the credibility test,
and a residual that jumps is the alarm: it means one of the named legs is
being read wrong, or something outside the named legs just moved.

On top of the levels sits the flow decomposition, which is the part a desk
actually trades off. Differencing the identity gives, exactly and to the
dollar:

    d(reserves) = d(assets) - d(currency) - d(TGA) - d(ON RRP)
                  - d(foreign RRP) - d(residual)

So the question "did reserves fall because the TGA rebuilt, because ON RRP
drained, or because the balance sheet shrank" stops being a matter of opinion.
The six legs sum to the reserve change, the largest one gets named, and the
letter line says it in one sentence with the numbers inline.

Inputs are the H.4.1 weekly series the board already fetches, plus currency in
circulation. Everything is aligned on the W-WED grid; ON RRP is daily, so the
ledger takes the Wednesday print. Amounts are read in $M (H.4.1 native) or $B
(RRPONTSYD native) and normalized to $B by magnitude.

Display and letter only. The ledger feeds no score: it is the arithmetic the
scores are built on top of, and dressing arithmetic as a signal would be the
one dishonest thing a reconciliation can do.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MIN_WEEKS = 14                 # need a 13w flow table plus the change into it
FLOW_WEEKS = 13                # rows in the published flow table
DEFAULT_WEEKS = 52             # window for the residual's own statistics
UNIT_THRESHOLD = 50_000.0      # $M levels sit above this, $B levels below it
JUMP_Z = 3.0                   # robust sigma on the weekly residual change
JUMP_MIN_B = 5.0               # and an absolute floor, so noise cannot alarm
ROBUST_SCALE_FLOOR_B = 0.1     # a residual with no measured dispersion still needs a scale
SOLE_DRIVER_SHARE = 0.80       # share of gross flow that makes one leg the whole story

# Order matters: this is the order the ledger prints and the order the legs
# are summed in. `sign` is the leg's contribution to reserves (assets supply
# reserves, every other liability competes with them for the same dollars).
LEGS = (
    ("assets", "Fed assets (WALCL)", "the balance sheet", +1),
    ("currency", "currency in circulation", "currency in circulation", -1),
    ("tga", "Treasury General Account", "the TGA", -1),
    ("onrrp", "ON RRP", "ON RRP", -1),
    ("foreign_rrp", "foreign RRP", "the foreign RRP pool", -1),
    ("residual", "unnamed residual", "the unnamed residual", -1),
)
LEG_KEYS = tuple(k for k, _, _, _ in LEGS)
LETTER_LABEL = {k: lab for k, _, lab, _ in LEGS}

# How each leg reads when it adds reserves and when it drains them. The verb
# carries the sign so the number never has to.
LEG_VERBS = {
    "assets": ("the balance sheet added", "the balance sheet shrank"),
    "currency": ("currency in circulation returned", "currency in circulation drained"),
    "tga": ("the TGA spent down", "the TGA rebuilt"),
    "onrrp": ("ON RRP released", "ON RRP absorbed"),
    "foreign_rrp": ("the foreign RRP pool released", "the foreign RRP pool absorbed"),
    "residual": ("the residual released", "the residual absorbed"),
}


# ---------------------------------------------------------------------------
# formatting and units
# ---------------------------------------------------------------------------
def _fmt(v: float) -> str:
    """Magnitude with the unit attached; the surrounding verb carries the sign.
    A tenth of a billion matters on a weekly flow and is noise on a stock of
    six trillion, so the precision follows the size."""
    a = abs(float(v))
    return f"${a:,.0f}B" if a >= 100 else f"${a:,.1f}B"


def _sfmt(v: float) -> str:
    """Same, but signed: a residual that has gone negative has to say so."""
    return ("-" if float(v) < 0 else "") + _fmt(v)


def _round(v, d: int = 1) -> float:
    """Round for publication, and never print a negative zero: a closing line
    that reads -0.0 makes a reader wonder what broke."""
    r = round(float(v), d)
    return 0.0 if r == 0 else r


def _to_b(s: pd.Series) -> pd.Series:
    """Accept $M (the H.4.1 native unit) or $B (RRPONTSYD native); return $B.
    Every series this ledger reads is either a trillion-scale level in $M or a
    facility balance in $B, so magnitude separates them cleanly. A $M series
    that never clears $50B would be misread, and none of the six do."""
    s = s.dropna() if s is not None else pd.Series(dtype=float)
    if s.empty:
        return s
    s = s.sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return s / 1000.0 if float(s.abs().max()) > UNIT_THRESHOLD else s


def _weekly(s: pd.Series | None) -> pd.Series:
    """Weekly H.4.1 level to $B on the W-WED grid. The source prints are
    already Wednesday stamped, so the resample is a regularizer, not a
    transformation."""
    b = _to_b(s)
    if b.empty:
        return b
    return b.resample("W-WED").last().dropna()


def _wednesday(daily: pd.Series | None, idx: pd.DatetimeIndex) -> tuple[pd.Series, pd.Series]:
    """ON RRP is daily; the identity is Wednesday. Take the Wednesday print,
    and where the Wednesday was a holiday carry the last print before it, with
    a flag on every carried week, because a carried print is an assumption."""
    if _empty(daily):
        return pd.Series(np.nan, index=idx), pd.Series(False, index=idx)
    b = _to_b(daily)
    exact = b.reindex(idx)
    carried = b.reindex(idx, method="ffill")
    return carried, (exact.isna() & carried.notna())


def _empty(s: pd.Series | None) -> bool:
    return s is None or (isinstance(s, pd.Series) and s.dropna().empty)


def _lint(*texts: str) -> list[str]:
    """The house copy rules are one publish-blocking lint, and prose written
    here has to pass the same gate the letter does. Imported lazily so the
    engine stays importable on its own."""
    try:
        from seiche.dispatch_daily import lint_letter
    except ImportError:
        return []
    return lint_letter(*[t for t in texts if t])


# ---------------------------------------------------------------------------
# the ledger
# ---------------------------------------------------------------------------
def reconcile(
    walcl: pd.Series,                        # WALCL, $M weekly Wednesday level
    wcurcir: pd.Series | None,               # WCURCIR, $M weekly; optional until the board fetches it
    wresbal: pd.Series,                      # WRESBAL, $M weekly
    wtregen: pd.Series,                      # WTREGEN, $M weekly
    rrp_daily: pd.Series,                    # RRPONTSYD, $B daily
    foreign_rrp: pd.Series,                  # WLRRAFOIAL, $M weekly
    weeks: int = DEFAULT_WEEKS,
) -> dict:
    if _empty(walcl):
        return {"ok": False, "reason": "no WALCL series"}
    if _empty(wresbal):
        return {"ok": False, "reason": "no reserves series"}
    if _empty(wtregen):
        return {"ok": False, "reason": "no TGA series"}
    if _empty(foreign_rrp):
        return {"ok": False, "reason": "no foreign RRP series"}

    currency_named = not _empty(wcurcir)

    cols = {
        "assets": _weekly(walcl),
        "reserves": _weekly(wresbal),
        "tga": _weekly(wtregen),
        "foreign_rrp": _weekly(foreign_rrp),
    }
    if currency_named:
        cols["currency"] = _weekly(wcurcir)
    frame = pd.concat(cols, axis=1, sort=True).dropna()
    if not currency_named:
        frame["currency"] = 0.0
    if frame.empty:
        return {"ok": False, "reason": "no overlapping weekly grid"}

    onrrp, carried_flag = _wednesday(rrp_daily, frame.index)
    frame["onrrp"] = onrrp
    rrp_named = not frame["onrrp"].dropna().empty
    if not rrp_named:
        frame["onrrp"] = 0.0
    frame["_carried"] = carried_flag.reindex(frame.index).fillna(False).astype(bool)
    frame = frame.dropna()

    window = max(int(weeks), MIN_WEEKS) + 1
    frame = frame.tail(window)
    if len(frame) < MIN_WEEKS:
        return {"ok": False, "reason": f"insufficient weekly overlap ({len(frame)} weeks)"}
    n_carried = int(frame.pop("_carried").sum())

    # The residual is the plug: assets minus every liability the ledger names.
    frame["residual"] = (
        frame["assets"] - frame["currency"] - frame["reserves"]
        - frame["tga"] - frame["onrrp"] - frame["foreign_rrp"]
    )

    latest = frame.iloc[-1]
    asof = frame.index[-1].date().isoformat()
    assets_now = float(latest["assets"])
    resid_now = float(latest["residual"])
    resid_share_pct = (resid_now / assets_now * 100.0) if assets_now else None

    # Level check. Zero by construction, and published anyway: a reconciliation
    # that will not show its own closing line is asking to be taken on faith.
    level_check = float(
        latest["assets"] - sum(
            latest[k] for k in ("currency", "reserves", "tga", "onrrp", "foreign_rrp", "residual")
        )
    )

    # --- flows: the differenced identity, exact to the dollar ---
    chg = frame.diff().dropna()
    flows = pd.DataFrame(index=chg.index)
    for key, _plain, _letter, sign in LEGS:
        flows[key] = sign * chg[key]
    flows["reserves_chg"] = chg["reserves"]
    flows["check"] = flows[list(LEG_KEYS)].sum(axis=1) - flows["reserves_chg"]

    week = flows.iloc[-1]
    dres = float(week["reserves_chg"])
    contrib = {k: float(week[k]) for k in LEG_KEYS}
    gross = float(sum(abs(v) for v in contrib.values()))
    driver = max(contrib, key=lambda k: abs(contrib[k]))
    driver_v = contrib[driver]
    driver_share_gross = (abs(driver_v) / gross) if gross > 1e-9 else None
    sole_driver = bool(
        driver_share_gross is not None
        and driver_share_gross >= SOLE_DRIVER_SHARE
        and (driver_v >= 0) == (dres >= 0)
    )
    share_of_move = (driver_v / dres) if abs(dres) > 1e-9 else None

    # --- the residual's own statistics: is it holding still ---
    resid_chg = frame["residual"].diff().dropna()
    sigma = float(resid_chg.std(ddof=1)) if len(resid_chg) > 2 else None
    med = float(resid_chg.median())
    mad = float((resid_chg - med).abs().median())
    scale = max(1.4826 * mad, ROBUST_SCALE_FLOOR_B)
    last_chg = float(resid_chg.iloc[-1])
    robust_z = (last_chg - med) / scale
    jump = bool(abs(robust_z) >= JUMP_Z and abs(last_chg) >= JUMP_MIN_B)

    share_txt = "" if resid_share_pct is None else f"{resid_share_pct:.2f}% of assets, "
    if jump:
        resid_verdict = (
            f"the residual moved {_sfmt(last_chg)} in one week, {abs(robust_z):.1f} robust sigma "
            "against its own history: read the named legs again before trusting the attribution"
        )
    else:
        span = _fmt(sigma) if sigma is not None else "its usual range"
        resid_verdict = (
            f"the residual is holding at {_sfmt(resid_now)}, {share_txt}"
            f"weekly moves inside {span}, which is what a correctly read identity looks like"
        )

    residual_note = (
        "The residual is real balance sheet, not an error term: other deposits held at the "
        "Reserve Banks, Treasury currency outstanding, other liabilities and capital, plus the "
        "timing basis between a Wednesday level and a week average. It is a plug for what this "
        "ledger does not name. A stable residual is the check that the identity is being read "
        "correctly; a jumping residual is the alarm."
    )

    # --- the letter line: one sentence, the numbers inline ---
    ranked = sorted(contrib.items(), key=lambda kv: -abs(kv[1]))
    moved = [(k, v) for k, v in ranked[:3] if abs(v) >= 0.05]
    named = ", ".join(f"{LEG_VERBS[k][0 if v >= 0 else 1]} {_fmt(v)}" for k, v in moved)
    res_now_txt = _fmt(float(latest["reserves"]))
    if abs(dres) < 0.05:
        opener = f"Reserves held flat on the week at {res_now_txt}"
    else:
        verb = "rose" if dres > 0 else "fell"
        opener = f"Reserves {verb} {_fmt(dres)} on the week to {res_now_txt}"
    middle = (
        f", and the ledger says where from: {named}, six legs summing to the reserve change "
        "to the dollar"
        if named else
        ", and no leg moved more than a rounding error, which is the six legs summing to the "
        "reserve change to the dollar the boring way"
    )
    share_clause = "" if resid_share_pct is None else f", {resid_share_pct:.2f}% of assets,"
    letter_line = (
        f"{opener}{middle} with {_sfmt(resid_now)} of the balance sheet{share_clause} "
        "still sitting in the unnamed residual."
    )
    lint_issues = _lint(letter_line, resid_verdict)

    caveats = [
        "H.4.1 publishes Thursday for the preceding Wednesday, so the whole ledger is a week "
        "old on the day it is read",
        "WALCL and the reverse repo legs are Wednesday levels while "
        + ("currency, reserves and the TGA are" if currency_named else "reserves and the TGA are")
        + " week averages on the same release, so the residual carries a timing basis of a few "
        "tens of billions that is arithmetic and not news",
        "the identity closes to the dollar by construction because the residual is defined as "
        "the plug; the test that matters is whether the residual holds still, not whether the "
        "columns add up",
    ]
    if not currency_named:
        caveats.append(
            "currency in circulation was not supplied, so roughly two and a half trillion of "
            "note liability sits inside the residual: the residual level is not comparable to a "
            "run with currency named, and a currency drain cannot be separated from anything "
            "else in there"
        )
    if not rrp_named:
        caveats.append(
            "no ON RRP series was supplied, so the facility balance sits inside the residual and "
            "an RRP drain reads as residual movement"
        )
    elif n_carried:
        caveats.append(
            f"{n_carried} Wednesdays in the window had no ON RRP print (a holiday) and carry the "
            "last print before them, which is an assumption and not an observation"
        )
    if jump:
        caveats.append(
            f"the residual jumped {_sfmt(last_chg)} on the latest week, so the attribution below "
            "is carrying an unexplained leg and should be read as a question rather than an answer"
        )
    if lint_issues:
        caveats.append("letter line failed the house copy lint: " + "; ".join(lint_issues))

    def _row(ts, r) -> dict:
        return {
            "date": ts.date().isoformat(),
            "reserves_chg_b": _round(float(r["reserves_chg"]), 2),
            **{f"{k}_b": _round(float(r[k]), 2) for k in LEG_KEYS},
            "check_b": _round(float(r["check"]), 6),
        }

    return {
        "ok": True,
        "asof": asof,
        "weeks": int(len(frame) - 1),
        "currency_named": bool(currency_named),
        "onrrp_named": bool(rrp_named),
        "levels": {
            "date": asof,
            "assets_b": _round(assets_now, 1),
            "currency_b": _round(float(latest["currency"]), 1),
            "reserves_b": _round(float(latest["reserves"]), 1),
            "tga_b": _round(float(latest["tga"]), 1),
            "onrrp_b": _round(float(latest["onrrp"]), 1),
            "foreign_rrp_b": _round(float(latest["foreign_rrp"]), 1),
            "residual_b": _round(resid_now, 1),
            "residual_share_pct": None if resid_share_pct is None else _round(resid_share_pct, 3),
            "check_b": _round(level_check, 6),
        },
        "week": _row(flows.index[-1], week),
        "attribution": {
            "driver": driver,
            "driver_label": LETTER_LABEL[driver],
            "driver_contribution_b": _round(driver_v, 2),
            "driver_share_of_gross": None if driver_share_gross is None else _round(driver_share_gross, 3),
            "share_of_move": None if share_of_move is None else _round(share_of_move, 3),
            "sole_driver": sole_driver,
            "reserves_chg_b": _round(dres, 2),
        },
        "residual_check": {
            "level_b": _round(resid_now, 1),
            "share_pct": None if resid_share_pct is None else _round(resid_share_pct, 3),
            "chg_b": _round(last_chg, 2),
            "sigma_b": None if sigma is None else _round(sigma, 2),
            "robust_scale_b": _round(scale, 3),
            "robust_z": _round(robust_z, 2),
            "jump": jump,
            "verdict": resid_verdict,
            "note": residual_note,
        },
        "flows_13w": [_row(ts, r) for ts, r in flows.tail(FLOW_WEEKS).iterrows()],
        "series": [
            [
                ts.date().isoformat(),
                _round(float(r["assets"]), 1),
                _round(float(r["currency"]), 1),
                _round(float(r["reserves"]), 1),
                _round(float(r["tga"]), 1),
                _round(float(r["onrrp"]), 1),
                _round(float(r["foreign_rrp"]), 1),
                _round(float(r["residual"]), 1),
            ]
            for ts, r in frame.tail(156).iterrows()
        ],
        "legs": [
            {"key": k, "label": plain, "letter_label": letter, "sign_on_reserves": sign}
            for k, plain, letter, sign in LEGS
        ],
        "letter_line": None if lint_issues else letter_line,
        "caveats": caveats,
        "method": (
            "H.4.1 weekly identity on the W-WED grid, $B: WALCL = currency + reserves + TGA + "
            "ON RRP (Wednesday print of the daily series) + foreign RRP + residual, with the "
            "residual defined as the plug so the level closes to the dollar; the flow "
            "decomposition differences the same identity, so d(reserves) = d(assets) minus the "
            "change in each named liability minus the change in the residual, and the six legs "
            f"sum to the reserve change exactly; the residual's weekly change is scored on a "
            f"robust z (median and MAD, scale floored at {ROBUST_SCALE_FLOOR_B}B) and flagged as "
            f"a jump at {JUMP_Z:.0f} sigma with a {JUMP_MIN_B:.0f}B absolute floor; one leg is "
            f"called the sole driver when it carries {SOLE_DRIVER_SHARE:.0%} of the week's gross "
            "flow in the same direction as reserves"
        ),
    }
