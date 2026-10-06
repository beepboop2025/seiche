"""Bounded official funding-reference adapters; no request-path acquisition."""
from __future__ import annotations

import calendar
import csv
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import io
import json
import os
import re
import unicodedata

from seiche.domain.observation import QualityState
from seiche.markets.funding_reference import (
    BIS_MARKETS, BOE_TENORS, CBC_MONTHLY, COUNTRY_MAP, CURVES, FRED_SERIES, JP_TENORS,
)
from seiche.sources.canonical import (
    FetchedDocument, FunctionalCanonicalAdapter, ParsedPoint, PublicationTimePolicy,
)

MOF_BASE = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/"
BOE_DATABASE = "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp"
ECB_DATA = "https://data-api.ecb.europa.eu/service/data/IRS/"
BIS_DATA = "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/"
CBC_HOME = "https://www.cbc.gov.tw/en/mp-2.html"
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024


def _text(document):
    try:
        return document.payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        if document.label.startswith("jp_mof_"):
            return document.payload.decode("cp932")
        raise


def _value(value):
    value = str(value).strip()
    if value in {"", "-", "--", ".", "..", "...", "NA", "N/A"}:
        return None
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("source rate is not numeric") from exc
    if not result.is_finite():
        raise ValueError("source rate is not finite")
    return result


def _point(identifier, event, value, evidence):
    return ParsedPoint(
        identifier, event, value,
        (json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        quality=QualityState.ESTIMATED,
        publication_time_policy=PublicationTimePolicy.UNKNOWN,
    )


def parse_mof_curve(document, *, start=None, history_before=None):
    """English MOF CSV: fixed maturity, not security-specific benchmark yields."""
    rows = csv.reader(io.StringIO(_text(document)))
    header = None
    points = []
    seen = set()
    for row in rows:
        if not row or not row[0].strip():
            continue
        if row[0].strip() == "Date":
            expected = ["Date", *(f"{tenor}Y" for tenor in JP_TENORS)]
            if header is not None or [cell.strip() for cell in row] != expected:
                raise ValueError("MOF maturity columns changed")
            header = expected
            continue
        if not re.match(r"^\d{4}/", row[0]):
            continue
        if header is None or len(row) != len(header):
            raise ValueError("MOF dated row does not match its maturity header")
        event = datetime.strptime(row[0].strip(), "%Y/%m/%d").date()
        if event in seen:
            raise ValueError("MOF dated row is duplicated")
        seen.add(event)
        if start and event < start or history_before and event >= history_before:
            continue
        for tenor, raw in zip(JP_TENORS, row[1:], strict=True):
            value = _value(raw)
            if value is not None:
                points.append(_point(f"JP.MOF.JGB_{tenor}Y", event, value,
                                     {"source": "MOF Japan", "date": event.isoformat(),
                                      "maturity": f"{tenor}Y", "percent": str(value)}))
    if header is None:
        raise ValueError("MOF maturity header missing")
    return tuple(points)


def parse_boe_curve(document):
    reader = csv.DictReader(io.StringIO(_text(document)))
    if reader.fieldnames != ["DATE", *BOE_TENORS]:
        raise ValueError("BoE curve series identity changed")
    points = []
    seen = set()
    for row in reader:
        if not (row.get("DATE") or "").strip():
            continue
        if None in row or any(value is None for value in row.values()):
            raise ValueError("BoE curve row is truncated or shifted")
        event = datetime.strptime(row["DATE"].strip(), "%d %b %Y").date()
        if event in seen:
            raise ValueError("BoE dated row is duplicated")
        seen.add(event)
        for code, tenor in BOE_TENORS.items():
            value = _value(row[code])
            if value is not None:
                points.append(_point(f"GB.BOE.ZERO_COUPON_{tenor}Y", event, value,
                                     {"series": code, "date": event.isoformat(), "percent": str(value)}))
    if not points:
        raise ValueError("BoE curve contains no numeric observations")
    return tuple(points)


def parse_ecb_national_yield(document):
    code = document.label.removeprefix("ecb_sovereign:")
    curve = next((c for c in CURVES if c.country == code and c.adapter_id == "ecb_sovereign_monthly"), None)
    if curve is None:
        raise ValueError("unknown ECB sovereign country")
    expected = {"FREQ": "M", "REF_AREA": code, "IR_TYPE": "L", "TR_TYPE": "L40",
                "MATURITY_CAT": "CI", "BS_COUNT_SECTOR": "0000", "CURRENCY_TRANS": "EUR",
                "IR_BUS_COV": "N", "IR_FV_TYPE": "Z", "UNIT": "PC", "UNIT_MULT": "0"}
    key = f"IRS.M.{code}.L.L40.CI.0000.EUR.N.Z"
    points = []
    for row in csv.DictReader(io.StringIO(_text(document))):
        if row.get("KEY") != key or any(row.get(k) != v for k, v in expected.items()):
            raise ValueError("ECB country, currency, maturity or frequency changed")
        if row.get("OBS_CONF") != "F":
            raise ValueError("ECB observation is not marked free for publication")
        value = _value(row.get("OBS_VALUE", ""))
        if value is None:
            continue
        period = row["TIME_PERIOD"]
        if not re.fullmatch(r"\d{4}-\d{2}", period):
            raise ValueError("ECB monthly reference period is invalid")
        year, month = map(int, period.split("-"))
        event = date(year, month, calendar.monthrange(year, month)[1])
        points.append(_point(curve.nodes[0][1], event, value, row))
    if not points:
        raise ValueError("ECB national reference has no observations")
    return tuple(points)


def parse_bis_policy(document):
    code = document.label.removeprefix("bis_policy:")
    if code not in BIS_MARKETS:
        raise ValueError("unknown BIS policy country")
    points = []
    for row in csv.DictReader(io.StringIO(_text(document))):
        if (row.get("REF_AREA") != code or row.get("FREQ") != "D"
                or row.get("UNIT_MEASURE") != "368" or row.get("UNIT_MULT") != "0"
                or row.get("OBS_CONF") != "F" or not row.get("SOURCE_REF")):
            raise ValueError("BIS policy country, units, source or publication permission changed")
        if row.get("OBS_STATUS") == "M":
            if row.get("OBS_VALUE", "").strip() not in {"", "NaN", "."}:
                raise ValueError("BIS missing flag conflicts with its value")
            continue
        value = _value(row.get("OBS_VALUE", ""))
        if value is not None:
            event = date.fromisoformat(row["TIME_PERIOD"])
            points.append(_point(code + ".BIS.POLICY_REFERENCE", event, value, row))
    if not points:
        raise ValueError("BIS policy reference has no observations")
    return tuple(points)


class _Tables(HTMLParser):
    """Extract table cells, excluding scripts and style; do not execute markup."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.table = None
        self.row = None
        self.cell = None
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.ignored += 1
        if self.ignored:
            return
        if tag == "table":
            if self.table is not None:
                raise ValueError("nested official data table is ambiguous")
            self.table = {"id": dict(attrs).get("id"), "rows": []}
        elif self.table is not None and tag == "tr":
            self.row = []
        elif self.row is not None and tag in {"td", "th"}:
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.ignored:
            self.ignored -= 1
            return
        if self.ignored:
            return
        if tag in {"td", "th"} and self.cell is not None and self.row is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None and self.table is not None:
            self.table["rows"].append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table = None

    def handle_data(self, value):
        if self.cell is not None and not self.ignored:
            self.cell.append(value)


def parse_fred_oecd(document, *, start=None):
    series = document.label.removeprefix("fred_oecd:")
    if series not in FRED_SERIES:
        raise ValueError("unknown OECD/FRED series")
    _, iso3, measure, identifier = FRED_SERIES[series]
    parser = _Tables()
    parser.feed(_text(document))
    metadata = [dict(t["rows"]) for t in parser.tables
                if t["rows"] and all(len(r) == 2 for r in t["rows"])
                and any(r[0] == "Series ID" for r in t["rows"])]
    if len(metadata) != 1:
        raise ValueError("OECD/FRED series metadata is missing or ambiguous")
    meta = metadata[0]
    expected = {"Series ID": series, "Source": "Organization for Economic Co-operation and Development",
                "Frequency": "Monthly", "Units": "Percent", "Seasonal Adjustment": "Not Seasonally Adjusted"}
    filters = {"REF_AREA": iso3, "MEASURE": measure, "UNIT_MEASURE": "PA", "ACTIVITY": "_Z",
               "ADJUSTMENT": "_Z", "TRANSFORMATION": "_Z", "FREQ": "M"}
    notes = meta.get("Notes", "")
    if (any(meta.get(k) != v for k, v in expected.items())
            or any(not re.search(r"\b" + key + r":\s*" + re.escape(value) + r"(?=\s|$)", notes)
                   for key, value in filters.items())):
        raise ValueError("OECD/FRED country, tenor, frequency or units changed")
    observations = [t["rows"] for t in parser.tables if t["id"] == "data-table-observations"]
    if len(observations) != 1 or observations[0][0] != ["DATE", "VALUE"]:
        raise ValueError("OECD/FRED observation table changed")
    points, seen = [], set()
    for row in observations[0][1:]:
        if len(row) != 2:
            raise ValueError("OECD/FRED observation row is malformed")
        label = date.fromisoformat(row[0])
        if label.day != 1 or label in seen:
            raise ValueError("OECD/FRED monthly label is invalid or duplicated")
        seen.add(label)
        event = label.replace(day=calendar.monthrange(label.year, label.month)[1])
        if start and event < start:
            continue
        value = _value(row[1])
        if value is not None:
            points.append(_point(identifier, event, value, {"series": series, "source_month_label": row[0],
                                 "percent": row[1], "source": meta["Source"], "filters": filters}))
    if not points:
        raise ValueError("OECD/FRED series has no numeric observations in the requested window")
    return tuple(points)


CBC_MONTHLY_HEADER = ["Month", "Discount Rates", "Interest Rates on Accommodations with Collateral",
    "1-Month Deposit Rates", "1-Year Deposit Rates", "Base Lending Rates(1)",
    "Weighted Averages of Overnight Interest Rates", "31-90 Days CP Rates in Secondary Market",
    "10-Year Gov't Bond Rates in Secondary Market"]


def parse_cbc_monthly(document, *, start=None):
    import xlrd
    workbook = xlrd.open_workbook(file_contents=document.payload, on_demand=True)
    try:
        if workbook.sheet_names() not in (["EINTEREST-N"], ["EINTEREST-H"]):
            raise ValueError("CBC selected-rate workbook identity changed")
        sheet = workbook.sheet_by_index(0)
        if (sheet.ncols != 9 or sheet.nrows < 4
                or unicodedata.normalize("NFKC", str(sheet.cell_value(0, 8))).strip() != "%Per Annum"
                or sheet.row_values(2) != CBC_MONTHLY_HEADER):
            raise ValueError("CBC selected-rate units or columns changed")
        points, seen = [], set()
        columns = {**CBC_MONTHLY, 8: ("TW.CBC.GOVERNMENT_10Y_MONTHLY", None)}
        for index in range(3, sheet.nrows):
            row = sheet.row_values(index)
            period = str(row[0]).strip()
            if not period or period.startswith("(1)"):
                continue
            if not re.fullmatch(r"\d{4}\.\d{2}", period) or period in seen:
                raise ValueError("CBC monthly date label changed or duplicated")
            seen.add(period)
            year, month = map(int, period.split("."))
            event = date(year, month, calendar.monthrange(year, month)[1])
            if start and event < start:
                continue
            for column, (identifier, _) in columns.items():
                value = _value(row[column])
                if value is not None:
                    points.append(_point(identifier, event, value, {"table": "CBC Selected Interest Rates",
                                         "period": period, "column": CBC_MONTHLY_HEADER[column], "percent": str(value)}))
        if not points:
            raise ValueError("CBC monthly workbook has no numeric observations in the requested window")
        return tuple(points)
    finally:
        workbook.release_resources()


class _CBCIndicators(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts = []
        self.rows = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.ignored += 1
        if tag == "div":
            if self.depth:
                self.depth += 1
            elif "tabItem" in dict(attrs).get("class", "").split():
                self.depth = 1
                self.parts = []

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.ignored:
            self.ignored -= 1
        if tag == "div" and self.depth:
            self.depth -= 1
            if not self.depth:
                self.rows.append(" ".join(" ".join(self.parts).split()))

    def handle_data(self, data):
        if self.depth and not self.ignored:
            self.parts.append(data)


def parse_cbc_rates(document):
    parser = _CBCIndicators()
    parser.feed(_text(document))
    selected = {"Interbank Overnight Call-loan Rate": "TW.CBC.OVERNIGHT_CALL",
                "Discount Rate": "TW.CBC.DISCOUNT_DECISION"}
    points = {}
    for text in parser.rows:
        for title, identifier in selected.items():
            if not text.startswith(title):
                continue
            match = re.fullmatch(re.escape(title) + r"\s+(\d{4}-\d{2}-\d{2})\s+([-+]?\d+(?:\.\d+)?)%", text)
            if not match:
                raise ValueError("CBC indicator lacks its own date or percent value")
            event, raw = match.groups()
            value = _value(raw)
            point = _point(identifier, date.fromisoformat(event), value,
                           {"indicator": title, "date": event, "percent": str(value)})
            if identifier in points and points[identifier] != point:
                raise ValueError("CBC duplicate indicator observations disagree")
            points[identifier] = point
    if set(points) != set(selected.values()):
        raise ValueError("CBC public rate indicators are missing")
    return tuple(points.values())


async def _documents(client, requests):
    documents = []
    for label, url, params in requests:
        async with client.stream("GET", url, params=params) as response:
            response.raise_for_status()
            chunks = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_DOCUMENT_BYTES:
                    raise ValueError("official reference document exceeds its size limit")
                chunks.append(chunk)
            documents.append(FetchedDocument(str(response.url), response.headers.get("Content-Type", "application/octet-stream"), b"".join(chunks), label))
    return tuple(documents)


def build_country_adapters(*, registry, repository=None, backfill=False, clock=None):
    def today():
        return (clock() if clock else datetime.now(UTC)).astimezone(UTC).date()

    def start():
        return date.fromisoformat(os.getenv("SEICHE_CANONICAL_START", "2018-01-01")) if backfill else today() - timedelta(days=400)

    def scoped_fetch(requests):
        async def fetch(client):
            return await _documents(client, requests())
        return fetch

    async def fetch_mof(client):
        return await _documents(client, (
            ("jp_mof_history", MOF_BASE + "historical/jgbcme_all.csv", None),
            ("jp_mof_current", MOF_BASE + "jgbcme.csv", None),
        ))

    def parse_mof(document):
        return parse_mof_curve(document, start=start(), history_before=(today().replace(day=1) if document.label == "jp_mof_history" else None))

    definitions = {
        "jp_mof_curve": (fetch_mof, parse_mof),
        "boe_sovereign_curve": (scoped_fetch(lambda: (("boe_sovereign_curve", BOE_DATABASE, {
            "csv.x": "yes", "Datefrom": start().strftime("%d/%b/%Y"), "Dateto": "now",
            "SeriesCodes": ",".join(BOE_TENORS), "CSVF": "TN", "UsingCodes": "Y", "VPD": "Y", "VFD": "N",
        }),)), parse_boe_curve),
        "cbc_public_rates": (scoped_fetch(lambda: (("cbc_public_rates", CBC_HOME, None),)), parse_cbc_rates),
        "cbc_monthly_rates": (scoped_fetch(lambda: (("cbc_monthly_rates",
            "https://www.cbc.gov.tw/public/data/Ebanking/EINTEREST-" + ("H" if backfill else "N") + ".xls", None),)),
            lambda document: parse_cbc_monthly(document, start=start())),
    }
    adapters = []
    for pack in registry.list():
        for spec in pack.source_adapters:
            if spec.adapter_id in definitions:
                fetcher, parser = definitions[spec.adapter_id]
            elif spec.adapter_id == "oecd_fred_reference":
                identifiers = tuple(series for series, (code, _, _, _) in FRED_SERIES.items()
                                    if COUNTRY_MAP[code].market_id == pack.market_id)
                fetcher = scoped_fetch(lambda identifiers=identifiers: tuple(("fred_oecd:" + series,
                    "https://fred.stlouisfed.org/data/" + series, None) for series in identifiers))
                parser = lambda document: parse_fred_oecd(document, start=start())
            elif spec.adapter_id == "bis_policy_reference":
                code = next(code for code, market in BIS_MARKETS.items() if market == pack.market_id)
                fetcher = scoped_fetch(lambda code=code: (("bis_policy:" + code, BIS_DATA + "D." + code,
                    {"format": "csv", "startPeriod": start().isoformat()}),))
                parser = parse_bis_policy
            elif spec.adapter_id == "ecb_sovereign_monthly":
                code = pack.jurisdiction_codes[0]
                fetcher = scoped_fetch(lambda code=code: (("ecb_sovereign:" + code,
                    ECB_DATA + f"M.{code}.L.L40.CI.0000.EUR.N.Z",
                    {"format": "csvdata", "startPeriod": start().strftime("%Y-%m")}),))
                parser = parse_ecb_national_yield
            else:
                continue
            adapters.append(FunctionalCanonicalAdapter(
                pack=pack, adapter_id=spec.adapter_id, source=spec.adapter_id,
                fetcher=fetcher, parser=parser, repository=repository, clock=clock,
                timeout_seconds=30, historical_backfill=backfill,
            ))
    return tuple(adapters)


PRODUCTION_KEYS = frozenset(
    [(curve.market_id, curve.adapter_id) for curve in CURVES]
    + [(market, "bis_policy_reference") for market in BIS_MARKETS.values()]
    + [("TW-TWD", "cbc_public_rates")]
    + [(COUNTRY_MAP[code].market_id, "oecd_fred_reference") for code, _, _, _ in FRED_SERIES.values()]
)
