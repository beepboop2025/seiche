"""Harbors — national money markets as harbors off the dollar ocean.

The basins engine measures how the dollar system's basins COUPLE; this one
walks into each harbor and reads the local water line: the overnight anchor
rate, what the currency is doing against the dollar, and whether local
policy is being forced. Five harbors clear the keyless-and-honest bar today:

  EURO AREA — €STR daily (ECB Data Portal)
  CHINA     — SHIBOR O/N daily (CFETS; local history accrues, see collector)
  INDIA     — call money monthly (OECD MEI via FRED, ~2 months late BY DESIGN)
  JAPAN     — TONA daily (BOJ stat-search flat file, history to 1998)
  KOREA     — overnight call rate monthly (OECD MEI via FRED)

plus the US as the reference tide gauge (EFFR). FX legs are the Fed's own
H.10 daily fixes, quoted local-per-USD (EUR inverted to match), so UP always
means the local currency weakening against the dollar.

Monthly OECD rates are lagged mirrors of daily policy reality — they carry
their cadence on their face and are never interpolated to fake a daily feed
(monthly series chart as points, not lines). Stress per harbor is a
percentile-of-own-history blend (weights in config): FX realized vol ("it is
moving"), FX depreciation ("which way"), local rate tightening ("policy is
being forced"). Missing components renormalize the weights rather than
scoring as calm; a harbor with no scorable component says so.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import (
    HARBOR_REGIME_BP,
    HARBOR_W_FX_DEPRECIATION,
    HARBOR_W_FX_VOL,
    HARBOR_W_RATE_TIGHTENING,
)

MIN_PCTL_OBS = 60      # a percentile against fewer own-history points is noise


def _pctl(hist: pd.Series, value: float) -> float | None:
    x = hist.dropna()
    if len(x) < MIN_PCTL_OBS:
        return None
    return float((x <= value).mean() * 100.0)


def _delta_hist(s: pd.Series, days: int) -> pd.Series:
    """History of date-based changes over `days` — works on monthly and daily
    cadence alike (no fixed-position shift, no interpolation)."""
    x = s.dropna()
    if x.empty:
        return pd.Series(dtype=float)
    past = x.reindex(x.index - pd.Timedelta(days=days), method="ffill")
    past.index = x.index
    return (x - past).dropna()


def _pct_change_hist(s: pd.Series, days: int) -> pd.Series:
    x = s.dropna()
    if x.empty:
        return pd.Series(dtype=float)
    past = x.reindex(x.index - pd.Timedelta(days=days), method="ffill")
    past.index = x.index
    return ((x / past - 1.0) * 100.0).dropna()


def _rows(series_map: dict[str, pd.Series], years: int = 3, digits: int = 4) -> tuple[list[list], list[str]]:
    live = {k: v.dropna() for k, v in series_map.items() if not v.dropna().empty}
    if not live:
        return [], []
    df = pd.concat(live, axis=1, sort=False).sort_index()
    df = df[df.index >= df.index.max() - pd.DateOffset(years=years)]
    rows = [
        [d.date().isoformat()] + [None if pd.isna(v) else round(float(v), digits) for v in row]
        for d, row in df.iterrows()
    ]
    return rows, [str(c) for c in df.columns]


def analyze(harbors: dict[str, dict], effr: pd.Series) -> dict:
    """harbors: name -> {rate, rate_label, cadence ('daily'|'monthly ~2mo lag'),
    fx, fx_label}; series may be empty — absence degrades, never fakes."""
    if not harbors:
        return {"ok": False, "reason": "no harbors configured"}

    out_harbors: list[dict] = []
    cycle = {"EASING": 0, "HOLDING": 0, "TIGHTENING": 0}
    rate_chart: dict[str, pd.Series] = {}
    fx_chart: dict[str, pd.Series] = {}

    for name, spec in harbors.items():
        r = spec.get("rate", pd.Series(dtype=float)).dropna()
        f = spec.get("fx", pd.Series(dtype=float)).dropna()
        entry: dict = {"harbor": name, "cadence": spec.get("cadence", "?")}

        regime = None
        tighten_pctl = None
        if not r.empty:
            chg6m = _delta_hist(r, 183)
            chg1y = _delta_hist(r, 365)
            last_6m_bp = float(chg6m.iloc[-1]) * 100.0 if not chg6m.empty else None
            if last_6m_bp is not None:
                if last_6m_bp > HARBOR_REGIME_BP:
                    regime = "TIGHTENING"
                elif last_6m_bp < -HARBOR_REGIME_BP:
                    regime = "EASING"
                else:
                    regime = "HOLDING"
                cycle[regime] += 1
                tighten_pctl = _pctl(chg6m, float(chg6m.iloc[-1]))
            entry["rate"] = {
                "label": spec.get("rate_label", ""),
                "last_pct": round(float(r.iloc[-1]), 3),
                "asof": r.index[-1].date().isoformat(),
                "chg_6m_bp": round(last_6m_bp, 1) if last_6m_bp is not None else None,
                "chg_1y_bp": round(float(chg1y.iloc[-1]) * 100.0, 1) if not chg1y.empty else None,
                "n_obs": int(len(r)),
            }
            rate_chart[name] = r
        else:
            entry["rate"] = None
        entry["regime"] = regime

        # optional second anchor (e.g. China's secured leg beside SHIBOR's
        # unsecured one) — display-only, never blended into the stress score,
        # and no cross-tenor spread is computed (o/n vs 7d are not comparable)
        r2 = spec.get("rate2", pd.Series(dtype=float))
        r2 = r2.dropna() if r2 is not None else pd.Series(dtype=float)
        if not r2.empty:
            entry["rate2"] = {
                "label": spec.get("rate2_label", ""),
                "last_pct": round(float(r2.iloc[-1]), 3),
                "asof": r2.index[-1].date().isoformat(),
            }
        else:
            entry["rate2"] = None

        vol_pctl = None
        dep_pctl = None
        if not f.empty:
            vol10 = (f.pct_change().rolling(10).std() * np.sqrt(252) * 100.0).dropna()
            if not vol10.empty:
                vol_pctl = _pctl(vol10, float(vol10.iloc[-1]))
            dep_hist = _pct_change_hist(f, 60)
            if not dep_hist.empty:
                dep_pctl = _pctl(dep_hist, float(dep_hist.iloc[-1]))
            entry["fx"] = {
                "label": spec.get("fx_label", ""),
                "last": round(float(f.iloc[-1]), 4),
                "asof": f.index[-1].date().isoformat(),
                "chg_60d_pct": round(float(dep_hist.iloc[-1]), 2) if not dep_hist.empty else None,
                "vol10_ann_pct": round(float(vol10.iloc[-1]), 2) if not vol10.empty else None,
            }
            fx_chart[name] = f
        else:
            entry["fx"] = None

        comps = [
            (HARBOR_W_FX_VOL, vol_pctl),
            (HARBOR_W_FX_DEPRECIATION, dep_pctl),
            (HARBOR_W_RATE_TIGHTENING, tighten_pctl),
        ]
        live = [(w, p) for w, p in comps if p is not None]
        if live:
            entry["stress"] = round(sum(w * p for w, p in live) / sum(w for w, _ in live), 1)
            entry["stress_coverage"] = round(sum(w for w, _ in live), 2)
        else:
            entry["stress"] = None
            entry["stress_coverage"] = 0.0
            entry["note"] = (
                f"history accruing — {len(r)} rate obs, {len(f)} fx obs; "
                f"scores unlock at {MIN_PCTL_OBS} of the relevant history"
            )
        out_harbors.append(entry)

    if all(h["rate"] is None and h["fx"] is None for h in out_harbors):
        return {"ok": False, "reason": "no harbor has any live series"}

    us = effr.dropna()
    us_ref = None
    if not us.empty:
        chg6m = _delta_hist(us, 183)
        us_ref = {
            "last_pct": round(float(us.iloc[-1]), 3),
            "asof": us.index[-1].date().isoformat(),
            "chg_6m_bp": round(float(chg6m.iloc[-1]) * 100.0, 1) if not chg6m.empty else None,
        }
        rate_chart["US (EFFR)"] = us

    # FX indexed to 100 one year back — one honest comparability transform.
    fx_indexed: dict[str, pd.Series] = {}
    for name, f in fx_chart.items():
        base_date = f.index[-1] - pd.DateOffset(years=1)
        base = f[f.index <= base_date]
        anchor = float(base.iloc[-1]) if not base.empty else float(f.iloc[0])
        if anchor != 0.0:
            fx_indexed[name] = f / anchor * 100.0

    out_harbors.sort(key=lambda h: -(h["stress"] if h["stress"] is not None else -1.0))
    asof_all = [h["fx"]["asof"] for h in out_harbors if h["fx"]] + [
        h["rate"]["asof"] for h in out_harbors if h["rate"]
    ]

    rate_rows, rate_labels = _rows(rate_chart, years=3, digits=3)
    fx_rows, fx_labels = _rows(fx_indexed, years=1, digits=2)

    return {
        "ok": True,
        "asof": max(asof_all) if asof_all else None,
        "harbors": out_harbors,
        "cycle": {**{k.lower(): v for k, v in cycle.items()}, "us_ref": us_ref},
        "rate_rows": rate_rows,
        "rate_labels": rate_labels,
        "fx_rows": fx_rows,
        "fx_labels": fx_labels,
        "caveats": [
            "India/Japan/Korea anchor rates are OECD MEI monthly mirrors, ~2 months late "
            "by design — charted as points, never interpolated to fake a daily feed",
            "SHIBOR history accrues locally (the CFETS API serves ~1 month per request); "
            "China scores stay quarantined until enough own history exists",
            "stress percentiles are each harbor's own history — no cross-economy calibration",
        ],
        "method": (
            "stress = weighted pctl-of-own-history blend: FX 10d realized vol "
            f"(w={HARBOR_W_FX_VOL}), 60d FX depreciation vs USD (w={HARBOR_W_FX_DEPRECIATION}), "
            f"6m anchor-rate tightening (w={HARBOR_W_RATE_TIGHTENING}); missing components "
            f"renormalize, never score as calm; regime = 6m Δrate vs ±{HARBOR_REGIME_BP}bp"
        ),
    }
