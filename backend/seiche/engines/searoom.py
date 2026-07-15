"""Sea Room — guaranteed coverage for the fleet's probability.

Sea room is the margin a navigator keeps between the ship and the rocks —
not a bet about where the ship is, a GUARANTEE about where it isn't. The
Stack publishes P(event); Venn–Abers calibrates that number. What neither
provides is a coverage guarantee: a daily statement over {event, no-event}
that CONTAINS the truth a stated fraction of the time, no matter how the
regime drifts. Adaptive Conformal Inference (Gibbs & Candès 2021) provides
exactly that, assumption-free: emit the set of labels whose nonconformity
score fits within a quantile of past scores, and steer the working
miscoverage level alpha_t by the realized errors —

    alpha_{t+1} = alpha_t + gamma * (alpha_target - err_t)

so long-run miscoverage tracks the target even under distribution shift
(the guarantee is on the FEEDBACK LOOP, not on any distributional
assumption). The daily reading is the set:

    {no-event}        confident quiet — the record licenses ruling stress out
    {event, no-event} the record cannot separate the outcomes today
    {event}           confident alarm — rare and loud
    {}                the forecast is so nonconforming no label fits (counted,
                      never hidden — it means alpha_t has been pushed high)

v2 — AgACI + regime-conditional accounting (arXiv:2512.03298 lineage):
  - single-gamma ACI trades adaptation speed against stability on one knob;
    AgACI (Zaffran et al. 2022) runs several gamma experts and aggregates
    their radii by exponentially-weighted average under PINBALL loss at the
    target level, so the data — not a config constant — picks the step size;
  - ACI's guarantee is MARGINAL: 90% on average can hide 70% inside STRESS
    and 97% inside CALM. When the fleet's regime series is supplied, realized
    coverage is additionally accounted PER REGIME with Wilson error bars, and
    the verdict names any regime whose interval excludes the target.

Honesty notes:
  - feedback is honestly DELAYED: a day's label resolves only when its 5bd
    event window closes, so its score joins the pool and its error steers
    alpha only then — the machinery never touches an unresolved label;
  - the empirical quantile uses the finite-sample ceil((n+1)(1-alpha))/n
    rule (validity, not asymptotics); warmup before any set is emitted;
  - deterministic — no RNG anywhere;
  - the informative rate (share of singleton days) is the honest headline: a
    coverage guarantee over {0,1} is trivially cheap, so the value of this
    engine is exactly how often it can say something SMALLER than "either".
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from seiche.config import (
    BACKTEST_EVENT_FWD_D,
    SEAROOM_ALPHA,
    SEAROOM_ETA,
    SEAROOM_GAMMAS,
    SEAROOM_WARMUP,
)

_LAG = BACKTEST_EVENT_FWD_D  # label resolution delay (bd)
_TAU = 1.0 - SEAROOM_ALPHA   # pinball level the experts are judged at


def _finite_sample_q(scores: list[float], alpha: float) -> float:
    """ceil((n+1)(1-alpha))/n empirical quantile — the split-conformal
    finite-sample rule. Returns +inf when the pool cannot support the level
    (the set is then {0,1}: honest, never anti-conservative)."""
    n = len(scores)
    k = math.ceil((n + 1) * (1.0 - alpha))
    if k > n:
        return float("inf")
    return float(np.sort(np.asarray(scores))[k - 1])


def _set_of(p: float, q: float) -> tuple[bool, bool]:
    """(contains_no_event, contains_event) at nonconformity radius q."""
    return (abs(0.0 - p) <= q, abs(1.0 - p) <= q)


def _pinball(q: float, s: float) -> float:
    """Pinball (quantile) loss of radius q against realized score s at level
    _TAU — the proper score for a (1-alpha) quantile, so EWA weights favor the
    expert whose radii are the best conformal quantiles, not the widest."""
    return _TAU * max(s - q, 0.0) + (1.0 - _TAU) * max(q - s, 0.0)


class _Expert:
    """One ACI run at a fixed gamma. Scores in [0,1] so radii are clipped to
    1.0 — a radius of 1 already contains both labels, and finite radii keep
    the pinball aggregation well-defined during thin-pool stretches."""

    def __init__(self, gamma: float):
        self.gamma = gamma
        self.alpha = SEAROOM_ALPHA
        self.emitted: dict[int, float] = {}   # day -> radius used
        self.cum_loss = 0.0
        self.covered: list[bool] = []

    def radius(self, resolved_scores: list[float], t: int) -> float:
        q = min(_finite_sample_q(resolved_scores, self.alpha), 1.0)
        self.emitted[t] = q
        return q

    def resolve(self, r: int, score: float) -> None:
        q_r = self.emitted.get(r)
        if q_r is None:
            return
        in_set = score <= q_r
        self.covered.append(bool(in_set))
        self.alpha += self.gamma * (SEAROOM_ALPHA - (0.0 if in_set else 1.0))
        self.alpha = min(max(self.alpha, 0.001), 0.999)
        self.cum_loss += _pinball(q_r, score)


def _weights(experts: list[_Expert]) -> np.ndarray:
    """EWA weights on cumulative pinball loss, anchored at the best expert so
    the exponentials never underflow to an all-zero vector."""
    losses = np.array([e.cum_loss for e in experts])
    w = np.exp(-SEAROOM_ETA * (losses - losses.min()))
    return w / w.sum()


def analyze(p_pub: pd.Series, y: pd.Series, regime: pd.Series | None = None) -> dict:
    """Inputs: the Stack's published OOS probability stream, the shared PROOF
    label (NaN while the forward window is open), and optionally the fleet's
    regime series for per-regime coverage accounting."""
    df = pd.concat({"p": p_pub, "y": y}, axis=1).dropna(subset=["p"])
    if len(df) < SEAROOM_WARMUP + 100:
        return {"ok": False, "reason": f"insufficient published-probability history ({len(df)}d)"}

    ps = df["p"].to_numpy(dtype=float)
    ys = df["y"].to_numpy(dtype=float)  # NaN where unresolved
    n = len(df)
    regimes = (
        regime.reindex(df.index).astype(str).to_numpy() if regime is not None else None
    )

    experts = [_Expert(g) for g in SEAROOM_GAMMAS]
    emitted: dict[int, tuple[float, float]] = {}  # i -> (aggregated q, p)
    kinds: list[str | None] = [None] * n
    covered: list[bool] = []
    covered_regime: list[str] = []
    resolved_scores: list[float] = []
    alphas: list[float] = []

    for t in range(n):
        # 1. resolve day t-LAG: its label is now known — score the emitted
        #    aggregated set, steer every expert, add its score to the pool.
        r = t - _LAG
        if r >= 0 and np.isfinite(ys[r]):
            score_r = abs(ys[r] - ps[r])
            if r in emitted:
                q_r, p_r = emitted[r]
                in_set = score_r <= q_r
                covered.append(bool(in_set))
                if regimes is not None:
                    covered_regime.append(regimes[r])
            for e in experts:
                e.resolve(r, score_r)
            resolved_scores.append(score_r)

        # 2. emit today's set: each expert proposes a radius at its own
        #    working alpha; the EWA-aggregated radius makes the set.
        if len(resolved_scores) >= SEAROOM_WARMUP:
            radii = np.array([e.radius(resolved_scores, t) for e in experts])
            w = _weights(experts)
            q = float(np.dot(w, radii))
            emitted[t] = (q, ps[t])
            has0, has1 = _set_of(ps[t], q)
            kinds[t] = (
                "both" if (has0 and has1) else
                "no_event" if has0 else
                "event" if has1 else "empty"
            )
        alphas.append(float(np.dot(_weights(experts),
                                   [e.alpha for e in experts])))

    scored = [k for k in kinds if k is not None]
    if len(covered) < 100 or not scored:
        return {"ok": False, "reason": "not enough resolved sets to state coverage"}

    counts = {k: scored.count(k) for k in ("no_event", "both", "event", "empty")}
    informative = (counts["no_event"] + counts["event"]) / len(scored)
    coverage = float(np.mean(covered))
    recent = [k for k in kinds[-250:] if k is not None]
    informative_250 = (
        (recent.count("no_event") + recent.count("event")) / len(recent) if recent else None
    )

    today_kind = next((k for k in reversed(kinds) if k is not None), None)
    q_now, p_now = emitted[max(emitted)] if emitted else (None, None)

    # Per-regime accounting: marginal coverage can hide regime-conditional
    # failure; name the regime where it does.
    coverage_by_regime: dict[str, dict] | None = None
    drifted_regimes: list[str] = []
    if regimes is not None and covered_regime:
        coverage_by_regime = {}
        for name in sorted(set(covered_regime)):
            hits = [c for c, g in zip(covered, covered_regime) if g == name]
            k_cov = sum(hits)
            lo, hi = _wilson(k_cov, len(hits))
            coverage_by_regime[name] = {
                "n": len(hits),
                "coverage": round(k_cov / len(hits), 3),
                "wilson95": [lo, hi],
            }
            if hi < (1.0 - SEAROOM_ALPHA) - 1e-9:
                drifted_regimes.append(name)

    w_now = _weights(experts)
    on_target = abs(coverage - (1.0 - SEAROOM_ALPHA)) <= 0.03
    verdict = (
        f"realized coverage {coverage:.1%} vs {1 - SEAROOM_ALPHA:.0%} target "
        + ("(guarantee holding)" if on_target else "(DRIFTED — read the caveats)")
        + f"; informative on {informative:.0%} of days"
        + (f" ({informative_250:.0%} over the last 250)" if informative_250 is not None else "")
        + (
            f"; REGIME LEAK: coverage below target inside {', '.join(drifted_regimes)}"
            if drifted_regimes else ""
        )
        + " — the rest of the time the honest statement is 'the record cannot rule either outcome out'"
    )

    today_reading = {
        "no_event": "confident quiet: the 90%-coverage set is {no event}",
        "both": "uncertain: the set is {event, no event} — coverage is guaranteed but uninformative today",
        "event": "CONFIDENT ALARM: the 90%-coverage set is {event}",
        "empty": "empty set: today's forecast conforms to neither outcome at the working level",
        None: "no set emitted yet",
    }[today_kind]

    out = {
        "ok": True,
        "asof": df.index[-1].date().isoformat(),
        "today": {
            "set": today_kind,
            "reading": today_reading,
            "p_published": round(float(p_now), 3) if p_now is not None else None,
            "q_radius": (round(float(q_now), 3) if q_now is not None and np.isfinite(q_now) else None),
            "alpha_working": round(float(alphas[-1]), 4),
        },
        "coverage": {
            "target": 1.0 - SEAROOM_ALPHA,
            "realized": round(coverage, 3),
            "n_resolved_sets": len(covered),
        },
        "set_counts": counts,
        "informative_rate": round(informative, 3),
        "informative_rate_250d": round(informative_250, 3) if informative_250 is not None else None,
        "experts": [
            {
                "gamma": e.gamma,
                "weight": round(float(w), 3),
                "coverage": (round(float(np.mean(e.covered)), 3) if e.covered else None),
                "alpha_working": round(e.alpha, 4),
            }
            for e, w in zip(experts, w_now)
        ],
        "verdict": verdict,
        "caveats": [
            f"label feedback is honestly delayed {_LAG}bd (the event window must close before "
            f"a label may steer alpha or join the score pool) — ACI's guarantee tolerates "
            f"delayed feedback at the cost of slower adaptation",
            "the coverage guarantee is over the FEEDBACK LOOP (long-run frequency), not per-day "
            "— any single day's set can be wrong; ~10% of them are supposed to be",
            "a set of {event, no-event} is guaranteed AND useless — the informative rate is "
            "the honest headline number, not the coverage",
            "AgACI: the step size gamma is not chosen by config but by exponentially-weighted "
            "aggregation of several gamma experts under pinball loss (the proper score for a "
            "quantile) — radii clipped at 1.0, which already contains both labels",
            "per-regime coverage is ACCOUNTING, not a per-regime guarantee — ACI steers the "
            "marginal rate; the regime table exists so conditional failure cannot hide in it",
            "finite-sample quantile rule ceil((n+1)(1-alpha))/n; warmup "
            f"{SEAROOM_WARMUP} resolved scores before the first set; deterministic, no RNG",
            "context layer over the Stack's published stream — never composite (doctrine)",
        ],
        "method": (
            f"Aggregated Adaptive Conformal Inference (Gibbs–Candès 2021; Zaffran et al. 2022) "
            f"on the published fleet probability: nonconformity |y − p|; gamma experts "
            f"{tuple(SEAROOM_GAMMAS)} each steering alpha_(t+1) = alpha_t + gamma(alpha* − err_t); "
            f"radii aggregated by EWA (eta={SEAROOM_ETA:g}) under pinball loss at the "
            f"{_TAU:.0%} level; errors evaluated on resolution ({_LAG}bd delay). Target "
            f"coverage {1 - SEAROOM_ALPHA:.0%}; sets over {{event, no-event}}; per-regime "
            f"coverage accounted with Wilson 95% intervals when the regime series is supplied."
        ),
    }
    if coverage_by_regime is not None:
        out["coverage_by_regime"] = coverage_by_regime
        out["regime_leaks"] = drifted_regimes
    return out


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval — honest small-n error bars for a proportion."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(max(0.0, center - half), 3), round(min(1.0, center + half), 3))
