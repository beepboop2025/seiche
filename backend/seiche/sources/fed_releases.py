"""Exact, reviewed Federal Reserve release series, acquired without FRED.

Fixed release ZIPs replace the retiring DDP package builder. Each atomic blob
contains a complete normalized capture, its source hash, and the exact parser
contract. No old observation/cache key is read, relabelled, merged or exported.
Current-vintage history has acquisition knowledge time, never historical PIT.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import sqlite3
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from seiche import store
from seiche.config import ALL_SERIES
from seiche.sources._async_store import run_store
from seiche.sources.base import Series, utcnow_iso

TERMS_URL = "https://www.federalreserve.gov/disclaimer.htm"
ATTRIBUTION = "Source: Board of Governors of the Federal Reserve System (US)."
SCHEMA = "seiche.original-fed-release.v1"
MAX_ARCHIVE_BYTES = 16_000_000
MAX_XML_BYTES = 160_000_000
MAX_POINTS = 50_000
CONTRACTS = json.loads(Path(__file__).with_name("fed_release_contracts.json").read_text())
URLS = {
    r: f"https://www.federalreserve.gov/releases/{r}/data/FRB_{n}_xml.zip"
    for r, n in (("h10", "h10"), ("h15", "h15"), ("h41", "h41"),
                 ("cp", "CP"), ("prates", "PRATES"), ("g17", "g17"))
}
_locks = {r: asyncio.Lock() for r in URLS}
_network = asyncio.Semaphore(2)
_parsing = asyncio.Semaphore(2)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def contract(mnemonic: str) -> dict:
    selected = CONTRACTS[mnemonic]
    spec = ALL_SERIES[mnemonic]
    return {
        **selected, "mnemonic": mnemonic, "legacy_remote_id": spec.remote_id,
        "unit": spec.unit, "freq": spec.freq, "start": spec.start,
        "source": "fed_" + selected["release"],
        "source_url": URLS[selected["release"]], "terms_url": TERMS_URL,
        "rights": "public_domain_with_attribution", "terms_reviewed_at": "2026-11-06",
        "scope": "Only these Board-published statistics; no underlying third-party transaction data or logos.",
    }


def _key(release: str) -> str:
    return f"original-publisher:fed:{release}:v1"


@dataclass
class FedSeries(Series):
    evidence: dict

    def provenance(self) -> dict:
        return {**super().provenance(), **self.evidence}

    def tail_records(self, n: int = 500) -> list[list]:
        return [[idx.date().isoformat(), None if pd.isna(value) else round(float(value), 6)]
                for idx, value in self.points.tail(n).items()]


def _series(m: str, record: dict, capture: dict) -> FedSeries:
    expected = contract(m)
    if record.get("contract") != expected or record.get("contract_sha256") != _digest(expected):
        raise ValueError("original publisher contract changed")
    rows = record.get("points")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_POINTS or record.get("points_sha256") != _digest(rows):
        raise ValueError("original publisher points invalid")
    last = None
    for row in rows:
        if not isinstance(row, list) or len(row) != 2:
            raise ValueError("original publisher point shape changed")
        day = date.fromisoformat(row[0])
        if row[0] != day.isoformat() or day < date.fromisoformat(expected["start"]) or (last and day <= last):
            raise ValueError("original publisher date invalid")
        if row[1] is not None and (type(row[1]) not in (int, float) or not math.isfinite(row[1])):
            raise ValueError("original publisher value invalid")
        last = day
    raw_hash = capture.get("raw_sha256", "")
    if len(raw_hash) != 64 or any(c not in "0123456789abcdef" for c in raw_hash):
        raise ValueError("original publisher capture hash invalid")
    stamp = datetime.fromisoformat(capture["fetched_at"])
    if stamp.tzinfo is None or stamp.utcoffset() is None or stamp > datetime.now(UTC) + timedelta(minutes=5):
        raise ValueError("original publisher capture clock invalid")
    if last > stamp.date():
        raise ValueError("original publisher future observation")
    evidence = {
        "source_url": expected["source_url"], "terms_url": TERMS_URL,
        "attribution": ATTRIBUTION, "redistribution_status": "allowed",
        "rights_basis": expected["rights"], "rights_scope": expected["scope"],
        "terms_reviewed_at": expected["terms_reviewed_at"],
        "raw_capture_sha256": raw_hash, "contract_sha256": record["contract_sha256"],
        "normalized_points_sha256": record["points_sha256"],
        "publisher_dimensions": expected["dimensions"],
        "observation_date_normalization": expected["date_normalization"],
        "knowledge_time": capture["fetched_at"], "source_publication_time": None,
        "publication_time_policy": "unknown", "historical_point_in_time": False,
        "vintage": "publisher current vintage acquired at knowledge_time",
        "missing_observations": sum(row[1] is None for row in rows),
    }
    if m.startswith("DXY_"):
        evidence["unit_definition_url"] = "https://www.federalreserve.gov/releases/h10/h10_technical_qa.htm"
        evidence["unit_note"] = "Jan 2006 monthly average=100 per publisher Q&A; legacy XML UNIT label retained above. No rescaling applied."
    if expected["release"] == "cp":
        evidence["definition_note"] = "Board-published 90-day AA commercial-paper rate; missing prints remain missing. Not the underlying DTCC trades."
    points = pd.Series([r[1] for r in rows], index=pd.DatetimeIndex([r[0] for r in rows]), dtype=float)
    spec = ALL_SERIES[m]
    return FedSeries(m, expected["source"], expected["dimensions"]["SERIES_NAME"],
                     spec.label, expected["unit"], expected["freq"], capture["fetched_at"], points, evidence)


def decode_capture(release: str, capture: object, mnemonics: list[str]) -> dict[str, FedSeries]:
    if not isinstance(capture, dict) or capture.get("schema") != SCHEMA or capture.get("source_url") != URLS[release]:
        return {}
    out = {}
    for m in mnemonics:
        if m not in CONTRACTS or CONTRACTS[m]["release"] != release:
            continue
        try:
            out[m] = _series(m, capture["series"][m], capture)
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
    return out


def load_many(mnemonics: list[str]) -> dict[str, FedSeries]:
    """Read only the isolated source-bound capture; never consult FRED rows."""
    out = {}
    for release in sorted({CONTRACTS[m]["release"] for m in mnemonics if m in CONTRACTS}):
        out.update(decode_capture(release, _load_capture(release), mnemonics))
    return out


def _load_capture(release: str) -> object:
    # Catalog/export reads must not initialize a database on a cold install.
    database = Path(store.DB_PATH)
    if not database.is_file():
        return None
    try:
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as conn:
            row = conn.execute("SELECT payload FROM blobs WHERE key=?", (_key(release),)).fetchone()
        return json.loads(row[0]) if row else None
    except (sqlite3.Error, ValueError, TypeError):
        return None


def load_series(mnemonic: str) -> FedSeries | None:
    return load_many([mnemonic]).get(mnemonic)


def parse_capture(release: str, payload: bytes, fetched_at: str) -> dict:
    """Fail closed on dimensional changes, conflicting duplicates or bad values."""
    if release not in URLS or not 0 < len(payload) <= MAX_ARCHIVE_BYTES:
        raise ValueError("unrecognized or oversized Fed release")
    wanted = {m: contract(m) for m, c in CONTRACTS.items() if c["release"] == release}
    by_id = {}
    for m, c in wanted.items():
        by_id.setdefault(c["dimensions"]["SERIES_NAME"], []).append(m)
    member_name = release.upper() + "_data.xml"
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = archive.infolist()
        matches = [m for m in members if m.filename == member_name]
        if len(members) > 32 or len(matches) != 1:
            raise ValueError("Fed release XML member changed")
        member = matches[0]
        if member.flag_bits & 1 or not 0 < member.file_size <= MAX_XML_BYTES:
            raise ValueError("Fed release XML bound exceeded")
        xml = archive.read(member)
    text = xml.decode("utf-8-sig")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("Fed release XML declarations forbidden")
    del text
    records = {}
    for _, node in ET.iterparse(io.BytesIO(xml), events=("end",)):
        if node.tag.rsplit("}", 1)[-1] != "Series":
            continue
        remote_id = node.attrib.get("SERIES_NAME")
        for m in by_id.get(remote_id, []):
            expected = wanted[m]
            if dict(node.attrib) != expected["dimensions"]:
                raise ValueError(f"{m}: Fed publisher dimensions changed")
            rows, seen = [], set()
            for obs in node:
                if obs.tag.rsplit("}", 1)[-1] != "Obs":
                    continue
                raw_day = obs.attrib["TIME_PERIOD"]
                day = date.fromisoformat(raw_day)
                if day.isoformat() != raw_day:
                    raise ValueError(f"{m}: invalid publisher date")
                if expected["date_normalization"] == "month_start":
                    # G.17 uses month-end reference dates; registry engines use
                    # month-start labels. Neither is a publication timestamp.
                    if (day + timedelta(days=1)).month == day.month:
                        raise ValueError("G17 reference date is not month-end")
                    day = day.replace(day=1)
                if day < date.fromisoformat(expected["start"]):
                    continue
                if day in seen:
                    raise ValueError(f"{m}: duplicate observation date")
                seen.add(day)
                status = obs.attrib.get("OBS_STATUS")
                if status == "A":
                    value = float(obs.attrib["OBS_VALUE"])
                    if not math.isfinite(value) or value == -999999:
                        raise ValueError(f"{m}: invalid available observation")
                elif status in {"NA", "ND", "NC"}:
                    # frb_common.xsd CL_OBS_STATUS: unavailable / no data /
                    # not calculable. Their numeric sentinels are not values.
                    value = None
                else:
                    raise ValueError(f"{m}: unreviewed observation status")
                rows.append([day.isoformat(), value])
            rows.sort()
            if not rows or not any(r[1] is not None for r in rows) or len(rows) > MAX_POINTS:
                raise ValueError(f"{m}: empty or excessive source history")
            record = {"contract": expected, "contract_sha256": _digest(expected),
                      "points": rows, "points_sha256": _digest(rows)}
            if m in records and (expected["duplicate_series_policy"] != "identical_only" or record != records[m]):
                raise ValueError(f"{m}: conflicting or unexpected duplicate series")
            records[m] = record
        node.clear()
    if records.keys() != wanted.keys():
        raise ValueError("Fed release lacks a contracted series")
    capture = {"schema": SCHEMA, "source_url": URLS[release], "fetched_at": fetched_at,
               "raw_sha256": hashlib.sha256(payload).hexdigest(), "raw_bytes": len(payload),
               "series": records}
    if len(decode_capture(release, capture, list(wanted))) != len(wanted):
        raise ValueError("Fed release capture validation failed")
    return capture


async def _download(client, release: str) -> bytes:
    async with _network, asyncio.timeout(75):
        async with client.stream("GET", URLS[release], follow_redirects=False, timeout=60,
                                 headers={"Accept-Encoding": "identity", "User-Agent": "Seiche-Original-Publisher-Research/1 contact mrinal@liquilens.in"}) as response:
            if response.status_code != 200 or str(response.url) != URLS[release]:
                raise ValueError(f"Fed {release} HTTP {response.status_code} or source changed")
            if response.headers.get("content-encoding", "").lower().strip() not in {"", "identity"}:
                raise ValueError("Fed transport encoding changed")
            declared = response.headers.get("content-length")
            if declared is not None and not 0 < int(declared) <= MAX_ARCHIVE_BYTES:
                raise ValueError("Fed archive bound exceeded")
            body = bytearray()
            async for chunk in response.aiter_raw():
                body.extend(chunk)
                if len(body) > MAX_ARCHIVE_BYTES:
                    raise ValueError("Fed archive bound exceeded")
    return bytes(body)


async def fetch_many(client, mnemonics: list[str], faults: list[dict] | None = None) -> dict[str, FedSeries]:
    out = {}

    async def one(release):
        group = [m for m, c in CONTRACTS.items() if c["release"] == release]
        async with _locks[release]:
            cached = await run_store(load_many, group)
            ttl = min(ALL_SERIES[m].ttl_minutes for m in group)
            now = datetime.now(UTC)
            from seiche.sources.publication import publication_refresh_due
            fresh = len(cached) == len(group) and all(
                now - datetime.fromisoformat(s.fetched_at) < timedelta(minutes=ttl)
                and not publication_refresh_due("fred", ALL_SERIES[m].remote_id, s.freq, s.asof, s.fetched_at, now=now)
                for m, s in cached.items()
            )
            if not fresh:
                try:
                    payload = await _download(client, release)
                    async with _parsing:
                        capture = await asyncio.to_thread(parse_capture, release, payload, utcnow_iso())
                    await run_store(store.save_blob, _key(release), capture)
                    cached = decode_capture(release, capture, group)
                except Exception as exc:
                    if faults is not None:
                        faults.append({"source": "fed_" + release, "reason": "original_publisher_refresh_failed",
                                       "detail": f"{type(exc).__name__}: source contract or transport failed; original capture clocks retained",
                                       "cache_used": bool(cached), "refresh_status": "failed"})
            out.update({m: s for m, s in cached.items() if m in mnemonics})
    await asyncio.gather(*(one(r) for r in sorted({CONTRACTS[m]["release"] for m in mnemonics if m in CONTRACTS})))
    return out


def main() -> int:
    """Collect only these keyless publishers and write a qualification receipt."""
    import argparse
    import httpx
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()

    async def collect():
        faults = []
        async with httpx.AsyncClient() as client:
            result = await fetch_many(client, list(CONTRACTS), faults)
        return result, faults

    result, faults = asyncio.run(collect())
    receipt = {
        "schema": "seiche.original-publisher-qualification.v1",
        "generated_at": utcnow_iso(), "contracted_series": len(CONTRACTS),
        "available_series": len(result), "faults": faults,
        "series": {m: {**s.provenance(), "first_observation": s.points.index[0].date().isoformat()}
                   for m, s in sorted(result.items())},
        "scope": "Individual series only. Existing mixed-source product gates are unchanged.",
    }
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"contracted": len(CONTRACTS), "available": len(result), "faults": len(faults), "receipt": str(args.receipt)}))
    return 0 if len(result) == len(CONTRACTS) and not faults else 1


if __name__ == "__main__":
    raise SystemExit(main())
