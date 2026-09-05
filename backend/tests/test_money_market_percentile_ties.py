"""Floating-point ties must not count twice in empirical stress ranks."""

import pandas as pd
import pytest

from seiche.engines import money_market


@pytest.mark.parametrize("level", [-10.0, -1.0, 1.0, 2.0, 5.0, 5000.0])
@pytest.mark.parametrize("direction", [-1, 1])
def test_approximately_flat_history_has_midrank_fifty(level, direction):
    index = pd.bdate_range("2026-01-01", periods=60)
    near = level + direction * abs(level) * 1e-12
    values = pd.Series([near] * 59 + [level], index=index)
    actual = money_market._percentile_3y(values, "daily")
    assert actual == 50.0
    assert 0 <= actual <= 100


def test_below_tied_and_above_observations_are_disjoint():
    index = pd.bdate_range("2026-01-01", periods=60)
    values = pd.Series(
        [1.0] * 20 + [2.0 - 1e-12] * 19 + [3.0] * 20 + [2.0], index=index
    )
    assert money_market._percentile_3y(values, "daily") == 50.0
    exact = pd.Series([1.0] * 59 + [2.0], index=index)
    assert money_market._percentile_3y(exact, "daily") == 99.2


def test_policy_anchor_change_preserves_constant_two_basis_point_spread():
    index = pd.bdate_range("2026-01-01", periods=60)
    desk = money_market.analyze(
        iorb=pd.Series([5.0] * 59 + [3.0], index=index),
        sofr=pd.Series([5.02] * 59 + [3.02], index=index),
    )
    assert desk["regime"]["raw_worst_stress_percentile"] == 50.0
    assert desk["regime"]["worst_stress_percentile"] == 50.0
    assert desk["regime"]["state"] == "NORMAL"
    indicator = desk["regime"]["worst_indicator"]
    assert indicator["raw_empirical_tail_probability"] == 0.5
    assert desk["diagnostics"]["persistence"]["windows"][2]["median_bp"] == 2.0
