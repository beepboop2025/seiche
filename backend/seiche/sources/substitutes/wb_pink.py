"""ALT-PINK: World Bank Commodity Price Data (the Pink Sheet), monthly.

Publisher: World Bank. The monthly workbook moves to a new folder every
year, so its link is read from the World Bank commodity markets page on each
run and must be the single "CMO-Historical-Data-Monthly.xlsx" link on
thedocs.worldbank.org. Nothing about the folder is hard-coded.

Terms (https://data.worldbank.org/summary-terms-of-use, linked from the
commodity page, quoted 2026-11-09): "Unless indicated otherwise in the data
or indicator metadata, you are free to copy, distribute, adapt, display or
include the data in other products for commercial or noncommercial purposes
at no cost under a Creative Commons Attribution 4.0 International License."
The Pink Sheet names third-party price sources without a stated restriction;
that residual risk is recorded in the substitutes registry.

Values are monthly averages in nominal US dollars (indices 2010=100). Each
month is dated at its last calendar day.
"""

from __future__ import annotations

import io
import re
from collections.abc import Sequence
from html.parser import HTMLParser

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
    validate_fixed_url,
    validate_zip_archive,
)

SUBSTITUTE_ID = "ALT-PINK"
SOURCE = "wb_pink_sheet"
PUBLISHER = "World Bank"
PAGE_URL = "https://www.worldbank.org/en/research/commodity-markets"
TERMS_URL = "https://data.worldbank.org/summary-terms-of-use"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
LINK_PATTERN = re.compile(
    r"https://thedocs\.worldbank\.org/en/doc/[0-9a-z]+(?:-[0-9a-z]+){0,4}"
    r"/related/CMO-Historical-Data-Monthly\.xlsx"
)
MAX_PAGE_BYTES = 2_000_000
MAX_BYTES = 16_000_000
MAX_MEMBERS = 64
MAX_EXPANDED_BYTES = 64_000_000
MAX_SHEETS = 16
MAX_ROWS = 5_000
MAX_COLUMNS = 256
PRICES_SHEET = "Monthly Prices"
INDICES_SHEET = "Monthly Indices"
_PERIOD = re.compile(r"(\d{4})M(0[1-9]|1[0-2])")
_MISSING = frozenset({"", "\u2026", "...", "..", "n.a.", "na"})

# measure: (sheet, publisher column label, publisher unit text, bounds)
_COLUMNS: dict[str, tuple[str, str, str, tuple[str, str]]] = {
    "crude_oil_brent": (PRICES_SHEET, "Crude oil, Brent", "($/bbl)", ("0.5", "1000")),
    "crude_oil_wti": (PRICES_SHEET, "Crude oil, WTI", "($/bbl)", ("0.5", "1000")),
    "natural_gas_us": (PRICES_SHEET, "Natural gas, US", "($/mmbtu)", ("0.01", "200")),
    "coal_australia": (PRICES_SHEET, "Coal, Australian", "($/mt)", ("1", "3000")),
    "copper": (PRICES_SHEET, "Copper", "($/mt)", ("100", "200000")),
    "aluminum": (PRICES_SHEET, "Aluminum", "($/mt)", ("50", "100000")),
    "nickel": (PRICES_SHEET, "Nickel", "($/mt)", ("100", "500000")),
    "wheat_us_hrw": (PRICES_SHEET, "Wheat, US HRW", "($/mt)", ("5", "5000")),
    "maize": (PRICES_SHEET, "Maize", "($/mt)", ("5", "5000")),
    "index_total": (INDICES_SHEET, "Total Index", "2010=100", ("0.1", "2000")),
    "index_energy": (INDICES_SHEET, "Energy", "2010=100", ("0.1", "2000")),
    "index_non_energy": (INDICES_SHEET, "Non-energy", "2010=100", ("0.1", "2000")),
}
MEASURES: dict[str, MeasureSpec] = {
    name: MeasureSpec(label, unit.strip("()"), "monthly", f"Pink Sheet {label}, monthly average")
    for name, (_sheet, label, unit, _bounds) in _COLUMNS.items()
}


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            for key, value in attrs:
                if key.lower() == "href" and value:
                    self.hrefs.append(value.strip())


def resolve_monthly_link(page: bytes) -> str:
    """Return the single monthly workbook link the commodity page carries."""

    parser = _LinkParser()
    parser.feed(page.decode("utf-8", errors="replace"))
    parser.close()
    links = {href for href in parser.hrefs if LINK_PATTERN.fullmatch(href)}
    if len(links) != 1:
        raise ValueError(
            f"World Bank commodity page shows {len(links)} monthly workbook links"
        )
    link = links.pop()
    validate_fixed_url(link)
    return link


async def fetch(
    client: httpx.AsyncClient, *, clock: Clock | None = None
) -> tuple[Acquisition, ...]:
    page = await fetch_fixed(
        client, PAGE_URL, max_bytes=MAX_PAGE_BYTES, label="World Bank commodity page",
        clock=clock, accept="text/html",
    )
    link = resolve_monthly_link(page.payload)
    book = await fetch_fixed(
        client, link, max_bytes=MAX_BYTES, label="World Bank Pink Sheet workbook",
        clock=clock,
    )
    return (page, book)


def _normal(value: object) -> str:
    return " ".join(str(value).split()).rstrip("*").strip() if value is not None else ""


def _sheet_rows(workbook, name: str) -> list[tuple]:
    if name not in workbook.sheetnames:
        raise ValueError(f"World Bank Pink Sheet lost its {name!r} sheet")
    sheet = workbook[name]
    rows: list[tuple] = []
    for values in sheet.iter_rows(values_only=True):
        if len(values) > MAX_COLUMNS or len(rows) >= MAX_ROWS:
            raise ValueError("World Bank Pink Sheet exceeds its sheet bounds")
        rows.append(tuple(values))
    return rows


def _first_data_row(rows: list[tuple], sheet: str) -> int:
    for index, row in enumerate(rows):
        if row and isinstance(row[0], str) and _PERIOD.fullmatch(row[0].strip()):
            return index
    raise ValueError(f"World Bank {sheet} has no monthly rows")


def _require_title(rows: list[tuple], start: int, needles: tuple[str, ...], sheet: str) -> None:
    title = " ".join(_normal(cell) for row in rows[:start] for cell in row[:1])
    for needle in needles:
        if needle not in title:
            raise ValueError(f"World Bank {sheet} no longer says {needle!r}")


def _column_index(rows: list[tuple], start: int, label: str, sheet: str) -> int:
    hits = [
        (row_index, column)
        for row_index, row in enumerate(rows[:start])
        for column, cell in enumerate(row)
        if column > 0 and _normal(cell) == label
    ]
    if len(hits) != 1:
        raise ValueError(f"World Bank {sheet} column {label!r} found {len(hits)} times")
    return hits[0][1]


def _price_units(rows: list[tuple], start: int, label: str) -> dict[int, str]:
    header = next(
        (index for index, row in enumerate(rows[:start])
         if any(_normal(cell) == label for cell in row)),
        None,
    )
    if header is None or header + 1 >= start:
        raise ValueError("World Bank Monthly Prices unit row missing")
    return {column: _normal(cell) for column, cell in enumerate(rows[header + 1])}


def parse(documents: Sequence[Acquisition]) -> tuple[SourceObservation, ...]:
    if len(documents) != 2:
        raise ValueError("World Bank Pink Sheet expects the page and the workbook")
    page, book = documents
    require_document(
        page, source_url=PAGE_URL, max_bytes=MAX_PAGE_BYTES,
        label="World Bank commodity page",
    )
    link = resolve_monthly_link(page.payload)
    require_document(
        book, source_url=link, max_bytes=MAX_BYTES, label="World Bank Pink Sheet workbook"
    )
    validate_zip_archive(
        book.payload, label="World Bank Pink Sheet workbook",
        max_members=MAX_MEMBERS, max_expanded_bytes=MAX_EXPANDED_BYTES,
    )
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - deployment extra
        raise RuntimeError("the Pink Sheet adapter needs seiche[collectors]") from exc
    workbook = openpyxl.load_workbook(io.BytesIO(book.payload), read_only=True, data_only=True)
    try:
        if len(workbook.sheetnames) > MAX_SHEETS:
            raise ValueError("World Bank Pink Sheet exceeds its sheet limit")
        sheets = {name: _sheet_rows(workbook, name) for name in (PRICES_SHEET, INDICES_SHEET)}
    finally:
        workbook.close()
    starts = {name: _first_data_row(rows, name) for name, rows in sheets.items()}
    _require_title(sheets[PRICES_SHEET], starts[PRICES_SHEET], ("nominal US dollars",), PRICES_SHEET)
    _require_title(
        sheets[INDICES_SHEET], starts[INDICES_SHEET], ("nominal US dollars", "2010=100"),
        INDICES_SHEET,
    )
    boundary = acquisition_day(*documents)
    points: list[SourceObservation] = []
    for measure, (sheet, label, unit, bounds) in _COLUMNS.items():
        rows, start = sheets[sheet], starts[sheet]
        column = _column_index(rows, start, label, sheet)
        if sheet == PRICES_SHEET:
            found_unit = _price_units(rows, start, label).get(column, "")
            if found_unit != unit:
                raise ValueError(f"World Bank unit for {label!r} changed to {found_unit!r}")
        seen = set()
        ended = False
        for row in rows[start:]:
            if not row or row[0] is None or not str(row[0]).strip():
                continue
            match = _PERIOD.fullmatch(str(row[0]).strip())
            if match is None:
                # A note below the monthly block ends it; months after it do not.
                ended = True
                continue
            if ended:
                raise ValueError(f"World Bank {sheet} monthly rows are interrupted")
            period = f"{match.group(1)}-{match.group(2)}"
            if period in seen:
                raise ValueError(f"World Bank {sheet} month duplicated: {period}")
            seen.add(period)
            cell = row[column] if column < len(row) else None
            if cell is None or (isinstance(cell, str) and cell.strip().lower() in _MISSING):
                continue
            value = decimal_from_cell(cell, label=f"World Bank {label}")
            check_bounds(value, *bounds, label=f"World Bank {label}")
            event_day = month_end(int(match.group(1)), int(match.group(2)))
            if event_day > boundary:
                continue
            points.append(make_observation(
                substitute_id=SUBSTITUTE_ID, measure=measure, spec=MEASURES[measure],
                event_date=event_day, period=period, value=value,
                source=SOURCE, publisher=PUBLISHER, source_url=link,
                terms_url=TERMS_URL, documents=(page, book),
                evidence={"source_url": link, "resolved_from": PAGE_URL, "sheet": sheet,
                          "column": label, "unit": unit, "period": str(row[0]).strip(),
                          "value": repr(cell)},
            ))
    if not points:
        raise ValueError("World Bank Pink Sheet has no usable observations")
    return tuple(sorted(points, key=lambda item: (item.event_date, item.measure)))
