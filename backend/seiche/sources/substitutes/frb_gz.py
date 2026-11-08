"""ALT-GZ: the Board's GZ credit spread and excess bond premium, monthly.

Publisher: Board of Governors of the Federal Reserve System (FEDS Notes
data file). Columns: date (first day of the month), gz_spread and ebp in
percentage points, est_prob (12-month recession probability, not read).
Terms (https://www.federalreserve.gov/disclaimer.htm, quoted 2026-11-09):
"Unless otherwise indicated, information on Board's website is in the public
domain and may be copied and distributed without permission. Please cite to
the Board as the source of the information." The CSV carries no restriction
notice. The FEDS Note names commercial bond-price inputs, so a residual
third-party risk is recorded in the substitutes registry; only the Board's
published calculations are read.

Monthly rows are dated at the last calendar day of their month, so the
future-date rule excludes a month that has not ended.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence

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

SUBSTITUTE_ID = "ALT-GZ"
SOURCE = "frb_gz"
PUBLISHER = "Board of Governors of the Federal Reserve System"
SOURCE_URL = "https://www.federalreserve.gov/econres/notes/feds-notes/ebp_csv.csv"
TERMS_URL = "https://www.federalreserve.gov/disclaimer.htm"
MAX_BYTES = 1_000_000
HEADER = ("date", "gz_spread", "ebp", "est_prob")
MEASURES: dict[str, MeasureSpec] = {
    "gz_spread": MeasureSpec(
        "gz_spread", "percentage points", "monthly", "Gilchrist-Zakrajsek credit spread"
    ),
    "ebp": MeasureSpec("ebp", "percentage points", "monthly", "excess bond premium"),
}
_BOUNDS = {"gz_spread": ("0", "25"), "ebp": ("-10", "15")}


async def fetch(
    client: httpx.AsyncClient, *, clock: Clock | None = None
) -> tuple[Acquisition, ...]:
    document = await fetch_fixed(
        client, SOURCE_URL, max_bytes=MAX_BYTES, label="Board GZ CSV", clock=clock,
    )
    return (document,)


def parse(documents: Sequence[Acquisition]) -> tuple[SourceObservation, ...]:
    if len(documents) != 1:
        raise ValueError("Board GZ expects exactly one document")
    document = documents[0]
    require_document(
        document, source_url=SOURCE_URL, max_bytes=MAX_BYTES, label="Board GZ CSV"
    )
    rows = list(csv.reader(io.StringIO(document.payload.decode("utf-8-sig"))))
    if not rows or tuple(cell.strip() for cell in rows[0]) != HEADER:
        raise ValueError("Board GZ CSV header changed")
    boundary = acquisition_day(document)
    seen = set()
    points: list[SourceObservation] = []
    for raw in rows[1:]:
        if not any(cell.strip() for cell in raw):
            continue
        if len(raw) != len(HEADER):
            raise ValueError("Board GZ CSV row width changed")
        label_day = iso_day(raw[0], label="Board GZ")
        if label_day.day != 1:
            raise ValueError("Board GZ dates are no longer first-of-month labels")
        period = f"{label_day.year:04d}-{label_day.month:02d}"
        if period in seen:
            raise ValueError(f"Board GZ month duplicated: {period}")
        seen.add(period)
        event_day = month_end(label_day.year, label_day.month)
        for index, name in ((1, "gz_spread"), (2, "ebp")):
            if not raw[index].strip():
                continue
            value = parse_decimal(raw[index], label=f"Board GZ {name}")
            check_bounds(value, *_BOUNDS[name], label=f"Board GZ {name}")
            if event_day > boundary:
                continue
            points.append(make_observation(
                substitute_id=SUBSTITUTE_ID, measure=name, spec=MEASURES[name],
                event_date=event_day, period=period, value=value,
                source=SOURCE, publisher=PUBLISHER, source_url=SOURCE_URL,
                terms_url=TERMS_URL, documents=(document,),
                evidence={"source_url": SOURCE_URL, "column": name,
                          "date": raw[0].strip(), "value": raw[index].strip()},
            ))
    if not points:
        raise ValueError("Board GZ CSV has no usable observations")
    return tuple(sorted(points, key=lambda item: (item.event_date, item.measure)))
