"""Global Basin Coupling — the dollar system as connected bodies of water.

Funding basins (US, euro area, UK) are connected through the FX-swap channel,
cross-border bank funding and the dollar itself. In calm regimes each basin
sloshes to its own local calendar; under global dollar pressure they
synchronize — one tide moves all of them, and the swap lines light up.

Three measurements, same physics as the domestic engines:

1. BASIN STATE — each basin's overnight rate vs its policy anchor (where a
   daily anchor exists keyless: US IORB, ECB deposit facility rate), z-scored.
2. THE TIDE — absorption ratio over the cross-basin panel (US spread, EUR
   spread, SONIA, broad dollar, foreign-official RRP): the variance share of
   the common component. Tide high = one basin, globally fragile.
   Cross-basin lead-lag edges show which basin is upstream this quarter.
3. SWAP-LINE CONFESSION — foreign central banks drawing USD liquidity swaps
   (NY Fed ops + H.4.1 outstanding). Small-value TEST operations are excluded
   (flagged upstream), because a test is not a confession.

Honest scope: Japan, China, Russia and African markets have no keyless,
reliable, daily public feed we can hold to the same provenance bar — they are
OUT of scope and say so here, rather than being faked in. New basins plug
into config when a qualifying feed exists.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import BASIN_EDGE_MIN_ABS, BASIN_WINDOW_D
from seiche.engines.hydrophone import _absorption


def _rolling_z(s: pd.Series, window: int = 250) -> float | None:
    x = s.dropna()
    if len(x) < 60:
        return None
    tail = x.tail(window)
    sd = float(tail.std())
    if sd <= 0:
        return None
    return float((float(x.iloc[-1]) - float(tail.mean())) / sd)


def analyze(
    spread_us_bp: pd.Series,     # SOFR - IORB, bp
    estr: pd.Series,             # €STR, %
    ecb_dfr: pd.Series,          # ECB deposit facility rate, %
    sonia: pd.Series,            # SONIA, %
    dxy: pd.Series,              # broad dollar index
    swap_lines_m: pd.Series,     # H.4.1 swaps outstanding, $M, weekly
    foreign_rrp_m: pd.Series,    # foreign official RRP, $M, weekly
    fx_ops: list[dict],          # NY Fed USD swap operations
    inr: pd.Series | None = None,       # INR per USD (FRED DEXINUS)
    usdt_peg_bp: pd.Series | None = None,  # USDT peg deviation, bp (crypto basin)
) -> dict:
    if spread_us_bp.dropna().empty:
        return {"ok": False, "reason": "no US spread history"}
    inr = inr if inr is not None else pd.Series(dtype=float)
    usdt_peg_bp = usdt_peg_bp if usdt_peg_bp is not None else pd.Series(dtype=float)

    # --- basin states -----------------------------------------------------
    eur_spread_bp = pd.Series(dtype=float)
    if not estr.dropna().empty and not ecb_dfr.dropna().empty:
        dfr = ecb_dfr.reindex(estr.index, method="ffill")
        eur_spread_bp = ((estr - dfr) * 100.0).dropna()

    basins = []
    us = spread_us_bp.dropna()
    basins.append({
        "basin": "US", "anchor": "SOFR − IORB",
        "value_bp": round(float(us.iloc[-1]), 1),
        "z": round(_rolling_z(us) or 0.0, 2),
        "asof": us.index[-1].date().isoformat(),
    })
    if not eur_spread_bp.empty:
        basins.append({
            "basin": "EURO AREA", "anchor": "€STR − DFR",
            "value_bp": round(float(eur_spread_bp.iloc[-1]), 1),
            "z": round(_rolling_z(eur_spread_bp) or 0.0, 2),
            "asof": eur_spread_bp.index[-1].date().isoformat(),
        })
    uk = sonia.dropna()
    if not uk.empty:
        basins.append({
            "basin": "UK", "anchor": "SONIA level (no keyless daily policy anchor)",
            "value_bp": round(float(uk.iloc[-1]) * 100.0, 1),
            "z": round(_rolling_z(uk.diff().dropna()) or 0.0, 2),
            "asof": uk.index[-1].date().isoformat(),
        })
    inr_d = inr.dropna()
    if not inr_d.empty:
        # FX channel only: CCIL is HTML-only and RBI DBIE presents a broken
        # SSL chain (probed 2026-07-07) — a rates anchor joins when a feed
        # meets the keyless bar. INR weakness/vol still couples the basin.
        inr_vol = (inr_d.pct_change().rolling(10).std() * np.sqrt(252) * 100.0).dropna()
        basins.append({
            "basin": "INDIA", "anchor": "INR/USD (FX channel only — rates feed pending)",
            "value_bp": round(float(inr_d.iloc[-1]), 2),
            "z": round(_rolling_z(inr_d) or 0.0, 2),
            "vol_z": round(_rolling_z(inr_vol) or 0.0, 2) if not inr_vol.empty else None,
            "asof": inr_d.index[-1].date().isoformat(),
        })
    peg = usdt_peg_bp.dropna()
    if not peg.empty:
        basins.append({
            "basin": "CRYPTO (offshore $)", "anchor": "USDT peg deviation",
            "value_bp": round(float(peg.iloc[-1]), 1),
            "z": round(_rolling_z(peg.abs()) or 0.0, 2),
            "asof": peg.index[-1].date().isoformat(),
        })

    # --- the tide -----------------------------------------------------------
    panel = {
        "US spread": us,
        "EUR spread": eur_spread_bp,
        "SONIA": uk,
        "Dollar idx": dxy.dropna(),
        "Foreign RRP": (foreign_rrp_m.dropna() / 1000.0),
        "Swap lines": (swap_lines_m.dropna() / 1000.0),
        "INR": inr_d,
        "USDT peg": peg,
    }
    panel = {k: v for k, v in panel.items() if not v.empty}
    df = pd.concat(panel, axis=1).sort_index().asfreq("B").ffill(limit=6)
    chg = df.diff().dropna(how="all")

    tide_series: list[list] = []
    tide_pctl = None
    absorption_now = None
    if len(chg) >= BASIN_WINDOW_D + 40:
        dates, values = [], []
        for end in range(BASIN_WINDOW_D, len(chg), 5):
            a = _absorption(chg.iloc[end - BASIN_WINDOW_D : end])
            if a is not None:
                dates.append(chg.index[end - 1])
                values.append(a)
        if values:
            tide = pd.Series(values, index=pd.DatetimeIndex(dates))
            absorption_now = float(tide.iloc[-1])
            tide_pctl = float((tide <= absorption_now).mean() * 100.0)
            tide_series = [
                [d.date().isoformat(), round(float(v), 3)] for d, v in tide.items()
            ]

    # Cross-basin lead-lag (which basin is upstream right now).
    edges = []
    win = chg.iloc[-BASIN_WINDOW_D:]
    win = win.dropna(axis=1, thresh=int(len(win) * 0.6))
    z = (win - win.mean()) / win.std().replace(0, np.nan)
    cols = list(z.columns)
    for a in cols:
        for b in cols:
            if a == b:
                continue
            best_r, best_lag = 0.0, 0
            for lag in (1, 2, 3):
                pair = pd.concat([z[a].shift(lag), z[b]], axis=1).dropna()
                if len(pair) < 40:
                    continue
                r = float(pair.corr().iloc[0, 1])
                if abs(r) > abs(best_r):
                    best_r, best_lag = r, lag
            if abs(best_r) >= BASIN_EDGE_MIN_ABS:
                edges.append({"lead": a, "follows": b, "lag_d": best_lag, "corr": round(best_r, 2)})
    edges.sort(key=lambda e: -abs(e["corr"]))
    edges = edges[:8]

    # --- swap-line confession ----------------------------------------------
    real_ops = [o for o in fx_ops if not o.get("is_small_value")]
    cutoff = (us.index[-1] - pd.Timedelta(days=30)).date().isoformat()
    ops_30d = [o for o in real_ops if (o.get("trade_date") or "") >= cutoff]
    total_30d_m = float(sum(o.get("amount_m") or 0.0 for o in ops_30d))
    by_cp: dict[str, float] = {}
    for o in ops_30d:
        cp = o.get("counterparty") or "?"
        by_cp[cp] = by_cp.get(cp, 0.0) + float(o.get("amount_m") or 0.0)
    swpt = swap_lines_m.dropna()
    n_small = sum(1 for o in fx_ops if o.get("is_small_value"))

    # --- score ---------------------------------------------------------------
    score = 0.0
    if tide_pctl is not None:
        score = tide_pctl * 0.6
    if total_30d_m >= 1000.0:
        score += 25.0
    fr = foreign_rrp_m.dropna() / 1000.0
    if len(fr) > 14 and float(fr.iloc[-1] - fr.iloc[-14]) < -50.0:
        score += 15.0  # foreign official dollar pool draining fast
    score = float(np.clip(score, 0.0, 100.0))

    return {
        "ok": True,
        "asof": us.index[-1].date().isoformat(),
        "score": round(score, 1),
        "basins": basins,
        "tide": {
            "absorption": round(absorption_now, 3) if absorption_now is not None else None,
            "pctl": round(tide_pctl, 0) if tide_pctl is not None else None,
            "series": tide_series,
            "n_series": int(len(panel)),
        },
        "edges": edges,
        "swap_lines": {
            "outstanding_m": round(float(swpt.iloc[-1]), 1) if not swpt.empty else None,
            "outstanding_asof": swpt.index[-1].date().isoformat() if not swpt.empty else None,
            "ops_30d_total_m": round(total_30d_m, 1),
            "ops_30d_by_counterparty": {k: round(v, 1) for k, v in sorted(by_cp.items(), key=lambda kv: -kv[1])},
            "recent_ops": real_ops[:10],
            "small_value_ops_excluded": n_small,
        },
        "channels": {
            "foreign_rrp_b": round(float(fr.iloc[-1]), 1) if not fr.empty else None,
            "foreign_rrp_chg_13w_b": round(float(fr.iloc[-1] - fr.iloc[-14]), 1) if len(fr) > 14 else None,
            "dollar_idx": round(float(dxy.dropna().iloc[-1]), 2) if not dxy.dropna().empty else None,
            "dollar_idx_z": round(_rolling_z(dxy.dropna()) or 0.0, 2),
        },
        "out_of_scope": (
            "Japan (TONA), China, Russia, African markets: no keyless daily feed that "
            "meets the provenance bar — excluded rather than faked. India rides the FX "
            "channel only (CCIL = HTML, RBI DBIE = broken SSL, probed 2026-07-07); a "
            "rates anchor joins when a qualifying feed exists"
        ),
        "method": (
            f"tide = top-2 PC variance share of {BASIN_WINDOW_D}bd standardized daily "
            "changes across basins+channels (sampled 5bd, pctl vs own history); edges = "
            f"max |lag-k xcorr| k=1..3, |r|>={BASIN_EDGE_MIN_ABS}; confession = NY Fed USD "
            "swap ops ex small-value tests + H.4.1 outstanding"
        ),
    }
