"""Merian Modes — the seiche eigenmodes, estimated instead of assumed.

Merian's formula T = 2L/sqrt(g h) gives a real basin's standing-wave period
from its geometry: length and depth in, period out. The funding basin's
geometry is unobservable, so we go the other way and read its ACTUAL modes
out of the plumbing panel with Hankel-DMD — time-delay-embedded dynamic mode
decomposition, a finite-dimensional estimate of the Koopman operator. The
lineage deserves stating plainly: the Koopman operator is classical dynamics
carried in Hilbert-space clothes (Koopman–von Neumann mechanics, 1931–32) —
the same operator formalism as quantum mechanics, used here for exactly what
it is: a spectral decomposition of the dynamics into modes, each with a
frequency and a growth rate. Nothing quantum happens to a repo rate.

Each mode j has eigenvalue lambda_j: period = 2*pi/|arg(lambda)| business
days, growth = ln|lambda| per business day. A mode with |lambda| > 1 is a
GROWING oscillation — instability visible in the dynamics before levels
move. The instability index g* is the walk-forward maximum growth rate over
the modes that actually carry amplitude, read as an expanding percentile vs
its own past. A high-amplitude ~21bd mode is Resonance's month-end forcing
seen a second, independent way — same bell, different instrument.

Honesty notes:
  - DMD is a LINEAR fit to a trailing window of a nonlinear basin: the modes
    are a local linearization and their eigenvalues drift with the window;
  - the 5bd linear mode forecast will very likely NOT beat persistence — the
    engine scores itself walk-forward and says so (forecast_skill): modes
    are STRUCTURE (periods, growth rates, what rings together), not a
    crystal ball;
  - expanding/trailing statistics only: trailing-median detrend, EXPANDING
    standardization, trailing fit windows, sampling at fixed integer offsets
    from the series start — the value at T never changes when future data
    arrives (Time Machine safe, enforced by a unit test);
  - no composite contribution by doctrine: Merian is context/amplifier
    evidence, not stress evidence.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import (
    MERIAN_DELAYS,
    MERIAN_ENERGY,
    MERIAN_MIN_HISTORY_D,
    MERIAN_MIN_SERIES,
    MERIAN_PERIOD_BAND_BD,
    MERIAN_RANK_MAX,
    MERIAN_SAMPLE_BD,
    MERIAN_TOP_MODES,
    MERIAN_WINDOW_D,
)

DETREND_D = 63           # trailing median detrend window (bd)
DETREND_MIN = 21
STD_MIN_OBS = 120        # expanding-std warmup (obs)
COVERAGE = 0.90          # min share of a fit window a series must cover
FFILL_LIMIT = 5
FORECAST_H_BD = 5        # honesty-block forecast horizon
PCTL_MIN_SAMPLES = 40    # expanding-percentile warmup (walk-forward samples)
AMP_SHARE_FLOOR = 0.05   # noise modes must not drive the gauge


def _standardize(s: pd.Series) -> pd.Series:
    """resid = x − trailing 63bd median; z = resid / EXPANDING std — strictly
    trailing, so mixed units (bp, $B) enter the fit on equal footing and the
    value at T never changes when future data arrives."""
    x = s.dropna()
    resid = x - x.rolling(DETREND_D, min_periods=DETREND_MIN).median()
    sd = resid.expanding(STD_MIN_OBS).std()
    return (resid / sd.replace(0.0, np.nan)).dropna()


def _window(df: pd.DataFrame, t: int) -> pd.DataFrame | None:
    """Trailing fit window ending at row t (exclusive): keep series covering
    >= 90% of the window, ffill small gaps, drop remaining-NaN rows. All
    decisions use window data only — trailing by construction."""
    win = df.iloc[t - MERIAN_WINDOW_D : t]
    win = win.loc[:, win.notna().mean() >= COVERAGE]
    if win.shape[1] < MERIAN_MIN_SERIES:
        return None
    win = win.ffill(limit=FFILL_LIMIT).dropna()
    if len(win) < MERIAN_WINDOW_D // 2:
        return None
    return win


def _dmd_fit(win: pd.DataFrame) -> dict | None:
    """One Hankel-DMD fit: delay-embed, SVD-truncate, eigendecompose the
    reduced propagator, exact modes, amplitudes from the LAST snapshot (the
    CURRENT excitation of each mode). Conjugate pairs collapsed for the
    reported mode list (raw spectrum kept for the forecast)."""
    Z = win.to_numpy(dtype=float).T                    # n_series x T
    n, T = Z.shape
    d = MERIAN_DELAYS
    if T < d + 10:
        return None
    m = T - d + 1                                      # snapshot count
    H = np.empty((n * d, m))
    for k in range(d):                                 # block k = lag k; block 0 = now
        H[k * n : (k + 1) * n, :] = Z[:, d - 1 - k : T - k]
    X, Xp = H[:, :-1], H[:, 1:]
    U, S, Vt = np.linalg.svd(X, full_matrices=False)
    if S[0] <= 0:
        return None
    energy = np.cumsum(S**2) / float(np.sum(S**2))
    r_energy = int(np.searchsorted(energy, MERIAN_ENERGY) + 1)
    r_num = int((S > S[0] * max(X.shape) * np.finfo(float).eps).sum())
    r = max(1, min(MERIAN_RANK_MAX, r_energy, r_num))
    B = Xp @ (Vt[:r].T / S[:r])                        # Xp V Sigma^-1
    lam, W = np.linalg.eig(U[:, :r].T @ B)             # Atilde spectrum
    Phi = B @ W                                        # exact DMD modes
    x_last = H[:, -1]
    b = np.linalg.lstsq(Phi.astype(complex), x_last.astype(complex), rcond=None)[0]

    # Collapse conjugate pairs: keep the arg(lambda) >= 0 member, double its
    # amplitude weight — each oscillation is reported ONCE.
    weights = np.abs(b) * np.linalg.norm(Phi, axis=0)
    kept: list[dict] = []
    for j in range(len(lam)):
        lj = complex(lam[j])
        if abs(lj) < 1e-12:
            continue
        if lj.imag < -1e-10:                           # conjugate partner
            continue
        w = float(weights[j]) * (2.0 if lj.imag > 1e-10 else 1.0)
        g = float(np.log(abs(lj)))
        ang = abs(float(np.angle(lj)))
        period = float(2.0 * np.pi / ang) if ang >= 1e-6 else None
        efold = float(1.0 / abs(g)) if abs(g) > 1e-8 else None
        kept.append({"g": g, "period": period, "efold": efold, "w": w})
    total = sum(mm["w"] for mm in kept)
    if not kept or total <= 0:
        return None
    for mm in kept:
        mm["share"] = mm["w"] / total
        p = mm["period"]
        mm["label"] = (
            "month-end mode" if p is not None and 18.0 <= p <= 24.0
            else "quarter mode" if p is not None and 55.0 <= p <= 70.0
            else None
        )
        mm["direction"] = "growing" if mm["g"] > 0 else "decaying"
    kept.sort(key=lambda mm: -mm["share"])
    return {
        "modes": kept,
        "rank": r,
        "lam": lam,
        "Phi": Phi,
        "b": b,
        "x_last": x_last,
        "cols": list(win.columns),
    }


def _g_star(modes: list[dict]) -> float | None:
    """Max growth rate over modes in the reported period band (or
    non-oscillatory) that carry real amplitude — the instability index."""
    lo, hi = MERIAN_PERIOD_BAND_BD
    best = None
    for mm in modes:
        if mm["share"] < AMP_SHARE_FLOOR:
            continue
        p = mm["period"]
        if p is not None and not (lo <= p <= hi):
            continue
        if best is None or mm["g"] > best:
            best = float(mm["g"])
    return best


def analyze(panel: dict[str, pd.Series], spread_key: str = "SOFR-IORB") -> dict:
    """panel: name -> daily level series (the hydrophone's panel, mixed
    units). Detrend/standardization handled here, strictly trailing."""
    zs: dict[str, pd.Series] = {}
    for name, s in panel.items():
        z = _standardize(s)
        if len(z) >= MERIAN_MIN_HISTORY_D:
            zs[name] = z
    if len(zs) < MERIAN_MIN_SERIES:
        return {
            "ok": False,
            "reason": (
                f"only {len(zs)} of {len(panel)} panel series carry >= "
                f"{MERIAN_MIN_HISTORY_D} usable obs after trailing standardization "
                f"(need {MERIAN_MIN_SERIES})"
            ),
        }
    df = pd.concat(zs, axis=1).sort_index().dropna(how="all")
    if len(df) < max(MERIAN_MIN_HISTORY_D, MERIAN_WINDOW_D + MERIAN_SAMPLE_BD):
        return {"ok": False, "reason": f"panel grid too short ({len(df)}d < {MERIAN_MIN_HISTORY_D}d)"}

    n_rows = len(df)
    spread = df[spread_key] if spread_key in df.columns else None

    # ---- walk-forward: one fit per deterministic sample position ----------
    # t = MERIAN_WINDOW_D + k*MERIAN_SAMPLE_BD, an integer offset from the
    # series START — appending future data never moves a historical sample.
    samp_dates: list[pd.Timestamp] = []
    samp_g: list[float] = []
    err_dmd: list[float] = []
    err_per: list[float] = []
    for t in range(MERIAN_WINDOW_D, n_rows + 1, MERIAN_SAMPLE_BD):
        win = _window(df, t)
        if win is None:
            continue
        fit = _dmd_fit(win)
        if fit is None:
            continue
        g = _g_star(fit["modes"])
        if g is not None:
            samp_dates.append(df.index[t - 1])
            samp_g.append(g)
        # Forecast honesty: propagate the raw spectrum 5bd, read the
        # spread coordinate (first delay block), score vs persistence.
        # Unresolved end-of-sample forecasts are EXCLUDED, not guessed.
        if spread is not None and spread_key in fit["cols"] and t - 1 + FORECAST_H_BD < n_rows:
            real = spread.iloc[t - 1 + FORECAST_H_BD]
            if pd.notna(real):
                i = fit["cols"].index(spread_key)
                x5 = np.real(fit["Phi"] @ ((fit["lam"].astype(complex) ** FORECAST_H_BD) * fit["b"]))
                err_dmd.append(abs(float(x5[i]) - float(real)))
                err_per.append(abs(float(fit["x_last"][i]) - float(real)))

    if not samp_g:
        return {"ok": False, "reason": "no walk-forward fit produced a qualifying mode"}
    g_samp = pd.Series(samp_g, index=pd.DatetimeIndex(samp_dates), dtype=float)
    pctl_samp = g_samp.expanding(PCTL_MIN_SAMPLES).rank(pct=True) * 100.0
    g_pctl_daily = pctl_samp.reindex(df.index).ffill().dropna()

    # ---- current fit: the live window ending today -------------------------
    cur_win = _window(df, n_rows)
    cur = _dmd_fit(cur_win) if cur_win is not None else None
    if cur is None:
        return {"ok": False, "reason": "current fit window not usable (coverage below floor)"}
    g_now = _g_star(cur["modes"])

    if g_now is None:
        pctl_now = None
        inst_verdict = "no mode with meaningful amplitude sits in the reported period band — gauge silent"
    else:
        if len(g_samp) >= PCTL_MIN_SAMPLES:
            ref = np.append(g_samp.to_numpy(), g_now)
            pctl_now = float((ref <= g_now).mean() * 100.0)
        else:
            pctl_now = None
        if g_now > 0 and pctl_now is not None and pctl_now >= 90.0:
            inst_verdict = (
                f"a growing mode is live (doubling every {np.log(2.0) / g_now:.0f} bd) — "
                f"percentile {pctl_now:.0f} of the gauge's own history"
            )
        elif g_now > 0:
            inst_verdict = (
                "a marginally growing mode, unremarkable vs the gauge's own history — "
                "watch, don't act"
            )
        else:
            inst_verdict = (
                f"dominant modes are decaying — the basin is damping its oscillations "
                f"(g* = {g_now:.4f}/bd)"
            )

    # ---- forecast skill: the block that keeps the engine honest ------------
    n_scored = len(err_dmd)
    if n_scored >= 20 and sum(err_per) > 0:
        ratio = float(np.mean(err_dmd) / np.mean(err_per))
        if ratio >= 1.0:
            fs_verdict = (
                f"the linear mode forecast does NOT beat persistence (MAE ratio {ratio:.2f}) — "
                "expected, and said plainly: the modes are structure (periods, growth rates, "
                "what rings together), not a crystal ball"
            )
        else:
            fs_verdict = (
                f"mode forecast edges persistence out-of-sample (MAE ratio {ratio:.2f}) — "
                "treat as fragile; the modes' value is still the structure, not the point forecast"
            )
        forecast_skill = {"mae_ratio": round(ratio, 3), "n_scored": int(n_scored), "verdict": fs_verdict}
    else:
        forecast_skill = {
            "mae_ratio": None,
            "n_scored": int(n_scored),
            "verdict": "too few resolved walk-forward forecasts to score the modes against persistence",
        }

    modes_pub = []
    for mm in cur["modes"][:MERIAN_TOP_MODES]:
        modes_pub.append({
            "period_bd": round(float(mm["period"]), 1) if mm["period"] is not None else None,
            "efold_bd": round(float(mm["efold"]), 1) if mm["efold"] is not None else None,
            "growth_per_bd": round(float(mm["g"]), 5),
            "direction": mm["direction"],
            "amp_share": round(float(mm["share"]), 3),
            "label": mm["label"],
        })

    # Chart rows at the walk-forward cadence — the honest granularity (the
    # daily series is a 5bd step function; _g_pctl_series carries the daily
    # forward-filled percentile for consumers).
    rows = [[d.date().isoformat(), float(round(float(v), 5))] for d, v in g_samp.items()][-500:]

    me_share = max((mm["share"] for mm in cur["modes"] if mm["label"] == "month-end mode"), default=0.0)
    resonance_note = (
        f" Current fit: a ~21bd mode carries {me_share:.0%} of amplitude — Resonance's "
        "month-end forcing seen a second, independent way."
        if me_share >= 0.2
        else " A high-amplitude ~21bd mode, when present, is Resonance's month-end forcing "
        "seen a second, independent way."
    )
    method = (
        f"per series: resid = x − trailing {DETREND_D}bd median; z = resid / EXPANDING std "
        f"(min {STD_MIN_OBS} obs) — trailing only, mixed units enter on equal footing. One "
        f"Hankel-DMD fit per trailing {MERIAN_WINDOW_D}bd window ({MERIAN_DELAYS} delay embeds; "
        f"series covering >= {COVERAGE:.0%} of the window, ffill <= {FFILL_LIMIT}; SVD rank <= "
        f"{MERIAN_RANK_MAX} at {MERIAN_ENERGY:.0%} energy): eigenvalues give period 2π/|arg λ| bd "
        f"and growth ln|λ| per bd; amplitudes solved against the LAST snapshot (current "
        f"excitation); conjugate pairs collapsed. Instability g* = max growth over modes with "
        f"period in {MERIAN_PERIOD_BAND_BD[0]:g}–{MERIAN_PERIOD_BAND_BD[1]:g}bd (or "
        f"non-oscillatory) and amp share >= {AMP_SHARE_FLOOR:.0%}, sampled every "
        f"{MERIAN_SAMPLE_BD}bd walk-forward at fixed offsets, read as an expanding percentile vs "
        f"own history. Forecast honesty: modes propagated {FORECAST_H_BD}bd, MAE vs persistence, "
        f"published either way." + resonance_note
    )

    return {
        "ok": True,
        "asof": df.index[-1].date().isoformat(),
        "n_series": int(len(cur["cols"])),
        "series_used": list(cur["cols"]),
        "window_bd": int(MERIAN_WINDOW_D),
        "rank": int(cur["rank"]),
        "modes": modes_pub,
        "instability": {
            "g_now": round(float(g_now), 5) if g_now is not None else None,
            "pctl": round(pctl_now, 0) if pctl_now is not None else None,
            "verdict": inst_verdict,
        },
        "rows": rows,
        "forecast_skill": forecast_skill,
        "_g_pctl_series": g_pctl_daily,
        "caveats": [
            "DMD is a linear fit to a trailing window of a nonlinear basin — modes are a local linearization, their eigenvalues drift with the window",
            "growth rates near zero flip sign under noise: read g* as an expanding percentile vs the gauge's own history, not as a literal doubling time, until the percentile is extreme",
            "the 5bd mode forecast rarely beats persistence and the verdict self-demotes when it doesn't — the modes are structure evidence, not a trading signal",
            "no composite contribution by doctrine: Merian is context/amplifier evidence, not stress evidence",
        ],
        "method": method,
    }
