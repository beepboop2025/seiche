"""ALT-BISCBTA: BIS central bank total assets for Japan, monthly.

Publisher: Bank for International Settlements, data set WS_CBTA, series
M.JP.B.XDC.JPY.N (end-of-month total assets of the Bank of Japan, national
currency, BIS-spliced, not break-adjusted, in billions of yen). Observations
are monthly; BIS releases them quarterly.

Terms (https://www.bis.org/about/legal/permitted-use-statistics, read
2026-11-09 through a fetch tool because the page refuses scripted clients):
"The use of the statistics is unrestricted, provided that: 1. if the
statistics are reproduced, the BIS must be cited in your publication or
product as the source of the statistics; ... 3. your use of the statistics
must not be potentially misleading, for example by implying endorsement or
affiliation with the BIS or any body affiliated with the BIS; 4. if the
statistics will be used in a commercial publication or product, their
inclusion in the publication or product will not result in any additional
charge to subscribers or other users". Seiche is free, so the registry
allows it on free surfaces only and never in a charged product.
"""

from __future__ import annotations

import csv
import io
import re
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
    make_observation,
    month_end,
    parse_decimal,
    require_document,
)

SUBSTITUTE_ID = "ALT-BISCBTA"
SOURCE = "bis_cbta"
PUBLISHER = "Bank for International Settlements"
SERIES_KEY = "M.JP.B.XDC.JPY.N"
SOURCE_URL = (
    "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBTA/1.0/"
    f"{SERIES_KEY}?format=csv"
)
TERMS_URL = "https://www.bis.org/about/legal/permitted-use-statistics"
MAX_BYTES = 8_000_000
PINNED = {
    "FREQ": "M", "REF_AREA": "JP", "COMP_METHOD": "B", "UNIT_MEASURE": "XDC",
    "CURRENCY": "JPY", "TRANSFORMATION": "N", "UNIT_MULT": "9",
}
MEASURES: dict[str, MeasureSpec] = {
    "total_assets_jpy": MeasureSpec(
        f"WS_CBTA {SERIES_KEY}", "JPY billions", "monthly",
        "Japan, central bank total assets, end of month, BIS-spliced",
    ),
}
_REQUIRED = frozenset({*PINNED, "TIME_PERIOD", "OBS_VALUE", "OBS_STATUS"})
_PERIOD = re.compile(r"(\d{4})-(0[1-9]|1[0-2])")
_VALUE_STATUSES = frozenset({"A", "B", "E", "P"})
_MISSING_STATUSES = frozenset({"M", "L", "N"})


async def fetch(
    client: httpx.AsyncClient, *, clock: Clock | None = None
) -> tuple[Acquisition, ...]:
    document = await fetch_fixed(
        client, SOURCE_URL, max_bytes=MAX_BYTES, label="BIS CBTA CSV",
        clock=clock, accept="text/csv",
    )
    return (document,)


def parse(documents: Sequence[Acquisition]) -> tuple[SourceObservation, ...]:
    if len(documents) != 1:
        raise ValueError("BIS CBTA expects exactly one document")
    document = documents[0]
    require_document(
        document, source_url=SOURCE_URL, max_bytes=MAX_BYTES, label="BIS CBTA CSV"
    )
    reader = csv.DictReader(io.StringIO(document.payload.decode("utf-8-sig")))
    if not _REQUIRED.issubset(reader.fieldnames or ()):
        raise ValueError("BIS CBTA CSV columns changed")
    boundary = acquisition_day(document)
    seen = set()
    points: list[SourceObservation] = []
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("BIS CBTA CSV row width changed")
        for column, expected in PINNED.items():
            if row[column].strip() != expected:
                raise ValueError(f"BIS CBTA {column} changed to {row[column]!r}")
        match = _PERIOD.fullmatch(row["TIME_PERIOD"].strip())
        if match is None:
            raise ValueError("BIS CBTA period is no longer YYYY-MM")
        period = f"{match.group(1)}-{match.group(2)}"
        if period in seen:
            raise ValueError(f"BIS CBTA month duplicated: {period}")
        seen.add(period)
        status = row["OBS_STATUS"].strip()
        raw_value = row["OBS_VALUE"].strip()
        if status in _MISSING_STATUSES and not raw_value:
            continue
        if status not in _VALUE_STATUSES:
            raise ValueError(f"BIS CBTA observation status {status!r} unknown")
        value = parse_decimal(raw_value, label="BIS CBTA")
        check_bounds(value, "100", "10000000", label="BIS CBTA")
        event_day = month_end(int(match.group(1)), int(match.group(2)))
        if event_day > boundary:
            continue
        points.append(make_observation(
            substitute_id=SUBSTITUTE_ID, measure="total_assets_jpy",
            spec=MEASURES["total_assets_jpy"], event_date=event_day, period=period,
            value=value, source=SOURCE, publisher=PUBLISHER, source_url=SOURCE_URL,
            terms_url=TERMS_URL, documents=(document,),
            evidence={"source_url": SOURCE_URL, "series_key": SERIES_KEY,
                      "time_period": row["TIME_PERIOD"].strip(), "obs_value": raw_value,
                      "obs_status": status, "unit_measure": PINNED["UNIT_MEASURE"],
                      "unit_mult": PINNED["UNIT_MULT"]},
        ))
    if not points:
        raise ValueError("BIS CBTA CSV has no usable observations")
    return tuple(sorted(points, key=lambda item: item.event_date))
