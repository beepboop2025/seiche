"""Keyless EIA petroleum-history collector.

EIA's current Cushing page links a legacy HTML history table.  The table is
first-party, weekly, and needs no API key, but it predates modern machine-data
formats.  This collector gives that HTML the same provenance/caching envelope
as Seiche's API-backed series and fails loud if EIA changes the table shape.
"""

from __future__ import annotations

from html.parser import HTMLParser
import re

import httpx
import pandas as pd

from seiche import store
from seiche.config import ALL_SERIES, USER_AGENT, SeriesSpec
from seiche.sources.base import Series, SourceFault, utcnow_iso

BASE = "https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx"
_MONTH_ROW = re.compile(r"^(\d{4})-([A-Z][a-z]{2})$")
_END_DATE = re.compile(r"^(\d{2})/(\d{2})$")


class _TableParser(HTMLParser):
    """Small structural parser for EIA's history rows; no HTML dependency."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self._row = []
        elif tag.lower() == "td" and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "td" and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
            self._cell = None


def parse_history(html: str) -> pd.Series:
    """Parse EIA month rows into actual weekly ending dates and $000 barrels."""

    parser = _TableParser()
    parser.feed(html)
    observations: list[tuple[pd.Timestamp, float]] = []
    for cells in parser.rows:
        if not cells:
            continue
        match = _MONTH_ROW.fullmatch(cells[0].strip())
        if match is None:
            continue
        year = int(match.group(1))
        # EIA emits five (end-date, value) pairs after the Year-Month stub.
        for index in range(1, len(cells) - 1, 2):
            date_match = _END_DATE.fullmatch(cells[index].strip())
            raw_value = cells[index + 1].replace(",", "").strip()
            if (
                date_match is None
                or not raw_value
                or raw_value in {"-", "--", "NA", "W"}
            ):
                continue
            month, day = map(int, date_match.groups())
            try:
                date = pd.Timestamp(year=year, month=month, day=day)
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            observations.append((date, value))
    if len(observations) < 52:
        raise ValueError(
            f"unexpected EIA history shape ({len(observations)} observations)"
        )
    points = pd.Series(
        [value for _, value in observations],
        index=pd.DatetimeIndex([date for date, _ in observations]),
        dtype=float,
    )
    return points[~points.index.duplicated(keep="last")].sort_index()


async def fetch_series(client: httpx.AsyncClient, spec: SeriesSpec) -> Series:
    if store.is_fresh(spec.mnemonic, spec.ttl_minutes):
        cached = store.load_series(spec.mnemonic)
        if cached is not None:
            return cached
    try:
        response = await client.get(
            BASE,
            params={"n": "PET", "s": spec.remote_id, "f": "W"},
            headers={"User-Agent": USER_AGENT},
            timeout=45,
        )
        response.raise_for_status()
        points = parse_history(response.text)
        points = points[points.index >= pd.Timestamp(spec.start)]
        series = Series(
            spec.mnemonic,
            "eia",
            spec.remote_id,
            spec.label,
            spec.unit,
            spec.freq,
            utcnow_iso(),
            points,
        )
        store.save_series(series)
        return series
    except Exception as exc:
        cached = store.load_series(spec.mnemonic)
        if cached is not None:
            return cached
        raise SourceFault(
            "eia", f"{spec.remote_id}: {type(exc).__name__}: {exc}"
        ) from exc


async def fetch_many(
    client: httpx.AsyncClient, mnemonics: list[str], faults: list[dict] | None = None
) -> dict[str, Series]:
    out: dict[str, Series] = {}
    for mnemonic in mnemonics:
        try:
            out[mnemonic] = await fetch_series(client, ALL_SERIES[mnemonic])
        except SourceFault as exc:
            if faults is not None:
                faults.append({"source": exc.source, "detail": exc.detail})
    return out
