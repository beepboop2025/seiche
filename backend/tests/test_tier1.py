"""Tier-1 engines: Thermohaline, Regatta, Sea Room, Sea State + BIS parser.

House invariants under test: determinism, truncation equality, honest
refusals, and each engine's core statistical claim on synthetic data with a
known answer (MCS keeps the skilled model; ACI coverage tracks its target;
the HMM finds planted regimes; delayed labels never leak).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from seiche.engines import regatta, searoom, seastate, thermohaline
from seiche.sources.bis import parse_sdmx_csv


# ---------------------------------------------------------------------------
# BIS SDMX-CSV parser
# ---------------------------------------------------------------------------

def test_bis_parser():
    csv_text = (
        "FREQ,TIME_PERIOD,OBS_VALUE,OBS_STATUS\n"
        "Q,2024-Q4,13139502.33,A\n"
        "Q,2025-Q1,13730686.529,A\n"
        "Q,2025-Q1,13730686.529,A\n"   # duplicate period — keep last
    )
    pts = parse_sdmx_csv(csv_text)
    assert len(pts) == 2
    assert pts.index[0] == pd.Timestamp("2024-12-31")
    assert pts.index[-1] == pd.Timestamp("2025-03-31")
    assert float(pts.iloc[-1]) == pytest.approx(13730686.529)


def test_bis_parser_empty_fails_loud():
    with pytest.raises(ValueError):
        parse_sdmx_csv("FREQ,TIME_PERIOD,OBS_VALUE\n")


# ---------------------------------------------------------------------------
# Thermohaline
# ---------------------------------------------------------------------------

class _FakeSeries:
    def __init__(self, pts: pd.Series):
        self.points = pts


def _quarterly(n: int, growth_q: float = 0.02, start: float = 5e6) -> pd.Series:
    idx = pd.date_range("2000-03-31", periods=n, freq="QE")
    vals = start * (1.0 + growth_q) ** np.arange(n)
    return pd.Series(vals, index=idx)


def test_thermohaline_reads_growth():
    bis = {
        "GLI_OFFSHORE_USD": _FakeSeries(_quarterly(80)),
        "CREDIT_GAP_US": _FakeSeries(pd.Series(
            np.linspace(-5, 8, 80), index=pd.date_range("2000-03-31", periods=80, freq="QE"))),
    }
    out = thermohaline.analyze(bis)
    assert out["ok"]
    # constant 2%/q compounding = ~8.24% yoy
    assert out["stock"]["yoy_pct"] == pytest.approx(8.2, abs=0.2)
    assert out["credit_gaps"][0]["reading"] == "credit above trend"
    assert "context" in " ".join(out["caveats"]).lower() or "composite" in " ".join(out["caveats"]).lower()


def test_thermohaline_refuses_short_history():
    out = thermohaline.analyze({"GLI_OFFSHORE_USD": _FakeSeries(_quarterly(10))})
    assert not out["ok"]


# ---------------------------------------------------------------------------
# Regatta (MCS)
# ---------------------------------------------------------------------------

def _race_inputs(n: int = 900):
    rng = np.random.default_rng(3)
    idx = pd.bdate_range("2020-01-01", periods=n)
    y = pd.Series((rng.random(n) < 0.2).astype(float), index=idx)
    cal = pd.DataFrame({
        "rule": (0.7 * y + 0.15 + 0.1 * rng.random(n)).clip(0, 1),
        "ml": (0.4 * y + 0.25 + 0.2 * rng.random(n)).clip(0, 1),
        "tide": pd.Series(rng.random(n), index=idx),
    }, index=idx)
    p_pub = pd.Series((0.6 * y + 0.2 + 0.1 * rng.random(n)).clip(0, 1), index=idx)
    return cal, p_pub, y


def test_regatta_keeps_skill_drops_noise():
    cal, p_pub, y = _race_inputs()
    out = regatta.analyze(cal, p_pub, y)
    assert out["ok"]
    by = {r["model"]: r for r in out["rows"]}
    assert by["rule"]["in_set"]
    assert not by["tide"]["in_set"]
    assert not by["climatology"]["in_set"]


def test_regatta_deterministic():
    cal, p_pub, y = _race_inputs()
    assert regatta.analyze(cal, p_pub, y)["rows"] == regatta.analyze(cal, p_pub, y)["rows"]


def test_regatta_dedupes_identical_entrants():
    cal, p_pub, y = _race_inputs()
    out = regatta.analyze(cal, cal["rule"].copy(), y)  # published == a member
    assert out["ok"]
    assert out["duplicates_merged"] == {"stack_published": "rule"}


def test_regatta_refuses_degenerate_field():
    n = 900
    idx = pd.bdate_range("2020-01-01", periods=n)
    rng = np.random.default_rng(1)
    y = pd.Series((rng.random(n) < 0.2).astype(float), index=idx)
    p = pd.Series(0.3, index=idx)
    cal = pd.DataFrame({"rule": p, "ml": p.copy()}, index=idx)  # all identical
    out = regatta.analyze(cal, p.copy(), y)
    assert not out["ok"]


# ---------------------------------------------------------------------------
# Sea Room (ACI)
# ---------------------------------------------------------------------------

def test_searoom_coverage_tracks_target():
    rng = np.random.default_rng(11)
    n = 2000
    idx = pd.bdate_range("2018-01-01", periods=n)
    y = pd.Series((rng.random(n) < 0.15).astype(float), index=idx)
    p = (0.5 * y + 0.1 + 0.15 * rng.random(n)).clip(0, 1)
    out = searoom.analyze(pd.Series(p, index=idx), y)
    assert out["ok"]
    assert abs(out["coverage"]["realized"] - out["coverage"]["target"]) <= 0.05
    assert out["today"]["set"] in ("no_event", "both", "event", "empty")


def test_searoom_delayed_feedback_no_leak():
    """The last 5 labels are unresolved in production; blanking them must not
    change ANY emitted set (the machinery never touches an open window)."""
    rng = np.random.default_rng(11)
    n = 1200
    idx = pd.bdate_range("2018-01-01", periods=n)
    y = pd.Series((rng.random(n) < 0.15).astype(float), index=idx)
    p = pd.Series((0.5 * y + 0.1 + 0.15 * rng.random(n)).clip(0, 1), index=idx)
    full = searoom.analyze(p, y)
    y_open = y.copy()
    y_open.iloc[-5:] = np.nan
    blanked = searoom.analyze(p, y_open)
    assert full["ok"] and blanked["ok"]
    assert full["today"] == blanked["today"]
    assert full["set_counts"] == blanked["set_counts"]


def test_searoom_refuses_short_history():
    idx = pd.bdate_range("2024-01-01", periods=100)
    out = searoom.analyze(pd.Series(0.2, index=idx), pd.Series(0.0, index=idx))
    assert not out["ok"]


# ---------------------------------------------------------------------------
# Sea State (2-state HMM)
# ---------------------------------------------------------------------------

def _two_regime_spread(n: int = 1500, seed: int = 5) -> tuple[pd.Series, np.ndarray]:
    """Planted regimes: long calm spells (sigma 1bp) with rough spells
    (sigma 12bp) every ~200 days lasting ~30 days."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-01", periods=n)
    rough = np.zeros(n, dtype=bool)
    for start in range(150, n - 40, 200):
        rough[start:start + 30] = True
    x = np.where(rough, rng.normal(2.0, 12.0, n), rng.normal(0.0, 1.0, n))
    return pd.Series(np.cumsum(np.zeros(n)) + x, index=idx), rough


def test_seastate_finds_planted_regimes():
    spread, rough = _two_regime_spread()
    out = seastate.analyze(spread)
    assert out["ok"]
    assert out["states"]["rough"]["sigma_bp"] > 3 * out["states"]["calm"]["sigma_bp"]
    p = out["_p_rough"].to_numpy()
    valid = np.isfinite(p)
    # filtered P(rough) must be much higher inside planted rough spells
    # (skip the first 5 days of each spell — a causal filter needs evidence)
    settled = rough.copy()
    for i in range(1, 6):
        settled &= np.roll(rough, i)
    assert np.nanmean(p[valid & settled[-len(p):]]) > np.nanmean(p[valid & ~rough[-len(p):]]) + 0.3


def test_seastate_truncation_equality():
    spread, _ = _two_regime_spread()
    full = seastate.analyze(spread)
    trunc = seastate.analyze(spread.iloc[:1100])
    assert full["ok"] and trunc["ok"]
    a = full["_p_rough"].dropna()
    b = trunc["_p_rough"].dropna()
    common = b.index[b.index.isin(a.index)]
    # published history only exists at prefix-fitted segments; values on the
    # common range must be identical (refits at fixed positions, causal filter)
    seg = common[common <= b.index[-1]]
    pd.testing.assert_series_equal(a.loc[seg], b.loc[seg])


def test_seastate_deterministic():
    spread, _ = _two_regime_spread()
    a = seastate.analyze(spread)
    b = seastate.analyze(spread)
    pd.testing.assert_series_equal(a["_p_rough"], b["_p_rough"])
    assert a["p_rough_now"] == b["p_rough_now"]


def test_seastate_refuses_short_history():
    idx = pd.bdate_range("2024-01-01", periods=200)
    out = seastate.analyze(pd.Series(0.0, index=idx))
    assert not out["ok"]
