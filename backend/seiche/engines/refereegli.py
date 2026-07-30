"""Referee GLI: the "global liquidity" story's headline claims, tested on
the layer any outsider can reconstruct. The category's flagship index is
proprietary, but its largest agreed component is public: G3 central bank
balance sheets in dollar terms. This engine rebuilds that layer monthly from
2003 (Fed, ECB, BoJ at monthly average spot) and puts the three public
claims through the board's own standards: a 3 to 6 month asset price lead, a
12 to 15 month business cycle lead, and a 65 month liquidity cycle. Every
correlation carries a moving block bootstrap interval, the predictive claim
gets a walk forward test with no look ahead and Granger tests in both
directions, and where the post 2003 sample is too short to decide a claim
the power problem is quantified instead of hidden.

Honesty rails: this is NOT a test of the proprietary index, only of its
publicly reconstructible layer, and the page that publishes these numbers
says so above the fold. The reconstruction is G3 only (no keyless PBoC or
BoE balance sheet feed exists) and central bank assets only (no private
credit, no cross border flows). Context, never composite: a referee report
has no business moving the stress index.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import signal, stats

from seiche.config import (
    REFEREE_BOOT_BLOCK_M,
    REFEREE_BOOT_N,
    REFEREE_GRANGER_LAGS,
    REFEREE_OOS_BURN_M,
    REFEREE_START,
)

_CORR_SEED = 42
_SPREAD_SEED = 7
_MIN_MONTHS = 120  # a lead-lag verdict on less than a decade is noise

CLAIMS = {
    "claim1": "liquidity leads asset prices by 3 to 6 months",
    "claim2": "liquidity leads the business cycle by 12 to 15 months",
    "claim3": "global liquidity follows a cycle of roughly 65 months",
}


# ---------------------------------------------------------------- helpers

def _monthly_g3(fed_assets: pd.Series, ecb_assets: pd.Series,
                boj_assets: pd.Series, usd_per_eur: pd.Series,
                jpy_per_usd: pd.Series) -> pd.DataFrame:
    """G3 balance sheets in USD bn, month end, converted at monthly average
    spot. Units in: Fed $M, ECB EUR M, BoJ JPY 100M."""
    fed = fed_assets.resample("ME").last() / 1_000.0
    ecb = ecb_assets.resample("ME").last() * usd_per_eur.resample("ME").mean() / 1_000.0
    boj = boj_assets.resample("ME").last() * 100.0 / jpy_per_usd.resample("ME").mean() / 1_000.0
    g3 = pd.DataFrame({"fed": fed, "ecb": ecb, "boj": boj}).dropna()
    g3 = g3.loc[REFEREE_START:]
    g3["g3"] = g3.sum(axis=1)
    return g3


def _block_bootstrap_corr(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """95 percent interval for corr(x, y) under a circular moving block
    bootstrap, so serial dependence widens the interval instead of being
    assumed away."""
    rng = np.random.default_rng(_CORR_SEED)
    xy = np.column_stack([x, y])
    n = len(xy)
    block = REFEREE_BOOT_BLOCK_M
    nblocks = int(np.ceil(n / block))
    draws = np.empty(REFEREE_BOOT_N)
    for r in range(REFEREE_BOOT_N):
        starts = rng.integers(0, n, nblocks)
        idx = np.concatenate([np.arange(s, s + block) % n for s in starts])[:n]
        bs = xy[idx]
        draws[r] = np.corrcoef(bs[:, 0], bs[:, 1])[0, 1]
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return float(lo), float(hi)


def _corr(a: pd.Series, b: pd.Series) -> dict | None:
    df = pd.DataFrame({"a": a, "b": b}).dropna()
    if len(df) < 36:
        return None
    r = float(df["a"].corr(df["b"]))
    lo, hi = _block_bootstrap_corr(df["a"].to_numpy(), df["b"].to_numpy())
    return {"corr": round(r, 3), "ci95": [round(lo, 3), round(hi, 3)], "n": len(df)}


def _granger(y: pd.Series, x: pd.Series) -> dict | None:
    """F test: do lags of x improve prediction of y beyond y's own lags?"""
    lags = REFEREE_GRANGER_LAGS
    df = pd.DataFrame({"y": y, "x": x}).dropna()
    if len(df) < lags * 6:
        return None
    Y, X = df["y"].to_numpy(), df["x"].to_numpy()
    rows, targets = [], []
    for t in range(lags, len(df)):
        rows.append(np.concatenate([[1.0], Y[t - lags:t][::-1], X[t - lags:t][::-1]]))
        targets.append(Y[t])
    A = np.array(rows)
    b = np.array(targets)
    beta_full, *_ = np.linalg.lstsq(A, b, rcond=None)
    rss_full = float(np.sum((b - A @ beta_full) ** 2))
    A_r = A[:, : 1 + lags]
    beta_r, *_ = np.linalg.lstsq(A_r, b, rcond=None)
    rss_r = float(np.sum((b - A_r @ beta_r) ** 2))
    df2 = len(b) - A.shape[1]
    if df2 <= 0 or rss_full <= 0:
        return None
    F = ((rss_r - rss_full) / lags) / (rss_full / df2)
    return {"F": round(float(F), 2), "p": round(float(1 - stats.f.cdf(F, lags, df2)), 4),
            "n": len(b), "lags": lags}


# ---------------------------------------------------------------- claims

def _walkforward(sig: pd.Series, eq_log: pd.Series) -> dict | None:
    """Trailing median signal, forward 6m outcome, no look ahead."""
    df = pd.DataFrame({"liq": sig, "fwd6": eq_log.shift(-6) - eq_log}).dropna()
    burn = REFEREE_OOS_BURN_M
    if len(df) <= burn + 24:
        return None
    labeled = np.array(
        [(1.0 if df["liq"].iloc[i] > df["liq"].iloc[:i].median() else 0.0,
          df["fwd6"].iloc[i]) for i in range(burn, len(df))]
    )
    hi = labeled[labeled[:, 0] == 1, 1]
    lo = labeled[labeled[:, 0] == 0, 1]
    if not len(hi) or not len(lo):
        return None
    rng = np.random.default_rng(_SPREAD_SEED)
    n = len(labeled)
    block = REFEREE_BOOT_BLOCK_M
    nblocks = int(np.ceil(n / block))
    draws = []
    for _ in range(REFEREE_BOOT_N):
        starts = rng.integers(0, n, nblocks)
        idx = np.concatenate([np.arange(s, s + block) % n for s in starts])[:n]
        bs = labeled[idx]
        h_, l_ = bs[bs[:, 0] == 1, 1], bs[bs[:, 0] == 0, 1]
        if len(h_) and len(l_):
            draws.append(float(h_.mean() - l_.mean()))
    lo_ci, hi_ci = np.percentile(draws, [2.5, 97.5])
    return {
        "eval_window": [df.index[burn].date().isoformat(),
                        df.index[-1].date().isoformat()],
        "months_high_liq": int(len(hi)),
        "months_low_liq": int(len(lo)),
        "mean_fwd6m_logret_high_liq": round(float(hi.mean()), 4),
        "mean_fwd6m_logret_low_liq": round(float(lo.mean()), 4),
        "spread_6m_logret": round(float(hi.mean() - lo.mean()), 4),
        "spread_ci95": [round(float(lo_ci), 4), round(float(hi_ci), 4)],
    }


def _claim1(liq_yoy: pd.Series, eq_log: pd.Series) -> dict:
    out: dict = {"stated": CLAIMS["claim1"]}

    horizons = {}
    for h in (1, 3, 6, 9, 12, 18):
        horizons[f"fwd_{h}m"] = _corr(liq_yoy, eq_log.shift(-h) - eq_log)
    out["forward_return_corr"] = horizons

    ret = eq_log.diff()
    correlogram: dict[int, float | None] = {}
    for k in range(0, 19):
        c = _corr(liq_yoy, ret.shift(-k))
        correlogram[k] = None if c is None else c["corr"]
    ks = [k for k, v in correlogram.items() if v is not None]
    out["monthly_correlogram"] = {str(k): v for k, v in correlogram.items()}
    out["peak_lead_months"] = None if not ks else int(max(ks, key=lambda k: correlogram[k]))
    out["walkforward_oos"] = _walkforward(liq_yoy, eq_log)

    out["granger"] = {
        "liq_to_returns": _granger(ret, liq_yoy.diff()),
        "returns_to_liq": _granger(liq_yoy.diff(), ret),
    }
    return out


def _claim2(liq_yoy: pd.Series, indpro: pd.Series) -> dict:
    ip_yoy = np.log(indpro).diff(12)
    correlogram: dict[int, float | None] = {}
    for k in range(0, 25):
        c = _corr(liq_yoy, ip_yoy.shift(-k))
        correlogram[k] = None if c is None else c["corr"]
    ks = [k for k, v in correlogram.items() if v is not None]
    peak_k = None if not ks else int(max(ks, key=lambda k: correlogram[k]))
    return {
        "stated": CLAIMS["claim2"],
        "correlogram": {str(k): v for k, v in correlogram.items()},
        "peak_lead_months": peak_k,
        "peak_detail": None if peak_k is None else _corr(liq_yoy, ip_yoy.shift(-peak_k)),
        "corr_at_claimed_13m": _corr(liq_yoy, ip_yoy.shift(-13)),
        "contemporaneous": correlogram.get(0),
    }


def _net_liquidity(fed_assets: pd.Series, tga: pd.Series | None,
                   rrp: pd.Series | None, eq_log: pd.Series) -> dict:
    """The strongest known variant of the asset price claim: net liquidity,
    central bank assets minus the Treasury cash balance minus reverse repo
    take up. The level chart of this series against equities is the famous
    exhibit; the honest question is whether its growth predicts forward
    returns out of sample, so it gets exactly the tests the gross aggregate
    got. US only by construction: the drains are dollar system drains."""
    if tga is None or tga.dropna().empty or rrp is None or rrp.dropna().empty:
        return {"ok": False, "reason": "tga or rrp series unavailable"}
    fed_bn = fed_assets.resample("ME").last() / 1_000.0
    tga_b = tga.dropna()
    if float(tga_b.iloc[-1]) > 50_000.0:  # a $M print, same heuristic as ledger
        tga_b = tga_b / 1_000.0
    tga_b = tga_b.resample("ME").last()
    rrp_b = rrp.dropna().resample("ME").last()
    net = (fed_bn - tga_b - rrp_b).dropna()
    if len(net) < _MIN_MONTHS:
        return {"ok": False, "reason": f"only {len(net)} overlapping months"}
    if float(net.min()) <= 0:
        return {"ok": False, "reason": "net liquidity non positive in window"}
    net_yoy = np.log(net).diff(12)
    fwd6 = eq_log.shift(-6) - eq_log
    return {
        "ok": True,
        "window": [net.index[0].date().isoformat(), net.index[-1].date().isoformat()],
        "latest_usd_tn": round(float(net.iloc[-1]) / 1000.0, 2),
        "forward_return_corr": {
            f"fwd_{h}m": _corr(net_yoy, eq_log.shift(-h) - eq_log) for h in (3, 6, 12)
        },
        "fwd6_since_2015": _corr(net_yoy.loc["2015-01-31":], fwd6.loc["2015-01-31":]),
        "walkforward_oos": _walkforward(net_yoy, eq_log),
    }


def _claim3(liq_yoy: pd.Series) -> dict:
    x = liq_yoy.dropna().to_numpy()
    n = len(x)
    freqs, power = signal.periodogram(x, detrend="linear")
    mask = (freqs > 1 / 120) & (freqs < 1 / 24)
    if not mask.any():
        return {"stated": CLAIMS["claim3"], "ok": False, "reason": "series too short"}
    dominant = 1 / freqs[mask][int(np.argmax(power[mask]))]
    smooth = pd.Series(x).rolling(6, center=True).mean().dropna().to_numpy()
    peaks, _ = signal.find_peaks(smooth, distance=24)
    return {
        "stated": CLAIMS["claim3"],
        "n_months": int(n),
        "dominant_period_months": round(float(dominant), 1),
        "spectral_resolution_at_65m_pm_months": round(65.0 ** 2 / n, 1),
        "n_complete_cycles_in_sample_if_65m": round(n / 65.0, 1),
        "peak_to_peak_spacings_months": [int(s) for s in np.diff(peaks)],
    }


# ------------------------------------------------------------------ entry

def analyze(fed_assets: pd.Series, ecb_assets: pd.Series, boj_assets: pd.Series,
            usd_per_eur: pd.Series, jpy_per_usd: pd.Series,
            equity: pd.Series, indpro: pd.Series,
            tga: pd.Series | None = None, rrp: pd.Series | None = None) -> dict:
    for name, s in (("fed", fed_assets), ("ecb", ecb_assets), ("boj", boj_assets),
                    ("eur", usd_per_eur), ("jpy", jpy_per_usd),
                    ("equity", equity), ("indpro", indpro)):
        if s is None or s.dropna().empty:
            return {"ok": False, "reason": f"{name} series unavailable"}

    g3 = _monthly_g3(fed_assets, ecb_assets, boj_assets, usd_per_eur, jpy_per_usd)
    if len(g3) < _MIN_MONTHS:
        return {"ok": False, "reason": f"only {len(g3)} overlapping months"}

    liq_yoy = np.log(g3["g3"]).diff(12)
    eq_log = np.log(equity.resample("ME").last().dropna())
    ip = indpro.copy()
    ip.index = ip.index + pd.offsets.MonthEnd(0)

    fwd6 = eq_log.shift(-6) - eq_log
    subsamples = {}
    for label, sl in (("full", slice(None, None)),
                      ("first_half", slice(None, "2014-12-31")),
                      ("second_half", slice("2015-01-31", None))):
        subsamples[label] = _corr(liq_yoy.loc[sl], fwd6.loc[sl])

    liq_6m = np.log(g3["g3"]).diff(6) * 2.0
    robustness = {f"fwd_{h}m": _corr(liq_6m, eq_log.shift(-h) - eq_log)
                  for h in (3, 6, 12)}

    return {
        "ok": True,
        "asof": g3.index[-1].date().isoformat(),
        "window": [g3.index[0].date().isoformat(), g3.index[-1].date().isoformat()],
        "n_months": int(len(g3)),
        "latest": {
            "g3_usd_tn": round(float(g3["g3"].iloc[-1]) / 1000.0, 2),
            "components_usd_tn": {
                k: round(float(g3[k].iloc[-1]) / 1000.0, 2) for k in ("fed", "ecb", "boj")
            },
            "yoy_pct": round(float(liq_yoy.dropna().iloc[-1]) * 100.0, 2)
            if not liq_yoy.dropna().empty else None,
        },
        "claim1": _claim1(liq_yoy, eq_log),
        "claim2": _claim2(liq_yoy, ip),
        "claim3": _claim3(liq_yoy),
        "subsample_stability_fwd6m": subsamples,
        "robustness_liq_6m": robustness,
        "robustness_net_liquidity": _net_liquidity(fed_assets, tga, rrp, eq_log),
        "series": [
            [d.date().isoformat(), round(float(v), 1),
             round(float(liq_yoy.loc[d]) * 100.0, 2) if pd.notna(liq_yoy.loc[d]) else None]
            for d, v in g3["g3"].items()
        ],
        "method": (
            "G3 central bank assets are converted to dollars at monthly average "
            "spot and summed month end from 2003. Year on year growth of that "
            "aggregate is correlated with forward equity returns at horizons of "
            "1 to 18 months and with forward industrial production growth at 0 "
            "to 24 months; every correlation carries a circular moving block "
            "bootstrap interval. The predictive claim is additionally tested "
            "walk forward with a trailing median signal and no look ahead, and "
            "with lagged OLS F tests in both causal directions. Cycle length is "
            "read from a linearly detrended periodogram with its resolution "
            "limit stated. The transform choice is stress tested by rerunning "
            "the forward correlations on six month annualized growth, and the "
            "strongest known variant of the claim, net liquidity defined as "
            "central bank assets minus the Treasury cash balance minus reverse "
            "repo take up, gets the same forward correlations and the same "
            "walk forward test."
        ),
    }
