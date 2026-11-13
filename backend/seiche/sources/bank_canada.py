"""Bounded public Valet reads with pinned identity, units and stable revision evidence."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
from seiche.markets.canada_cad import SERIES_MAP, TERMS
from seiche.sources.canonical import (
    FetchedDocument,
    FunctionalCanonicalAdapter,
    ParsedPoint,
    PublicationTimePolicy,
)
from seiche.sources.rates_sources import _documents

UNITS = {"rate": "Percent", "amount": "Canadian dollars (CAD)", "count": "Number"}


def parse_valet(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    payload = json.loads(document.payload)
    details = payload.get("seriesDetail")
    if (
        payload.get("terms", {}).get("url") != TERMS
        or not isinstance(details, dict)
        or set(details) != set(SERIES_MAP)
    ):
        raise ValueError("Bank of Canada identity or terms changed")
    for remote, (_, _, kind, _) in SERIES_MAP.items():
        d = details[remote]
        if (
            d.get("dateFormat") != "daily"
            or d.get("dimension", {}).get("key") != "d"
            or d.get("metadata", {}).get("unit") != UNITS[kind]
            or d.get("metadata", {}).get("frequency") != "Business daily"
        ):
            raise ValueError("Bank of Canada unit or frequency changed")
    observations = payload.get("observations")
    if not isinstance(observations, list):
        raise ValueError("Bank of Canada observations missing")
    seen = set()
    points = []
    for row in observations:
        event = date.fromisoformat(row["d"])
        if set(row) - {"d"} - set(SERIES_MAP):
            raise ValueError("Bank of Canada undeclared series")
        for remote, (identifier, _, kind, _) in SERIES_MAP.items():
            item = row.get(remote)
            if item is None:
                continue
            if (remote, event) in seen:
                raise ValueError("Bank of Canada repeated instrument date")
            seen.add((remote, event))
            if not isinstance(item, dict) or "v" not in item:
                raise ValueError("Bank of Canada malformed observation")
            raw = item["v"]
            if raw in ("", None, "Bank holiday", "N/A", "NA"):
                continue
            try:
                value = Decimal(str(raw))
            except InvalidOperation as exc:
                raise ValueError("Bank of Canada value not numeric") from exc
            if (
                not value.is_finite()
                or (kind == "rate" and not Decimal(-5) <= value <= Decimal(100))
                or (kind != "rate" and value < 0)
                or (kind == "count" and value != value.to_integral_value())
            ):
                raise ValueError("Bank of Canada value outside bounds")
            evidence = json.dumps(
                {
                    "series": remote,
                    "date": event.isoformat(),
                    "value": str(raw),
                    "unit": UNITS[kind],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            points.append(
                ParsedPoint(
                    identifier,
                    event,
                    value,
                    evidence,
                    publication_time_policy=PublicationTimePolicy.UNKNOWN,
                )
            )
    if not points:
        raise ValueError("Bank of Canada contains no numeric observations")
    return tuple(points)


def build_adapters(*, registry, repository=None, backfill=False, clock=None):
    if not any(p.market_id == "CA-CAD" for p in registry.list()):
        return ()

    async def fetch(client):
        today = (clock() if clock else datetime.now(UTC)).date()
        start = date(1997, 1, 1) if backfill else today - timedelta(days=45)
        url = (
            "https://www.bankofcanada.ca/valet/observations/"
            + ",".join(SERIES_MAP)
            + "/json"
        )
        return await _documents(
            client,
            (
                (
                    "boc_valet",
                    url,
                    {"start_date": start.isoformat(), "end_date": today.isoformat()},
                ),
            ),
        )

    return (
        FunctionalCanonicalAdapter(
            pack=registry.get("CA-CAD"),
            adapter_id="boc_valet",
            source="boc_valet",
            fetcher=fetch,
            parser=parse_valet,
            repository=repository,
            clock=clock,
            timeout_seconds=90,
            historical_backfill=backfill,
        ),
    )
