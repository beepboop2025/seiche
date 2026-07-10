"""Regatta — the fleet raced honestly (Model Confidence Set).

With a fleet of forecast members there is an objection PROOF's per-engine
permutation nulls cannot answer: WITH THIS MANY BOATS, ONE HAD TO LOOK GOOD.
Multiple-comparison inflation is how quant shops fool themselves — rank
fifteen models on the same sample and the winner's edge is part skill, part
selection. The Model Confidence Set (Hansen, Lunde & Nason 2011) is the
standard cure: starting from the full fleet, iteratively eliminate boats
whose loss is statistically worse than the best remaining, using a block
bootstrap of the daily loss differentials to respect serial correlation.
What survives is the set of models statistically INDISTINGUISHABLE from the
leader at the stated confidence — the honest podium, snoop-corrected.

Entrants: every calibrated fleet member's out-of-sample stream (the same
walk-forward series the Stack races), the PUBLISHED stack probability, and
expanding calendar climatology as the boat everyone must beat. Losses are
daily Brier scores on the shared PROOF label. The race runs only on days
where EVERY entrant has a value (a balanced panel is an MCS requirement) —
the coverage cost of that intersection is printed.

Honesty notes:
  - the MCS bootstrap uses a fixed block size and fixed seed — the podium is
    deterministic and re-runnable;
  - members share upstream data, so losses are positively correlated: the
    MCS handles correlated differentials by construction (that is its whole
    point), but the caveat that this shrinks effective information is stated;
  - inclusion is NOT a skill claim — a set containing everything means the
    sample cannot separate the fleet yet, and the verdict says so plainly;
  - context/honesty layer: nothing here feeds the composite or the Stack.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import (
    BACKTEST_EVENT_FWD_D,
    REGATTA_BLOCK_BD,
    REGATTA_MCS_SIZE,
    REGATTA_MIN_ROWS,
    REGATTA_REPS,
    REGATTA_SEED,
)

MEMBER_LABELS = {
    "rule": "rule index (calibrated)",
    "ml": "ML Lab",
    "tide": "Tide Tables",
    "swell": "Swell curve",
    "bathy": "Bathymetry",
    "tell": "The Tell (calibrated)",
    "stack_published": "the Stack (as published)",
    "climatology": "calendar climatology",
}


def analyze(cal: pd.DataFrame, p_pub: pd.Series, y: pd.Series) -> dict:
    """Inputs are the Stack's own OOS streams: `cal` = calibrated member
    probabilities (walk-forward), `p_pub` = the published fleet probability,
    `y` = the shared PROOF label (NaN while the window is open)."""
    try:
        from arch.bootstrap import MCS
    except ImportError:
        return {"ok": False, "reason": "arch not installed (pip install arch)"}

    if cal is None or cal.dropna(how="all").empty:
        return {"ok": False, "reason": "no member OOS streams"}

    probs = cal.copy()
    if p_pub is not None and not p_pub.dropna().empty:
        probs["stack_published"] = p_pub
    # PIT climatology: expanding event rate over labels RESOLVED by each day
    # (a label needs its forward window closed before it may inform the rate).
    y = y.reindex(probs.index)
    resolved_rate = y.expanding(min_periods=60).mean().shift(BACKTEST_EVENT_FWD_D)
    probs["climatology"] = resolved_rate

    panel = pd.concat({"y": y}, axis=1)
    panel.columns = ["y"]
    for c in probs.columns:
        panel[c] = probs[c]
    panel = panel.dropna()
    models = [c for c in panel.columns if c != "y"]
    if len(panel) < REGATTA_MIN_ROWS:
        return {
            "ok": False,
            "reason": f"only {len(panel)} common scored days across all entrants "
                      f"(< {REGATTA_MIN_ROWS}) — the balanced panel is too short to race",
        }
    if len(models) < 3:
        return {"ok": False, "reason": f"only {len(models)} entrants — no race"}

    yv = panel["y"].to_numpy(dtype=float)
    losses = pd.DataFrame(
        {m: (panel[m].to_numpy(dtype=float) - yv) ** 2 for m in models},
        index=panel.index,
    )

    # Numerically identical entrants (e.g. the published stack when it IS the
    # single-member mean) have zero loss differential and break the MCS
    # elimination step — dedupe before the race, disclose the merge.
    keep: list[str] = []
    duplicates: dict[str, str] = {}
    for m in losses.columns:
        twin = next((k for k in keep if np.allclose(losses[m], losses[k], atol=1e-12)), None)
        if twin is None:
            keep.append(m)
        else:
            duplicates[m] = twin
    losses = losses[keep]
    models = keep
    if len(models) < 3:
        return {"ok": False, "reason": (
            f"only {len(models)} distinct entrants after deduplication "
            f"({', '.join(f'{a}≡{b}' for a, b in duplicates.items()) or 'none merged'}) — no race"
        )}

    try:
        mcs = MCS(
            losses, size=REGATTA_MCS_SIZE, reps=REGATTA_REPS,
            block_size=REGATTA_BLOCK_BD, method="R", bootstrap="stationary",
            seed=REGATTA_SEED,
        )
        mcs.compute()
    except Exception as exc:  # noqa: BLE001 — a broken race must print, not crash the board
        return {"ok": False, "reason": f"MCS failed: {type(exc).__name__}: {exc}"}
    pvals = mcs.pvalues["Pvalue"]
    included = set(mcs.included)

    rows = []
    for m in sorted(models, key=lambda k: float(losses[k].mean())):
        rows.append({
            "model": m,
            "label": MEMBER_LABELS.get(m, m),
            "brier": round(float(losses[m].mean()), 4),
            "mcs_pvalue": round(float(pvals.get(m, np.nan)), 3),
            "in_set": m in included,
        })

    n_in = len(included)
    beat_clim = "climatology" not in included
    leader = rows[0]["model"]
    if n_in == len(models):
        verdict = (
            f"the sample cannot separate the fleet: ALL {len(models)} entrants survive the "
            f"{int((1 - REGATTA_MCS_SIZE) * 100)}% confidence set — treat member rankings "
            f"as provisional, not earned"
        )
    elif beat_clim:
        verdict = (
            f"{n_in} of {len(models)} entrants survive at {int((1 - REGATTA_MCS_SIZE) * 100)}% "
            f"confidence and climatology is ELIMINATED — the surviving fleet "
            f"({', '.join(sorted(included))}) has snoop-corrected skill; best point Brier: {leader}"
        )
    else:
        verdict = (
            f"{n_in} of {len(models)} entrants survive — but CLIMATOLOGY IS AMONG THEM: "
            f"no boat has statistically separated from the base rate on this sample; "
            f"point rankings are decoration"
        )

    coverage = float(len(panel)) / float(max(1, int(probs.notna().any(axis=1).sum())))
    return {
        "ok": True,
        "asof": panel.index[-1].date().isoformat(),
        "n_days": int(len(panel)),
        "n_events": int(yv.sum()),
        "size": REGATTA_MCS_SIZE,
        "rows": rows,
        "included": sorted(included),
        "verdict": verdict,
        "duplicates_merged": duplicates or None,
        "balanced_panel_coverage": round(coverage, 2),
        "caveats": [
            "MCS inclusion is a statement about statistical indistinguishability, not skill: "
            "a set containing everything means the sample is too short to separate the fleet",
            "the race runs only on days where EVERY entrant has a value — coverage share of "
            "the balanced panel is printed above",
            "members share upstream data, so losses are positively correlated; the MCS "
            "bootstraps loss DIFFERENTIALS (its design), but shared data still shrinks the "
            "effective information",
            "overlapping 5bd labels are serially correlated — the stationary block bootstrap "
            f"(block {REGATTA_BLOCK_BD}bd) is the mitigation, not a cure",
            "context/honesty layer: nothing here feeds the composite or the Stack (doctrine)",
        ],
        "method": (
            f"Model Confidence Set (Hansen–Lunde–Nason 2011) over daily Brier losses vs the "
            f"shared PROOF label; entrants = calibrated fleet members + published stack + "
            f"expanding PIT climatology; stationary block bootstrap, block {REGATTA_BLOCK_BD}bd, "
            f"{REGATTA_REPS} reps, seed {REGATTA_SEED}, elimination rule 'R', size "
            f"{REGATTA_MCS_SIZE:g}. Survivors are indistinguishable from the best at "
            f"{int((1 - REGATTA_MCS_SIZE) * 100)}% confidence."
        ),
    }
