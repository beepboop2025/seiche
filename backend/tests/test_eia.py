"""Official keyless EIA inventory parsing and stale-serve behavior."""

from __future__ import annotations

import asyncio

import pandas as pd
import pytest

from seiche.config import ALL_SERIES
from seiche.sources import eia_petroleum as eia
from seiche.sources.base import Series, SourceFault


def _history_html(periods: int = 60) -> str:
    dates = pd.date_range("2024-01-05", periods=periods, freq="W-FRI")
    rows = []
    for month, group in pd.Series(dates, index=dates).groupby(
        lambda value: value.strftime("%Y-%b")
    ):
        cells = [f'<td class="B6">{month}</td>']
        for offset, date in enumerate(group.tolist()):
            cells.append(f'<td class="B6">{date.strftime("%m/%d")}</td>')
            cells.append(f'<td class="B6">{410_000 + offset:,}</td>')
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return f"<html><table>{''.join(rows)}</table></html>"


def test_parse_weekly_history_keeps_eia_dates_and_numeric_units() -> None:
    points = eia.parse_history(_history_html())

    assert len(points) == 60
    assert points.index.is_monotonic_increasing
    assert points.index[0] == pd.Timestamp("2024-01-05")


class _Response:
    def __init__(self, text: str, *, error: Exception | None = None) -> None:
        self.text = text
        self.error = error

    def raise_for_status(self) -> None:
        if self.error is not None:
            raise self.error


class _Client:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_fetch_series_uses_official_keyless_history_and_caches(monkeypatch) -> None:
    saved: list[Series] = []
    monkeypatch.setattr(eia.store, "is_fresh", lambda *_: False)
    monkeypatch.setattr(eia.store, "load_series", lambda *_: None)
    monkeypatch.setattr(eia.store, "save_series", saved.append)
    client = _Client(_Response(_history_html()))

    result = asyncio.run(
        eia.fetch_series(client, ALL_SERIES["CRUDE_STOCKS_EX_SPR"])
    )

    assert result.source == "eia"
    assert result.remote_id == "WCESTUS1"
    assert len(result.points) == 60
    assert saved == [result]
    url, kwargs = client.calls[0]
    assert url == eia.BASE
    assert kwargs["params"] == {"n": "PET", "s": "WCESTUS1", "f": "W"}


def test_fetch_series_serves_stale_cache_after_http_failure(monkeypatch) -> None:
    stale = Series(
        "CRUDE_STOCKS_EX_SPR", "eia", "WCESTUS1", "stocks", "kbbl", "W",
        "2026-01-01T00:00:00+00:00",
        pd.Series([400_000.0], index=pd.DatetimeIndex(["2025-12-26"])),
    )
    monkeypatch.setattr(eia.store, "is_fresh", lambda *_: False)
    monkeypatch.setattr(eia.store, "load_series", lambda *_: stale)
    client = _Client(_Response("", error=RuntimeError("upstream down")))

    result = asyncio.run(
        eia.fetch_series(client, ALL_SERIES["CRUDE_STOCKS_EX_SPR"])
    )

    assert result is stale


def test_fetch_series_fails_loud_without_history_or_cache(monkeypatch) -> None:
    monkeypatch.setattr(eia.store, "is_fresh", lambda *_: False)
    monkeypatch.setattr(eia.store, "load_series", lambda *_: None)
    client = _Client(_Response("<html>no history</html>"))

    with pytest.raises(SourceFault, match="unexpected EIA history shape"):
        asyncio.run(eia.fetch_series(client, ALL_SERIES["CRUDE_STOCKS_EX_SPR"]))
