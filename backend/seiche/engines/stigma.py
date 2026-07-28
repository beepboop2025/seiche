"""SRF Stigma Gauge: a ceiling that leaks names the banks that will not walk in.

The Standing Repo Facility is supposed to cap secured funding by arbitrage:
any counterparty that can borrow from the Fed at the offering rate has no
reason to pay more in the market. So repo volume persistently printing ABOVE
that rate while SRF take-up sits near zero is the classic stigma signature,
desks paying up rather than be seen at the window. September 2019 is the
canonical case of a backstop arriving late because nobody wanted to be first.

Honesty constraint: the NY Fed publishes only P1/P25/P75/P99 of the SOFR
distribution, so the mass above the ceiling can only be BOUNDED, never
measured. A P99 breach proves at least 1 percent of volume paid above the
ceiling (existence bound); a P75 breach proves at least 25 percent did
(material-mass bound). We publish both and refuse to interpolate between.

Score = 100 * absence * (0.6 * leak + 0.4 * persistence), where leak is the
20d sum of the P99 breach in bp days capped at 40 (a 2 bp average leak for a
month saturates), persistence is the share of the last 20 sessions with any
P99 breach, and absence = 1 - min(1, 20d max take-up / $25B). Heavy take-up
zeroes the score by construction: a used facility is pressure, not stigma.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FACILITY_SCALE_B = 500.0   # SRF aggregate operation limit, $B
DE_MINIMIS_B = 1.0         # below this a print is test trades and small value ops
MATERIAL_B = 25.0          # at or above this the facility is doing real work
RECORD_MATERIAL_B = 10.0   # a trailing-record print at or above this is material
LEAK_SAT_BP_DAYS = 40.0    # 20d P99 bp-day sum that saturates the leak term


def classify_print(last: float | None, history: pd.Series | None = None) -> str:
    """Classify one day's SRF take-up print ($B) by stated dollar thresholds.

    de_minimis: under $1B (test trades and small value operations).
    notable: $1B up to $25B.
    material: $25B and above, or a $10B+ print that exceeds everything in the
    supplied history (a double digit record at a standing facility is real
    usage even below the flat threshold; needs 60+ prior prints to invoke).
    """
    if last is None or not np.isfinite(last):
        return "de_minimis"
    last = float(last)
    if last >= MATERIAL_B:
        return "material"
    if history is not None:
        h = pd.Series(history).dropna()
        if len(h) >= 60 and last >= RECORD_MATERIAL_B and last > float(h.max()):
            return "material"
    if last >= DE_MINIMIS_B:
        return "notable"
    return "de_minimis"


def _ceiling(index: pd.DatetimeIndex,
             srf_rate: pd.Series | float | None,
             iorb: pd.Series | None) -> tuple[pd.Series | None, str | None]:
    """Ceiling series aligned to index, plus its source label."""
    if isinstance(srf_rate, pd.Series) and not srf_rate.dropna().empty:
        return srf_rate.dropna().sort_index().reindex(index, method="ffill"), "srf_rate"
    if isinstance(srf_rate, (int, float)) and np.isfinite(srf_rate):
        return pd.Series(float(srf_rate), index=index), "srf_rate"
    if isinstance(iorb, pd.Series) and not iorb.dropna().empty:
        return iorb.dropna().sort_index().reindex(index, method="ffill"), "iorb_proxy"
    return None, None


def gauge(sofr_percentiles: pd.DataFrame,
          srf_rate: pd.Series | float | None,
          srf_takeup: pd.Series,
          iorb: pd.Series | None = None) -> dict:
    if sofr_percentiles is None or sofr_percentiles.empty:
        return {"ok": False, "reason": "no SOFR percentile frame"}
    for col in ("percentPercentile99", "percentPercentile75"):
        if col not in sofr_percentiles.columns:
            return {"ok": False, "reason": f"missing column {col}"}
    if srf_takeup is None or srf_takeup.dropna().empty:
        return {"ok": False, "reason": "no SRF take-up history"}

    p99 = pd.to_numeric(sofr_percentiles["percentPercentile99"], errors="coerce").dropna()
    if len(p99) < 20:
        return {"ok": False, "reason": f"insufficient percentile history ({len(p99)}d)"}
    p75 = pd.to_numeric(sofr_percentiles["percentPercentile75"], errors="coerce").reindex(p99.index)

    ceil, source = _ceiling(p99.index, srf_rate, iorb)
    if ceil is None:
        return {"ok": False, "reason": "no ceiling available (srf_rate and iorb both missing)"}

    leak99 = ((p99 - ceil) * 100.0).clip(lower=0.0).dropna()
    leak75 = ((p75 - ceil) * 100.0).clip(lower=0.0).reindex(leak99.index)
    if leak99.empty:
        return {"ok": False, "reason": "no overlap between percentiles and ceiling"}

    roll99 = leak99.rolling(20, min_periods=1).sum()
    roll75 = leak75.fillna(0.0).rolling(20, min_periods=1).sum()
    last20 = leak99.tail(20)
    sum20_99 = float(last20.sum())
    sum20_75 = float(leak75.fillna(0.0).tail(20).sum())
    days_above = int((last20 > 0).sum())

    tk = srf_takeup.dropna()
    latest_tk = float(tk.iloc[-1])
    max20_tk = float(tk.tail(20).max())
    tk_class = classify_print(latest_tk, tk.iloc[:-1])

    absence = 1.0 - min(1.0, max20_tk / MATERIAL_B)
    leak_sat = min(1.0, sum20_99 / LEAK_SAT_BP_DAYS)
    persistence = days_above / 20.0
    score = round(100.0 * absence * (0.6 * leak_sat + 0.4 * persistence), 1)

    if sum20_99 > 0 and max20_tk >= MATERIAL_B:
        verdict = ("ceiling breached with the facility in use: that is pressure "
                   "exceeding the backstop, not stigma")
    elif score >= 60:
        verdict = "stigma signature live: repo clearing above the ceiling while the facility sits idle"
    elif score >= 25:
        verdict = "leak forming: repeated prints above the ceiling with take-up still thin"
    elif sum20_99 > 0:
        verdict = "trace leak: isolated prints above the ceiling, nothing persistent yet"
    else:
        verdict = "ceiling holding: no distribution mass observed above the offering rate"

    ceiling_name = "SRF" if source == "srf_rate" else "IORB proxy"
    if days_above > 0:
        letter_line = (
            f"SOFR's 99th percentile cleared the {ceiling_name} ceiling on {days_above} of the "
            f"last 20 sessions for {sum20_99:.0f} bp days of leak while SRF take-up peaked at "
            f"${max20_tk:.1f}B against a ${FACILITY_SCALE_B:.0f}B facility, stigma score "
            f"{score:.0f} of 100."
        )
    else:
        letter_line = (
            f"Repo held under the {ceiling_name} ceiling for all of the last 20 sessions with "
            f"SRF take-up at ${latest_tk:.1f}B, stigma score {score:.0f} of 100, the backstop "
            f"is capping quietly."
        )

    caveats = [
        "only P1/P25/P75/P99 are published, so mass above the ceiling is bounded, not "
        "measured: a P99 breach proves at least 1 percent of volume paid up, a P75 breach "
        "proves at least 25 percent; the true share in between is unobservable from this feed",
        "percentiles are volume weighted rate levels: bp above the ceiling is rate distance, "
        "not dollar volume",
        f"share of facility uses the ${FACILITY_SCALE_B:.0f}B aggregate operation limit",
    ]
    if source == "iorb_proxy":
        caveats.append(
            "no SRF offering rate series supplied: using IORB as the ceiling proxy; IORB sits "
            "at or below the SRF rate, so leak measured against IORB overstates the true "
            "above-ceiling mass"
        )

    return {
        "ok": True,
        "asof": leak99.index[-1].date().isoformat(),
        "ceiling": {
            "source": source,
            "latest_pct": round(float(ceil.dropna().iloc[-1]), 3),
        },
        "bp_days_above_ceiling": {
            "p99_last_bp": round(float(leak99.iloc[-1]), 1),
            "p75_last_bp": round(float(leak75.fillna(0.0).iloc[-1]), 1),
            "p99_sum20_bp_days": round(sum20_99, 1),
            "p75_sum20_bp_days": round(sum20_75, 1),
            "days_above_20d": days_above,
        },
        "takeup": {
            "latest_b": round(latest_tk, 2),
            "asof": tk.index[-1].date().isoformat(),
            "max20_b": round(max20_tk, 2),
            "share_of_facility_pct": round(latest_tk / FACILITY_SCALE_B * 100.0, 3),
            "facility_scale_b": FACILITY_SCALE_B,
            "classification": tk_class,
        },
        "stigma_score": score,
        "verdict": verdict,
        "letter_line": letter_line,
        "series": [
            [ts.date().isoformat(), round(float(v), 1)]
            for ts, v in leak99.tail(500).items()
        ],
        "series_sum20": [
            [ts.date().isoformat(), round(float(v), 1)]
            for ts, v in roll99.tail(500).items()
        ],
        "series_p75_sum20": [
            [ts.date().isoformat(), round(float(v), 1)]
            for ts, v in roll75.tail(500).items()
        ],
        "caveats": caveats,
        "method": (
            "P99 and P75 minus the SRF offering rate (IORB proxy if unavailable), clipped at "
            "zero, in bp; rolling 20d sums; score = 100 * absence * (0.6 * min(1, P99 20d sum "
            f"/ {LEAK_SAT_BP_DAYS:.0f} bp days) + 0.4 * days above / 20), absence = 1 - min(1, "
            f"20d max take-up / ${MATERIAL_B:.0f}B); prints classified de_minimis under "
            f"${DE_MINIMIS_B:.0f}B, notable to ${MATERIAL_B:.0f}B, material at or above "
            f"(or a ${RECORD_MATERIAL_B:.0f}B+ trailing record)"
        ),
    }
