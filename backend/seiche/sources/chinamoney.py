"""Retired v1 ChinaMoney compatibility surface.

The historical parser remains importable for old fixtures, but the direct
collector cannot make a request or read/write the legacy series cache. CFETS
collection now exists only in the canonical CN-CNY adapter, whose approval
artifact binds the exact endpoints, products, retained fields, evidence file,
and nonpublication scope before each request.
"""

from __future__ import annotations

import httpx
import pandas as pd

from seiche.config import SeriesSpec
from seiche.sources.base import Series, SourceFault

_RETIRED_DETAIL = (
    "legacy direct ChinaMoney collection is retired; use the canonical "
    "CN-CNY cfets_rates adapter with a validated approval artifact"
)


def parse_records(payload: dict, tenor: str) -> pd.Series:
    """Pure parse: ShiborHis records -> daily series for one tenor."""
    recs = payload.get("records")
    if not isinstance(recs, list) or not recs:
        raise ValueError("no records in payload (empty body = throttled, not zero)")
    rows: list[tuple[pd.Timestamp, float]] = []
    for r in recs:
        d, v = r.get("showDateCN"), r.get(tenor)
        if d is None or v in (None, ""):
            continue
        rows.append((pd.Timestamp(str(d)[:10]), float(v)))
    if not rows:
        raise ValueError(f"records carry no '{tenor}' values")
    s = pd.Series(
        [v for _, v in rows],
        index=pd.DatetimeIndex([d for d, _ in rows]),
        dtype=float,
    )
    return s[~s.index.duplicated(keep="last")].sort_index()


async def fetch_series(client: httpx.AsyncClient, spec: SeriesSpec) -> Series:
    del client
    raise SourceFault("chinamoney", f"{spec.remote_id}: {_RETIRED_DETAIL}")


async def fetch_many(
    client: httpx.AsyncClient, mnemonics: list[str], faults: list[dict] | None = None
) -> dict[str, Series]:
    del client
    if faults is not None:
        faults.extend(
            {"source": "chinamoney", "detail": f"{mnemonic}: {_RETIRED_DETAIL}"}
            for mnemonic in mnemonics
        )
    return {}
