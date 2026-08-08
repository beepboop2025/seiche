from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from seiche import store
from seiche.sources.base import Series


def _series(fetched_at: str, first_value: float) -> Series:
    return Series(
        "TEST",
        "test",
        "TEST",
        "test series",
        "index",
        "D",
        fetched_at,
        pd.Series(
            [first_value, 2.0],
            index=pd.DatetimeIndex(["2026-01-01", "2026-01-02"]),
        ),
    )


def test_series_store_reconstructs_captured_revision_as_of_knowledge_time(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "vintages.sqlite")
    first_time = "2026-01-03T12:00:00+00:00"
    revised_time = "2026-01-05T12:00:00+00:00"
    store.save_series(_series(first_time, 1.0))
    store.save_series(_series(revised_time, 9.0))

    before_capture = store.load_series_as_of(
        "TEST", datetime(2026, 1, 3, 11, tzinfo=UTC)
    )
    first_vintage = store.load_series_as_of("TEST", first_time)
    revised_vintage = store.load_series_as_of("TEST", revised_time)

    assert before_capture is None
    assert first_vintage is not None
    assert first_vintage.points.iloc[0] == 1.0
    assert revised_vintage is not None
    assert revised_vintage.points.iloc[0] == 9.0
