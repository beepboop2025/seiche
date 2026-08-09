"""Official/open production adapters for monetary-area packs.

Each adapter is independently schedulable.  This module contains source
vocabulary and endpoint details; neither the market-neutral observation
contract nor the universal kernel imports it.
"""

from __future__ import annotations

import calendar as civil_calendar
import csv
import hashlib
import html
import io
import json
import os
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from seiche.markets.registry import MarketRegistry, default_registry
from seiche.repository import MarketRepository
from seiche.sources.canonical import (
    FetchedDocument,
    FunctionalCanonicalAdapter,
    ParsedPoint,
    get_documents,
)

USER_AGENT = "Seiche/0.9 (+https://seiche.info; official-data research collector)"


def bounded_date_windows(
    start: date,
    end: date,
    *,
    maximum_days: int,
) -> tuple[tuple[date, date], ...]:
    """Return non-overlapping inclusive windows within an upstream limit."""

    if maximum_days < 1:
        raise ValueError("maximum_days must be positive")
    if start > end:
        return ()
    windows = []
    cursor = start
    while cursor <= end:
        window_end = min(cursor + timedelta(days=maximum_days - 1), end)
        windows.append((cursor, window_end))
        cursor = window_end + timedelta(days=1)
    return tuple(windows)


def _number(value: object) -> Decimal | None:
    text = str(value).strip().replace(",", "")
    if text in {"", ".", "-", "NA", "N/A", "ND", "None", "nan"}:
        return None
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _date(value: object, *formats: str) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T.*)?", text):
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            pass
    return None


def _row_evidence(label: str, values: object) -> bytes:
    return json.dumps(
        {"label": label, "row": values},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def parse_fred_csv(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    instrument_id = document.label
    text = document.payload.decode("utf-8-sig")
    lines = text.splitlines()
    if len(lines) < 2:
        raise ValueError("FRED CSV contains no data rows")
    points: list[ParsedPoint] = []
    for raw_line in lines[1:]:
        row = next(csv.reader([raw_line]))
        if len(row) < 2:
            continue
        event_day = _date(row[0], "%Y-%m-%d")
        value = _number(row[1])
        if event_day is None or value is None:
            continue
        points.append(
            ParsedPoint(
                instrument_id,
                event_day,
                value,
                raw_line.encode("utf-8"),
            )
        )
    if not points:
        raise ValueError("FRED CSV contains no numeric observations")
    return tuple(points)


def parse_ecb_csv(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    text = document.payload.decode("utf-8-sig")
    lines = text.splitlines()
    if len(lines) < 2:
        raise ValueError("ECB SDMX CSV contains no data rows")
    header = next(csv.reader([lines[0]]))
    points: list[ParsedPoint] = []
    for raw_line in lines[1:]:
        values = next(csv.reader([raw_line]))
        if len(values) != len(header):
            continue
        row = dict(zip(header, values, strict=True))
        event_day = _date(row.get("TIME_PERIOD", ""), "%Y-%m-%d")
        value = _number(row.get("OBS_VALUE"))
        if event_day is None or value is None:
            continue
        revision = str(row.get("OBS_STATUS") or "").strip() or None
        points.append(
            ParsedPoint(
                document.label,
                event_day,
                value,
                raw_line.encode("utf-8"),
                revision_id=(
                    f"ecb:{revision}:{event_day.isoformat()}" if revision else None
                ),
            )
        )
    if not points:
        raise ValueError("ECB SDMX CSV contains no numeric observations")
    return tuple(points)


def parse_boe_csv(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    text = document.payload.decode("utf-8-sig")
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError("Bank of England CSV contains no data rows")
    points: list[ParsedPoint] = []
    for raw_line in lines[1:]:
        row = next(csv.reader([raw_line]))
        if len(row) < 2:
            continue
        event_day = _date(row[0], "%d %b %Y", "%d/%b/%Y", "%Y-%m-%d")
        value = _number(row[1])
        if event_day is None or value is None:
            continue
        points.append(
            ParsedPoint(document.label, event_day, value, raw_line.encode("utf-8"))
        )
    if not points:
        raise ValueError("Bank of England CSV contains no numeric observations")
    return tuple(points)


def parse_boj_csv(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    text = document.payload.decode("utf-8-sig")
    lines = text.splitlines()
    code_row: list[str] | None = None
    update_day: date | None = None
    points: list[ParsedPoint] = []
    for raw_line in lines:
        row = next(csv.reader([raw_line]))
        if row and row[0] == "Series code":
            code_row = row[1:]
            continue
        if row and row[0] == "Last update":
            update_day = _date(row[1] if len(row) > 1 else "", "%Y/%m/%d")
            continue
        if not row:
            continue
        daily = _date(row[0], "%Y/%m/%d")
        monthly = None
        if daily is None and re.fullmatch(r"\d{4}/\d{2}", row[0].strip()):
            year, month = map(int, row[0].split("/"))
            monthly = date(year, month, civil_calendar.monthrange(year, month)[1])
        event_day = daily or monthly
        if event_day is None:
            continue
        if document.label == "JP.BOJ.CURRENT_ACCOUNTS":
            if not code_row:
                raise ValueError("BOJ monthly CSV has no series-code row")
            try:
                column = code_row.index("MD01'MABS1AN113") + 1
            except ValueError as exc:
                raise ValueError("BOJ current-account series column is absent") from exc
        else:
            column = 1
        if len(row) <= column:
            continue
        value = _number(row[column])
        if value is None:
            continue
        publication = None
        if (
            monthly is not None
            and update_day is not None
            and event_day.year == update_day.year
            and event_day.month == update_day.month - 1
        ):
            publication = datetime.combine(
                update_day,
                datetime.min.time(),
                tzinfo=ZoneInfo("Asia/Tokyo"),
            )
        points.append(
            ParsedPoint(
                document.label,
                event_day,
                value,
                raw_line.encode("utf-8"),
                source_publication_time=publication,
            )
        )
    if not points:
        raise ValueError("BOJ CSV contains no numeric observations")
    return tuple(points)


def parse_cfets_csv(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    points: list[ParsedPoint] = []
    for raw_line in document.payload.decode("utf-8-sig").splitlines():
        row = next(csv.reader([raw_line]))
        if not row:
            continue
        event_day = _date(row[0], "%Y-%m-%d")
        if event_day is None:
            continue
        if document.label == "CN.CFETS.DR007":
            # CFETS' FDR007 fixing is the public depository-institution
            # seven-day repo benchmark corresponding to this pack role.
            column = 7
        else:
            column = 1
        if len(row) <= column:
            continue
        value = _number(row[column])
        if value is None:
            continue
        points.append(
            ParsedPoint(document.label, event_day, value, raw_line.encode("utf-8"))
        )
    if not points:
        raise ValueError("CFETS CSV contains no numeric observations")
    return tuple(points)


def parse_cfets_rates(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    if document.label == "CN.CFETS.DR007":
        return parse_cfets_csv(document)
    payload = json.loads(document.payload)
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("CFETS SHIBOR response has no records")
    points: list[ParsedPoint] = []
    for row in records:
        if not isinstance(row, dict):
            continue
        event_day = _date(row.get("showDateCN"), "%Y-%m-%d")
        value = _number(row.get("ON"))
        if event_day is None or value is None:
            continue
        points.append(
            ParsedPoint(
                "CN.CFETS.SHIBOR_ON",
                event_day,
                value,
                _row_evidence("SHIBOR_ON", row),
            )
        )
    if not points:
        raise ValueError("CFETS SHIBOR response contains no overnight values")
    return tuple(points)


def parse_nyfed_rates(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    payload = json.loads(document.payload)
    rows = payload.get("refRates")
    if not isinstance(rows, list):
        raise ValueError("NY Fed response has no refRates list")
    mapping = {
        # ``percentRate`` is the published transaction-weighted median.  P25
        # is a different distribution point and must never stand in for it.
        "percentRate": "US.NYFED.SOFR_MEDIAN",
        "percentPercentile99": "US.NYFED.SOFR_P99",
        "volumeInBillions": "US.NYFED.SOFR_VOLUME",
    }
    points: list[ParsedPoint] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("type") != "SOFR":
            continue
        event_day = _date(row.get("effectiveDate"), "%Y-%m-%d")
        if event_day is None:
            continue
        revision = str(row.get("revisionIndicator") or "").strip()
        for field, instrument in mapping.items():
            value = _number(row.get(field))
            if value is None:
                continue
            row_evidence = _row_evidence(field, row)
            revision_token = re.sub(
                r"[^A-Za-z0-9._-]+", "-", revision or "unrevised"
            ).strip("-")
            content_token = hashlib.sha256(row_evidence).hexdigest()[:16]
            points.append(
                ParsedPoint(
                    instrument,
                    event_day,
                    value,
                    row_evidence,
                    # Bind the semantic source field and event explicitly even
                    # when upstream supplies no revision indicator.  This lets
                    # downstream profiles prove that corrected median rows came
                    # from percentRate while retaining the former P25-derived
                    # rows as ordinary earlier revisions in the append-only
                    # canonical store.
                    revision_id=(
                        f"nyfed:{field}:{event_day.isoformat()}:"
                        f"{revision_token or 'unrevised'}-{content_token}"
                    ),
                )
            )
    if not points:
        raise ValueError("NY Fed response contains no SOFR distribution rows")
    return tuple(points)


def parse_nyfed_facilities(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    payload = json.loads(document.payload)
    operations = (payload.get("repo") or {}).get("operations")
    if not isinstance(operations, list):
        raise ValueError("NY Fed response has no repo operations")
    grouped: dict[date, list[dict[str, Any]]] = {}
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        event_day = _date(operation.get("operationDate"), "%Y-%m-%d")
        # Missing is not zero. Explicit numeric zero remains a valid no-takeup
        # observation because the official result reported it.
        if event_day is None or operation.get("totalAmtAccepted") is None:
            continue
        grouped.setdefault(event_day, []).append(operation)
    points = []
    for event_day, rows in grouped.items():
        amounts = [_number(row["totalAmtAccepted"]) for row in rows]
        if any(value is None for value in amounts):
            continue
        # API amounts are dollars; pack normalization expects USD billions.
        value = sum(amounts, Decimal(0)) / Decimal(1_000_000_000)  # type: ignore[arg-type]
        points.append(
            ParsedPoint(
                "US.NYFED.SRF_TAKEUP",
                event_day,
                value,
                _row_evidence("totalAmtAccepted", rows),
            )
        )
    if not points:
        raise ValueError("NY Fed response contains no explicit facility results")
    return tuple(points)


def parse_fiscal_tga(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    payload = json.loads(document.payload)
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("FiscalData response has no data list")
    priority = {
        "Treasury General Account (TGA) Opening Balance": 0,
        "Treasury General Account (TGA)": 1,
        "Federal Reserve Account": 2,
    }
    selected: dict[date, tuple[int, dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        event_day = _date(row.get("record_date"), "%Y-%m-%d")
        value = _number(row.get("open_today_bal"))
        account = str(row.get("account_type") or "")
        if event_day is None or value is None or account not in priority:
            continue
        candidate = (priority[account], row)
        if event_day not in selected or candidate[0] < selected[event_day][0]:
            selected[event_day] = candidate
    points = []
    for event_day, (_, row) in sorted(selected.items()):
        # FiscalData reports USD millions; pack source unit is USD billions.
        value = _number(row["open_today_bal"])
        points.append(
            ParsedPoint(
                "US.TREASURY.GOVERNMENT_CASH",
                event_day,
                value / Decimal(1000),  # type: ignore[operator]
                _row_evidence("tga", row),
            )
        )
    if not points:
        raise ValueError("FiscalData response contains no TGA rows")
    return tuple(points)


def _workbook(document: FetchedDocument):
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - deployment extra
        raise RuntimeError(
            "official workbook adapter needs seiche[collectors]"
        ) from exc
    return openpyxl.load_workbook(
        io.BytesIO(document.payload),
        read_only=True,
        data_only=True,
    )


def _workbook_series_rows(document: FetchedDocument) -> tuple[list[str], list[tuple]]:
    workbook = _workbook(document)
    worksheet = (
        workbook["Data"] if "Data" in workbook.sheetnames else workbook.worksheets[0]
    )
    series_ids: list[str] | None = None
    rows: list[tuple] = []
    for values in worksheet.iter_rows(values_only=True):
        row = tuple(values)
        if row and str(row[0]).strip().lower() == "series id":
            series_ids = [
                str(value).strip() if value is not None else "" for value in row
            ]
            continue
        if series_ids is not None and row and isinstance(row[0], (date, datetime)):
            rows.append(row)
    if series_ids is None or not rows:
        raise ValueError("official workbook has no Series ID/data rows")
    return series_ids, rows


def parse_rba_workbook(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    series_ids, rows = _workbook_series_rows(document)
    mappings = {
        "rba_cash": {"FIRMMCRID": "AU.RBA.AONIA"},
        "rba_policy_daily": {"FIRMMCRTD": "AU.RBA.CASH_TARGET"},
        "rba_policy_changes": {
            "ARBAMPNRPESB": "AU.RBA.ES_FLOOR",
            "ARBAMPNORR": "AU.RBA.STANDING_CEILING",
        },
    }
    try:
        selected = mappings[document.label]
    except KeyError as exc:
        raise ValueError(f"unknown RBA document label {document.label!r}") from exc
    columns = {
        index: selected[series_id]
        for index, series_id in enumerate(series_ids)
        if series_id in selected
    }
    if not columns:
        raise ValueError("RBA workbook no longer contains expected series IDs")
    points: list[ParsedPoint] = []
    for row in rows:
        event_day = _date(row[0])
        if event_day is None:
            continue
        for column, instrument in columns.items():
            if len(row) <= column:
                continue
            value = _number(row[column])
            if value is None:
                continue
            evidence = _row_evidence(
                series_ids[column],
                [event_day.isoformat(), str(row[column])],
            )
            points.append(ParsedPoint(instrument, event_day, value, evidence))
    if not points:
        raise ValueError("RBA workbook contains no expected numeric observations")
    return tuple(points)


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "tr":
            self._row = []
        elif tag.lower() in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"td", "th"} and self._cell is not None:
            value = " ".join("".join(self._cell).replace("\xa0", " ").split())
            if self._row is not None:
                self._row.append(value)
            self._cell = None
        elif tag.lower() == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


class _HiddenInputParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        if (
            tag.lower() == "input"
            and values.get("type", "").lower() == "hidden"
            and values.get("name")
        ):
            self.values[values["name"]] = values.get("value", "")


def _table_rows(payload: bytes) -> list[list[str]]:
    parser = _TableParser()
    parser.feed(payload.decode("utf-8-sig", errors="replace"))
    return parser.rows


def parse_mas_sora(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    year: int | None = None
    month: int | None = None
    points: list[ParsedPoint] = []
    for row in _table_rows(document.payload):
        if len(row) != 6:
            continue
        if row[0].isdigit():
            year = int(row[0])
        if row[1]:
            try:
                month = datetime.strptime(row[1][:3], "%b").month
            except ValueError:
                continue
        if year is None or month is None or not row[2].isdigit():
            continue
        event_day = date(year, month, int(row[2]))
        publication_day = _date(row[3], "%d %b %Y")
        value = _number(row[4])
        if publication_day is None or value is None:
            continue
        publication = datetime.combine(
            publication_day,
            datetime.min.time().replace(hour=9),
            tzinfo=ZoneInfo("Asia/Singapore"),
        )
        points.append(
            ParsedPoint(
                "SG.MAS.SORA",
                event_day,
                value,
                _row_evidence("sora", row),
                source_publication_time=publication,
            )
        )
    if not points:
        raise ValueError("MAS result contains no SORA rows")
    return tuple(points)


def parse_mas_rates(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    year: int | None = None
    month: int | None = None
    points: list[ParsedPoint] = []
    for row in _table_rows(document.payload):
        if len(row) != 5:
            continue
        if row[0].isdigit():
            year = int(row[0])
        if row[1]:
            try:
                month = datetime.strptime(row[1][:3], "%b").month
            except ValueError:
                continue
        if year is None or month is None or not row[2].isdigit():
            continue
        event_day = date(year, month, int(row[2]))
        for column, instrument in (
            (3, "SG.MAS.STANDING_DEPOSIT"),
            (4, "SG.MAS.STANDING_BORROWING"),
        ):
            value = _number(row[column])
            if value is not None:
                points.append(
                    ParsedPoint(
                        instrument,
                        event_day,
                        value,
                        _row_evidence(instrument, row),
                    )
                )
    if not points:
        raise ValueError("MAS result contains no standing-facility rows")
    return tuple(points)


def parse_hkma_json(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    payload = json.loads(document.payload)
    records = (payload.get("result") or {}).get("records")
    if not isinstance(records, list):
        raise ValueError("HKMA response has no records")
    mapping = {
        "disc_win_base_rate": "HK.HKMA.BASE_RATE",
        "closing_balance": "HK.HKMA.AGGREGATE_BALANCE",
    }
    points: list[ParsedPoint] = []
    for row in records:
        if not isinstance(row, dict):
            continue
        event_day = _date(row.get("end_of_date"), "%Y-%m-%d")
        if event_day is None:
            continue
        for field, instrument in mapping.items():
            value = _number(row.get(field))
            if value is None:
                continue
            points.append(
                ParsedPoint(
                    instrument,
                    event_day,
                    value,
                    _row_evidence(field, row),
                )
            )
    if not points:
        raise ValueError("HKMA response contains no mapped rows")
    return tuple(points)


def _html_text(fragment: str) -> str:
    return " ".join(
        html.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ").split()
    )


def _html_row_by_id(text: str, row_id: str) -> tuple[list[str], bytes] | None:
    for match in re.finditer(r"<tr\b[^>]*>.*?</tr>", text, re.IGNORECASE | re.DOTALL):
        block = match.group(0)
        if not re.search(rf'id=["\']{re.escape(row_id)}["\']', block, re.IGNORECASE):
            continue
        cells = [
            _html_text(value)
            for value in re.findall(
                r"<(?:td|th)\b[^>]*>(.*?)</(?:td|th)>",
                block,
                re.IGNORECASE | re.DOTALL,
            )
        ]
        return cells, block.encode("utf-8")
    return None


def parse_rbi_html(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    text = document.payload.decode("utf-8-sig", errors="replace")
    points: list[ParsedPoint] = []
    if document.label == "rbi_home":
        event_match = re.search(
            r"(?:As at|as on)\s+(?:\d{1,2}[.:]\d{2}\s*(?:am|pm)\s+of\s+)?"
            r"([A-Z][a-z]+\s+\d{1,2},\s+\d{4})",
            _html_text(text),
        )
        event_day = _date(event_match.group(1), "%B %d, %Y") if event_match else None
        if event_day is None:
            return ()
        plain = _html_text(text)
        for label, instrument in (
            ("Policy Repo Rate", "IN.RBI.POLICY_REPO"),
            ("91 day T-bills", "IN.RBI.TBILL_3M"),
        ):
            match = re.search(
                rf"{re.escape(label)}\s*(?::)?\s*([0-9]+(?:\.[0-9]+)?)\s*%?",
                plain,
                re.IGNORECASE,
            )
            if match:
                points.append(
                    ParsedPoint(
                        instrument,
                        event_day,
                        match.group(1),
                        _row_evidence(label, match.group(0)),
                    )
                )
        return tuple(points)

    event_match = re.search(r"Money Market Operations as on\s+([^<]+)</b>", text, re.I)
    event_day = (
        _date(_html_text(event_match.group(1)), "%B %d, %Y") if event_match else None
    )
    if event_day is None:
        raise ValueError("RBI page has no money-market event date")

    def add_from_row(row_id: str, column: int, instrument: str) -> None:
        found = _html_row_by_id(text, row_id)
        if found is None:
            return
        cells, evidence = found
        if column >= len(cells):
            return
        value = _number(cells[column])
        if value is not None:
            points.append(ParsedPoint(instrument, event_day, value, evidence))

    add_from_row("OSCallMoney", 2, "IN.MARKET.CALL_WAR")
    add_from_row("OSTriparty", 1, "IN.RBI.TRIPARTY_REPO_VOLUME")
    add_from_row("MSF3", 5, "IN.RBI.MSF")
    add_from_row("SDF2", 5, "IN.RBI.SDF")
    add_from_row("Netliquidityinjectedoutstandingtoday", 4, "IN.RBI.SYSTEM_LIQUIDITY")
    add_from_row("CashBalRBI", 2, "IN.RBI.CASH_BALANCES")
    add_from_row("GovernmentIndiaSurplusCashBalance", 2, "IN.GOVERNMENT.CASH_BALANCE")

    facility_rows = [
        found
        for row_id in ("MSF3", "SDF2")
        if (found := _html_row_by_id(text, row_id)) is not None
    ]
    amounts = [_number(cells[4]) for cells, _ in facility_rows if len(cells) > 4]
    if amounts and all(value is not None for value in amounts):
        points.append(
            ParsedPoint(
                "IN.RBI.FACILITY_TAKEUP",
                event_day,
                sum(amounts, Decimal(0)),  # type: ignore[arg-type]
                b"\n".join(evidence for _, evidence in facility_rows),
            )
        )
    if not points:
        raise ValueError("RBI page contains no mapped money-market rows")
    return tuple(points)


def parse_rbnz(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    mapping = {
        "NZ.RBNZ.OCR": ("official cash rate", "ocr"),
        "NZ.RBNZ.OVERNIGHT_DEPOSIT": ("overnight deposit", "deposit"),
        "NZ.RBNZ.OVERNIGHT_REVERSE_REPO": ("overnight reverse", "reverse"),
    }
    points: list[ParsedPoint] = []
    if "spreadsheet" in document.media_type or document.source_uri.endswith(".xlsx"):
        workbook = _workbook(document)
        worksheet = workbook.worksheets[0]
        header_row: tuple | None = None
        header_index = 0
        rows = list(worksheet.iter_rows(values_only=True))
        for index, row in enumerate(rows):
            if row and str(row[0]).strip().lower() == "date":
                header_row = tuple(row)
                header_index = index
                break
        if header_row is None:
            raise ValueError("RBNZ workbook has no Date header")
        columns: dict[int, str] = {}
        for index, heading in enumerate(header_row):
            normalized = " ".join(str(heading or "").lower().split())
            for instrument, (needle, _) in mapping.items():
                if needle in normalized:
                    columns[index] = instrument
        for row in rows[header_index + 1 :]:
            if not row:
                continue
            event_day = _date(row[0])
            if event_day is None:
                continue
            for column, instrument in columns.items():
                if len(row) <= column or (value := _number(row[column])) is None:
                    continue
                points.append(
                    ParsedPoint(
                        instrument,
                        event_day,
                        value,
                        _row_evidence(instrument, [event_day, row[column]]),
                    )
                )
    else:
        for row in _table_rows(document.payload):
            if len(row) < 5:
                continue
            event_day = _date(row[0], "%d %b %Y")
            if event_day is None:
                continue
            for column, instrument in (
                (1, "NZ.RBNZ.OCR"),
                (2, "NZ.RBNZ.OVERNIGHT_DEPOSIT"),
                (3, "NZ.RBNZ.OVERNIGHT_REVERSE_REPO"),
            ):
                value = _number(row[column])
                if value is not None:
                    points.append(
                        ParsedPoint(
                            instrument,
                            event_day,
                            value,
                            _row_evidence(instrument, row),
                        )
                    )
    allowed = {
        "rbnz_policy": {"NZ.RBNZ.OCR"},
        "rbnz_wholesale": {
            "NZ.RBNZ.OVERNIGHT_DEPOSIT",
            "NZ.RBNZ.OVERNIGHT_REVERSE_REPO",
        },
    }[document.label]
    selected = tuple(point for point in points if point.instrument_id in allowed)
    if not selected:
        raise ValueError("RBNZ source contains no mapped rows")
    return selected


async def _mas_documents(
    client: httpx.AsyncClient,
    *,
    label: str,
    columns: tuple[int, ...],
    start_year: int,
    end_year: int,
    end_month: int,
) -> tuple[FetchedDocument, ...]:
    uri = "https://eservices.mas.gov.sg/statistics/dir/DomesticInterestRates.aspx"
    response = await client.get(uri)
    response.raise_for_status()
    hidden = _HiddenInputParser()
    hidden.feed(response.text)
    form = {
        **hidden.values,
        "ctl00$ContentPlaceHolder1$StartYearDropDownList": str(start_year),
        "ctl00$ContentPlaceHolder1$EndYearDropDownList": str(end_year),
        "ctl00$ContentPlaceHolder1$StartMonthDropDownList": "1",
        "ctl00$ContentPlaceHolder1$EndMonthDropDownList": str(end_month),
        "ctl00$ContentPlaceHolder1$Button1": "Display",
    }
    for column in columns:
        form[f"ctl00$ContentPlaceHolder1$ColumnsCheckBoxList${column}"] = "on"
    result = await client.post(uri, data=form)
    result.raise_for_status()
    if "ResultsContainerPanel" not in result.text:
        raise ValueError("MAS form returned no result table")
    return (
        FetchedDocument(
            str(result.url),
            "text/html",
            result.content,
            label,
        ),
    )


def build_official_adapters(
    *,
    registry: MarketRegistry | None = None,
    repository: MarketRepository | None = None,
    backfill: bool = False,
    clock=None,
) -> tuple[FunctionalCanonicalAdapter, ...]:
    """Construct every deployable official adapter.

    Licensed and tenant connectors are declared by packs but are not invented
    here. SONIA is the one restricted official publication we ingest for
    derived computation; its pack policy prevents raw-value redistribution.
    """

    markets = registry or default_registry()
    now = (clock() if clock is not None else datetime.now(UTC)).astimezone(UTC)
    recent_start = now.date() - timedelta(days=45)
    configured_start = date.fromisoformat(
        os.getenv("SEICHE_CANONICAL_START", "2018-01-01")
    )
    start = configured_start if backfill else recent_start
    adapters: list[FunctionalCanonicalAdapter] = []

    def add(
        market_id: str, adapter_id: str, source: str, fetcher, parser, timeout=60.0
    ):
        adapters.append(
            FunctionalCanonicalAdapter(
                pack=markets.get(market_id),
                adapter_id=adapter_id,
                source=source,
                fetcher=fetcher,
                parser=parser,
                repository=repository,
                clock=clock,
                timeout_seconds=timeout,
                historical_backfill=backfill,
            )
        )

    fred_base = "https://fred.stlouisfed.org/graph/fredgraph.csv"
    fred_daily = {
        "US.FED.IORB": "IORB",
        "US.NYFED.SOFR": "SOFR",
        "US.NYFED.EFFR": "EFFR",
        "US.FED.POLICY_CEILING": "DFEDTARU",
        "US.TREASURY.TBILL_3M": "DTB3",
        "US.CP.NONFINANCIAL_3M": "DCPN3M",
    }

    async def fetch_fred_daily(client):
        return await get_documents(
            client,
            (
                (
                    instrument,
                    fred_base,
                    {"id": series, "cosd": start.isoformat()},
                )
                for instrument, series in fred_daily.items()
            ),
        )

    async def fetch_fred_weekly(client):
        return await get_documents(
            client,
            (
                (
                    "US.FED.RESERVE_BALANCES",
                    fred_base,
                    {"id": "WRESBAL", "cosd": start.isoformat()},
                ),
            ),
        )

    add("US-USD", "fred_daily", "fred", fetch_fred_daily, parse_fred_csv)
    add("US-USD", "fred_weekly", "fred", fetch_fred_weekly, parse_fred_csv)

    async def fetch_nyfed_rates(client):
        end = now.date().isoformat()
        return await get_documents(
            client,
            (
                (
                    "nyfed_secured_rates",
                    "https://markets.newyorkfed.org/api/rates/secured/all/search.json",
                    {"startDate": start.isoformat(), "endDate": end},
                ),
            ),
        )

    async def fetch_nyfed_facilities(client):
        # The public endpoint accepts at most 500 results (1,000/1,200 return
        # HTTP 400). This reaches roughly one year of standing-facility
        # operations at current cadence and fails neither the US pack nor its
        # siblings during the one-time canonical import.
        count = 500 if backfill else 120
        return await get_documents(
            client,
            (
                (
                    "nyfed_repo_operations",
                    f"https://markets.newyorkfed.org/api/rp/repo/all/results/last/{count}.json",
                    None,
                ),
            ),
        )

    add("US-USD", "nyfed_rates", "nyfed_rates", fetch_nyfed_rates, parse_nyfed_rates)
    add(
        "US-USD",
        "nyfed_facilities",
        "nyfed_facilities",
        fetch_nyfed_facilities,
        parse_nyfed_facilities,
    )

    async def fetch_fiscaldata(client):
        uri = (
            "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
            "v1/accounting/dts/operating_cash_balance"
        )
        common = {
            "filter": (
                f"record_date:gte:{start.isoformat()},account_type:in:("
                "Treasury General Account (TGA) Opening Balance,"
                "Treasury General Account (TGA),Federal Reserve Account)"
            ),
            "fields": "record_date,account_type,open_today_bal",
            "sort": "record_date",
            "page[size]": 1000,
        }
        documents = []
        page = 1
        total_pages = 1
        while page <= total_pages:
            response = await client.get(uri, params={**common, "page[number]": page})
            response.raise_for_status()
            payload = response.json()
            total_pages = min(
                int((payload.get("meta") or {}).get("total-pages", 1)), 20
            )
            documents.append(
                FetchedDocument(
                    str(response.url),
                    "application/json",
                    response.content,
                    f"tga-page-{page}",
                )
            )
            page += 1
        return tuple(documents)

    add("US-USD", "fiscaldata", "fiscaldata", fetch_fiscaldata, parse_fiscal_tga)

    ecb_base = "https://data-api.ecb.europa.eu/service/data"

    def ecb_fetcher(series: dict[str, str]):
        async def fetch(client):
            return await get_documents(
                client,
                (
                    (
                        instrument,
                        f"{ecb_base}/{remote}",
                        {"format": "csvdata", "startPeriod": start.isoformat()},
                    )
                    for instrument, remote in series.items()
                ),
            )

        return fetch

    add(
        "EA-EUR",
        "ecb_benchmark",
        "ecb_benchmark",
        ecb_fetcher(
            {
                "EA.ECB.ESTR": "EST/B.EU000A2X2A25.WT",
                "EA.ECB.ESTR_VOLUME": "EST/B.EU000A2X2A25.TT",
            }
        ),
        parse_ecb_csv,
    )
    add(
        "EA-EUR",
        "ecb_policy",
        "ecb_policy",
        ecb_fetcher(
            {
                "EA.ECB.DFR": "FM/D.U2.EUR.4F.KR.DFR.LEV",
                "EA.ECB.MRO": "FM/D.U2.EUR.4F.KR.MRR_FR.LEV",
                "EA.ECB.MLF": "FM/D.U2.EUR.4F.KR.MLFR.LEV",
            }
        ),
        parse_ecb_csv,
    )
    add(
        "EA-EUR",
        "ecb_liquidity",
        "ecb_liquidity",
        ecb_fetcher({"EA.ECB.EXCESS_LIQUIDITY": "ILM/D.U2.C.EXLIQ.U2.EUR"}),
        parse_ecb_csv,
    )

    boe_base = (
        "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp"
    )

    def boe_fetcher(instrument: str, code: str):
        async def fetch(client):
            return await get_documents(
                client,
                (
                    (
                        instrument,
                        boe_base,
                        {
                            "csv.x": "yes",
                            "Datefrom": start.strftime("%d/%b/%Y"),
                            "Dateto": "now",
                            "SeriesCodes": code,
                            "CSVF": "TN",
                            "UsingCodes": "Y",
                            "VPD": "Y",
                            "VFD": "N",
                        },
                    ),
                ),
            )

        return fetch

    add(
        "UK-GBP",
        "boe_sonia",
        "boe_sonia",
        boe_fetcher("GB.BOE.SONIA", "IUDSOIA"),
        parse_boe_csv,
    )
    add(
        "UK-GBP",
        "boe_policy",
        "boe_policy",
        boe_fetcher("GB.BOE.BANK_RATE", "IUDBEDR"),
        parse_boe_csv,
    )

    rba_f1 = "https://www.rba.gov.au/statistics/tables/xls/f01d.xlsx"
    rba_a2 = "https://www.rba.gov.au/statistics/tables/xls/a02hist.xlsx"

    async def fetch_rba_cash(client):
        return await get_documents(client, (("rba_cash", rba_f1, None),))

    async def fetch_rba_policy(client):
        return await get_documents(
            client,
            (("rba_policy_daily", rba_f1, None), ("rba_policy_changes", rba_a2, None)),
        )

    add("AU-AUD", "rba_cash", "rba_cash", fetch_rba_cash, parse_rba_workbook, 90)
    add("AU-AUD", "rba_policy", "rba_policy", fetch_rba_policy, parse_rba_workbook, 90)

    boj_base = "https://www.stat-search.boj.or.jp/ssi/mtshtml/csv"

    async def fetch_boj_rates(client):
        return await get_documents(
            client,
            (
                ("JP.BOJ.TONA", f"{boj_base}/fm01_d_1_en.csv", None),
                ("JP.BOJ.BASIC_LOAN", f"{boj_base}/ir01_d_1_en.csv", None),
            ),
        )

    async def fetch_boj_accounts(client):
        return await get_documents(
            client,
            (("JP.BOJ.CURRENT_ACCOUNTS", f"{boj_base}/md01_m_1_en.csv", None),),
        )

    add("JP-JPY", "boj_rates", "boj_rates", fetch_boj_rates, parse_boj_csv)
    add("JP-JPY", "boj_accounts", "boj_accounts", fetch_boj_accounts, parse_boj_csv)

    async def fetch_cfets(client):
        end = now.date()
        window_start = start if backfill else max(start, end - timedelta(days=31))
        fdr = await client.get(
            "https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/currency/fdr-chrt.csv",
            headers={"Referer": "https://www.chinamoney.com.cn/english/bmkfrr/"},
        )
        fdr.raise_for_status()
        documents = [
            FetchedDocument(
                str(fdr.url),
                "text/csv",
                fdr.content,
                "CN.CFETS.DR007",
            )
        ]
        # CFETS states and enforces a one-year maximum per historical query.
        # Use 360 inclusive days to avoid leap-year boundary ambiguity.
        shibor_start = max(window_start, date(2007, 1, 4))
        for chunk_start, chunk_end in bounded_date_windows(
            shibor_start,
            end,
            maximum_days=360,
        ):
            shibor = await client.get(
                "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-shibor/ShiborHis",
                params={
                    "lang": "en",
                    "startDate": chunk_start.isoformat(),
                    "endDate": chunk_end.isoformat(),
                },
                headers={"Referer": "https://www.chinamoney.com.cn/english/bmkshibor/"},
            )
            shibor.raise_for_status()
            records = shibor.json().get("records") or []
            if records:
                documents.append(
                    FetchedDocument(
                        str(shibor.url),
                        "application/json",
                        shibor.content,
                        "CN.CFETS.SHIBOR_ON",
                    )
                )
        return tuple(documents)

    add("CN-CNY", "cfets_rates", "cfets_rates", fetch_cfets, parse_cfets_rates, 90)

    async def fetch_hkma(client):
        return await get_documents(
            client,
            (
                (
                    "hkma_liquidity",
                    "https://api.hkma.gov.hk/public/market-data-and-statistics/"
                    "daily-monetary-statistics/daily-figures-interbank-liquidity",
                    {"pagesize": 1000},
                ),
            ),
        )

    add("HK-HKD", "hkma_official", "hkma_official", fetch_hkma, parse_hkma_json)

    async def fetch_rbi(client):
        return await get_documents(
            client,
            (
                (
                    "rbi_mmo",
                    "https://www.rbi.org.in/Scripts/BS_ViewMMO.aspx/Statistics.aspx",
                    None,
                ),
                ("rbi_home", "https://www.rbi.org.in/", None),
            ),
        )

    add("IN-INR", "rbi_official", "rbi_official", fetch_rbi, parse_rbi_html, 90)

    rbnz_xlsx = (
        "https://www.rbnz.govt.nz/-/media/project/sites/rbnz/files/statistics/"
        "series/b/b2/hb2-daily-close.xlsx"
    )
    rbnz_page = (
        "https://www.rbnz.govt.nz/en/statistics/series/exchange-and-interest-rates/"
        "wholesale-interest-rates"
    )

    def rbnz_fetcher(label: str):
        async def fetch(client):
            response = await client.get(rbnz_xlsx)
            if response.is_success:
                return (
                    FetchedDocument(
                        str(response.url),
                        response.headers.get(
                            "content-type",
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        ).split(";", 1)[0],
                        response.content,
                        label,
                    ),
                )
            fallback = await client.get(rbnz_page)
            fallback.raise_for_status()
            return (
                FetchedDocument(
                    str(fallback.url), "text/html", fallback.content, label
                ),
            )

        return fetch

    add(
        "NZ-NZD",
        "rbnz_policy",
        "rbnz_policy",
        rbnz_fetcher("rbnz_policy"),
        parse_rbnz,
        90,
    )
    add(
        "NZ-NZD",
        "rbnz_wholesale",
        "rbnz_wholesale",
        rbnz_fetcher("rbnz_wholesale"),
        parse_rbnz,
        90,
    )

    mas_start_year = configured_start.year if backfill else now.year

    async def fetch_mas_sora(client):
        return await _mas_documents(
            client,
            label="mas_sora",
            columns=(13, 18),
            start_year=mas_start_year,
            end_year=now.year,
            end_month=now.month,
        )

    async def fetch_mas_rates(client):
        return await _mas_documents(
            client,
            label="mas_rates",
            columns=(10, 11),
            start_year=mas_start_year,
            end_year=now.year,
            end_month=now.month,
        )

    add("SG-SGD", "mas_sora", "mas_sora", fetch_mas_sora, parse_mas_sora, 120)
    add("SG-SGD", "mas_rates", "mas_rates", fetch_mas_rates, parse_mas_rates, 120)

    return tuple(adapters)


PRODUCTION_ADAPTER_KEYS = frozenset(
    {
        ("US-USD", "fred_daily"),
        ("US-USD", "fred_weekly"),
        ("US-USD", "nyfed_rates"),
        ("US-USD", "nyfed_facilities"),
        ("US-USD", "fiscaldata"),
        ("EA-EUR", "ecb_benchmark"),
        ("EA-EUR", "ecb_policy"),
        ("EA-EUR", "ecb_liquidity"),
        ("UK-GBP", "boe_sonia"),
        ("UK-GBP", "boe_policy"),
        ("JP-JPY", "boj_rates"),
        ("JP-JPY", "boj_accounts"),
        ("CN-CNY", "cfets_rates"),
        ("HK-HKD", "hkma_official"),
        ("IN-INR", "rbi_official"),
        ("AU-AUD", "rba_cash"),
        ("AU-AUD", "rba_policy"),
        ("NZ-NZD", "rbnz_policy"),
        ("NZ-NZD", "rbnz_wholesale"),
        ("SG-SGD", "mas_sora"),
        ("SG-SGD", "mas_rates"),
    }
)
