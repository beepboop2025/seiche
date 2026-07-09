"""The Gyre — is prediction possible at all?

A gyre is a basin-scale circulation: persistent structure in what looks like
open water. You cannot see it from a single wave, but drop enough drifters
and the same paths keep re-emerging — the water has a geometry. Takens'
theorem (1981) is the mathematical license to look for that geometry in one
observable: the attractor of a deterministic dynamical system can be
reconstructed, up to diffeomorphism, from delay vectors of a single measured
series. If the funding basin's dynamics are low-dimensional and
deterministic, then near neighbors in delay space have near futures — and
that forecast skill DECAYS with horizon, because deterministic chaos
amplifies small differences exponentially. That decay is the chaos
fingerprint. Linearly filtered noise wears the opposite signature: whatever
"skill" it shows is pure autocorrelation, exactly reproducible by a
phase-randomized surrogate that keeps the spectrum and destroys everything
nonlinear — which is precisely what the surrogate gate here tests.

Empirical dynamic modeling (Sugihara & May 1990; Sugihara 1994) supplies the
instruments:

  SIMPLEX PROJECTION   forecast x(t+h) as the weighted future of the E+1
                       nearest delay-space neighbors; skill by horizon is
                       the decay curve — the determinism fingerprint.
  SURROGATE GATE       phase-randomized surrogates preserve the linear
                       autocorrelation exactly; actual skill must clear
                       their 95th percentile or the verdict self-demotes.
  S-MAP THETA TEST     locally weighted linear maps: if localizing the fit
                       (theta > 0) beats the global linear map, the basin
                       obeys DIFFERENT RULES AT DIFFERENT STATES —
                       state-dependent (nonlinear) dynamics.
  LOCAL STABILITY      the S-map coefficients are a local linearization of
                       the dynamics; as the top row of a companion matrix
                       their largest |eigenvalue| is the local expansion
                       multiplier — > 1 means locally expanding water,
                       where forecast skill dies fastest.

Kinship, stated honestly. Tide Tables is this engine's epistemological
cousin: Tide Tables asks WHICH history rhymes (find the nearest analogs,
publish what followed them); the Gyre asks whether the dynamics are
deterministic enough to rhyme AT ALL, how fast that predictability decays
with horizon, and whether it is state-dependent. A failed determinism gate
here is a direct caveat on every analog forecast: the analogs then inherit
only linear (autocorrelation) skill. The sibling physics engine Bathymetry
(engines/bathymetry.py) reconstructs the STOCHASTIC equation of motion —
drift plus noise, the Langevin picture; the Gyre asks the complementary
question: is there low-dimensional DETERMINISTIC structure beyond it? Both
can be right at once — a noisy well with a deterministic eddy inside it.

Honesty rules (the house bar):
  - expanding/trailing statistics only: trailing-median detrend, EXPANDING
    MAD scaling, libraries whose futures resolved strictly before the
    prediction date, Theiler exclusion so a vector can never match itself
    on autocorrelation alone — the value at T never changes when future
    data arrives (truncation-equality unit test);
  - the embedding dimension E is chosen ONCE on the warmup segment (targets
    strictly inside the first GYRE_WARMUP_D rows) and FROZEN, which makes
    the choice truncation-stable — later data never re-votes it;
  - surrogate rng is seeded (GYRE_SEED): the board is deterministic;
  - every claim carries a self-demoting verdict, and the live forecast is
    gated on BOTH walk-forward skill and the determinism gate;
  - no composite score: the Gyre is a forecast-layer citizen — evidence
    about predictability, not evidence of stress.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import (
    GYRE_DETREND_D,
    GYRE_EMBED_SCAN,
    GYRE_HORIZONS_BD,
    GYRE_MIN_HISTORY_D,
    GYRE_MIN_LIB,
    GYRE_SEED,
    GYRE_SURROGATES,
    GYRE_THETAS,
    GYRE_WARMUP_D,
)

DETREND_MIN = 10          # rolling-median detrend min_periods
MAD_MIN_OBS = 120         # expanding-MAD warmup (obs)
MIN_POST_WARMUP = 120     # scored rows past warmup before the engine speaks
SCAN_MIN_PAIRS = 30       # warmup pairs before an E's rho counts in the scan
SURR_SUBSAMPLE = 3        # every 3rd post-warmup target in the surrogate gate
SMAP_SUBSAMPLE = 5        # every 5th post-warmup target in the theta test
NONLIN_DELTA = 0.03       # rho_best − rho_linear above this = state dependence
THETA_STAB_DEFAULT = 2.0  # stability theta when the theta test reads flat
FORECAST_H_BD = 5         # live-forecast horizon
PCTL_MIN_SAMPLES = 120    # expanding-percentile warmup for lambda_loc


# ---------------------------------------------------------------------------
# preprocessing: Undertow's residual family, robust-scaled, trailing-only
# ---------------------------------------------------------------------------

def _expanding_mad(resid: pd.Series, min_periods: int = MAD_MIN_OBS) -> pd.Series:
    """Expanding median absolute deviation — inclusive of the current value,
    so it is trailing-only: appending future data never changes T's value."""
    return resid.expanding(min_periods).apply(
        lambda a: float(np.median(np.abs(a - np.median(a)))), raw=True
    )


# ---------------------------------------------------------------------------
# delay embedding + simplex machinery
# ---------------------------------------------------------------------------

def _embed(xv: np.ndarray, E: int) -> np.ndarray:
    """Delay matrix: row i = v_t = (x_t, x_{t-1}, …, x_{t-E+1}) for
    t = i + E − 1 (column 0 is the CURRENT value — the S-map coefficient
    order below depends on this)."""
    sw = np.lib.stride_tricks.sliding_window_view(xv, E)
    return np.ascontiguousarray(sw[:, ::-1])


def _dists(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """Euclidean distances, vectorized (‖a‖² + ‖b‖² − 2a·b)."""
    d2 = (A * A).sum(1)[:, None] + (B * B).sum(1)[None, :] - 2.0 * (A @ B.T)
    return np.sqrt(np.clip(d2, 0.0, None))


def _fut(xv: np.ndarray, E: int, h: int, M: int) -> np.ndarray:
    """fut[j] = x_{s+h} for the vector ending at s = j + E − 1 (NaN when the
    future runs off the sample)."""
    f = np.full(M, np.nan)
    f[: M - h] = xv[E - 1 + h:]
    return f


def _simplex(drow: np.ndarray, lib_end: int, fut: np.ndarray, k: int) -> tuple[float, np.ndarray]:
    """One simplex forecast: k nearest library rows (0..lib_end−1),
    weights exp(−d/d_min)."""
    d = drow[:lib_end]
    idx = np.argpartition(d, k - 1)[:k] if lib_end > k else np.arange(lib_end)
    dd = d[idx]
    dmin = max(float(dd.min()), 1e-12)
    w = np.exp(-dd / dmin)
    return float(np.dot(w, fut[idx]) / w.sum()), idx


def _simplex_batch(
    Dt: np.ndarray, rows: np.ndarray, emb: np.ndarray, fut: np.ndarray, E: int, h: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Simplex hindcast over candidate target rows. Library for target row i
    is rows 0..i−E−h: the neighbor's future resolved strictly before t AND
    |s−t| ≥ E+h (Theiler exclusion — no self-matching on autocorrelation
    alone). Targets with library < GYRE_MIN_LIB or an unresolved future are
    SKIPPED, not guessed. Returns (forecast, realized, persistence)."""
    M = len(emb)
    k = E + 1
    xh: list[float] = []
    yr: list[float] = []
    pe: list[float] = []
    for r_i, i in enumerate(rows):
        lib_end = i - E - h + 1
        if lib_end < GYRE_MIN_LIB or i + h > M - 1:
            continue
        f, _ = _simplex(Dt[r_i], lib_end, fut, k)
        xh.append(f)
        yr.append(float(fut[i]))       # fut[i] = x_{t+h}, resolved by the filter
        pe.append(float(emb[i, 0]))    # persistence forecast = x_t
    return np.asarray(xh), np.asarray(yr), np.asarray(pe)


def _pearson(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 3 or float(np.std(a)) <= 1e-12 or float(np.std(b)) <= 1e-12:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def _choose_E(xv: np.ndarray) -> tuple[int | None, list[dict]]:
    """Scan E on the WARMUP SEGMENT ONLY (every target, library vector and
    realized future strictly inside the first GYRE_WARMUP_D rows) at h=1;
    E* = argmax Pearson rho — then FROZEN. Because nothing past row
    GYRE_WARMUP_D ever enters, the choice is truncation-stable."""
    xw = xv[:GYRE_WARMUP_D]
    scan: list[dict] = []
    best_E, best_rho = None, -np.inf
    for E in range(GYRE_EMBED_SCAN[0], GYRE_EMBED_SCAN[1] + 1):
        emb = _embed(xw, E)
        fut = _fut(xw, E, 1, len(emb))
        rows = np.arange(len(emb))
        xh, yr, _ = _simplex_batch(_dists(emb, emb), rows, emb, fut, E, 1)
        rho = _pearson(xh, yr) if len(xh) >= SCAN_MIN_PAIRS else None
        scan.append({"E": int(E), "rho": round(float(rho), 3) if rho is not None else None})
        if rho is not None and rho > best_rho:
            best_rho, best_E = rho, E
    return best_E, scan


# ---------------------------------------------------------------------------
# surrogates + S-map
# ---------------------------------------------------------------------------

def _phase_surrogate(F: np.ndarray, n: int, rng: np.random.Generator) -> np.ndarray:
    """Phase-randomize the non-DC/non-Nyquist rfft bins: preserves the power
    spectrum (hence the full linear autocorrelation) exactly, destroys any
    nonlinear structure."""
    hi = len(F) - 1 if n % 2 == 0 else len(F)   # keep the Nyquist bin real
    ph = rng.uniform(0.0, 2.0 * np.pi, max(hi - 1, 0))
    Fs = F.copy()
    Fs[1:hi] = np.abs(F[1:hi]) * np.exp(1j * ph)
    return np.fft.irfft(Fs, n=n)


def _smap_fit(
    d: np.ndarray, lib: np.ndarray, y: np.ndarray, vq: np.ndarray, theta: float
) -> tuple[np.ndarray, float]:
    """One S-map: locally weighted linear regression over the FULL library,
    weights exp(−theta·d/d̄), intercept included, solved by lstsq on the
    weighted design. Returns (coefficients incl. intercept, prediction)."""
    if theta > 0:
        dbar = max(float(d.mean()), 1e-12)
        sw = np.sqrt(np.exp(-theta * d / dbar))
    else:
        sw = np.ones_like(d)
    A = np.empty((len(d), lib.shape[1] + 1))
    A[:, 0] = 1.0
    A[:, 1:] = lib
    A *= sw[:, None]
    c, *_ = np.linalg.lstsq(A, y * sw, rcond=None)
    return c, float(c[0] + np.dot(c[1:], vq))


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------

def analyze(spread_bp: pd.Series) -> dict:
    """spread_bp: daily SOFR−IORB (bp) with DatetimeIndex."""
    s = spread_bp.dropna()
    if len(s) < GYRE_MIN_HISTORY_D:
        return {"ok": False, "reason": f"insufficient history ({len(s)}d < {GYRE_MIN_HISTORY_D}d)"}

    # Undertow's residual family: level regimes out, noise dynamics in.
    resid = (s - s.rolling(GYRE_DETREND_D, min_periods=DETREND_MIN).median()).dropna()
    mad = _expanding_mad(resid)
    x = (resid / mad.replace(0.0, np.nan)).dropna()
    if len(x) < GYRE_WARMUP_D + MIN_POST_WARMUP:
        return {
            "ok": False,
            "reason": (
                f"only {len(x)} scaled residual rows survive the detrend/MAD warmup — "
                f"need >= {GYRE_WARMUP_D + MIN_POST_WARMUP} for post-warmup scoring"
            ),
        }
    xv = x.to_numpy(dtype=float)
    N = len(xv)
    mad_last = float(mad.loc[x.index[-1]])

    # ---- E selection: warmup segment only, then frozen ----------------------
    E, scan = _choose_E(xv)
    if E is None:
        return {"ok": False, "reason": "embedding scan produced no scorable rho on the warmup segment"}

    # One full distance matrix at E*, masked per target everywhere below.
    emb = _embed(xv, E)
    M = len(emb)
    D = _dists(emb, emb)
    rows_post = np.arange(max(GYRE_WARMUP_D - E + 1, 0), M)   # target time t >= warmup
    D_post = D[rows_post]
    fut1 = _fut(xv, E, 1, M)

    # ---- decay curve: the chaos fingerprint ----------------------------------
    decay: list[dict] = []
    for h in GYRE_HORIZONS_BD:
        fut_h = fut1 if h == 1 else _fut(xv, E, h, M)
        xh, yr, pe = _simplex_batch(D_post, rows_post, emb, fut_h, E, h)
        rho = _pearson(xh, yr)
        mae_ratio = None
        if len(xh):
            mae_p = float(np.mean(np.abs(pe - yr)))
            if mae_p > 0:
                mae_ratio = float(np.mean(np.abs(xh - yr))) / mae_p
        decay.append({
            "h": int(h),
            "rho": round(float(rho), 3) if rho is not None else None,
            "mae_ratio": round(mae_ratio, 3) if mae_ratio is not None else None,
            "n": int(len(xh)),
        })

    # ---- determinism gate: actual vs phase-randomized surrogates -------------
    rows_gate = rows_post[::SURR_SUBSAMPLE]
    xh_a, yr_a, _ = _simplex_batch(D[rows_gate], rows_gate, emb, fut1, E, 1)
    rho_act = _pearson(xh_a, yr_a)
    rng = np.random.default_rng(GYRE_SEED)
    F = np.fft.rfft(xv)
    sur_rhos: list[float] = []
    for _ in range(GYRE_SURROGATES):
        xs = _phase_surrogate(F, N, rng)
        emb_s = _embed(xs, E)
        fut_s = _fut(xs, E, 1, M)
        xh_s, yr_s, _ = _simplex_batch(_dists(emb_s[rows_gate], emb_s), rows_gate, emb_s, fut_s, E, 1)
        r_s = _pearson(xh_s, yr_s)
        if r_s is not None:
            sur_rhos.append(r_s)
    p95 = float(np.percentile(sur_rhos, 95)) if sur_rhos else None
    det_pass = bool(rho_act is not None and p95 is not None and rho_act > p95)
    determinism = {
        "rho": round(float(rho_act), 3) if rho_act is not None else None,
        "surrogate_p95": round(p95, 3) if p95 is not None else None,
        "n_surrogates": int(len(sur_rhos)),
        "verdict": (
            "deterministic structure beyond linear autocorrelation"
            if det_pass else
            "indistinguishable from linearly-filtered noise — analog forecasts here inherit only linear skill"
        ),
    }

    # ---- S-map theta test: state-dependent dynamics? --------------------------
    rows_smap = rows_post[::SMAP_SUBSAMPLE]
    preds: dict[float, list[float]] = {float(th): [] for th in GYRE_THETAS}
    reals: list[float] = []
    for i in rows_smap:
        lib_end = i - E   # h=1 Theiler + strictly-resolved-library rule
        if lib_end < GYRE_MIN_LIB or i + 1 > M - 1:
            continue
        d = D[i, :lib_end]
        lib, y = emb[:lib_end], fut1[:lib_end]
        for th in GYRE_THETAS:
            _, p = _smap_fit(d, lib, y, emb[i], float(th))
            preds[float(th)].append(p)
        reals.append(float(fut1[i]))
    reals_a = np.asarray(reals)
    rho_by = {th: _pearson(np.asarray(v), reals_a) for th, v in preds.items()}
    valid = {th: r for th, r in rho_by.items() if r is not None}
    if 0.0 in valid and len(valid) >= 2:
        theta_best = max(valid, key=lambda th: valid[th])
        rho_best, rho_lin = valid[theta_best], valid[0.0]
        delta = rho_best - rho_lin
        nonlin_pass = bool(theta_best > 0 and delta > NONLIN_DELTA)
        nonlinearity = {
            "theta_best": float(theta_best),
            "rho_linear": round(rho_lin, 3),
            "rho_best": round(rho_best, 3),
            "delta_rho": round(delta, 3),
            "n_targets": int(len(reals)),
            "verdict": (
                "state-dependent (nonlinear) dynamics — the basin obeys different rules at different states"
                if nonlin_pass else
                "a global linear map does as well — no detectable state dependence"
            ),
        }
    else:
        theta_best, nonlin_pass = None, False
        nonlinearity = {
            "theta_best": None, "rho_linear": None, "rho_best": None,
            "delta_rho": None, "n_targets": int(len(reals)),
            "verdict": "too few scorable S-map targets — the theta test is silent",
        }

    # ---- local stability: S-map Jacobian as a companion matrix ----------------
    theta_stab = float(theta_best) if nonlin_pass else THETA_STAB_DEFAULT
    tops: list[np.ndarray] = []
    dates: list[pd.Timestamp] = []
    for i in rows_post:
        lib_end = i - E
        if lib_end < GYRE_MIN_LIB:
            continue
        c, _ = _smap_fit(D[i, :lib_end], emb[:lib_end], fut1[:lib_end], emb[i], theta_stab)
        tops.append(c[1:])
        dates.append(x.index[i + E - 1])
    if not tops:
        return {"ok": False, "reason": "no post-warmup target reached the library floor"}
    comp = np.zeros((len(tops), E, E))
    comp[:, 0, :] = np.asarray(tops)
    if E >= 2:
        rr = np.arange(E - 1)
        comp[:, rr + 1, rr] = 1.0
    lam = np.abs(np.linalg.eigvals(comp)).max(axis=1)
    lam_s = pd.Series(lam, index=pd.DatetimeIndex(dates))
    pctl_s = lam_s.expanding(PCTL_MIN_SAMPLES).rank(pct=True) * 100.0
    lam_now = float(lam_s.iloc[-1])
    pctl_now = float(pctl_s.iloc[-1]) if pd.notna(pctl_s.iloc[-1]) else None
    if pctl_now is None:
        stab_verdict = "too few scored targets to read the expansion gauge against its own history"
    elif lam_now > 1.0 and pctl_now >= 90.0:
        stab_verdict = (
            f"locally expanding water (lambda {lam_now:.2f}, pctl {pctl_now:.0f}) — nearby "
            "trajectories are diverging; forecast skill dies fastest from states like today's"
        )
    elif lam_now > 1.0:
        stab_verdict = (
            f"marginally expanding (lambda {lam_now:.2f}) — unremarkable vs the gauge's own history"
        )
    else:
        stab_verdict = (
            f"locally contracting (lambda {lam_now:.2f}) — perturbations near today's state damp out"
        )
    # chart rows sampled from the END backwards so today is always included
    keep = np.arange(len(lam_s) - 1, -1, -max(1, int(np.ceil(len(lam_s) / 500))))[::-1]
    stability_rows = [
        [lam_s.index[j].date().isoformat(), round(float(lam_s.iloc[j]), 4)] for j in keep
    ]

    # ---- live forecast: simplex h=5 from the latest vector --------------------
    skill5 = next((row for row in decay if row["h"] == FORECAST_H_BD), None)
    fut5 = _fut(xv, E, FORECAST_H_BD, M)
    i_last = M - 1
    lib_end = i_last - E - FORECAST_H_BD + 1
    fc_gate = bool(
        det_pass and skill5 is not None
        and skill5["mae_ratio"] is not None and skill5["mae_ratio"] < 1.0
    )
    if lib_end >= GYRE_MIN_LIB:
        xhat5, idx5 = _simplex(D[i_last], lib_end, fut5, E + 1)
        p25, p75 = np.percentile(fut5[idx5], [25, 75])
        forecast = {
            "h_bd": int(FORECAST_H_BD),
            "point_bp": round(xhat5 * mad_last, 2),
            "p25_bp": round(float(p25) * mad_last, 2),
            "p75_bp": round(float(p75) * mad_last, 2),
            "skill": {
                "rho": skill5["rho"] if skill5 else None,
                "mae_ratio": skill5["mae_ratio"] if skill5 else None,
            },
            "verdict": (
                f"simplex beats persistence at {FORECAST_H_BD}bd (MAE ratio "
                f"{skill5['mae_ratio']:.2f}) and the determinism gate passes — the neighbor "
                "fan carries real skill"
                if fc_gate else
                f"no demonstrated {FORECAST_H_BD}bd skill vs persistence or the determinism "
                "gate failed — read the fan as neighbor context, not a forecast"
            ),
        }
    else:
        forecast = {
            "h_bd": int(FORECAST_H_BD), "point_bp": None, "p25_bp": None, "p75_bp": None,
            "skill": {"rho": None, "mae_ratio": None},
            "verdict": "library below floor for the live vector — no forecast",
        }

    return {
        "ok": True,
        "asof": x.index[-1].date().isoformat(),
        "n": int(N),
        "embedding": {"E": int(E), "scan": scan, "chosen_on": "warmup segment only, frozen"},
        "decay": decay,
        "determinism": determinism,
        "nonlinearity": nonlinearity,
        "stability": {
            "lambda_now": round(lam_now, 3),
            "pctl": round(pctl_now, 0) if pctl_now is not None else None,
            "verdict": stab_verdict,
        },
        "stability_rows": stability_rows,
        "forecast": forecast,
        "_stability_pctl_series": pctl_s.dropna(),
        "caveats": [
            "Tide Tables asks WHICH history rhymes; the Gyre asks whether the dynamics are deterministic enough to rhyme at all — a failed determinism gate demotes every analog forecast to linear skill",
            "Bathymetry reconstructs the STOCHASTIC equation of motion (drift + noise) for the same basin; the Gyre asks the complementary deterministic question — both can be right at once",
            "residual family = Undertow's residual (spread minus rolling median), so this shares the PROOF event's variable family: predictability evidence, never composite stress evidence",
            "daily closes only — the intraday dynamics where much of the funding action lives are invisible at this sampling",
            "E was chosen once on the warmup segment and frozen — later data never re-votes the embedding, which is what makes the choice truncation-stable",
        ],
        "method": (
            f"resid = spread − rolling {GYRE_DETREND_D}bd median (Undertow's residual family), "
            f"scaled by EXPANDING MAD (min {MAD_MIN_OBS} obs, trailing) → x. Takens delay "
            f"embedding v_t = (x_t,…,x_(t−E+1)); E scanned {GYRE_EMBED_SCAN[0]}–{GYRE_EMBED_SCAN[1]} "
            f"at h=1 with every target, library vector and realized future strictly inside the "
            f"first {GYRE_WARMUP_D} rows, then FROZEN — the choice is truncation-stable because "
            f"later data never re-votes it. Simplex projection: k=E+1 nearest library neighbors, "
            f"library = vectors whose h-step future resolved strictly before t with Theiler "
            f"exclusion |s−t| ≥ E+h (min {GYRE_MIN_LIB} vectors), weights exp(−d/d_min). Decay: "
            f"Pearson rho and MAE-vs-persistence per h in {tuple(GYRE_HORIZONS_BD)} over "
            f"post-warmup targets. Determinism gate: {GYRE_SURROGATES} phase-randomized "
            f"surrogates (rfft phases scrambled, DC/Nyquist kept, seed {GYRE_SEED}) preserve the "
            f"linear autocorrelation exactly; actual rho must clear their 95th percentile "
            f"(targets subsampled every {SURR_SUBSAMPLE}, same subsample for the actual). "
            f"S-map: locally weighted linear regression over the full library, weights "
            f"exp(−theta·d/d̄), theta in {tuple(GYRE_THETAS)}, lstsq on the weighted design "
            f"(every {SMAP_SUBSAMPLE}th target); state dependence = rho_best − rho(0) > "
            f"{NONLIN_DELTA}. Stability: the S-map coefficients at theta {theta_stab:g} form "
            f"the top row of an E×E companion matrix; largest |eigenvalue| = local expansion "
            f"multiplier, read as an expanding percentile vs its own past. Live forecast: "
            f"simplex h={FORECAST_H_BD} from the latest v_t, neighbor-future p25/p75, rescaled "
            f"to residual bp by the last expanding MAD; verdict gated on BOTH MAE ratio < 1 at "
            f"h={FORECAST_H_BD} and the determinism gate"
        ),
    }
