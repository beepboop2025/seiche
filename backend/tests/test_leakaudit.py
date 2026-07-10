"""Leak Audit: the one-switch protocol must itself be leak-free and honest.

The audit's own invariants:
  - the clean variant is bit-reproducible (two builds hash identically);
  - each toggle changes the index (the switch actually switches);
  - history.build(leak=...) rejects unknown modes and defaults to clean;
  - the clean lite index equals the pre-audit build call exactly (adding the
    leak parameter must not perturb the published pipeline).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from seiche.engines import history as eng_history
from seiche.engines import leakaudit


def _inputs(n_days: int = 1400) -> dict:
    idx = pd.bdate_range("2018-01-02", periods=n_days)
    rng = np.random.default_rng(42)
    base = pd.Series(rng.normal(0, 2.0, n_days), index=idx).cumsum() * 0.05
    pops = rng.random(n_days) < 0.05
    spread = base + pd.Series(np.where(pops, 15.0, 0.0), index=idx)
    empty = pd.Series(dtype=float)
    rrp = pd.Series(np.maximum(0.0, 2000.0 - np.arange(n_days)), index=idx)
    res_gdp = pd.Series(0.14 - 0.00002 * np.arange(n_days), index=idx)
    return dict(
        spread_bp=spread, tail_bp=empty, srf_accepted=empty, dw_b=empty,
        rrp_b=rrp, res_gdp=res_gdp, pair_b=empty, digestion=empty,
    )


def test_leak_param_default_is_identity():
    kw = _inputs()
    a = eng_history.build(**kw)
    b = eng_history.build(**kw, leak="none")
    pd.testing.assert_series_equal(a["index"], b["index"])


def test_unknown_leak_mode_rejected():
    with pytest.raises(ValueError):
        eng_history.build(**_inputs(), leak="future_peek")


def test_toggles_actually_switch():
    kw = _inputs()
    clean = eng_history.build(**kw)["index"]
    for mode in ("norm_global", "temp_center"):
        leaky = eng_history.build(**kw, leak=mode)["index"]
        assert not clean.round(6).equals(leaky.round(6)), mode


def test_audit_runs_and_is_reproducible():
    kw = _inputs()
    out = leakaudit.run(kw, kw["spread_bp"])
    assert out["ok"]
    assert out["bit_reproducible"] is True
    toggles = {r["toggle"] for r in out["rows"]}
    assert {"clean", "NORM_GLOBAL", "TEMP_CENTER"} <= toggles
    clean_row = next(r for r in out["rows"] if r["toggle"] == "clean")
    assert clean_row["lg_auroc"] == 0.0
    # deterministic: same inputs, same hash
    out2 = leakaudit.run(kw, kw["spread_bp"])
    assert out2["clean_index_sha256"] == out["clean_index_sha256"]
