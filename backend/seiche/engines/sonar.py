"""SONAR — the daily anomaly sweep.

Every series the collectors hold, pinged every day with the same question:
"is your latest print unusual, on level or on change?" Robust statistics only
(median/MAD — a squeeze day must not inflate its own yardstick). Output is a
ranked movers board: the terminal's answer to "what actually moved today?"

Context pane, not a composite input: an anomaly is a question, not a verdict.

Freshness is part of the answer. A slow series (monthly OECD, month-end MMF)
can sit at a structurally extreme level z for weeks, and ranking on |z| alone
pinned those to the top of the board every day — which is how the daily letter
came to report a June print as an overnight move for five days running. The
sweep still SHOWS them, because the level is real information; it just refuses
to FLAG a print that is too old to have moved overnight, and it says how old
every print is. Every downstream consumer (dispatch, brief, desk assistant)
filters on the flag, so the gate lands in one place.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import SONAR_FRESH_D, SONAR_LOOKBACK_D, SONAR_TOP_N, SONAR_Z_FLAG


def _robust_z(s: pd.Series) -> float | None:
    x = s.dropna().tail(SONAR_LOOKBACK_D)
    if len(x) < 60:
        return None
    med = float(x.median())
    mad = float((x - med).abs().median())
    scale = 1.4826 * mad
    if scale <= 0:
        return None
    return float((float(x.iloc[-1]) - med) / scale)


def sweep(series_map: dict[str, tuple[str, str, pd.Series]]) -> dict:
    """series_map: name -> (label, unit, daily/weekly level series)."""
    # Age every print against the newest print on the board rather than the
    # wall clock, because as-of replay reconstructs historical boards and
    # today's date would mark an entire past sweep stale.
    #
    # But "newest print" must exclude FORWARD-dated ones. Administered rates
    # carry a future effective date by design (IORB is announced effective
    # from a date up to a fortnight out), and letting one set the reference
    # silently aged every other series toward the gate — the same bug as
    # calling a stale print a mover, running the other way. The wall clock is
    # a ceiling only, never the reference, which is safe for replay because
    # _truncate_sources already caps every replayed series at its as-of date.
    today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    asofs = [p.index[-1] for _, _, s in series_map.values()
             if not (p := s.dropna()).empty]
    observed = [a for a in asofs if a <= today]
    board_asof = max(observed) if observed else (max(asofs) if asofs else None)

    movers = []
    for name, (label, unit, s) in series_map.items():
        pts = s.dropna()
        if len(pts) < 60:
            continue
        # Clamped at 0: a forward-dated administered rate is current by
        # construction, not negatively old.
        age_d = max(0, int((board_asof - pts.index[-1]).days)) if board_asof is not None else 0
        level_z = _robust_z(pts)
        change_z = _robust_z(pts.diff())
        zs = [abs(z) for z in (level_z, change_z) if z is not None]
        if not zs:
            continue
        worst = max(zs)
        # Scale context travels with the z. A 16-sigma change on a series
        # whose baseline is near zero is a wake-up call, not a large flow;
        # downstream prose needs the peak to say which, so it ships here.
        peak = float(pts.abs().max())
        last_v = float(pts.iloc[-1])
        movers.append(
            {
                "name": name,
                "label": label,
                "unit": unit,
                "last": round(float(pts.iloc[-1]), 3),
                "chg_1d": round(float(pts.diff().iloc[-1]), 3) if len(pts) > 1 else None,
                "level_z": round(level_z, 2) if level_z is not None else None,
                "change_z": round(change_z, 2) if change_z is not None else None,
                "max_abs_z": round(worst, 2),
                "hist_peak_abs": round(peak, 3),
                "share_of_peak": round(abs(last_v) / peak, 4) if peak > 0 else None,
                "woke_from_zero": bool(len(pts) > 1 and float(pts.iloc[-2]) == 0.0 and last_v != 0.0),
                "flag": worst >= SONAR_Z_FLAG and age_d <= SONAR_FRESH_D,
                "stale": age_d > SONAR_FRESH_D,
                "age_d": age_d,
                "asof": pts.index[-1].date().isoformat(),
            }
        )
    movers.sort(key=lambda m: -m["max_abs_z"])
    return {
        "ok": bool(movers),
        "n_scanned": len(movers),
        "n_flagged": sum(1 for m in movers if m["flag"]),
        "n_stale": sum(1 for m in movers if m["stale"]),
        "board_asof": board_asof.date().isoformat() if board_asof is not None else None,
        "movers": movers[:SONAR_TOP_N],
        "method": (
            f"robust z = (last − median) / (1.4826·MAD) over trailing {SONAR_LOOKBACK_D} obs, "
            f"on level and 1d change; flag |z| ≥ {SONAR_Z_FLAG} AND the print is no more "
            f"than {SONAR_FRESH_D} days behind the board. A slower series still shows here "
            f"with its age; it is not called a mover."
        ),
    }
