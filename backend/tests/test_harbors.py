"""Harbors engine + chinamoney parser: fail-loud and never-fake invariants."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from seiche.engines import harbors
from seiche.sources.chinamoney import parse_records


def _daily(n: int, val: float = 2.0, jitter: float = 0.0, seed: int = 0,
           end: str = "2026-07-10") -> pd.Series:
    idx = pd.bdate_range(end=end, periods=n)
    rng = np.random.default_rng(seed)
    vals = val + (rng.standard_normal(n) * jitter if jitter else np.zeros(n))
    return pd.Series(vals, index=idx)


def _monthly_ramp(n: int, start_val: float, step: float, end: str = "2026-05-01") -> pd.Series:
    idx = pd.date_range(end=end, periods=n, freq="MS")
    return pd.Series(start_val + step * np.arange(n), index=idx)


def _harbor(rate: pd.Series, fx: pd.Series, cadence: str = "daily") -> dict:
    return {"rate": rate, "rate_label": "test rate", "cadence": cadence,
            "fx": fx, "fx_label": "LOC per USD"}


EMPTY = pd.Series(dtype=float)


def test_refuses_empty_config():
    out = harbors.analyze({}, EMPTY)
    assert out["ok"] is False


def test_refuses_all_dead_harbors():
    out = harbors.analyze({"X": _harbor(EMPTY, EMPTY)}, EMPTY)
    assert out["ok"] is False
    assert "no harbor" in out["reason"]


def test_regime_classification():
    tightening = _monthly_ramp(24, 4.0, 0.05)   # +30bp over 6 months
    easing = _monthly_ramp(24, 6.0, -0.05)
    holding = _monthly_ramp(24, 5.5, 0.0)
    fx = _daily(400, 80.0, jitter=0.2)
    out = harbors.analyze({
        "T": _harbor(tightening, fx, "monthly ~2mo lag"),
        "E": _harbor(easing, fx, "monthly ~2mo lag"),
        "H": _harbor(holding, fx, "monthly ~2mo lag"),
    }, EMPTY)
    assert out["ok"]
    regimes = {h["harbor"]: h["regime"] for h in out["harbors"]}
    assert regimes == {"T": "TIGHTENING", "E": "EASING", "H": "HOLDING"}
    assert out["cycle"]["easing"] == 1 and out["cycle"]["tightening"] == 1


def test_stress_bounds_and_renormalized_coverage():
    rate = _daily(500, 2.0, jitter=0.02, seed=1)
    fx = _daily(500, 80.0, jitter=0.3, seed=2)
    out = harbors.analyze({"X": _harbor(rate, fx)}, _daily(500, 4.3, jitter=0.01, seed=3))
    h = out["harbors"][0]
    assert h["stress"] is not None and 0.0 <= h["stress"] <= 100.0
    assert h["stress_coverage"] == 1.0
    assert out["cycle"]["us_ref"] is not None


def test_short_history_quarantined_not_faked():
    # 20 observations: no percentile may fire — the harbor must say it is
    # accruing rather than scoring as calm.
    out = harbors.analyze({"CN": _harbor(_daily(20, 1.36), _daily(20, 6.8))}, EMPTY)
    h = out["harbors"][0]
    assert h["stress"] is None
    assert h["stress_coverage"] == 0.0
    assert "accruing" in h["note"]
    assert h["rate"]["last_pct"] == pytest.approx(1.36)


def test_monthly_rates_charted_without_interpolation():
    rate = _monthly_ramp(24, 5.0, 0.0)
    fx = _daily(400, 80.0, jitter=0.2)
    out = harbors.analyze({"IN": _harbor(rate, fx, "monthly ~2mo lag")}, EMPTY)
    col = out["rate_labels"].index("IN") + 1
    non_null = [r for r in out["rate_rows"] if r[col] is not None]
    window_start = pd.Timestamp(out["rate_rows"][0][0])
    expected = int((rate.index >= window_start).sum())
    assert len(non_null) == expected  # every point is a real print, none invented


def test_fx_indexed_to_100():
    fx = _daily(600, 80.0)  # constant series indexes to exactly 100 everywhere
    out = harbors.analyze({"X": _harbor(_daily(600, 2.0), fx)}, EMPTY)
    vals = [r[1] for r in out["fx_rows"] if r[1] is not None]
    assert vals and all(v == pytest.approx(100.0) for v in vals)


# --- chinamoney parser -------------------------------------------------------

def test_parse_records_happy_path():
    payload = {"records": [
        {"showDateCN": "2026-07-10", "ON": "1.3601", "1W": "1.3830"},
        {"showDateCN": "2026-07-09", "ON": "1.3620"},
        {"showDateCN": "2026-07-08", "ON": ""},  # blank value skipped, not zeroed
    ]}
    s = parse_records(payload, "ON")
    assert list(s.index) == [pd.Timestamp("2026-07-09"), pd.Timestamp("2026-07-10")]
    assert s.iloc[-1] == pytest.approx(1.3601)


def test_parse_records_empty_body_fails_loud():
    # The CFETS throttle answers with an empty body — that is a fault, not
    # "no data today".
    with pytest.raises(ValueError):
        parse_records({}, "ON")
    with pytest.raises(ValueError):
        parse_records({"records": []}, "ON")


def test_parse_records_missing_tenor_fails_loud():
    with pytest.raises(ValueError):
        parse_records({"records": [{"showDateCN": "2026-07-10", "1W": "1.38"}]}, "ON")
