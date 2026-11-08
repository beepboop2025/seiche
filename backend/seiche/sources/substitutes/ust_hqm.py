"""ALT-HQM: Treasury HQM corporate bond par yields and their Treasury spreads.

Publisher: U.S. Department of the Treasury, "The Treasury High Quality
Market (HQM) Corporate Bond Yield Curve", monthly average par yields in
percent at 2, 5, 10 and 30 years (hqm_qh_pars.xls, a BIFF workbook).

The proxy role in Seiche is a spread, so each maturity is also served as
HQM par yield minus the Treasury constant maturity yield at the same
maturity, monthly average, read from the Board's H.15 release package (the
H.15 credits these yields to the U.S. Treasury). H.15 footnote 9 says the
30-year series was discontinued from 2002-02-18 to 2006-02-09 and that the
Treasury published a 20-year adjustment factor for that period; the package
still carries 30-year monthly values for those months, and the spread uses
them as published.

Terms: works of the U.S. Government are not subject to copyright. 17 U.S.C.
105(a): "Copyright protection under this title is not available for any
work of the United States Government"
(https://www.copyright.gov/title17/92chap1.html). The H.15 leg falls under
the Board notice (https://www.federalreserve.gov/disclaimer.htm).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

import httpx

from seiche.sources.substitutes._common import (
    Acquisition,
    Clock,
    MeasureSpec,
    SourceObservation,
    acquisition_day,
    check_bounds,
    decimal_from_cell,
    fetch_fixed,
    make_observation,
    month_end,
    require_document,
)
from seiche.sources.substitutes._frb_xml import available_values, read_series

SUBSTITUTE_ID = "ALT-HQM"
SOURCE = "ust_hqm"
PUBLISHER = "U.S. Department of the Treasury"
SOURCE_URL = "https://home.treasury.gov/system/files/226/hqm_qh_pars.xls"
H15_URL = "https://www.federalreserve.gov/releases/h15/data/FRB_H15_xml.zip"
TERMS_URL = "https://www.copyright.gov/title17/92chap1.html"
MAX_BYTES = 4_000_000
MAX_H15_ARCHIVE_BYTES = 32_000_000
MAX_H15_XML_BYTES = 256_000_000
MAX_ROWS = 5_000
MAX_COLUMNS = 64
TITLE = "The Treasury High Quality Market (HQM) Corporate Bond Yield Curve"
SUBTITLE = "Monthly Average Par Yields, Percent"
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_MONTHS = {name: index for index, name in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
_ROW_LABEL = re.compile(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{4})")
MATURITIES = {"2y": ("2 Years", "Y2"), "5y": ("5 Years", "Y5"),
              "10y": ("10 Years", "Y10"), "30y": ("30 Years", "Y30")}
TREASURY_SERIES = {
    tenor: f"RIFLGFCY{code[1:].zfill(2)}_N.M" for tenor, (_label, code) in MATURITIES.items()
}
TREASURY_PINNED = {
    TREASURY_SERIES[tenor]: {
        "CURRENCY": "NA", "FREQ": "129", "INSTRUMENT": "TCMNOM", "MATURITY": code,
        "SERIES_NAME": TREASURY_SERIES[tenor], "UNIT": "Percent:_Per_Year", "UNIT_MULT": "1",
    }
    for tenor, (_label, code) in MATURITIES.items()
}
MEASURES: dict[str, MeasureSpec] = {
    **{f"par_{tenor}": MeasureSpec(
        label, "percent", "monthly", f"HQM corporate par yield, {label}, monthly average")
       for tenor, (label, _code) in MATURITIES.items()},
    **{f"spread_{tenor}": MeasureSpec(
        f"{label} minus {TREASURY_SERIES[tenor]}", "percentage points", "monthly",
        f"HQM par yield minus Treasury constant maturity yield, {label}, monthly average")
       for tenor, (label, _code) in MATURITIES.items()},
}
_YIELD_BOUNDS = ("-5", "30")
_SPREAD_BOUNDS = ("-5", "15")


async def fetch(
    client: httpx.AsyncClient, *, clock: Clock | None = None
) -> tuple[Acquisition, ...]:
    hqm = await fetch_fixed(
        client, SOURCE_URL, max_bytes=MAX_BYTES, label="Treasury HQM workbook", clock=clock,
    )
    h15 = await fetch_fixed(
        client, H15_URL, max_bytes=MAX_H15_ARCHIVE_BYTES, label="Board H.15 release",
        clock=clock,
    )
    return (hqm, h15)


def _text(sheet, row: int, column: int) -> str:
    value = sheet.cell_value(row, column) if column < sheet.row_len(row) else ""
    return " ".join(str(value).split()) if isinstance(value, str) else ""


def _hqm_levels(document: Acquisition) -> dict[str, dict[str, tuple[Decimal, str]]]:
    require_document(
        document, source_url=SOURCE_URL, max_bytes=MAX_BYTES, label="Treasury HQM workbook"
    )
    if not document.payload.startswith(_OLE2_MAGIC):
        raise ValueError("Treasury HQM workbook is no longer a BIFF file")
    try:
        import xlrd
    except ImportError as exc:  # pragma: no cover - deployment extra
        raise RuntimeError("the HQM adapter needs seiche[collectors]") from exc
    try:
        book = xlrd.open_workbook(file_contents=document.payload, on_demand=True)
    except Exception as exc:  # noqa: BLE001 - normalize parser failures
        raise ValueError(f"Treasury HQM workbook unreadable: {type(exc).__name__}") from exc
    try:
        if book.nsheets < 1 or book.nsheets > 4:
            raise ValueError("Treasury HQM workbook sheet count changed")
        sheet = book.sheet_by_index(0)
        if sheet.nrows > MAX_ROWS or sheet.ncols > MAX_COLUMNS:
            raise ValueError("Treasury HQM workbook exceeds its sheet bounds")
        if _text(sheet, 0, 0) != TITLE or _text(sheet, 1, 0) != SUBTITLE:
            raise ValueError("Treasury HQM title or units changed")
        header = next((row for row in range(min(sheet.nrows, 20))
                       if _text(sheet, row, 2) == MATURITIES["2y"][0]), None)
        if header is None:
            raise ValueError("Treasury HQM maturity header missing")
        columns = {}
        for tenor, (label, _code) in MATURITIES.items():
            hits = [col for col in range(sheet.row_len(header)) if _text(sheet, header, col) == label]
            if len(hits) != 1:
                raise ValueError(f"Treasury HQM maturity {label!r} found {len(hits)} times")
            columns[tenor] = hits[0]
        levels: dict[str, dict[str, tuple[Decimal, str]]] = {tenor: {} for tenor in MATURITIES}
        seen = set()
        for row in range(header + 1, sheet.nrows):
            label = _text(sheet, row, 0)
            if not label:
                continue
            match = _ROW_LABEL.fullmatch(label)
            if match is None:
                raise ValueError(f"Treasury HQM row label {label[:20]!r} unexpected")
            period = f"{int(match.group(2)):04d}-{_MONTHS[match.group(1)]:02d}"
            if period in seen:
                raise ValueError(f"Treasury HQM month duplicated: {period}")
            seen.add(period)
            for tenor, column in columns.items():
                if column >= sheet.row_len(row):
                    continue
                cell_type = sheet.cell_type(row, column)
                if cell_type in {xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK}:
                    continue
                if cell_type != xlrd.XL_CELL_NUMBER:
                    raise ValueError(f"Treasury HQM {period} {tenor} is not numeric")
                value = decimal_from_cell(sheet.cell_value(row, column), label="Treasury HQM")
                check_bounds(value, *_YIELD_BOUNDS, label=f"Treasury HQM {tenor}")
                levels[tenor][period] = (value, label)
    finally:
        book.release_resources()
    if not any(levels.values()):
        raise ValueError("Treasury HQM workbook has no yields")
    return levels


def _treasury_legs(document: Acquisition) -> dict[str, dict[str, Decimal]]:
    rows = read_series(
        document, source_url=H15_URL, member="H15_data.xml", dataset="H15",
        pinned=TREASURY_PINNED, label="Board H.15 release",
        max_archive_bytes=MAX_H15_ARCHIVE_BYTES, max_xml_bytes=MAX_H15_XML_BYTES,
    )
    legs: dict[str, dict[str, Decimal]] = {}
    for tenor, series in TREASURY_SERIES.items():
        legs[tenor] = {}
        for day, value, _attributes in available_values(rows[series], label=series):
            if day != month_end(day.year, day.month):
                raise ValueError(f"{series} is no longer dated at month end")
            check_bounds(value, *_YIELD_BOUNDS, label=series)
            legs[tenor][f"{day.year:04d}-{day.month:02d}"] = value
    return legs


def parse(documents: Sequence[Acquisition]) -> tuple[SourceObservation, ...]:
    if len(documents) != 2:
        raise ValueError("Treasury HQM expects the HQM workbook and the H.15 package")
    hqm_document, h15_document = documents
    levels = _hqm_levels(hqm_document)
    legs = _treasury_legs(h15_document)
    boundary = acquisition_day(*documents)
    points: list[SourceObservation] = []
    for tenor, by_period in levels.items():
        for period, (value, row_label) in by_period.items():
            year, month = map(int, period.split("-"))
            event_day: date = month_end(year, month)
            if event_day > boundary:
                continue
            points.append(make_observation(
                substitute_id=SUBSTITUTE_ID, measure=f"par_{tenor}",
                spec=MEASURES[f"par_{tenor}"], event_date=event_day, period=period,
                value=value, source=SOURCE, publisher=PUBLISHER, source_url=SOURCE_URL,
                terms_url=TERMS_URL, documents=(hqm_document,),
                evidence={"source_url": SOURCE_URL, "row": row_label,
                          "maturity": MATURITIES[tenor][0], "value": str(value)},
            ))
            treasury = legs[tenor].get(period)
            if treasury is None:
                continue
            spread = value - treasury
            check_bounds(spread, *_SPREAD_BOUNDS, label=f"Treasury HQM spread {tenor}")
            points.append(make_observation(
                substitute_id=SUBSTITUTE_ID, measure=f"spread_{tenor}",
                spec=MEASURES[f"spread_{tenor}"], event_date=event_day, period=period,
                value=spread, source=SOURCE, publisher=PUBLISHER, source_url=SOURCE_URL,
                terms_url=TERMS_URL, documents=(hqm_document, h15_document),
                evidence={"formula": "hqm_par_minus_treasury_cmt",
                          "hqm": {"source_url": SOURCE_URL, "row": row_label,
                                  "maturity": MATURITIES[tenor][0], "value": str(value)},
                          "treasury": {"source_url": H15_URL, "series": TREASURY_SERIES[tenor],
                                       "period": period, "value": str(treasury)}},
            ))
    if not points:
        raise ValueError("Treasury HQM has no usable observations")
    return tuple(sorted(points, key=lambda item: (item.event_date, item.measure)))
