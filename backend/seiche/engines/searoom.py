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
    SEAROOM_GAMMA,
    SEAROOM_WARMUP,
)

_LAG = BACKTEST_EVENT_FWD_D  # label resolution delay (bd)


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


def analyze(p_pub: pd.Series, y: pd.Series) -> dict:
    """Inputs: the Stack's published OOS probability stream and the shared
    PROOF label (NaN while the forward window is open)."""
    df = pd.concat({"p": p_pub, "y": y}, axis=1).dropna(subset=["p"])
    if len(df) < SEAROOM_WARMUP + 100:
        return {"ok": False, "reason": f"insufficient published-probability history ({len(df)}d)"}

    ps = df["p"].to_numpy(dtype=float)
    ys = df["y"].to_numpy(dtype=float)  # NaN where unresolved
    n = len(df)

    alpha = SEAROOM_ALPHA
    resolved_scores: list[float] = []
    emitted: dict[int, tuple[float, float]] = {}  # i -> (q used, p)
    kinds: list[str | None] = [None] * n
    covered: list[bool] = []
    alphas: list[float] = []

    for t in range(n):
        # 1. resolve day t-LAG: its label is now known — score the emitted
        #    set, steer alpha, and add its nonconformity to the pool.
        r = t - _LAG
        if r >= 0 and np.isfinite(ys[r]):
            if r in emitted:
                q_r, p_r = emitted[r]
                in_set = abs(ys[r] - p_r) <= q_r
                covered.append(bool(in_set))
                alpha = alpha + SEAROOM_GAMMA * (SEAROOM_ALPHA - (0.0 if in_set else 1.0))
                alpha = min(max(alpha, 0.001), 0.999)
            resolved_scores.append(abs(ys[r] - ps[r]))

        # 2. emit today's set from RESOLVED scores only.
        if len(resolved_scores) >= SEAROOM_WARMUP:
            q = _finite_sample_q(resolved_scores, alpha)
            emitted[t] = (q, ps[t])
            has0, has1 = _set_of(ps[t], q)
            kinds[t] = (
                "both" if (has0 and has1) else
                "no_event" if has0 else
                "event" if has1 else "empty"
            )
        alphas.append(alpha)

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

    on_target = abs(coverage - (1.0 - SEAROOM_ALPHA)) <= 0.03
    verdict = (
        f"realized coverage {coverage:.1%} vs {1 - SEAROOM_ALPHA:.0%} target "
        + ("(guarantee holding)" if on_target else "(DRIFTED — read the caveats)")
        + f"; informative on {informative:.0%} of days"
        + (f" ({informative_250:.0%} over the last 250)" if informative_250 is not None else "")
        + " — the rest of the time the honest statement is 'the record cannot rule either outcome out'"
    )

    today_reading = {
        "no_event": "confident quiet: the 90%-coverage set is {no event}",
        "both": "uncertain: the set is {event, no event} — coverage is guaranteed but uninformative today",
        "event": "CONFIDENT ALARM: the 90%-coverage set is {event}",
        "empty": "empty set: today's forecast conforms to neither outcome at the working level",
        None: "no set emitted yet",
    }[today_kind]

    return {
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
        "verdict": verdict,
        "caveats": [
            f"label feedback is honestly delayed {_LAG}bd (the event window must close before "
            f"a label may steer alpha or join the score pool) — ACI's guarantee tolerates "
            f"delayed feedback at the cost of slower adaptation",
            "the coverage guarantee is over the FEEDBACK LOOP (long-run frequency), not per-day "
            "— any single day's set can be wrong; ~10% of them are supposed to be",
            "a set of {event, no-event} is guaranteed AND useless — the informative rate is "
            "the honest headline number, not the coverage",
            "finite-sample quantile rule ceil((n+1)(1-alpha))/n; warmup "
            f"{SEAROOM_WARMUP} resolved scores before the first set; deterministic, no RNG",
            "context layer over the Stack's published stream — never composite (doctrine)",
        ],
        "method": (
            f"Adaptive Conformal Inference (Gibbs–Candès 2021) on the published fleet "
            f"probability: nonconformity |y − p|, finite-sample quantile at working level "
            f"alpha_t, alpha_(t+1) = alpha_t + {SEAROOM_GAMMA:g}(({SEAROOM_ALPHA:g}) − err_t), "
            f"errors evaluated on resolution ({_LAG}bd delay). Target coverage "
            f"{1 - SEAROOM_ALPHA:.0%}; sets over {{event, no-event}}."
        ),
    }
