"""ALT-CISS: the ECB New CISS for the United States, daily.

Publisher: European Central Bank. Series CISS.D.US.Z0Z.4F.EC.SS_CIN.IDX, a
composite of 15 market-based stress measures across money, equity, bond and
FX markets and financial intermediaries, bounded between 0 and 1.
Terms (https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html,
quoted 2026-11-09): "users of this website may make free use of the
information obtained directly from it subject to the following conditions:
When such information is distributed or reproduced, it must appear
accurately and the ECB must be cited as the source." Modified data must be
stated as modified; this adapter does not modify values.
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
    parse_decimal,
    require_document,
)

SUBSTITUTE_ID = "ALT-CISS"
SOURCE = "ecb_ciss"
PUBLISHER = "European Central Bank"
SERIES_KEY = "CISS.D.US.Z0Z.4F.EC.SS_CIN.IDX"
SOURCE_URL = (
    "https://data-api.ecb.europa.eu/service/data/CISS/"
    "D.US.Z0Z.4F.EC.SS_CIN.IDX?format=csvdata"
)
TERMS_URL = "https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html"
MAX_BYTES = 16_000_000
MEASURES: dict[str, MeasureSpec] = {
    "ciss": MeasureSpec(
        SERIES_KEY, "index (0 to 1)", "daily",
        "New Composite Indicator of Systemic Stress, United States",
    ),
}
# The SDMX identity of the one series this adapter accepts.
PINNED = {
    "KEY": SERIES_KEY, "FREQ": "D", "REF_AREA": "US", "UNIT": "PURE_NUMB",
    "UNIT_MULT": "0",
}
_VALUE_STATUSES = frozenset({"A", "E", "P"})
_MISSING_STATUSES = frozenset({"M", "L", "N"})
_REQUIRED = frozenset({*PINNED, "TIME_PERIOD", "OBS_VALUE", "OBS_STATUS"})


async def fetch(
    client: httpx.AsyncClient, *, clock: Clock | None = None
) -> tuple[Acquisition, ...]:
    document = await fetch_fixed(
        client, SOURCE_URL, max_bytes=MAX_BYTES, label="ECB CISS CSV",
        clock=clock, accept="text/csv",
    )
    return (document,)


def parse(documents: Sequence[Acquisition]) -> tuple[SourceObservation, ...]:
    if len(documents) != 1:
        raise ValueError("ECB CISS expects exactly one document")
    document = documents[0]
    require_document(
        document, source_url=SOURCE_URL, max_bytes=MAX_BYTES, label="ECB CISS CSV"
    )
    reader = csv.DictReader(io.StringIO(document.payload.decode("utf-8-sig")))
    if not _REQUIRED.issubset(reader.fieldnames or ()):
        raise ValueError("ECB CISS CSV columns changed")
    boundary = acquisition_day(document)
    seen = set()
    points: list[SourceObservation] = []
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("ECB CISS CSV row width changed")
        for column, expected in PINNED.items():
            if row[column].strip() != expected:
                raise ValueError(f"ECB CISS {column} changed to {row[column]!r}")
        day = iso_day(row["TIME_PERIOD"], label="ECB CISS")
        if day in seen:
            raise ValueError(f"ECB CISS date duplicated: {day.isoformat()}")
        seen.add(day)
        status = row["OBS_STATUS"].strip()
        raw_value = row["OBS_VALUE"].strip()
        if status in _MISSING_STATUSES and not raw_value:
            continue
        if status not in _VALUE_STATUSES:
            raise ValueError(f"ECB CISS observation status {status!r} unknown")
        value = parse_decimal(raw_value, label="ECB CISS")
        check_bounds(value, "0", "1", label="ECB CISS")
        if day > boundary:
            continue
        points.append(make_observation(
            substitute_id=SUBSTITUTE_ID, measure="ciss", spec=MEASURES["ciss"],
            event_date=day, period=day.isoformat(), value=value,
            source=SOURCE, publisher=PUBLISHER, source_url=SOURCE_URL,
            terms_url=TERMS_URL, documents=(document,),
            evidence={"source_url": SOURCE_URL, "key": SERIES_KEY,
                      "time_period": row["TIME_PERIOD"].strip(), "obs_value": raw_value,
                      "obs_status": status, "unit": PINNED["UNIT"],
                      "unit_mult": PINNED["UNIT_MULT"]},
        ))
    if not points:
        raise ValueError("ECB CISS CSV has no usable observations")
    return tuple(sorted(points, key=lambda item: item.event_date))
