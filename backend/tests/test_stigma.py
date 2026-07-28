"""Stigma gauge tests, synthetic data only.

The invariants that matter: a clean cap scores zero, the classic stigma
signature (persistent P99 leak with the facility idle) scores high, heavy
take-up kills the score by construction, the IORB fallback confesses itself
in the caveats, and the letter line ships without em or en dashes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.limit_memory("256 MB")

from seiche.engines import stigma


def _bdays(n: int, start: str = "2024-01-01") -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n)


def _frame(idx: pd.DatetimeIndex, p99: float | np.ndarray, p75: float | np.ndarray) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "percentRate": 4.40,
            "percentPercentile1": 4.30,
            "percentPercentile25": 4.35,
            "percentPercentile75": p75,
            "percentPercentile99": p99,
            "volumeInBillions": 2500.0,
        },
        index=idx,
    )


def _zeros(idx: pd.DatetimeIndex) -> pd.Series:
    return pd.Series(0.0, index=idx)


# --------------------------------------------------------------------------
# Clean cap
# --------------------------------------------------------------------------

def test_clean_cap_scores_zero():
    idx = _bdays(120)
    r = stigma.gauge(_frame(idx, 4.45, 4.42), 4.50, _zeros(idx))
    assert r["ok"]
    b = r["bp_days_above_ceiling"]
    assert b["p99_sum20_bp_days"] == 0.0
    assert b["p75_sum20_bp_days"] == 0.0
    assert b["days_above_20d"] == 0
    assert r["stigma_score"] == 0.0
    assert "holding" in r["verdict"]
    assert r["ceiling"]["source"] == "srf_rate"


# --------------------------------------------------------------------------
# Classic stigma
# --------------------------------------------------------------------------

def test_classic_stigma_scores_high():
    idx = _bdays(250)
    p99 = np.full(len(idx), 4.45)
    p99[-25:] = 4.55  # 5 bp above the ceiling for the last 25 sessions
    r = stigma.gauge(_frame(idx, p99, 4.42), 4.50, _zeros(idx))
    assert r["ok"]
    b = r["bp_days_above_ceiling"]
    assert b["days_above_20d"] == 20
    assert b["p99_sum20_bp_days"] == pytest.approx(100.0, abs=1.0)
    assert b["p75_sum20_bp_days"] == 0.0  # material-mass bound stays silent
    assert r["stigma_score"] >= 90.0
    assert "stigma" in r["verdict"]
    assert r["takeup"]["classification"] == "de_minimis"
    assert r["takeup"]["share_of_facility_pct"] == 0.0


def test_heavy_takeup_kills_the_score():
    idx = _bdays(250)
    p99 = np.full(len(idx), 4.55)  # leaking throughout
    takeup = pd.Series(40.0, index=idx)  # facility doing real work
    r = stigma.gauge(_frame(idx, p99, 4.42), 4.50, takeup)
    assert r["ok"]
    assert r["stigma_score"] == 0.0
    assert "not stigma" in r["verdict"]
    assert r["takeup"]["classification"] == "material"


# --------------------------------------------------------------------------
# Classifier thresholds
# --------------------------------------------------------------------------

def test_classify_print_dollar_thresholds():
    # history has seen a bigger print, so the record rule stays out of the way
    hist = pd.Series(0.0, index=_bdays(250))
    hist.iloc[5] = 30.0
    assert stigma.classify_print(0.4, hist) == "de_minimis"
    assert stigma.classify_print(0.99, hist) == "de_minimis"
    assert stigma.classify_print(1.0, hist) == "notable"
    assert stigma.classify_print(5.0, hist) == "notable"
    assert stigma.classify_print(24.9, hist) == "notable"
    assert stigma.classify_print(25.0, hist) == "material"
    assert stigma.classify_print(80.0, hist) == "material"


def test_classify_print_record_escalation_and_edges():
    quiet = pd.Series(0.0, index=_bdays(250))
    # a $12B print that is a trailing record at a standing facility is material
    assert stigma.classify_print(12.0, quiet) == "material"
    # same print against a history that has seen $15B stays notable
    busy = quiet.copy()
    busy.iloc[10] = 15.0
    assert stigma.classify_print(12.0, busy) == "notable"
    # short history cannot invoke the record rule
    assert stigma.classify_print(12.0, quiet.tail(10)) == "notable"
    assert stigma.classify_print(None, quiet) == "de_minimis"
    assert stigma.classify_print(float("nan"), quiet) == "de_minimis"


# --------------------------------------------------------------------------
# Ceiling fallback
# --------------------------------------------------------------------------

def test_missing_srf_rate_falls_back_to_iorb_with_caveat():
    idx = _bdays(120)
    iorb = pd.Series(4.40, index=idx)
    r = stigma.gauge(_frame(idx, 4.45, 4.42), None, _zeros(idx), iorb=iorb)
    assert r["ok"]
    assert r["ceiling"]["source"] == "iorb_proxy"
    assert r["ceiling"]["latest_pct"] == pytest.approx(4.40)
    assert any("IORB" in c for c in r["caveats"])
    # 4.45 against the 4.40 proxy is a leak the true SRF ceiling might cap
    assert r["bp_days_above_ceiling"]["p99_last_bp"] == pytest.approx(5.0, abs=0.1)


def test_no_ceiling_at_all_refuses():
    idx = _bdays(120)
    r = stigma.gauge(_frame(idx, 4.45, 4.42), None, _zeros(idx), iorb=None)
    assert r["ok"] is False
    assert "ceiling" in r["reason"]


# --------------------------------------------------------------------------
# Refusals on empty or thin inputs
# --------------------------------------------------------------------------

def test_empty_inputs_refuse():
    idx = _bdays(120)
    assert stigma.gauge(pd.DataFrame(), 4.50, _zeros(idx))["ok"] is False
    assert stigma.gauge(None, 4.50, _zeros(idx))["ok"] is False
    assert stigma.gauge(_frame(idx, 4.45, 4.42), 4.50, pd.Series(dtype=float))["ok"] is False
    assert stigma.gauge(_frame(idx, 4.45, 4.42), 4.50, None)["ok"] is False
    short = _frame(_bdays(10), 4.45, 4.42)
    assert stigma.gauge(short, 4.50, _zeros(_bdays(10)))["ok"] is False
    no_cols = pd.DataFrame({"percentRate": [4.4]}, index=_bdays(1))
    assert stigma.gauge(no_cols, 4.50, _zeros(idx))["ok"] is False


# --------------------------------------------------------------------------
# Letter line hygiene
# --------------------------------------------------------------------------

def test_letter_line_has_no_em_or_en_dash():
    idx = _bdays(250)
    p99 = np.full(len(idx), 4.45)
    p99[-25:] = 4.55
    for r in (
        stigma.gauge(_frame(idx, 4.45, 4.42), 4.50, _zeros(idx)),
        stigma.gauge(_frame(idx, p99, 4.42), 4.50, _zeros(idx)),
    ):
        assert r["ok"]
        line = r["letter_line"]
        assert "\u2014" not in line and "\u2013" not in line
        assert "\n" not in line
        assert line.endswith(".")
        assert "\u2014" not in r["verdict"] and "\u2013" not in r["verdict"]
        assert "\u2014" not in r["method"] and "\u2013" not in r["method"]
