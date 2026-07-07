"""Bathymetry — the shape of the basin floor, measured from the water's motion.

Every other engine reads the surface. This one reconstructs the DYNAMICS the
surface obeys — the physics program applied end to end, in four blocks that
share one estimated object:

  THE FLOOR (Langevin / Kramers–Moyal reconstruction). Treat the daily pop
  statistic x (SOFR−IORB minus its trailing 5bd median — THE shared event
  statistic) as a diffusion  dx = D1(x)dt + sqrt(2 D2(x)) dW  and estimate
  drift D1 and diffusion D2 from binned conditional moments of the observed
  daily increments (Friedrich & Peinke's empirical-Langevin method). The
  effective potential V(x) = −∫D1 dx IS the basin floor: the well is where
  the spread rests, the wall stiffness V''(x*) is the restoring force, and a
  flattening well is damping loss expressed in the dynamics themselves.

  THE SPECTRUM (the quantum block). The same transitions, binned, give an
  expanding-count Markov transition operator — the discretized Fokker–Planck
  propagator. Under detailed balance that operator maps exactly to a
  Schrödinger Hamiltonian (the standard FP↔QM duality): its stationary
  density is the ground state |ψ0|², its eigenvalue moduli are energy levels
  E_k = −ln|λ_k| per business day, and the GAP between ground and first
  excited state is the inverse of the slowest relaxation time. A closing gap
  is critical slowing down measured spectrally — the operator-theoretic
  reading of what Undertow measures with autocorrelation. (Empirical markets
  are not perfectly reversible, so the mapping is stated as approximate and
  the spectrum is read on moduli — see THE ARROW.)

  THE ARROW (stochastic thermodynamics). A system in equilibrium produces no
  entropy; a DRIVEN system does, and the Schnakenberg entropy production rate
  σ = ½ Σ (J_ij − J_ji) ln(J_ij/J_ji) over stationary probability currents
  J_ij = π_i P_ij measures exactly how hard the basin is being forced away
  from detailed balance, in nats/day. Calm funding markets relax; stressed
  ones are pumped. σ ≥ 0 always (log-sum inequality) and its expanding
  percentile is the arrow-of-time gauge.

  THE ESCAPE (first-passage forecast — the prediction machine). Make the
  event bins (pop ≥ 10bp, the PROOF event) absorbing and the fitted operator
  answers the desk question EXACTLY, no simulation: P(event within h bd |
  today's state) = 1 − e_x'Q^h·1, and the mean first-passage time
  (I−Q)^{-1}·1 is the expected business days to the next event under frozen
  dynamics — Kramers' escape problem solved on the measured potential.
  Walk-forward validated vs climatology the house way, and the daily
  probability joins the Stack as its own member with its own record.

Honesty notes:
  - expanding transition counts only — the value at T never changes when
    future data arrives (truncation-equality unit test, house invariant);
  - bin edges are FIXED editorial constants (config), because data-dependent
    bins would leak the future into the past;
  - x is the event's own variable family: this is a forecast layer and a
    dynamics diagnosis, NEVER composite evidence;
  - the operator is Markov(1) on a binned daily state — memory beyond one
    day and intraday structure are not modeled, and the calendar is not an
    input (Swell owns the calendar; Bathymetry deliberately reads only the
    autonomous dynamics — the two forecasts disagree exactly where forcing,
    not dynamics, drives the risk);
  - forward probabilities hold today's operator frozen over the horizon;
  - the FP↔Schrödinger map is exact only under detailed balance, so the
    measured irreversibility (THE ARROW) is printed next to the spectrum it
    qualifies.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import (
    BACKTEST_EVENT_FWD_D,
    BACKTEST_SPIKE_BP,
    BATHY_BIN_BP,
    BATHY_BIN_MIN_N,
    BATHY_MFPT_CAP_BD,
    BATHY_SHRINK_K,
    BATHY_SPECTRUM_EVERY_BD,
    BATHY_WARMUP_D,
    BATHY_X_MIN_BP,
)
from seiche.engines.backtest import _wilson, pop_bp
from seiche.engines.tidetables import _auroc

N_BELOW = int(round((BACKTEST_SPIKE_BP - BATHY_X_MIN_BP) / BATHY_BIN_BP))
N_STATES = N_BELOW + 1                      # + one event bin [SPIKE, inf)
EVENT_BIN = N_BELOW
_CENTERS = BATHY_X_MIN_BP + (np.arange(N_STATES) + 0.5) * BATHY_BIN_BP


def _bin_of(x: np.ndarray) -> np.ndarray:
    """Fixed-edge state bins: [X_MIN, SPIKE) in BIN_BP steps, clipped below,
    one absorbing-candidate event bin at/above the PROOF spike threshold."""
    idx = np.floor((x - BATHY_X_MIN_BP) / BATHY_BIN_BP).astype(int)
    return np.clip(idx, 0, EVENT_BIN)


class _Operator:
    """Expanding sufficient statistics of the observed transitions: the count
    matrix C[i,j], the pooled increment histogram (the homogeneous-diffusion
    shrinkage prior — a thin bin borrows the panel's average step kernel, the
    same empirical-Bayes move as Swell's parent buckets), and per-bin drift/
    diffusion moments."""

    def __init__(self) -> None:
        n = N_STATES
        self.C = np.zeros((n, n))
        self.inc = np.zeros(2 * n - 1)          # increment j-i in [-(n-1), n-1]
        self.n_i = np.zeros(n)
        self.dx_sum = np.zeros(n)               # actual (not binned) increments
        self.dx_sq = np.zeros(n)
        self.total = 0
        self._ver = 0
        self._P_cache: tuple[int, np.ndarray] | None = None

    def update(self, i: int, j: int, dx: float) -> None:
        self.C[i, j] += 1.0
        self.inc[j - i + N_STATES - 1] += 1.0
        self.n_i[i] += 1.0
        self.dx_sum[i] += dx
        self.dx_sq[i] += dx * dx
        self.total += 1
        self._ver += 1

    def P(self) -> np.ndarray:
        """Row-stochastic smoothed operator: (C[i,·] + K·prior_i)/(n_i + K),
        prior_i = pooled increment kernel translated to row i (boundary mass
        folded into the edge bins)."""
        if self._P_cache is not None and self._P_cache[0] == self._ver:
            return self._P_cache[1]
        n = N_STATES
        g = self.inc + 0.5                       # Laplace floor keeps every J > 0
        g = g / g.sum()
        prior = np.zeros((n, n))
        for d in range(-(n - 1), n):
            w = g[d + n - 1]
            if w <= 0:
                continue
            src = np.arange(n)
            dst = np.clip(src + d, 0, n - 1)
            np.add.at(prior, (src, dst), w)
        P = (self.C + BATHY_SHRINK_K * prior) / (self.n_i + BATHY_SHRINK_K)[:, None]
        self._P_cache = (self._ver, P)
        return P

    def p_event(self, i: int, horizon: int) -> float:
        """Exact first-passage: event bins absorbing, P(hit within horizon
        steps | start bin i) = 1 − e_i' Q^horizon 1."""
        if i >= EVENT_BIN:
            return 1.0
        Q = self.P()[:EVENT_BIN, :EVENT_BIN]
        v = np.zeros(EVENT_BIN)
        v[i] = 1.0
        for _ in range(horizon):
            v = v @ Q
        return float(np.clip(1.0 - v.sum(), 0.0, 1.0))


def _stationary(P: np.ndarray, iters: int = 300) -> np.ndarray:
    """Stationary distribution by deterministic power iteration (every entry
    of the smoothed P is positive, so Perron–Frobenius guarantees convergence
    to a unique fixed point)."""
    pi = np.full(P.shape[0], 1.0 / P.shape[0])
    for _ in range(iters):
        pi = pi @ P
        pi = pi / pi.sum()
    return pi


def _visited_chain(op: _Operator) -> tuple[np.ndarray, np.ndarray]:
    """The operator restricted to bins the data has actually visited (n_i >=
    BIN_MIN_N, else any visit). Spectrum and entropy are read HERE: in
    unvisited corners of state space the smoothed operator is pure prior, and
    a prior's slow random walk across empty bins would masquerade as a slow
    physical mode. No evidence, no eigenvalue."""
    mask = op.n_i >= BATHY_BIN_MIN_N
    if mask.sum() < 4:
        mask = op.n_i > 0
    P = op.P()[np.ix_(mask, mask)]
    P = P / P.sum(axis=1, keepdims=True)
    return P, mask


def _spectrum(P: np.ndarray) -> dict:
    """Eigenvalue moduli of the propagator, read as energy levels of the dual
    Schrödinger problem: E_k = −ln|λ_k| per bd; gap = E_1 (ground state has
    E_0 = 0); slowest relaxation time τ = 1/E_1."""
    lam = np.sort(np.abs(np.linalg.eigvals(P)))[::-1]
    lam2 = float(min(lam[1], 1.0 - 1e-12)) if len(lam) > 1 else 0.0
    gap = 1.0 - lam2
    tau = float(-1.0 / np.log(lam2)) if lam2 > 0 else 0.0
    levels = [float(-np.log(max(v, 1e-12))) for v in lam[1:5]]
    return {"lam2": lam2, "gap": gap, "tau_bd": tau, "levels": levels}


def _entropy_production(P: np.ndarray, pi: np.ndarray) -> float:
    """Schnakenberg entropy production of the stationary Markov chain,
    nats/bd: σ = ½ Σ_ij (J_ij − J_ji) ln(J_ij / J_ji), J_ij = π_i P_ij.
    Zero iff detailed balance holds; every term ≥ 0."""
    J = pi[:, None] * P
    Jt = J.T
    with np.errstate(divide="ignore", invalid="ignore"):
        terms = (J - Jt) * np.log(J / Jt)
    return float(np.nansum(terms) / 2.0)


def _potential(op: _Operator) -> dict:
    """The floor: D1/D2 from per-bin conditional moments, thin bins (< MIN_N)
    interpolated from their neighbors; V(x) = −∫D1 dx, min-normalized."""
    n_i, mean_ok = op.n_i, op.n_i >= BATHY_BIN_MIN_N
    if mean_ok.sum() < 4:
        return {"ok": False, "reason": "too few populated state bins"}
    d1_raw = np.where(n_i > 0, op.dx_sum / np.maximum(n_i, 1.0), np.nan)
    ex2 = np.where(n_i > 0, op.dx_sq / np.maximum(n_i, 1.0), np.nan)
    d2_raw = np.maximum((ex2 - d1_raw**2) / 2.0, 1e-9)
    xs = _CENTERS[mean_ok]
    d1 = np.interp(_CENTERS, xs, d1_raw[mean_ok])
    d2 = np.interp(_CENTERS, xs, d2_raw[mean_ok])
    V = -np.cumsum(d1) * BATHY_BIN_BP
    V -= V.min()
    well = int(np.argmin(V))
    # stiffness V'' = −dD1/dx at the well (restoring force per bp displaced)
    lo, hi = max(well - 1, 0), min(well + 1, N_STATES - 1)
    stiffness = float(-(d1[hi] - d1[lo]) / ((hi - lo) * BATHY_BIN_BP))
    temperature = float(d2[well])               # bp²/bd — the thermal energy scale
    barrier = float(V[well:].max() - V[well])   # bp²/bd, well -> event side
    return {
        "ok": True,
        "well_bp": round(float(_CENTERS[well]), 1),
        "stiffness": round(stiffness, 3),
        "temperature_bp2_bd": round(temperature, 2),
        "barrier_bp2_bd": round(barrier, 2),
        "barrier_kt": round(barrier / temperature, 2) if temperature > 0 else None,
        "curve": [
            [round(float(_CENTERS[k]), 1), round(float(V[k]), 2),
             round(float(d1[k]), 3), round(float(d2[k]), 2), int(n_i[k])]
            for k in range(N_STATES)
        ],
        "_V": V, "_well": well,
    }


def analyze(spread_bp: pd.Series, horizon: int = BACKTEST_EVENT_FWD_D) -> dict:
    s = spread_bp.dropna()
    if len(s) < BATHY_WARMUP_D + 100:
        return {"ok": False, "reason": f"insufficient spread history ({len(s)}d)"}

    grid = pd.bdate_range(s.index.min(), s.index.max())
    pop = pop_bp(s, grid).to_numpy()
    bins = np.where(np.isnan(pop), -1, _bin_of(np.nan_to_num(pop)))

    op = _Operator()
    ps, ys, cs, scored_pos = [], [], [], []
    ev_hits, ev_n = 0, 0                        # expanding climatology counter
    spec_rows: list[tuple[pd.Timestamp, float, float]] = []

    for t in range(len(grid)):
        # 1. learn the transition that ENDED today (available at today's close)
        if t > 0 and bins[t - 1] >= 0 and bins[t] >= 0:
            op.update(int(bins[t - 1]), int(bins[t]), float(pop[t] - pop[t - 1]))
        # 2. spectral/thermodynamic time series on the operator as of today
        if op.total >= BATHY_WARMUP_D and (
            op.total % BATHY_SPECTRUM_EVERY_BD == 0 or t == len(grid) - 1
        ):
            Pv, _ = _visited_chain(op)
            piv = _stationary(Pv)
            spec_rows.append((grid[t], _spectrum(Pv)["tau_bd"], _entropy_production(Pv, piv)))
        # 3. walk-forward forecast for today, scored against the future
        if (
            op.total >= BATHY_WARMUP_D
            and 0 <= bins[t] < EVENT_BIN            # from a non-event state only
            and t + horizon < len(grid)
        ):
            fpops = pop[t + 1 : t + 1 + horizon]
            if not np.all(np.isnan(fpops)):
                p = op.p_event(int(bins[t]), horizon)
                clim_day = (ev_hits + 0.5) / (ev_n + 1.0)
                ps.append(p)
                ys.append(float(np.nanmax(fpops) >= BACKTEST_SPIKE_BP))
                cs.append(1.0 - (1.0 - clim_day) ** horizon)
                scored_pos.append(t)
        if bins[t] >= 0:
            ev_hits += int(bins[t] == EVENT_BIN)
            ev_n += 1

    if op.total < BATHY_WARMUP_D:
        return {"ok": False, "reason": f"only {op.total} usable transitions (< {BATHY_WARMUP_D})"}

    # ---- validation: the house way ------------------------------------------
    validation: dict = {"ok": False, "reason": "insufficient scored history"}
    if len(ps) >= 200:
        pa, ya, ca = np.array(ps), np.array(ys), np.array(cs)
        brier = float(np.mean((pa - ya) ** 2))
        brier_clim = float(np.mean((ca - ya) ** 2))
        auroc = _auroc(ya, pa)
        reliability = []
        for lo, hi in ((0.0, 0.10), (0.10, 0.20), (0.20, 0.35), (0.35, 1.01)):
            g = (pa >= lo) & (pa < hi)
            if g.sum() >= 5:
                reliability.append({
                    "bin": f"{lo:.2f}-{hi:.2f}",
                    "mean_pred": round(float(pa[g].mean()), 3),
                    "realized": round(float(ya[g].mean()), 3),
                    "n": int(g.sum()),
                    "realized_ci95": _wilson(int(ya[g].sum()), int(g.sum())),
                })
        beats = brier < brier_clim and (auroc or 0.0) > 0.55
        validation = {
            "ok": True,
            "n_scored": len(ps),
            "n_events": int(ya.sum()),
            "auroc": round(auroc, 3) if auroc is not None else None,
            "brier": round(brier, 4),
            "brier_climatology": round(brier_clim, 4),
            "brier_skill": round(1.0 - brier / brier_clim, 3) if brier_clim > 0 else None,
            "reliability": reliability,
            "verdict": (
                "the fitted dynamics beat climatology out-of-sample — the state carries information "
                "beyond the base rate"
                if beats else
                "the fitted dynamics do NOT beat climatology on levels — read the landscape "
                "(well, barrier, gap) as structure, not the probabilities as odds"
            ),
        }

    # ---- live blocks on the full-history operator ---------------------------
    P = op.P()
    Pv, vmask = _visited_chain(op)
    piv = _stationary(Pv)
    spec = _spectrum(Pv)
    sigma = _entropy_production(Pv, piv)
    floor = _potential(op)

    tau_series = pd.Series([r[1] for r in spec_rows], index=[r[0] for r in spec_rows])
    sig_series = pd.Series([r[2] for r in spec_rows], index=[r[0] for r in spec_rows])
    tau_pctl = float(tau_series.rank(pct=True).iloc[-1] * 100.0) if len(tau_series) >= 10 else None
    sig_pctl = float(sig_series.rank(pct=True).iloc[-1] * 100.0) if len(sig_series) >= 10 else None

    last_pop = next((pop[k] for k in range(len(grid) - 1, -1, -1) if not np.isnan(pop[k])), None)
    bin_now = int(_bin_of(np.array([last_pop]))[0]) if last_pop is not None else None

    p_now, p_by_h, mfpt = None, {}, None
    if bin_now is not None:
        p_by_h = {f"h{h}": round(op.p_event(bin_now, h), 3) for h in (1, 2, 3, 5, 10)}
        p_now = p_by_h[f"h{horizon}"]
        if bin_now < EVENT_BIN:
            Q = P[:EVENT_BIN, :EVENT_BIN]
            m = np.linalg.solve(np.eye(EVENT_BIN) - Q, np.ones(EVENT_BIN))
            mfpt = float(m[bin_now])

    vcenters = _CENTERS[vmask]
    ground_state = [
        [round(float(vcenters[k]), 1), round(float(np.sqrt(piv[k])), 4)]
        for k in range(len(vcenters))
    ]

    return {
        "ok": True,
        "asof": s.index.max().date().isoformat(),
        "p_event_5bd": p_now,
        "p_by_horizon": p_by_h,
        "mfpt_bd": (round(mfpt, 0) if mfpt is not None and mfpt <= BATHY_MFPT_CAP_BD else None),
        "mfpt_capped": bool(mfpt is not None and mfpt > BATHY_MFPT_CAP_BD),
        "mfpt_cap_bd": BATHY_MFPT_CAP_BD,
        "state_now": {
            "pop_bp": round(float(last_pop), 1) if last_pop is not None else None,
            "in_event_bin": bool(bin_now == EVENT_BIN) if bin_now is not None else None,
        },
        "floor": {k: v for k, v in floor.items() if not str(k).startswith("_")},
        "spectrum": {
            "gap": round(spec["gap"], 4),
            "tau_bd": round(spec["tau_bd"], 1),
            "tau_pctl": round(tau_pctl, 0) if tau_pctl is not None else None,
            "energy_levels": [round(e, 3) for e in spec["levels"]],
            "ground_state": ground_state,
        },
        "arrow": {
            "sigma_nats_bd": round(sigma, 4),
            "pctl": round(sig_pctl, 0) if sig_pctl is not None else None,
        },
        "series": [
            [d.date().isoformat(), round(float(tau_series.loc[d]), 2),
             round(float(sig_series.loc[d]), 4)]
            for d in tau_series.index
        ][-400:],
        "n_transitions": int(op.total),
        "validation": validation,
        "_p5_series": (
            pd.Series(ps, index=grid[scored_pos]) if scored_pos else pd.Series(dtype=float)
        ),
        "caveats": [
            "x is the PROOF event's own variable family — a forecast layer and dynamics diagnosis, never composite evidence",
            "Markov(1) on binned daily states: longer memory, intraday structure and the calendar are not modeled (the calendar is Swell's job — disagreement between the two is itself information)",
            "forward probabilities and the MFPT hold today's operator frozen over the horizon",
            "the Fokker–Planck ↔ Schrödinger mapping is exact only under detailed balance; the measured irreversibility (the arrow) says how far from that the basin runs, and the spectrum is read on eigenvalue moduli",
            "bin edges are fixed editorial constants — data-dependent bins would leak the future into the past",
        ],
        "method": (
            f"state x = SOFR−IORB pop (the shared PROOF statistic), binned "
            f"[{BATHY_X_MIN_BP:g}, {BACKTEST_SPIKE_BP:g})bp in {BATHY_BIN_BP:g}bp steps + one event "
            f"bin ≥ {BACKTEST_SPIKE_BP:g}bp; expanding transition counts shrunk toward the pooled "
            f"increment kernel (K={BATHY_SHRINK_K:g}). Floor: Kramers–Moyal D1/D2 per bin, "
            f"V = −∫D1 dx, barrier printed in units of the well's diffusion (k_BT). Spectrum: "
            f"E_k = −ln|λ_k| of the propagator restricted to visited bins (no evidence, no "
            f"eigenvalue), τ = 1/gap. Arrow: Schnakenberg entropy production of the same chain. Escape: absorbing-boundary first passage, P(event ≤ h bd) "
            f"and MFPT, walk-forward validated vs climatology (warmup {BATHY_WARMUP_D} transitions)"
        ),
    }
