"""The Breakwater — the rescuer modeled as part of the system.

Every public forecaster treats the Federal Reserve as weather. It is not
weather; it is a PLAYER — the institution paid to stop the exact event this
terminal predicts, with a reaction function it never publishes but cannot
help revealing: every intervention is a confession of where its pain
threshold sat that day. Nobody instruments this. The pros carry it in their
heads; the Breakwater writes it down.

Method (zero fitted parameters — a revealed-preference catalog, not a model):
for every dated plumbing intervention in the public record (config carries
the catalog with editorial dating flagged), replay the board as of the day
BEFORE the announcement using expanding statistics only: the spread's
expanding percentile, its 20-day maximum, and the SRF's 20-day maximum
usage. The distribution of those pre-intervention states IS the Fed's
revealed reaction function. From it:

  - the REVEALED THRESHOLD: the median pre-intervention spread percentile —
    the level of visible stress at which the goalie has historically moved;
  - RESCUE PROXIMITY (0-100): how far today's board sits from historical
    rescue conditions — high proximity cuts BOTH ways and the engine says
    so: pressure is high enough to expect relief, and relief arriving is
    itself the confession that pressure was real;
  - the POSTURE note: the game changed in 2021 — a STANDING repo facility
    is a goalie who never leaves the net (tail-capping by construction),
    which is why post-SRF pops cap where pre-SRF pops ran.

Context engine, never weighted into the composite: the Fed's likely response
is not evidence of stress — it is the reason predicted stress sometimes
doesn't arrive, and an honest forecast says which of its misses were saves.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import BREAKWATER_INTERVENTIONS, BREAKWATER_PROXIMITY_FLOOR_PCTL


def _expanding_pctl_asof(s: pd.Series, ts: pd.Timestamp) -> float | None:
    """Percentile of the last value at/before ts within its OWN past only."""
    hist = s.loc[:ts].dropna()
    if len(hist) < 120:
        return None
    return round(float((hist <= hist.iloc[-1]).mean() * 100.0), 1)


def analyze(
    spread_bp: pd.Series,
    srf_accepted: pd.Series,
    interventions: list[dict] | None = None,
) -> dict:
    cat = interventions if interventions is not None else BREAKWATER_INTERVENTIONS
    s = spread_bp.dropna()
    if len(s) < 300:
        return {"ok": False, "reason": f"insufficient spread history ({len(s)}d)"}
    srf = srf_accepted.dropna() if srf_accepted is not None else pd.Series(dtype=float)

    rows = []
    for iv in cat:
        ts = pd.Timestamp(iv["date"]) - pd.Timedelta(days=1)   # the board they saw
        if ts < s.index.min():
            rows.append({**{k: iv[k] for k in ("date", "label", "kind")},
                         "in_sample": False})
            continue
        window = s.loc[:ts].tail(20)
        srf_max20 = float(srf.loc[:ts].tail(20).max()) if not srf.loc[:ts].empty else None
        rows.append({
            "date": iv["date"],
            "label": iv["label"],
            "kind": iv["kind"],
            "dating": iv.get("dating", "public record"),
            "in_sample": True,
            "spread_pctl_before": _expanding_pctl_asof(s, ts),
            "spread_max20_bp": round(float(window.max()), 1) if not window.empty else None,
            "srf_max20_b": round(srf_max20, 1) if srf_max20 is not None else None,
        })

    seen = [r for r in rows if r.get("in_sample") and r.get("spread_pctl_before") is not None]
    if len(seen) < 3:
        return {"ok": False, "reason": f"only {len(seen)} interventions replayable — catalog too thin for this sample"}

    pctls = np.array([r["spread_pctl_before"] for r in seen])
    threshold = {
        "median_pctl": round(float(np.median(pctls)), 0),
        "min_pctl": round(float(pctls.min()), 0),
        "max_pctl": round(float(pctls.max()), 0),
        "n": len(seen),
    }

    now_pctl = _expanding_pctl_asof(s, s.index[-1])
    gap = round(threshold["median_pctl"] - now_pctl, 0) if now_pctl is not None else None
    # proximity: 0 when far below the floor, 100 at/above the revealed median
    proximity = None
    if now_pctl is not None:
        lo = BREAKWATER_PROXIMITY_FLOOR_PCTL
        hi = threshold["median_pctl"]
        proximity = round(float(np.clip((now_pctl - lo) / max(hi - lo, 1e-9), 0.0, 1.0) * 100.0), 0)

    post_srf = s.index[-1] >= pd.Timestamp("2021-07-28")
    return {
        "ok": True,
        "asof": s.index[-1].date().isoformat(),
        "interventions": rows,
        "revealed_threshold": threshold,
        "current": {"spread_pctl": now_pctl, "gap_to_threshold_pctl": gap},
        "rescue_proximity": proximity,
        "posture": (
            "post-2021 game: the Standing Repo Facility is a goalie who never leaves the net — "
            "it caps the tail by construction, so pops cap where pre-SRF pops ran; the live risk "
            "is the STIGMA channel (nobody wants to be seen using it first)"
            if post_srf else "pre-SRF game: rescues were ad hoc — thresholds ran higher"
        ),
        "reading": (
            None if proximity is None else
            "board at/inside historical rescue conditions — expect relief, and read any relief as the confession"
            if proximity >= 100 else
            f"board {gap:.0f} percentile points below the revealed rescue threshold"
        ),
        "caveats": [
            f"n={len(seen)} interventions — this is a revealed-preference CATALOG, not a fitted model; ranges printed, no point estimate trusted",
            "announcement dating is editorial where flagged; the board replay uses final-vintage data",
            "high proximity cuts both ways: relief becomes likely exactly when the predicted event is real — a forecast miss after an intervention is a SAVE, not a false alarm",
            "context engine: the goalie's likely move is not evidence of stress and never enters the composite",
        ],
        "method": (
            "for each dated plumbing intervention, replay the board as of the day before the "
            "announcement (expanding percentile of SOFR−IORB, 20d max spread, 20d max SRF usage); "
            "revealed threshold = median pre-intervention spread percentile; rescue proximity = "
            f"today's percentile mapped 0-100 between the {BREAKWATER_PROXIMITY_FLOOR_PCTL:g}th "
            "percentile floor and the revealed median"
        ),
    }
