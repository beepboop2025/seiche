"""Leak Audit — the one-switch leakage protocol, run against ourselves.

The strongest finding in the 2026 backtest-leakage literature is that
look-ahead bias is SELECTIVE: of all the ways a pipeline can cheat, two
dominate everywhere they are measured — feature constructors that reach
forward in time, and statistics normalized on the full sample (arXiv:
2605.23959, "When Alpha Disappears": one-switch toggles, Leakage Gain of
+15..26 Sharpe-equivalents from the dominant leaks; arXiv:2601.13770,
Look-Ahead-Bench: LLM alpha decays −15..−22pp out of sample while honest
point-in-time baselines hold near 0).

Seiche's PROOF page claims the lite index is leak-free by construction
(expanding windows, trailing smoothers, a config-frozen alert threshold).
This engine turns that claim into a measurement: rebuild the SAME index with
exactly ONE discipline deliberately broken, score both against the SAME
events, and publish the Leakage Gain each break would buy.

    LG(toggle) = metric(deliberately leaky variant) − metric(clean)

A clean pipeline is one whose published number sits at the BOTTOM of its own
audit table — the gains above it are what we refuse to claim. A leak in the
real pipeline would show up here as a clean-vs-leaky gap of ~0 (the "leak"
buys nothing because it is already being eaten).

Toggles (each breaks ONE thing, everything else held fixed):
  - NORM_GLOBAL:  every expanding z / percentile -> its full-sample twin
                  (the whole future leaks into every day's standardization);
  - TEMP_CENTER:  the tails smoother -> a centered window that peeks
                  LEAKAUDIT_TEMP_CENTER_W//2 days forward (the dominant leak
                  class in arXiv:2605.23959);
  - THRESH_FIT:   the alert threshold fitted in-sample to maximize F1 over
                  LEAKAUDIT_THRESH_GRID instead of frozen in config (the
                  "you cherry-picked the threshold" objection, quantified —
                  PROOF's threshold-free AUROC answers it in principle; this
                  row answers it in percentage points).

Plus the determinism check from the implementation-risk literature
(arXiv:2603.20319: five engines disagreed by up to 3.7% annually on
identical inputs, every divergence a bug): the clean build runs twice and
the audit prints whether the two runs are bit-identical, with the content
hash a skeptic (or the notary chain) can pin.

Honesty notes: the leaky variants are NEVER published as signals — they
exist only inside this audit; all variants score against the identical
declustered event list and warmup slice as PROOF; no RNG anywhere.
"""

from __future__ import annotations

import hashlib
import json

import pandas as pd

from seiche.config import (
    BACKTEST_ALERT_PCTL,
    BACKTEST_MIN_WARMUP_D,
    LEAKAUDIT_THRESH_GRID,
)
from seiche.engines import backtest as eng_backtest
from seiche.engines import history as eng_history

_TOGGLES = [
    ("clean", "none", "the published pipeline: expanding windows, trailing smoothers"),
    ("NORM_GLOBAL", "norm_global", "every z/percentile standardized on the FULL sample"),
    ("TEMP_CENTER", "temp_center", "tails smoother centered — peeks 2 days forward"),
]


def _score(pctl: pd.Series, spread_bp: pd.Series, alert_pctl: float = BACKTEST_ALERT_PCTL) -> dict | None:
    """PROOF-identical scoring: same warmup slice, same declustered events,
    same capture window. Returns the three load-bearing numbers."""
    pct = pctl.dropna()
    if len(pct) < BACKTEST_MIN_WARMUP_D + 100:
        return None
    pct = pct.iloc[BACKTEST_MIN_WARMUP_D:]
    events = eng_backtest._funding_events(spread_bp)
    events = events[(events >= pct.index[0]) & (events <= pct.index[-1])]
    if len(events) == 0:
        return None
    auroc = eng_backtest._event_auroc(pct, events)
    # local capture at an arbitrary threshold (THRESH_FIT needs this knob;
    # _capture_stats hard-codes the config threshold by design)
    alert = pct >= alert_pctl
    captured = 0
    for ev in events:
        loc = pct.index.searchsorted(ev)
        if alert.iloc[max(loc - eng_backtest.BACKTEST_EVENT_FWD_D, 0):loc].any():
            captured += 1
    recall = captured / len(events)
    runs: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    in_run = False
    for i, flag in enumerate(alert):
        if flag and not in_run:
            start = pct.index[i]
            in_run = True
        elif not flag and in_run:
            runs.append((start, pct.index[i - 1]))
            in_run = False
    if in_run:
        runs.append((start, pct.index[-1]))
    runs_hit = sum(
        1 for s, e in runs
        if bool(((events >= s) & (events <= e + pd.Timedelta(days=9))).any())
    )
    precision_runs = runs_hit / len(runs) if runs else None
    return {
        "auroc": auroc,
        "recall": round(recall, 3),
        "precision_runs": round(precision_runs, 3) if precision_runs is not None else None,
        "n_events": int(len(events)),
        "n_alert_runs": len(runs),
    }


def _hash_index(index: pd.Series) -> str:
    payload = json.dumps(
        [[d.date().isoformat(), round(float(v), 6)] for d, v in index.items()],
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def run(hist_kwargs: dict, spread_bp: pd.Series) -> dict:
    """One-switch leakage audit of the lite index. `hist_kwargs` are the
    exact keyword arguments the deep layer passes to history.build — the
    audit rebuilds the same index, clean and deliberately broken."""
    # ---- determinism: the clean build, twice --------------------------------
    clean_a = eng_history.build(**hist_kwargs)
    clean_b = eng_history.build(**hist_kwargs)
    hash_a = _hash_index(clean_a["index"])
    reproducible = hash_a == _hash_index(clean_b["index"])

    rows: list[dict] = []
    clean_metrics: dict | None = None
    for name, mode, what in _TOGGLES:
        variant = clean_a if mode == "none" else eng_history.build(**hist_kwargs, leak=mode)
        m = _score(variant["pctl"], spread_bp)
        if m is None:
            return {"ok": False, "reason": "insufficient scored history for the audit"}
        if mode == "none":
            clean_metrics = m
        rows.append({
            "toggle": name,
            "what_breaks": what,
            "auroc": m["auroc"],
            "recall": m["recall"],
            "precision_runs": m["precision_runs"],
            "lg_auroc": (
                round(m["auroc"] - clean_metrics["auroc"], 3)
                if clean_metrics and m["auroc"] is not None and clean_metrics["auroc"] is not None
                else None
            ),
            "lg_recall": round(m["recall"] - clean_metrics["recall"], 3) if clean_metrics else None,
        })

    # ---- THRESH_FIT: fit the alert threshold in-sample ----------------------
    best = None
    for thr in LEAKAUDIT_THRESH_GRID:
        m = _score(clean_a["pctl"], spread_bp, alert_pctl=thr)
        if m is None or m["precision_runs"] is None:
            continue
        p, r = m["precision_runs"], m["recall"]
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
        if best is None or f1 > best["f1"]:
            best = {"f1": f1, "thr": thr, **m}
    if best is not None and clean_metrics is not None:
        rows.append({
            "toggle": "THRESH_FIT",
            "what_breaks": (
                f"alert threshold fitted in-sample (best F1 at pctl {best['thr']:g} "
                f"vs config-frozen {BACKTEST_ALERT_PCTL:g})"
            ),
            "auroc": clean_metrics["auroc"],  # threshold-free, unchanged by design
            "recall": best["recall"],
            "precision_runs": best["precision_runs"],
            "lg_auroc": 0.0,
            "lg_recall": round(best["recall"] - clean_metrics["recall"], 3),
        })

    # Leak selectivity is the literature's own headline: some breaks buy skill,
    # others buy nothing (or even lose it — an expanding window is not just
    # honest, it is often the better instrument). Say which is which.
    parts: list[str] = []
    by = {r["toggle"]: r for r in rows}
    tc = by.get("TEMP_CENTER")
    if tc and tc["lg_auroc"] is not None:
        parts.append(
            f"the forward-peeking smoother would buy {tc['lg_auroc']:+.3f} AUROC — refused"
            if tc["lg_auroc"] > 0 else
            f"the forward-peeking smoother buys nothing here ({tc['lg_auroc']:+.3f} AUROC)"
        )
    ng = by.get("NORM_GLOBAL")
    if ng and ng["lg_auroc"] is not None:
        parts.append(
            f"full-sample standardization would buy {ng['lg_auroc']:+.3f} AUROC — refused"
            if ng["lg_auroc"] > 0 else
            f"full-sample standardization would LOSE {ng['lg_auroc']:+.3f} AUROC (the expanding "
            f"discipline is also the better instrument)"
        )
    tf = by.get("THRESH_FIT")
    if tf and clean_metrics is not None and tf["precision_runs"] is not None \
            and clean_metrics["precision_runs"] is not None:
        parts.append(
            f"a self-fitted threshold would print run-precision {tf['precision_runs']:.2f} "
            f"instead of the honest {clean_metrics['precision_runs']:.2f}"
        )
    reading = (
        "leak selectivity, measured on ourselves: " + "; ".join(parts)
        if parts else
        "audit degenerate — no toggle produced a scoreable variant; investigate before publishing"
    )

    return {
        "ok": True,
        "asof": clean_a["index"].index[-1].date().isoformat(),
        "bit_reproducible": bool(reproducible),
        "clean_index_sha256": hash_a[:16],
        "rows": rows,
        "reading": reading,
        "caveats": [
            "the leaky variants exist ONLY inside this audit — they are never published as "
            "signals and never feed any other engine",
            "all variants score against the identical declustered event list and warmup slice "
            "as PROOF — one switch flips per row, everything else held fixed",
            "Leakage Gain is measured on final-vintage data like PROOF itself; the audit "
            "measures pipeline discipline, not vendor revisions",
            "a near-zero gain on one toggle does not certify the pipeline leak-free — it "
            "certifies immunity to THAT leak class only",
        ],
        "method": (
            "one-switch protocol (arXiv:2605.23959): rebuild the lite index with exactly one "
            "discipline broken (NORM_GLOBAL full-sample standardization; TEMP_CENTER centered "
            "smoother; THRESH_FIT in-sample threshold), score every variant with PROOF's event "
            "list, AUROC and run-level capture, publish LG = leaky − clean. Determinism check "
            "(arXiv:2603.20319): the clean build runs twice and must hash identically "
            "(sha256 over the dated index values)."
        ),
    }
