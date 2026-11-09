"""ALT-OFRFSI: the OFR Financial Stress Index, level and contributions.

Publisher: Office of Financial Research (U.S. Department of the Treasury).
Endpoint: one keyless CSV, daily, "current from two business days prior".
Terms (https://www.financialresearch.gov/legal-notices/, quoted 2026-11-09):
"No copyright may be claimed for any work on this website that was created
by a federal employee in the course of his or her duties. However, credit is
requested if you reproduce or copy any such work."

The index is an OFR calculation from market inputs OFR licenses; only the
published index and its contributions are read, never the inputs. The five
category contributions sum to the level. That identity is checked on every
row, so a rescaled or relabelled column fails closed.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from decimal import Decimal

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

SUBSTITUTE_ID = "ALT-OFRFSI"
SOURCE = "ofr_fsi"
PUBLISHER = "Office of Financial Research, U.S. Department of the Treasury"
SOURCE_URL = "https://www.financialresearch.gov/financial-stress-index/data/fsi.csv"
TERMS_URL = "https://www.financialresearch.gov/legal-notices/"
MAX_BYTES = 8_000_000
# Five contributions rounded to three decimals can miss the level by 0.003.
IDENTITY_TOLERANCE = Decimal("0.006")

_INDEX = "index (0 = average stress)"
_POINTS = "index points (contribution to the OFR FSI)"
MEASURES: dict[str, MeasureSpec] = {
    "fsi": MeasureSpec("OFR FSI", _INDEX, "daily", "OFR Financial Stress Index level"),
    "credit": MeasureSpec("Credit", _POINTS, "daily", "credit category contribution"),
    "equity_valuation": MeasureSpec(
        "Equity valuation", _POINTS, "daily", "equity valuation category contribution"
    ),
    "safe_assets": MeasureSpec(
        "Safe assets", _POINTS, "daily", "safe assets category contribution"
    ),
    "funding": MeasureSpec("Funding", _POINTS, "daily", "funding category contribution"),
    "volatility": MeasureSpec(
        "Volatility", _POINTS, "daily", "volatility category contribution"
    ),
    "united_states": MeasureSpec(
        "United States", _POINTS, "daily", "United States regional contribution"
    ),
    "other_advanced_economies": MeasureSpec(
        "Other advanced economies", _POINTS, "daily",
        "other advanced economies regional contribution",
    ),
    "emerging_markets": MeasureSpec(
        "Emerging markets", _POINTS, "daily", "emerging markets regional contribution"
    ),
}
HEADER = ("Date", *(spec.label for spec in MEASURES.values()))
_CATEGORIES = ("credit", "equity_valuation", "safe_assets", "funding", "volatility")
_BOUNDS = ("-50", "100")


async def fetch(
    client: httpx.AsyncClient, *, clock: Clock | None = None
) -> tuple[Acquisition, ...]:
    document = await fetch_fixed(
        client, SOURCE_URL, max_bytes=MAX_BYTES, label="OFR FSI CSV",
        clock=clock, accept="text/csv",
    )
    return (document,)


def parse(documents: Sequence[Acquisition]) -> tuple[SourceObservation, ...]:
    if len(documents) != 1:
        raise ValueError("OFR FSI expects exactly one document")
    document = documents[0]
    require_document(
        document, source_url=SOURCE_URL, max_bytes=MAX_BYTES, label="OFR FSI CSV"
    )
    rows = list(csv.reader(io.StringIO(document.payload.decode("utf-8-sig"))))
    if not rows or tuple(cell.strip() for cell in rows[0]) != HEADER:
        raise ValueError("OFR FSI CSV header changed")
    boundary = acquisition_day(document)
    names = tuple(MEASURES)
    seen = set()
    points: list[SourceObservation] = []
    for raw in rows[1:]:
        if not any(cell.strip() for cell in raw):
            continue
        if len(raw) != len(HEADER):
            raise ValueError("OFR FSI CSV row width changed")
        day = iso_day(raw[0], label="OFR FSI")
        if day in seen:
            raise ValueError(f"OFR FSI date duplicated: {day.isoformat()}")
        seen.add(day)
        values: dict[str, Decimal] = {}
        for name, cell in zip(names, raw[1:], strict=True):
            if not cell.strip():
                continue
            value = parse_decimal(cell, label=f"OFR FSI {name}")
            check_bounds(value, *_BOUNDS, label=f"OFR FSI {name}")
            values[name] = value
        if "fsi" in values and all(name in values for name in _CATEGORIES):
            gap = abs(sum(values[name] for name in _CATEGORIES) - values["fsi"])
            if gap > IDENTITY_TOLERANCE:
                raise ValueError(
                    f"OFR FSI categories no longer sum to the level on {day}"
                )
        if day > boundary:
            continue
        for name, value in values.items():
            points.append(make_observation(
                substitute_id=SUBSTITUTE_ID, measure=name, spec=MEASURES[name],
                event_date=day, period=day.isoformat(), value=value,
                source=SOURCE, publisher=PUBLISHER, source_url=SOURCE_URL,
                terms_url=TERMS_URL, documents=(document,),
                evidence={"source_url": SOURCE_URL, "column": MEASURES[name].label,
                          "date": raw[0].strip(), "value": raw[names.index(name) + 1].strip()},
            ))
    if not points:
        raise ValueError("OFR FSI CSV has no usable observations")
    return tuple(sorted(points, key=lambda item: (item.event_date, item.measure)))
