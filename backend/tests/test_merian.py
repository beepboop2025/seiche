"""Merian Modes tests — planted spectra and the honesty invariants.

Same philosophy as test_engines.py: the tests that matter are not "does it
run" but "does it refuse to cheat" — the engine must find a period it was
given, flag a growing mode, read a decaying world as calm, never look ahead,
refuse thin panels, report each oscillation once (conjugates collapsed), and
publish a JSON-safe payload.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from seiche.engines import merian


def _bdays(n: int, start: str = "2019-01-01") -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n)


@pytest.fixture()
def rng():
    return np.random.default_rng(7)


def _mode_panel(
    rng,
    n: int = 1200,
    n_series: int = 6,
    period: float = 21.0,
    noise: float = 0.3,
    growth_last: int | None = None,
    tau: float | None = None,
) -> dict[str, pd.Series]:
    """Series sharing one planted oscillation with per-series phases; optional
    exponential amplitude ramp over the last `growth_last` days (tau > 0
    growing — roughly doubling within the 250bd fit window at tau ~ 360 —
    tau < 0 decaying)."""
    idx = _bdays(n)
    t = np.arange(n, dtype=float)
    amp = np.ones(n)
    if growth_last is not None:
        s0 = n - growth_last
        amp[s0:] = np.exp((t[s0:] - s0) / tau)
    panel: dict[str, pd.Series] = {}
    for i in range(n_series):
        phase = 2.0 * np.pi * i / n_series
        sig = amp * np.sin(2.0 * np.pi * t / period + phase)
        name = "SOFR-IORB" if i == 0 else f"pipe{i}"
        panel[name] = pd.Series(sig + rng.normal(0, noise, n), index=idx)
    return panel


_TAU_DOUBLE_PER_WINDOW = 250.0 / np.log(2.0)  # amplitude doubles per fit window


def test_merian_finds_planted_period(rng):
    r = merian.analyze(_mode_panel(rng))
    assert r["ok"]
    top2 = r["modes"][:2]
    assert any(
        m["period_bd"] is not None and 18.0 <= m["period_bd"] <= 24.0 for m in top2
    ), f"planted 21bd oscillation must surface in the top-2 modes, got {top2}"


def test_merian_flags_growing_mode(rng):
    r = merian.analyze(_mode_panel(rng, growth_last=300, tau=_TAU_DOUBLE_PER_WINDOW))
    assert r["ok"]
    inst = r["instability"]
    assert inst["g_now"] > 0, "an exponentially growing oscillation must read |lambda| > 1"
    assert inst["pctl"] >= 90, "a live growing mode must sit in the top decile of its own history"


def test_merian_decaying_world_reads_calm(rng):
    r = merian.analyze(_mode_panel(rng, growth_last=300, tau=-_TAU_DOUBLE_PER_WINDOW))
    assert r["ok"]
    assert r["instability"]["g_now"] < 0, "a decaying oscillation must read |lambda| < 1"


def test_merian_no_look_ahead(rng):
    panel = _mode_panel(rng, growth_last=300, tau=_TAU_DOUBLE_PER_WINDOW)
    full = merian.analyze(panel)
    trunc = merian.analyze({k: s.iloc[:-120] for k, s in panel.items()})
    assert full["ok"] and trunc["ok"]
    t = trunc["_g_pctl_series"].index[-1]
    assert abs(float(full["_g_pctl_series"].loc[t]) - float(trunc["_g_pctl_series"].loc[t])) < 1e-9, \
        "instability percentile at T changed when future data was appended — look-ahead leak"


def test_merian_refuses_thin_panel(rng):
    # 2 series: below MERIAN_MIN_SERIES regardless of history length
    thin = {k: v for k, v in list(_mode_panel(rng).items())[:2]}
    r = merian.analyze(thin)
    assert not r["ok"]
    # 6 series but only 300 days: below MERIAN_MIN_HISTORY_D
    short = {k: s.iloc[:300] for k, s in _mode_panel(rng).items()}
    r2 = merian.analyze(short)
    assert not r2["ok"]


def test_merian_conjugate_pairs_collapsed(rng):
    r = merian.analyze(_mode_panel(rng))
    assert r["ok"]
    seen = set()
    for m in r["modes"]:
        key = (m["period_bd"], m["efold_bd"])
        assert key not in seen, \
            f"mode {key} reported twice — conjugate pair leaked into the mode table"
        seen.add(key)


def test_merian_payload_json_safe(rng):
    r = merian.analyze(_mode_panel(rng))
    assert r["ok"]
    payload = {k: v for k, v in r.items() if not str(k).startswith("_")}
    for key in (
        "ok", "asof", "n_series", "series_used", "window_bd", "rank", "modes",
        "instability", "rows", "forecast_skill", "caveats", "method",
    ):
        assert key in payload, f"missing output key {key}"
    json.dumps(payload)  # must not raise
    assert len(r["rows"]) <= 500
