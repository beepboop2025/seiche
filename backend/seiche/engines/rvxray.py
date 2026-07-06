"""RV X-Ray — leveraged-positioning size and fragility for the Treasury RV
complex (basis trade + swap-spread trade funding leg).

Published estimates of the basis trade disagree by $500B (MS $1.5T vs IMF
$1T) partly because methods are opaque. Ours is deliberately transparent:

  pair proxy  = sum over contracts of min(lev-fund shorts, asset-mgr longs)
                x face value        -> classic cash-futures basis footprint
  gross short = lev-fund shorts x face                     -> whole RV complex
  DV01        = lev-fund shorts x per-contract DV01        -> shock arithmetic

Fragility couples positioning to funding: size x repo dependence (DVP volume)
vs a dealer-capacity proxy. The margin-shock simulator answers: for an X bp
adverse move, what's the mark-to-market hit, and how many days of DVP volume
would an unwind of Y% of the trade absorb?
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import UST_CONTRACTS


def position_history(tff: pd.DataFrame) -> pd.DataFrame:
    """Weekly pair-proxy / gross-short / DV01 history from TFF rows."""
    rows = []
    for date, grp in tff.groupby("date"):
        pair_notional = 0.0
        gross_short = 0.0
        dv01 = 0.0
        for _, r in grp.iterrows():
            c = UST_CONTRACTS.get(r["contract"])
            if c is None:  # crowding-panel extras (FF/SOFR/ES) — not RV legs
                continue
            ls = float(r.get("lev_money_positions_short_all") or 0)
            al = float(r.get("asset_mgr_positions_long_all") or 0)
            pair_notional += min(ls, al) * c["face"]
            gross_short += ls * c["face"]
            dv01 += ls * c["dv01"]
        rows.append(
            {"date": date, "pair_b": pair_notional / 1e9, "gross_short_b": gross_short / 1e9, "dv01_m": dv01 / 1e6}
        )
    return pd.DataFrame(rows).set_index("date").sort_index()


def analyze(tff: pd.DataFrame, dvp_vol: pd.Series) -> dict:
    if tff.empty:
        return {"ok": False, "reason": "no TFF data"}

    hist = position_history(tff)
    if hist.empty:
        return {"ok": False, "reason": "no UST contracts in TFF data"}
    latest = hist.iloc[-1]

    chg_13w = (
        float(latest["pair_b"] - hist["pair_b"].iloc[-14])
        if len(hist) > 14
        else None
    )
    z = (hist["pair_b"] - hist["pair_b"].mean()) / (hist["pair_b"].std() or np.nan)
    size_z = float(z.iloc[-1])

    dvp_now = float(dvp_vol.dropna().iloc[-1]) if not dvp_vol.dropna().empty else None
    if dvp_now is not None and dvp_now > 1e6:
        dvp_now /= 1e9  # OFR volume mnemonics are raw dollars, not $B

    # Margin-shock scenarios: adverse basis moves of 5/15/30 bp.
    scenarios = []
    for shock_bp in (5, 15, 30):
        mtm_b = float(latest["dv01_m"]) * shock_bp / 1000.0  # $M DV01 x bp -> $B
        unwind_b = 0.10 * float(latest["gross_short_b"])     # assume 10% forced unwind
        days_of_dvp = unwind_b / dvp_now if dvp_now else None
        scenarios.append(
            {
                "shock_bp": shock_bp,
                "mtm_loss_b": round(mtm_b, 1),
                "assumed_unwind_b": round(unwind_b, 1),
                "unwind_days_of_dvp": round(days_of_dvp, 2) if days_of_dvp else None,
            }
        )

    return {
        "_pair_full": hist["pair_b"],  # pd.Series for the history layer; stripped from payloads
        "ok": True,
        "asof": hist.index[-1].date().isoformat(),
        "pair_proxy_b": round(float(latest["pair_b"]), 1),
        "gross_short_b": round(float(latest["gross_short_b"]), 1),
        "dv01_m_per_bp": round(float(latest["dv01_m"]), 1),
        "pair_change_13w_b": round(chg_13w, 1) if chg_13w is not None else None,
        "size_z": round(size_z, 2),
        "dvp_volume_b": round(dvp_now, 1) if dvp_now else None,
        "scenarios": scenarios,
        "series": [
            [d.date().isoformat(), round(float(r["pair_b"]), 1), round(float(r["gross_short_b"]), 1)]
            for d, r in hist.tail(200).iterrows()
        ],
        "method": "TFF futures-only; pair=min(levShort,amLong)xface; DV01 per-contract constants in config; scenarios assume 10% forced unwind vs DVP daily volume",
    }


def rvxray_score(result: dict) -> float:
    """0-100: size percentile vs own history + growth impulse."""
    if not result.get("ok"):
        return 0.0
    sz = result.get("size_z") or 0.0
    grow = result.get("pair_change_13w_b") or 0.0
    base = 100.0 / (1.0 + np.exp(-(sz - 0.8) * 1.3))
    if grow > 50:  # +$50B in 13 weeks = rapid build
        base = min(base + 15.0, 100.0)
    return float(np.clip(base, 0.0, 100.0))


def crowding(tff: pd.DataFrame, lookback_weeks: int = 156) -> dict:
    """Positioning crowding per contract: leveraged-fund NET position as a
    share of open interest, z-scored and percentiled vs its own trailing
    history. Crowded shorts in duration + crowded longs in equities is the
    classic pre-unwind constellation (Apr 2025). T+3 provenance as always."""
    if tff.empty:
        return {"ok": False, "reason": "no TFF data"}
    out = []
    for contract, grp in tff.groupby("contract"):
        g = grp.sort_values("date")
        oi = g["open_interest_all"].astype(float)
        net = (
            g["lev_money_positions_long_all"].fillna(0)
            - g["lev_money_positions_short_all"].fillna(0)
        )
        share = (net / oi.replace(0, np.nan)).dropna()
        if len(share) < 60:
            continue
        hist = share.tail(lookback_weeks)
        cur = float(hist.iloc[-1])
        sd = float(hist.std()) or np.nan
        z = (cur - float(hist.mean())) / sd if np.isfinite(sd) else 0.0
        pctl = float((hist <= cur).mean() * 100.0)
        out.append(
            {
                "contract": contract,
                "lev_net_share_oi": round(cur, 3),
                "z": round(float(z), 2),
                "pctl": round(pctl, 0),
                "asof": g["date"].iloc[-1].date().isoformat(),
            }
        )
    if not out:
        return {"ok": False, "reason": "no contracts with enough history"}
    out.sort(key=lambda r: -abs(r["z"]))
    return {
        "ok": True,
        "rows": out,
        "method": (
            "leveraged-fund net position / open interest per contract; z and percentile "
            f"vs trailing {lookback_weeks}w; |z| ranks the board (extremes = crowding)"
        ),
    }
