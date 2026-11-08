"""ALT-CPBILL: AA financial 3-month CP minus the 3-month Treasury bill, daily.

Both legs come from the Board's own release XML packages, never from FRED
and never from the Data Download Program "Build Your Package" endpoints:

* CP leg: Commercial Paper release, series RIFSPPFAAD90_N.B (AA financial,
  90 days, percent), from FRB_CP_xml.zip;
* bill leg: H.15, series RIFSGFSM03_N.B (3-month Treasury bill, secondary
  market, percent per year), from FRB_H15_xml.zip.

H.15 footnotes both rates as annualized on a 360-day year and on a discount
basis, so no basis conversion is applied. The spread is computed by Seiche
for dates where both legs are available.

Terms: the Board notice (https://www.federalreserve.gov/disclaimer.htm)
places its website information in the public domain "Unless otherwise
indicated". The CP release "is derived from data supplied by The Depository
Trust & Clearing Corporation (DTCC)" (https://www.federalreserve.gov/releases/cp/about.htm).
Whether that is an "otherwise indicated" case is an open owner decision (the
same one that holds DCPN3M and DCPF3M), so the registry keeps this
substitute metadata-only for publication until she decides.
"""

from __future__ import annotations

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
)
from seiche.sources.substitutes._frb_xml import available_values, read_series

SUBSTITUTE_ID = "ALT-CPBILL"
SOURCE = "frb_cp_bill"
PUBLISHER = "Board of Governors of the Federal Reserve System"
CP_URL = "https://www.federalreserve.gov/releases/cp/data/FRB_CP_xml.zip"
H15_URL = "https://www.federalreserve.gov/releases/h15/data/FRB_H15_xml.zip"
SOURCE_URL = CP_URL
TERMS_URL = "https://www.federalreserve.gov/disclaimer.htm"
CP_MEMBER = "CP_data.xml"
H15_MEMBER = "H15_data.xml"
MAX_ARCHIVE_BYTES = 32_000_000
MAX_CP_XML_BYTES = 320_000_000
MAX_H15_XML_BYTES = 256_000_000
CP_SERIES = "RIFSPPFAAD90_N.B"
BILL_SERIES = "RIFSGFSM03_N.B"
CP_PINNED = {
    "CP_TERM": "90", "CP_TYPE": "FAA", "CURRENCY": "NA", "FREQ": "9",
    "SERIES_NAME": CP_SERIES, "UNIT": "Percent", "UNIT_MULT": "1",
}
BILL_PINNED = {
    "CURRENCY": "NA", "FREQ": "9", "INSTRUMENT": "TB", "MATURITY": "M3",
    "SERIES_NAME": BILL_SERIES, "UNIT": "Percent:_Per_Year", "UNIT_MULT": "1",
}
MEASURE = "cp_aa_financial_3m_minus_tbill_3m"
MEASURES: dict[str, MeasureSpec] = {
    MEASURE: MeasureSpec(
        f"{CP_SERIES} minus {BILL_SERIES}", "percentage points", "daily",
        "AA financial 90-day commercial paper rate minus 3-month Treasury bill rate",
    ),
}
_LEG_BOUNDS = ("-5", "30")
_SPREAD_BOUNDS = ("-10", "10")


async def fetch(
    client: httpx.AsyncClient, *, clock: Clock | None = None
) -> tuple[Acquisition, ...]:
    cp = await fetch_fixed(
        client, CP_URL, max_bytes=MAX_ARCHIVE_BYTES, label="Board CP release", clock=clock,
    )
    h15 = await fetch_fixed(
        client, H15_URL, max_bytes=MAX_ARCHIVE_BYTES, label="Board H.15 release", clock=clock,
    )
    return (cp, h15)


def _leg(document: Acquisition, *, url, member, dataset, series, pinned, label, max_xml):
    rows = read_series(
        document, source_url=url, member=member, dataset=dataset,
        pinned={series: pinned}, label=label, max_archive_bytes=MAX_ARCHIVE_BYTES,
        max_xml_bytes=max_xml,
    )[series]
    values = {}
    for day, value, attributes in available_values(rows, label=f"{label} {series}"):
        check_bounds(value, *_LEG_BOUNDS, label=f"{label} {series}")
        values[day] = (value, attributes)
    return values


def parse(documents: Sequence[Acquisition]) -> tuple[SourceObservation, ...]:
    if len(documents) != 2:
        raise ValueError("Board CP minus bill expects the CP and H.15 packages")
    cp_document, h15_document = documents
    cp = _leg(cp_document, url=CP_URL, member=CP_MEMBER, dataset="RATES",
              series=CP_SERIES, pinned=CP_PINNED, label="Board CP release",
              max_xml=MAX_CP_XML_BYTES)
    bill = _leg(h15_document, url=H15_URL, member=H15_MEMBER, dataset="H15",
                series=BILL_SERIES, pinned=BILL_PINNED, label="Board H.15 release",
                max_xml=MAX_H15_XML_BYTES)
    boundary = acquisition_day(*documents)
    points: list[SourceObservation] = []
    for day in sorted(set(cp) & set(bill)):
        if day > boundary:
            continue
        (cp_value, cp_row), (bill_value, bill_row) = cp[day], bill[day]
        spread = cp_value - bill_value
        check_bounds(spread, *_SPREAD_BOUNDS, label="Board CP minus bill")
        points.append(make_observation(
            substitute_id=SUBSTITUTE_ID, measure=MEASURE, spec=MEASURES[MEASURE],
            event_date=day, period=day.isoformat(), value=spread,
            source=SOURCE, publisher=PUBLISHER, source_url=SOURCE_URL,
            terms_url=TERMS_URL, documents=(cp_document, h15_document),
            evidence={"formula": "cp_minus_bill",
                      "cp": {"source_url": CP_URL, "series": CP_SERIES, **cp_row},
                      "bill": {"source_url": H15_URL, "series": BILL_SERIES, **bill_row}},
        ))
    if not points:
        raise ValueError("Board CP minus bill has no dates with both legs")
    return tuple(points)
