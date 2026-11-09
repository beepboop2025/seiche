"""ALT-FCIG: the Board's FCI-G financial conditions index, monthly.

Publisher: Board of Governors of the Federal Reserve System (FEDS Notes
research product, baseline with a three-year lookback). Positive values are
headwinds to GDP growth over the next year, in percentage points; the seven
factor contributions sum to the index, which is checked on every row.
The Board's data definitions call it "a research product and not an
official statistical release", subject to revision or discontinuation.

Terms (https://www.federalreserve.gov/disclaimer.htm, quoted 2026-11-09):
"Unless otherwise indicated, information on Board's website is in the public
domain and may be copied and distributed without permission. Please cite to
the Board as the source of the information." Inputs include commercial
series; only the Board's published index and contributions are read.

The source dates each month at its last business day; Seiche dates it at
the last calendar day and keeps the source date in the row evidence.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from decimal import Decimal

import httpx

from seiche.sources.substitutes._common import (
    Acquisition,
    Clock,
    MeasureSpec,
    SourceObservation,
    acquisition_day,
    check_bounds,
    fetch_fixed,
    iso_day,
    make_observation,
    month_end,
    parse_decimal,
    require_document,
)

SUBSTITUTE_ID = "ALT-FCIG"
SOURCE = "frb_fcig"
PUBLISHER = "Board of Governors of the Federal Reserve System"
SOURCE_URL = (
    "https://www.federalreserve.gov/econres/notes/feds-notes/fci_g_public_monthly_3yr.csv"
)
TERMS_URL = "https://www.federalreserve.gov/disclaimer.htm"
MAX_BYTES = 2_000_000
IDENTITY_TOLERANCE = Decimal("0.000001")
_UNIT = "percentage points of GDP growth over the next year"
_COLUMNS = (
    ("fci_g", "FCI-G Index (baseline)", "FCI-G index, baseline, three-year lookback"),
    ("ffr", "FFR", "federal funds rate contribution"),
    ("treasury_10y", "10Yr Treasury", "10-year Treasury yield contribution"),
    ("mortgage_rate", "Mortgage Rate", "30-year mortgage rate contribution"),
    ("bbb", "BBB", "BBB corporate bond yield contribution"),
    ("stock_market", "Stock Market", "stock market contribution"),
    ("house_prices", "House Prices", "house prices contribution"),
    ("dollar", "Dollar", "dollar contribution"),
)
HEADER = ("date", *(label for _name, label, _text in _COLUMNS))
MEASURES: dict[str, MeasureSpec] = {
    name: MeasureSpec(label, _UNIT, "monthly", text) for name, label, text in _COLUMNS
}


async def fetch(
    client: httpx.AsyncClient, *, clock: Clock | None = None
) -> tuple[Acquisition, ...]:
    document = await fetch_fixed(
        client, SOURCE_URL, max_bytes=MAX_BYTES, label="Board FCI-G CSV", clock=clock,
    )
    return (document,)


def parse(documents: Sequence[Acquisition]) -> tuple[SourceObservation, ...]:
    if len(documents) != 1:
        raise ValueError("Board FCI-G expects exactly one document")
    document = documents[0]
    require_document(
        document, source_url=SOURCE_URL, max_bytes=MAX_BYTES, label="Board FCI-G CSV"
    )
    rows = list(csv.reader(io.StringIO(document.payload.decode("utf-8-sig"))))
    if not rows or tuple(cell.strip() for cell in rows[0]) != HEADER:
        raise ValueError("Board FCI-G CSV header changed")
    boundary = acquisition_day(document)
    names = tuple(name for name, _label, _text in _COLUMNS)
    seen = set()
    points: list[SourceObservation] = []
    for raw in rows[1:]:
        if not any(cell.strip() for cell in raw):
            continue
        if len(raw) != len(HEADER):
            raise ValueError("Board FCI-G CSV row width changed")
        source_day = iso_day(raw[0], label="Board FCI-G")
        period = f"{source_day.year:04d}-{source_day.month:02d}"
        if period in seen:
            raise ValueError(f"Board FCI-G month duplicated: {period}")
        seen.add(period)
        values = {
            name: parse_decimal(cell, label=f"Board FCI-G {name}")
            for name, cell in zip(names, raw[1:], strict=True)
        }
        for name, value in values.items():
            check_bounds(value, "-20", "20", label=f"Board FCI-G {name}")
        gap = abs(sum(values[name] for name in names[1:]) - values["fci_g"])
        if gap > IDENTITY_TOLERANCE:
            raise ValueError(f"Board FCI-G factors no longer sum to the index in {period}")
        event_day = month_end(source_day.year, source_day.month)
        if event_day > boundary:
            continue
        for index, name in enumerate(names, 1):
            points.append(make_observation(
                substitute_id=SUBSTITUTE_ID, measure=name, spec=MEASURES[name],
                event_date=event_day, period=period, value=values[name],
                source=SOURCE, publisher=PUBLISHER, source_url=SOURCE_URL,
                terms_url=TERMS_URL, documents=(document,),
                evidence={"source_url": SOURCE_URL, "column": MEASURES[name].label,
                          "date": raw[0].strip(), "value": raw[index].strip()},
            ))
    if not points:
        raise ValueError("Board FCI-G CSV has no usable observations")
    return tuple(sorted(points, key=lambda item: (item.event_date, item.measure)))
