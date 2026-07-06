"""Stablecoin Moorings — the offshore-dollar basin's tie lines.

A moored ship strains its lines before they snap. Stablecoins are the
moorings between the crypto basin and the T-bill market: USDT/USDC hold
$200B+ of bills, redemptions force bill sales, and the peg price is a
real-time print of offshore dollar demand. Three instruments:

1. PEG BOARD — the top USD stablecoins' current deviation from $1 (bp),
   plus USDT's full daily peg history (Coinbase) robust-z'd: is the strain
   unusual for THIS mooring?
2. OFFSHORE DOLLAR DEMAND — total stablecoin circulation ($B, ~8y daily):
   growth = dollar demand the banking system doesn't see; contraction =
   redemptions = someone is selling bills into the same market Treasury is
   flooding.
3. THE 24/7 CANARY — BTC realized vol and the largest weekend move of the
   last month: crypto is the only dollar market open when funding markets
   sleep, so Saturday panic prints here first.

Reported alongside the Seiche Index, never weighted into it: the crypto
basin's stress is context for the dollar system, not evidence of US funding
stress by itself. USDC/DAI pegs are spot values (no free history) — labeled.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import PEG_DEV_FLAG_BP, STABLE_DRAIN_FLAG_PCT


def _robust_z_last(s: pd.Series, lookback: int = 250) -> float | None:
    x = s.dropna().tail(lookback)
    if len(x) < 60:
        return None
    med = float(x.median())
    mad = float((x - med).abs().median())
    scale = 1.4826 * mad
    if scale <= 0:
        return None
    return float((float(x.iloc[-1]) - med) / scale)


def analyze(
    board: list[dict],           # DeFiLlama current peg board
    usdt_usd: pd.Series,         # Coinbase USDT-USD daily closes (peg history)
    stable_total_b: pd.Series,   # total stablecoin circulation, $B daily
    btc_usd: pd.Series,          # BTC daily closes
) -> dict:
    if not board and usdt_usd.dropna().empty:
        return {"ok": False, "reason": "no stablecoin data"}

    # --- peg board ---------------------------------------------------------
    pegs = []
    for row in board:
        price = row.get("price")
        dev_bp = round((float(price) - 1.0) * 10_000.0, 1) if price is not None else None
        pegs.append(
            {
                "symbol": row.get("symbol"),
                "circulating_b": row.get("circulating_b"),
                "price": price,
                "dev_bp": dev_bp,
                "flag": dev_bp is not None and abs(dev_bp) >= PEG_DEV_FLAG_BP,
            }
        )

    usdt = usdt_usd.dropna()
    usdt_block = {}
    if not usdt.empty:
        dev = (usdt - 1.0) * 10_000.0
        abs_dev = dev.abs()
        usdt_block = {
            "dev_bp": round(float(dev.iloc[-1]), 1),
            "abs_dev_z": round(_robust_z_last(abs_dev) or 0.0, 2),
            "worst_30d_bp": round(float(dev.tail(30).abs().max()), 1),
            "asof": usdt.index[-1].date().isoformat(),
            "series": [
                [d.date().isoformat(), round(float(v), 1)] for d, v in dev.tail(400).items()
            ],
        }

    # --- offshore dollar demand ---------------------------------------------
    total = stable_total_b.dropna()
    demand = {}
    if len(total) > 100:
        chg_30d = float(total.iloc[-1] - total.iloc[-31])
        chg_30d_pct = chg_30d / float(total.iloc[-31]) * 100.0
        chg_13w = float(total.iloc[-1] - total.iloc[-92]) if len(total) > 92 else None
        demand = {
            "total_b": round(float(total.iloc[-1]), 1),
            "chg_30d_b": round(chg_30d, 1),
            "chg_30d_pct": round(chg_30d_pct, 2),
            "chg_13w_b": round(chg_13w, 1) if chg_13w is not None else None,
            "draining": chg_30d_pct <= STABLE_DRAIN_FLAG_PCT,
            "asof": total.index[-1].date().isoformat(),
            "series": [
                [d.date().isoformat(), round(float(v), 1)] for d, v in total.iloc[::3].tail(400).items()
            ],
        }

    # --- 24/7 canary ----------------------------------------------------------
    btc = btc_usd.dropna()
    canary = {}
    if len(btc) > 120:
        ret = btc.pct_change()
        rv10 = ret.rolling(10).std() * np.sqrt(365) * 100.0  # annualized %
        weekend = ret[ret.index.dayofweek >= 5].abs() * 100.0
        wk4 = weekend.tail(8)  # ~4 weekends of Sat+Sun prints
        canary = {
            "btc_last": round(float(btc.iloc[-1]), 0),
            "btc_rv10_pct": round(float(rv10.dropna().iloc[-1]), 1) if not rv10.dropna().empty else None,
            "btc_rv10_z": round(_robust_z_last(rv10.dropna()) or 0.0, 2),
            "max_weekend_move_4w_pct": round(float(wk4.max()), 2) if not wk4.empty else None,
            "weekend_move_z": round(_robust_z_last(weekend) or 0.0, 2) if len(weekend) > 60 else None,
            "asof": btc.index[-1].date().isoformat(),
        }

    # --- score (context, NOT in the composite) --------------------------------
    score = 0.0
    if usdt_block:
        score += float(np.clip((usdt_block["abs_dev_z"] or 0.0) / 4.0, 0.0, 1.0)) * 40.0
    if demand:
        score += float(np.clip(-demand["chg_30d_pct"] / 6.0, 0.0, 1.0)) * 35.0
    if canary:
        score += float(np.clip((canary["btc_rv10_z"] or 0.0) / 4.0, 0.0, 1.0)) * 25.0
    score = float(np.clip(score, 0.0, 100.0))

    return {
        "ok": True,
        "score": round(score, 1),
        "pegs": pegs,
        "usdt": usdt_block,
        "demand": demand,
        "canary": canary,
        "caveat": (
            "USDC/DAI pegs are spot values (no free keyless history); USDT peg history "
            "is Coinbase daily closes; context for the dollar system, not weighted into "
            "the Seiche Index"
        ),
        "method": (
            f"peg dev bp vs $1 (flag |dev| ≥ {PEG_DEV_FLAG_BP}bp); USDT |dev| robust-z 250d; "
            f"offshore demand = Δ total circulation (drain flag ≤ {STABLE_DRAIN_FLAG_PCT}%/30d); "
            "canary = BTC 10d realized vol z + largest weekend move (crypto trades when "
            "funding markets sleep)"
        ),
    }
