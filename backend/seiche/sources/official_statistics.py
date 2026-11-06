"""Exact original statistical series with isolated capture rights.

Permissions are specific to these statistics. Source inventory is not a grant
for unrelated ECB feeds, EIA vendor prices, publisher logos or old FRED rows.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import math
import posixpath
import re
import sqlite3
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd

from seiche import store
from seiche.config import ALL_SERIES
from seiche.sources._async_store import run_store
from seiche.sources.base import Series, utcnow_iso
from seiche.public_faults import sanitize_fault

SCHEMA = "seiche.original-statistics.v1"
MAX_BYTES = 8_000_000
MAX_POINTS = 50_000
BLS_URL = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
BEA_URL = "https://apps.bea.gov/national/Release/XLS/Survey/Section1All_xls.xlsx"
ECB_BASE = "https://data-api.ecb.europa.eu/service/data/"
EIA_BASE = "https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx"
TERMS = {
    "bls": "https://www.bls.gov/bls/linksite.htm",
    "bea": "https://www.bea.gov/help/faq/147",
    "ecb": "https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html",
    "eia": "https://www.eia.gov/about/copyrights_reuse.php",
    "nyfed": "https://www.newyorkfed.org/privacy/termsofuse",
    "boe": "https://www.bankofengland.co.uk/legal",
}
NAMES = {"bls": "U.S. Bureau of Labor Statistics", "bea": "U.S. Bureau of Economic Analysis",
         "ecb": "European Central Bank", "eia": "U.S. Energy Information Administration",
         "nyfed": "Federal Reserve Bank of New York", "boe": "Bank of England"}
CONTRACTS = {
    "CORE_CPI": {"provider": "bls", "group": "bls", "remote_id": "CUSR0000SA0L1E", "unit": "index 1982-84=100", "normalization": "monthly reference period to month-start"},
    "ENERGY_CPI": {"provider": "bls", "group": "bls", "remote_id": "CUSR0000SA0E", "unit": "index 1982-84=100", "normalization": "monthly reference period to month-start"},
    "GDP": {"provider": "bea", "group": "GDP", "remote_id": "NIPA/T10105-Q/A191RC", "unit": "$B", "normalization": "millions USD / 1000 to billions USD; SAAR; quarter-start reference labels"},
    "ESTR": {"provider": "ecb", "group": "ESTR", "remote_id": "EST/B.EU000A2X2A25.WT", "unit": "%", "normalization": "publisher effective date; values unchanged", "dimensions": {"KEY": "EST.B.EU000A2X2A25.WT", "FREQ": "B", "BENCHMARK_ITEM": "EU000A2X2A25", "DATA_TYPE_EST": "WT", "UNIT_MEASURE": "PC", "UNIT_MULT": "0"}},
    "ECB_DFR": {"provider": "ecb", "group": "ECB_DFR", "remote_id": "FM/D.U2.EUR.4F.KR.DFR.LEV", "unit": "%", "normalization": "publisher effective date; values unchanged", "dimensions": {"KEY": "FM.D.U2.EUR.4F.KR.DFR.LEV", "FREQ": "D", "REF_AREA": "U2", "CURRENCY": "EUR", "PROVIDER_FM": "4F", "INSTRUMENT_FM": "KR", "PROVIDER_FM_ID": "DFR", "DATA_TYPE_FM": "LEV", "UNIT": "PCPA", "UNIT_MULT": "0"}},
    "ECB_ASSETS": {"provider": "ecb", "group": "ECB_ASSETS", "remote_id": "ILM/W.U2.C.T000000.Z5.Z01", "unit": "EUR M", "normalization": "ISO reference week to Friday; millions EUR unchanged; Eurosystem changing composition", "dimensions": {"KEY": "ILM.W.U2.C.T000000.Z5.Z01", "FREQ": "W", "REF_AREA": "U2", "BS_REP_SECTOR": "C", "BS_ITEM": "T000000", "COUNT_AREA": "Z5", "CURRENCY_TRANS": "Z01", "UNIT": "EUR", "UNIT_MULT": "6"}},
    "CUSHING_STOCKS": {"provider": "eia", "group": "CUSHING_STOCKS", "remote_id": "W_EPC0_SAX_YCUOK_MBBL", "unit": "thousand barrels", "normalization": "publisher weekly ending date; values unchanged", "title": "Weekly Cushing, OK Ending Stocks excluding SPR of Crude Oil (Thousand Barrels)"},
    "CRUDE_STOCKS_EX_SPR": {"provider": "eia", "group": "CRUDE_STOCKS_EX_SPR", "remote_id": "WCESTUS1", "unit": "thousand barrels", "normalization": "publisher weekly ending date; values unchanged", "title": "Weekly U.S. Ending Stocks excluding SPR of Crude Oil (Thousand Barrels)"},
    "SOFR": {"provider": "nyfed", "group": "SOFR", "remote_id": "SOFR", "unit": "%", "normalization": "effectiveDate, percentRate unchanged; overnight rate, not Term SOFR"},
    "EFFR": {"provider": "nyfed", "group": "EFFR", "remote_id": "EFFR", "unit": "%", "normalization": "effectiveDate, percentRate unchanged; not the target range"},
    "RRPONTSYD": {"provider": "nyfed", "group": "rrp", "remote_id": "ON_RRP_TREASURY", "unit": "$B", "normalization": "daily sum of overnight fixed-rate Treasury reverse-repo accepted USD / 1e9, including small-value exercises; operation date; no calendar filling"},
    "RRP_LONG": {"provider": "nyfed", "group": "rrp", "remote_id": "ON_RRP_TREASURY", "unit": "$B", "normalization": "daily sum of overnight fixed-rate Treasury reverse-repo accepted USD / 1e9, including small-value exercises; operation date; no calendar filling"},
    "SONIA": {"provider": "boe", "group": "SONIA", "remote_id": "IUDSOIA", "unit": "%", "normalization": "publisher daily reference date and percent rate unchanged; delayed Database release"},
}
for _mnemonic in ("RRPONTSYD", "RRP_LONG"):
    CONTRACTS[_mnemonic]["dimensions"] = {
        "operationType": "Reverse Repo", "term": "Overnight", "operationMethod": "Fixed Rate",
        "auctionStatus": "Results", "settlementType": "Same Day", "securityType": "Treasury",
        "source_amount_unit": "USD", "daily_group": "operationDate; settlementDate must equal operationDate",
        "deduplication": "reject repeated operationId; sum distinct operations, including exercises",
        "maturity": "1-5 calendar days after settlement, accounting for weekends/holidays",
    }
_locks = {c["group"]: asyncio.Lock() for c in CONTRACTS.values()}
_network = asyncio.Semaphore(2)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def source_url(m: str) -> str:
    c = CONTRACTS[m]
    if c["provider"] == "bls":
        return BLS_URL
    if c["provider"] == "bea":
        return BEA_URL
    if c["provider"] == "ecb":
        start = "2019-10-01" if m == "ESTR" else ALL_SERIES[m].start
        return ECB_BASE + c["remote_id"] + "?" + urlencode({"format": "csvdata", "startPeriod": start})
    if c["provider"] == "nyfed":
        if c["group"] == "rrp":
            return "https://markets.newyorkfed.org/api/rp/results/search.json?" + urlencode({
                "startDate": min(ALL_SERIES[n].start for n, v in CONTRACTS.items() if v["group"] == "rrp"),
                "operationTypes": "Reverse Repo", "method": "fixed", "securityType": "tsy", "term": "overnight"})
        family = "secured" if m == "SOFR" else "unsecured"
        return f"https://markets.newyorkfed.org/api/rates/{family}/{m.lower()}/search.json?" + urlencode({"startDate": ALL_SERIES[m].start})
    if c["provider"] == "boe":
        return "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?" + urlencode({
            "csv.x": "yes", "Datefrom": date.fromisoformat(ALL_SERIES[m].start).strftime("%d/%b/%Y"),
            "Dateto": "now", "SeriesCodes": c["remote_id"], "CSVF": "TN", "UsingCodes": "Y", "VPD": "Y", "VFD": "N"})
    return EIA_BASE + "?" + urlencode({"n": "PET", "s": c["remote_id"], "f": "W"})


def contract(m: str) -> dict:
    c = CONTRACTS[m]
    spec = ALL_SERIES[m]
    if c["unit"] != spec.unit:
        raise ValueError("registry unit changed; publisher contract needs review")
    if c["provider"] in {"nyfed", "boe"} and spec.freq != "D":
        raise ValueError("registry frequency changed; daily publisher contract needs review")
    return {**c, "mnemonic": m, "source": c["provider"] + "_original", "source_url": source_url(m),
            "freq": spec.freq, "start": spec.start, "legacy_remote_id": spec.remote_id,
            "terms_url": TERMS[c["provider"]], "terms_reviewed_at": "2026-11-06",
            "rights": {"ecb": "reuse_with_attribution_and_free_source_notice",
                       "nyfed": "business_reuse_with_notices_and_same_downstream_terms",
                       "boe": "OGL_v3_with_SONIA_attribution"}.get(c["provider"], "public_domain_with_attribution"),
            "scope": "Only the explicitly identified publisher-produced statistical series."}


def _key(group: str) -> str:
    return "original-publisher:statistics:" + group + ":v1"


def _load(group: str):
    database = Path(store.DB_PATH)
    if not database.is_file():
        return None
    conn = None
    try:
        conn = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        row = conn.execute("SELECT payload FROM blobs WHERE key=?", (_key(group),)).fetchone()
        return json.loads(row[0]) if row else None
    except (sqlite3.Error, ValueError, TypeError):
        return None
    finally:
        if conn is not None:
            conn.close()


@dataclass
class StatisticsSeries(Series):
    evidence: dict

    def provenance(self) -> dict:
        return {**super().provenance(), **self.evidence}

    def tail_records(self, n: int = 500) -> list[list]:
        return [[idx.date().isoformat(), None if pd.isna(value) else round(float(value), 6)]
                for idx, value in self.points.tail(n).items()]


def _rows(m: str, rows: list, *, captured: str | None = None) -> list:
    start = date.fromisoformat(ALL_SERIES[m].start)
    out, seen = [], set()
    if not 1 <= len(rows) <= MAX_POINTS:
        raise ValueError("source observation count outside bounds")
    for row in rows:
        if not isinstance(row, list) or len(row) != 2:
            raise ValueError("source row shape changed")
        day = date.fromisoformat(row[0])
        if row[0] != day.isoformat() or day in seen:
            raise ValueError("duplicate or invalid source date")
        seen.add(day)
        value = row[1]
        missing = value is None and CONTRACTS[m]["provider"] == "bls"
        if not missing and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError("nonfinite source value")
        if captured and day > datetime.fromisoformat(captured).date():
            raise ValueError("future source observation")
        if day >= start:
            out.append([day.isoformat(), None if missing else float(value)])
    out.sort()
    if not out or not any(row[1] is not None for row in out):
        raise ValueError("empty source window")
    return out


def make_capture(group: str, series: dict[str, tuple[list, dict]], documents: list[dict], fetched_at: str) -> dict:
    expected = {m for m, c in CONTRACTS.items() if c["group"] == group}
    if set(series) != expected:
        raise ValueError("incomplete publisher series set")
    records = {}
    for m, (rows, extra) in series.items():
        rows = _rows(m, rows, captured=fetched_at)
        c = contract(m)
        records[m] = {"contract": c, "contract_sha256": digest(c), "points": rows,
                      "points_sha256": digest(rows), "extra": extra, "extra_sha256": digest(extra)}
    capture = {"schema": SCHEMA, "group": group, "fetched_at": fetched_at,
               "documents": documents, "series": records}
    if len(decode(group, capture)) != len(records):
        raise ValueError("publisher capture did not validate")
    return capture


def decode(group: str, capture: object) -> dict[str, StatisticsSeries]:
    if not isinstance(capture, dict) or capture.get("schema") != SCHEMA or capture.get("group") != group:
        return {}
    out = {}
    try:
        stamp = datetime.fromisoformat(capture["fetched_at"])
        if stamp.tzinfo is None or stamp.utcoffset() is None or stamp > datetime.now(UTC) + timedelta(minutes=5):
            return {}
        docs = capture["documents"]
        expected_urls = {source_url(m) for m, c in CONTRACTS.items() if c["group"] == group}
        if not isinstance(docs, list) or not 1 <= len(docs) <= 4:
            return {}
        for doc in docs:
            if doc["source_url"] not in expected_urls or not re.fullmatch(r"[0-9a-f]{64}", doc["sha256"]) or not 0 < doc["bytes"] <= MAX_BYTES:
                return {}
        for m, record in capture["series"].items():
            if m not in CONTRACTS or CONTRACTS[m]["group"] != group:
                return {}
            c = contract(m)
            if record["contract"] != c or record["contract_sha256"] != digest(c) or record["points_sha256"] != digest(record["points"]) or record["extra_sha256"] != digest(record["extra"]):
                continue
            rows = _rows(m, record["points"], captured=capture["fetched_at"])
            if rows != record["points"]:
                continue
            provider = c["provider"]
            extra = record["extra"]
            attribution = "Source: " + NAMES[provider] + "."
            if extra.get("source_publication_date"):
                attribution = "Source: " + NAMES[provider] + " (" + extra["source_publication_date"] + ")."
            evidence = {
                "source_url": c["source_url"], "terms_url": c["terms_url"], "terms_reviewed_at": c["terms_reviewed_at"],
                "attribution": attribution, "redistribution_status": "allowed", "rights_basis": c["rights"], "rights_scope": c["scope"],
                "raw_capture_sha256s": [d["sha256"] for d in docs], "contract_sha256": record["contract_sha256"],
                "source_retrieval_times": [d.get("retrieved_at") for d in docs],
                "normalized_points_sha256": record["points_sha256"], "publisher_dimensions": c.get("dimensions", {"series_id": c["remote_id"]}),
                "observation_date_normalization": c["normalization"], "knowledge_time": capture["fetched_at"],
                "source_publication_time": None, "publication_time_policy": "unknown", "historical_point_in_time": False,
                "vintage": "publisher current vintage acquired at knowledge_time", "missing_observations": sum(row[1] is None for row in rows),
                "source_publication_date": extra.get("source_publication_date"),
                "missing_observation_notes": extra.get("missing_observation_notes", []),
            }
            if provider == "ecb":
                evidence["free_source_notice"] = "This information is available free of charge from the European Central Bank."
                evidence["modification_notice"] = c["normalization"]
            if provider == "nyfed":
                evidence["attribution"] = "© " + str(stamp.year) + " Federal Reserve Bank of New York. Content from the New York Fed subject to the Terms of Use at newyorkfed.org."
                evidence["required_notices"] = [
                    "The source data are available without charge from " + c["source_url"],
                    "Redistribution must retain the same permissions, conditions and restrictions in the New York Fed Terms of Use; no more restrictive downstream terms may be imposed.",
                    "Modifications by Seiche: " + c["normalization"],
                ]
                if m in {"SOFR", "EFFR"}:
                    evidence["required_notices"].extend([
                        f"The {m} data is subject to the Terms of Use posted at newyorkfed.org. The New York Fed is not responsible for publication of the {m} data by LiquiLens Private Limited, does not sanction or endorse any particular republication, and has no liability for your use.",
                        "LiquiLens Private Limited is not affiliated with the New York Fed. The New York Fed does not sanction, endorse, or recommend any products or services offered by LiquiLens Private Limited.",
                    ])
                if m == "SOFR":
                    evidence["required_notices"].append("SOFR Data is calculated using data provided under a license granted to the New York Fed by DTCC Solutions LLC, an affiliate of The Depository Trust & Clearing Corporation. DTCC Solutions LLC, its affiliates, and third parties from which they obtained data have no liability for the content of this material.")
                evidence["source_revision_times"] = extra.get("source_revision_times", {})
                evidence["source_revision_time_policy"] = extra.get("source_revision_time_policy", "not supplied")
                evidence["revision_indicators"] = extra.get("revision_indicators", {})
                evidence["source_operation_ids"] = extra.get("source_operation_ids", {})
                if c["group"] == "rrp":
                    evidence["measure_kind"] = "derived_daily_total_of_original_publisher_operations"
                    evidence["legacy_definition_url"] = "https://fred.stlouisfed.org/series/RRPONTSYD"
                    evidence["legacy_definition_equivalence"] = "Same daily aggregate Treasury overnight RRP measure, rebuilt from original reported operations; individual historical mirror values may differ."
                evidence["publication_time_policy"] = "unknown; effective dates and operation revision clocks are not first-publication receipts"
            if provider == "boe":
                evidence["attribution"] = "SONIA and/or SONIA Compounded Index data licensed under the Open Government Licence v3.0 and copyright the Governor and Company of the Bank of England. The trade marks “Bank of England” and “SONIA” are registered trade marks of the Bank of England."
                evidence["required_notices"] = ["Open Government Licence v3.0: https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
                                                "Database SONIA is freely available by 10am on the business day after first publication; this capture grants no real-time benchmark feed entitlement."]
                evidence["publication_time_policy"] = "unknown; reference date is not the delayed Database release timestamp"
            spec = ALL_SERIES[m]
            points = pd.Series([r[1] for r in rows], index=pd.DatetimeIndex([r[0] for r in rows]), dtype=float)
            out[m] = StatisticsSeries(m, c["source"], c["remote_id"], spec.label, c["unit"], spec.freq,
                                      capture["fetched_at"], points, evidence)
    except (KeyError, ValueError, TypeError, OverflowError):
        return {}
    return out


def load_many(mnemonics: list[str]) -> dict[str, StatisticsSeries]:
    out = {}
    for group in {CONTRACTS[m]["group"] for m in mnemonics if m in CONTRACTS}:
        out.update({m: s for m, s in decode(group, _load(group)).items() if m in mnemonics})
    return out


def parse_ecb(m: str, payload: bytes) -> tuple[list, dict]:
    rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
    if not rows or len(rows) > MAX_POINTS:
        raise ValueError("ECB row bound")
    c = CONTRACTS[m]
    points = []
    for row in rows:
        if any(row.get(k) != v for k, v in c["dimensions"].items()) or row.get("OBS_STATUS") != "A":
            raise ValueError("ECB source dimensions or status changed")
        period = row["TIME_PERIOD"]
        if m == "ECB_ASSETS":
            match = re.fullmatch(r"(\d{4})-W(\d{2})", period)
            if not match:
                raise ValueError("ECB weekly period changed")
            day = date.fromisocalendar(int(match[1]), int(match[2]), 5).isoformat()
        else:
            day = period
        points.append([day, float(row["OBS_VALUE"])])
    return _rows(m, points), {}


def parse_nyfed_rates(m: str, payload: bytes) -> tuple[list, dict]:
    entries = json.loads(payload)["refRates"]
    if m not in {"SOFR", "EFFR"} or not isinstance(entries, list):
        raise ValueError("NYFed reference-rate identity changed")
    points, revisions = [], {}
    for row in entries:
        if row.get("type") != m or type(row.get("percentRate")) not in (int, float):
            raise ValueError("NYFed rate identity or percent unit changed")
        day = row["effectiveDate"]
        points.append([day, row["percentRate"]])
        # The indicator is not a timestamp or a reconstructed historical vintage.
        if row.get("revisionIndicator"):
            revisions[day] = row["revisionIndicator"]
    return _rows(m, points), {"revision_indicators": revisions}


def parse_nyfed_rrp(payload: bytes) -> dict[str, tuple[list, dict]]:
    entries = json.loads(payload)["repo"]["operations"]
    if not isinstance(entries, list):
        raise ValueError("NYFed operations shape changed")
    totals, revisions, operation_ids, seen_ids = {}, {}, {}, set()
    for row in entries:
        if (row.get("operationType") != "Reverse Repo" or row.get("term") != "Overnight"
                or row.get("operationMethod") != "Fixed Rate" or row.get("auctionStatus") != "Results"
                or row.get("settlementType") != "Same Day"):
            raise ValueError("NYFed RRP definition changed")
        if not row.get("operationId") or row["operationId"] in seen_ids:
            raise ValueError("duplicate NYFed operation identity")
        seen_ids.add(row["operationId"])
        day = row["operationDate"]
        settlement, maturity = date.fromisoformat(row["settlementDate"]), date.fromisoformat(row["maturityDate"])
        if settlement.isoformat() != day or not 1 <= (maturity - settlement).days <= 5:
            raise ValueError("NYFed overnight settlement changed")
        treasury = [x for x in row["details"] if x.get("securityType") == "Treasury"]
        if len(treasury) != 1 or type(treasury[0].get("amtAccepted")) not in (int, float):
            raise ValueError("NYFed Treasury amount or USD unit changed")
        amount = treasury[0]["amtAccepted"]
        if amount < 0:
            raise ValueError("negative RRP amount")
        totals[day] = totals.get(day, 0) + amount
        operation_ids.setdefault(day, []).append(row["operationId"])
        revised = row.get("lastUpdated")
        if revised:
            datetime.strptime(revised, "%Y-%m-%d %H:%M:%S")
            revisions[row["operationId"]] = revised
    points = [[day, amount / 1_000_000_000] for day, amount in totals.items()]
    return {m: (_rows(m, points), {"source_revision_times": revisions,
                                  "source_operation_ids": operation_ids,
                                  "source_revision_time_policy": "lastUpdated verbatim; response does not supply timezone; not first-publication time"})
            for m in ("RRPONTSYD", "RRP_LONG")}


def parse_boe_sonia(payload: bytes) -> tuple[list, dict]:
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    if reader.fieldnames != ["DATE", "IUDSOIA"]:
        raise ValueError("BoE SONIA daily rate identity changed")
    points = []
    for row in reader:
        if None in row or not row["IUDSOIA"]:
            raise ValueError("BoE SONIA missing or malformed observation")
        day = datetime.strptime(row["DATE"], "%d %b %Y").date().isoformat()
        points.append([day, float(row["IUDSOIA"])])
    return _rows("SONIA", points), {}


def parse_bls(payload: bytes, first: int, last: int) -> dict[str, tuple[list, dict]]:
    data = json.loads(payload)
    if data.get("status") != "REQUEST_SUCCEEDED" or data.get("message"):
        raise ValueError("BLS request incomplete or limited")
    entries = data["Results"]["series"]
    targets = {c["remote_id"]: m for m, c in CONTRACTS.items() if c["provider"] == "bls"}
    if len(entries) != len(targets) or {s["seriesID"] for s in entries} != set(targets):
        raise ValueError("BLS identity changed")
    out = {}
    for series in entries:
        points, missing_notes = [], []
        for row in series["data"]:
            year, period = int(row["year"]), row["period"]
            if not first <= year <= last:
                raise ValueError("BLS response outside request period")
            if period == "M13":  # annual average is not a monthly observation
                continue
            if not re.fullmatch(r"M(0[1-9]|1[0-2])", period):
                raise ValueError("BLS monthly period changed")
            reference_date = date(year, int(period[1:]), 1).isoformat()
            if row["value"] == "-":
                notes = [n for n in row.get("footnotes", []) if n.get("code") == "X" and n.get("text")]
                if not notes:
                    raise ValueError("BLS missing value lacks publisher explanation")
                value = None
                missing_notes.extend({"reference_date": reference_date, "code": n["code"], "reason": n["text"]} for n in notes)
            else:
                value = float(row["value"])
            points.append([reference_date, value])
        m = targets[series["seriesID"]]
        out[m] = (_rows(m, points), {"missing_observation_notes": missing_notes})
    return out


def parse_eia(m: str, payload: bytes) -> tuple[list, dict]:
    from seiche.sources.eia_petroleum import _TableParser
    text = payload.decode("utf-8-sig")
    title = re.findall(r"<title>(.*?)</title>", text, re.S | re.I)
    if len(title) != 1 or " ".join(title[0].split()) != CONTRACTS[m]["title"]:
        raise ValueError("EIA inventory identity or units changed")
    publication = re.findall(r">Release Date:\s*(\d{1,2}/\d{1,2}/\d{4})", text)
    if len(publication) != 1:
        raise ValueError("EIA publication date missing")
    parser = _TableParser()
    parser.feed(text)
    points = []
    for cells in parser.rows:
        if not cells or not re.fullmatch(r"\d{4}-[A-Z][a-z]{2}", cells[0]):
            continue
        if len(cells) != 11:
            raise ValueError("EIA inventory row shape changed")
        year = int(cells[0][:4])
        for index in range(1, 11, 2):
            raw_day, value = cells[index].strip(), cells[index + 1].replace(",", "").strip()
            if not raw_day and not value:
                continue
            if not re.fullmatch(r"\d{2}/\d{2}", raw_day) or not value or value in {"-", "--", "NA", "W"}:
                raise ValueError("EIA missing or invalid dated inventory value")
            month, day = map(int, raw_day.split("/"))
            points.append([date(year, month, day).isoformat(), float(value)])
    if len(points) < 52:
        raise ValueError("EIA inventory history incomplete")
    return _rows(m, points), {"source_publication_date": datetime.strptime(publication[0], "%m/%d/%Y").date().isoformat()}


def parse_bea(payload: bytes) -> tuple[list, dict]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        if len(archive.infolist()) > 512 or len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError("BEA workbook member bound")
        def xml(name):
            info = archive.getinfo(name)
            if info.flag_bits & 1 or not 0 < info.file_size <= 20_000_000:
                raise ValueError("BEA worksheet bound")
            raw = archive.read(info)
            if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                raise ValueError("BEA XML declarations forbidden")
            return ET.fromstring(raw)
        workbook = xml("xl/workbook.xml")
        sheets = [x for x in workbook.iter() if x.tag.endswith("}sheet") and x.attrib.get("name") == "T10105-Q"]
        if len(sheets) != 1:
            raise ValueError("BEA GDP quarterly worksheet missing")
        rid = sheets[0].attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
        relations = [x for x in xml("xl/_rels/workbook.xml.rels") if x.attrib.get("Id") == rid]
        if len(relations) != 1 or relations[0].attrib.get("TargetMode") == "External":
            raise ValueError("BEA worksheet relation changed")
        target = posixpath.normpath("xl/" + relations[0].attrib["Target"])
        if not re.fullmatch(r"xl/worksheets/sheet\d+\.xml", target):
            raise ValueError("BEA worksheet path changed")
        shared = ["".join(x.itertext()) for x in xml("xl/sharedStrings.xml")]
        cells = {}
        for cell in xml(target).iter():
            if not cell.tag.endswith("}c"):
                continue
            value = next((x.text for x in cell if x.tag.endswith("}v")), None)
            if value is not None:
                cells[cell.attrib["r"]] = shared[int(value)] if cell.attrib.get("t") == "s" else value
    if cells.get("A1") != "Table 1.1.5. Gross Domestic Product" or cells.get("A2") != "[Millions of dollars] Seasonally adjusted at annual rates" or cells.get("C9") != "A191RC" or cells.get("B9", "").strip() != "Gross domestic product" or cells.get("A9") != "1":
        raise ValueError("BEA GDP definition or units changed")
    points = []
    for location, period in cells.items():
        if re.fullmatch(r"[A-Z]+8", location) and re.fullmatch(r"\d{4}Q[1-4]", period):
            year, quarter = int(period[:4]), int(period[-1])
            value = float(cells[location[:-1] + "9"]) / 1000
            points.append([date(year, 3 * quarter - 2, 1).isoformat(), value])
    publication = datetime.strptime(cells["A5"], "Data published %B %d, %Y").date().isoformat()
    return _rows("GDP", points), {"source_publication_date": publication}


async def _download(client, url: str, request: dict | None = None) -> tuple[bytes, dict]:
    async with _network, asyncio.timeout(60):
        async with client.stream("POST" if request else "GET", url, json=request, follow_redirects=False,
                                 timeout=45, headers={"Accept-Encoding": "identity", "User-Agent": "Seiche-Original-Publisher-Research/1 contact mrinal@liquilens.in"}) as response:
            response.raise_for_status()
            if response.status_code != 200 or str(response.url) != url:
                raise ValueError("publisher HTTP status or source changed")
            if response.headers.get("content-encoding", "").lower().strip() not in {"", "identity"}:
                raise ValueError("publisher transport encoding changed")
            length = response.headers.get("content-length")
            if length is not None and not 0 < int(length) <= MAX_BYTES:
                raise ValueError("publisher response size exceeded")
            payload = bytearray()
            async for chunk in response.aiter_bytes():
                payload.extend(chunk)
                if len(payload) > MAX_BYTES:
                    raise ValueError("publisher response size exceeded")
    body = bytes(payload)
    return body, {"source_url": url, "request": request, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body), "retrieved_at": utcnow_iso()}


async def fetch_many(client, mnemonics: list[str], faults: list[dict] | None = None) -> dict[str, StatisticsSeries]:
    out = {}
    async def one(group):
        members = [m for m, c in CONTRACTS.items() if c["group"] == group]
        provider = CONTRACTS[members[0]]["provider"]
        async with _locks[group]:
            cached = await run_store(load_many, members)
            ttl = 1440 if provider in {"bls", "bea"} else min(ALL_SERIES[m].ttl_minutes for m in members)
            if len(cached) != len(members) or any(datetime.now(UTC) - datetime.fromisoformat(s.fetched_at) >= timedelta(minutes=ttl) for s in cached.values()):
                try:
                    documents, parsed = [], {}
                    if provider == "bls":
                        year = datetime.now(UTC).year
                        first = min(int(ALL_SERIES[m].start[:4]) for m in members)
                        if not 0 <= year - first < 40:
                            raise ValueError("BLS request window requires contract review")
                        for start in range(first, year + 1, 10):
                            end = min(start + 9, year)
                            request = {"seriesid": [CONTRACTS[m]["remote_id"] for m in members], "startyear": str(start), "endyear": str(end)}
                            body, document = await _download(client, BLS_URL, request)
                            documents.append(document)
                            for m, (rows, extra) in parse_bls(body, start, end).items():
                                accumulated, metadata = parsed.setdefault(m, ([], {"missing_observation_notes": []}))
                                accumulated.extend(rows)
                                metadata["missing_observation_notes"].extend(extra["missing_observation_notes"])
                    else:
                        m = members[0]
                        body, document = await _download(client, source_url(m))
                        documents.append(document)
                        if provider == "ecb":
                            parsed[m] = parse_ecb(m, body)
                        elif provider == "eia":
                            parsed[m] = parse_eia(m, body)
                        elif provider == "nyfed":
                            if group == "rrp":
                                parsed = parse_nyfed_rrp(body)
                            else:
                                parsed[m] = parse_nyfed_rates(m, body)
                        elif provider == "boe":
                            parsed[m] = parse_boe_sonia(body)
                        else:
                            parsed[m] = await asyncio.to_thread(parse_bea, body)
                    capture = make_capture(group, parsed, documents, utcnow_iso())
                    await run_store(store.save_blob, _key(group), capture)
                    cached = decode(group, capture)
                except Exception as exc:
                    if faults is not None:
                        faults.append({"source": provider + "_original", "group": group,
                                       "reason": "original_publisher_refresh_failed", "refresh_status": "failed",
                                       "cache_used": bool(cached), "detail": sanitize_fault(exc, status="FAILED")})
            out.update({m: s for m, s in cached.items() if m in mnemonics})
    await asyncio.gather(*(one(g) for g in sorted({CONTRACTS[m]["group"] for m in mnemonics if m in CONTRACTS})))
    return out
