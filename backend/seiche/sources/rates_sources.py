"""Official rate adapters for the rates and funding research routes.

Parsers pin every identity column the publisher prints (series key, frequency,
unit, unit multiplier, publication confidentiality) and fail closed when one
changes. Row evidence is a stable encoding of the value-bearing fields, so an
unrelated edit to a descriptive column never re-vintages history. A value the
publisher changes later becomes a REVISED vintage through the canonical adapter;
the earlier vintage is kept.
"""
from __future__ import annotations

import csv
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import io
import json

from seiche.domain.observation import QualityState
from seiche.markets.rates_reference import DECLARED_CURVES, ECB_YC_FIRST_DATE, ECB_YC_KEYS, RATE_CURVES
from seiche.sources.canonical import FetchedDocument, FunctionalCanonicalAdapter, ParsedPoint

ECB_DATA_API = "https://data-api.ecb.europa.eu/service/data/"
MAX_DOCUMENT_BYTES = 12 * 1024 * 1024
ROUTINE_WINDOW_DAYS = 45
# Publisher observation status codes accepted for a numeric value. Anything else
# (a break, a forecast, an unknown code) stops the parse instead of guessing.
_ECB_STATUS_QUALITY = {"A": None, "E": QualityState.ESTIMATED, "P": QualityState.PROVISIONAL}
_ECB_YC_REQUIRED = ("KEY", "FREQ", "TIME_PERIOD", "OBS_VALUE", "OBS_STATUS", "OBS_CONF", "UNIT", "UNIT_MULT")
# Plausibility bounds for a euro government spot rate, in percent. A value outside
# them is a unit or identity fault, not a market observation.
ECB_YC_BOUNDS = (Decimal("-5"), Decimal("25"))


def _decimal(raw: str) -> Decimal | None:
    value = (raw or "").strip()
    if value in {"", "NaN", "-", "."}:
        return None
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("ECB observation value is not numeric") from exc
    if not result.is_finite():
        raise ValueError("ECB observation value is not finite")
    return result


def _evidence(fields: dict[str, str]) -> bytes:
    return (json.dumps(fields, sort_keys=True, separators=(",", ":")) + "\n").encode()


def parse_ecb_yield_curve(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    """One ECB YC spot-rate series in SDMX csvdata, percent per annum."""
    instrument_id = document.label
    expected_key = ECB_YC_KEYS.get(instrument_id)
    if expected_key is None:
        raise ValueError("unknown ECB yield curve instrument")
    reader = csv.DictReader(io.StringIO(document.payload.decode("utf-8-sig")))
    if reader.fieldnames is None or any(column not in reader.fieldnames for column in _ECB_YC_REQUIRED):
        raise ValueError("ECB yield curve columns changed")
    points: list[ParsedPoint] = []
    seen: set[date] = set()
    low, high = ECB_YC_BOUNDS
    for row in reader:
        if None in row or any(row.get(column) is None for column in _ECB_YC_REQUIRED):
            raise ValueError("ECB yield curve row is truncated or shifted")
        if row["KEY"] != expected_key or row["FREQ"] != "B":
            raise ValueError("ECB yield curve series key or frequency changed")
        if row["UNIT"] != "PCPA" or row["UNIT_MULT"] != "0":
            raise ValueError("ECB yield curve unit changed")
        if row["OBS_CONF"] != "F":
            raise ValueError("ECB observation is not marked free for publication")
        event = date.fromisoformat(row["TIME_PERIOD"])
        if event in seen:
            raise ValueError("ECB yield curve repeats an observation date")
        seen.add(event)
        value = _decimal(row["OBS_VALUE"])
        status = row["OBS_STATUS"].strip()
        if value is None:
            # A missing value stays missing; it is never filled.
            continue
        if status not in _ECB_STATUS_QUALITY:
            raise ValueError("ECB observation status is not a published value")
        if not low <= value <= high:
            raise ValueError("ECB yield curve value outside its plausibility bounds")
        points.append(ParsedPoint(
            instrument_id, event, value,
            _evidence({"key": row["KEY"], "date": row["TIME_PERIOD"], "value": row["OBS_VALUE"].strip(),
                       "status": status, "conf": row["OBS_CONF"], "unit": row["UNIT"],
                       "unit_mult": row["UNIT_MULT"]}),
            quality=_ECB_STATUS_QUALITY[status],
        ))
    if not points:
        raise ValueError("ECB yield curve contains no numeric observations")
    return tuple(points)


async def _documents(client, requests) -> tuple[FetchedDocument, ...]:
    """Sequential, size-bounded GETs: one series per request, politely paced by order."""
    documents = []
    for label, url, params in requests:
        async with client.stream("GET", url, params=params) as response:
            response.raise_for_status()
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_DOCUMENT_BYTES:
                    raise ValueError("official rate document exceeds its size limit")
                chunks.append(chunk)
            documents.append(FetchedDocument(
                str(response.url),
                response.headers.get("Content-Type", "application/octet-stream").split(";", 1)[0].lower(),
                b"".join(chunks), label,
            ))
    return tuple(documents)


def ecb_yield_curve_requests(start: date):
    for curve in RATE_CURVES:
        if curve.adapter_id != "ecb_yield_curve":
            continue
        for identifier, key in curve.remote_keys:
            dataset, series = key.split(".", 1)
            yield (identifier, f"{ECB_DATA_API}{dataset}/{series}",
                   {"format": "csvdata", "startPeriod": start.isoformat()})


def build_rates_adapters(*, registry, repository=None, backfill=False, clock=None):
    """Adapters for the declared rate sources. Backfill imports the full published history."""

    def today() -> date:
        return (clock() if clock else datetime.now(UTC)).astimezone(UTC).date()

    def yc_start() -> date:
        return date.fromisoformat(ECB_YC_FIRST_DATE) if backfill else today() - timedelta(days=ROUTINE_WINDOW_DAYS)

    async def fetch_ecb_yield_curve(client):
        return await _documents(client, tuple(ecb_yield_curve_requests(yc_start())))

    definitions = {"ecb_yield_curve": (fetch_ecb_yield_curve, parse_ecb_yield_curve)}
    adapters = []
    for pack in registry.list():
        for spec in pack.source_adapters:
            if spec.adapter_id not in definitions:
                continue
            fetcher, parser = definitions[spec.adapter_id]
            adapters.append(FunctionalCanonicalAdapter(
                pack=pack, adapter_id=spec.adapter_id, source=spec.adapter_id,
                fetcher=fetcher, parser=parser, repository=repository, clock=clock,
                timeout_seconds=90, historical_backfill=backfill,
            ))
    return tuple(adapters)


PRODUCTION_KEYS = frozenset({(curve.market_id, curve.adapter_id) for curve in DECLARED_CURVES})
