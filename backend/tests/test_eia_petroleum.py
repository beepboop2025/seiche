"""EIA legacy-table parsing stays structural and dependency-free."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pandas as pd
import pytest

from seiche import store
from seiche.config import OIL_FUNDING_EIA_SERIES
from seiche.sources.base import Series
from seiche.sources.eia_petroleum import fetch_series, parse_history


def test_parse_history_reads_weekly_pairs_and_ignores_missing_values() -> None:
    rows = []
    for year in range(2024, 2027):
        for month, name in enumerate(
            (
                "Jan",
                "Feb",
                "Mar",
                "Apr",
                "May",
                "Jun",
                "Jul",
                "Aug",
                "Sep",
                "Oct",
                "Nov",
                "Dec",
            ),
            start=1,
        ):
            cells = [f"<td class='B6'>{year}-{name}</td>"]
            for day, value in ((7, 20_000 + month), (14, 20_100 + month)):
                cells.extend(
                    [
                        f"<td class='B5'>{month:02d}/{day:02d}</td>",
                        f"<td class='B3'>{value:,}</td>",
                    ]
                )
            cells.extend(["<td class='B5'>&nbsp;</td>", "<td class='B3'>NA</td>"])
            rows.append(f"<tr>{''.join(cells)}</tr>")

    series = parse_history(f"<table><tbody>{''.join(rows)}</tbody></table>")
    assert len(series) == 72
    assert series.index[0].date().isoformat() == "2024-01-07"
    assert series.index[-1].date().isoformat() == "2026-12-14"
    assert series.iloc[0] == 20_001.0
    assert series.iloc[-1] == 20_112.0


@pytest.mark.asyncio
async def test_cached_fallback_records_the_live_source_fault(monkeypatch) -> None:
    spec = OIL_FUNDING_EIA_SERIES[0]
    cached = Series(
        spec.mnemonic,
        "eia",
        spec.remote_id,
        spec.label,
        spec.unit,
        spec.freq,
        "2026-08-01T00:00:00+00:00",
        pd.Series([20_955.0], index=pd.DatetimeIndex(["2026-07-31"])),
    )
    monkeypatch.setattr(store, "is_fresh", lambda *_: False)
    monkeypatch.setattr(store, "load_series", lambda *_: cached)
    client = AsyncMock()
    client.get.side_effect = RuntimeError("upstream table unavailable")
    faults: list[dict] = []

    result = await fetch_series(client, spec, faults)

    assert result is cached
    assert faults == [
        {
            "source": "eia",
            "detail": (
                "W_EPC0_SAX_YCUOK_MBBL: RuntimeError: upstream table unavailable"
            ),
        }
    ]
