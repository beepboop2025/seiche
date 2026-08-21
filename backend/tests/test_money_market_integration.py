from __future__ import annotations

import pandas as pd
from seiche import assemble


def test_ofr_dollar_volume_scaling_does_not_depend_on_latest_value() -> None:
    index = pd.date_range("2026-08-18", periods=3, freq="D")
    raw_dollars = pd.Series([1_200_000_000_000.0, 900_000_000_000.0, 0.0], index=index)

    billions = assemble._vol_b(raw_dollars)

    assert billions.tolist() == [1200.0, 900.0, 0.0]


def test_already_normalized_billions_are_not_scaled_twice() -> None:
    index = pd.date_range("2026-08-18", periods=3, freq="D")
    reported_billions = pd.Series([1200.0, 900.0, 0.0], index=index)

    assert assemble._vol_b(reported_billions).equals(reported_billions)
