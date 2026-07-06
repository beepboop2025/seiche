"""CFTC Socrata collector: Traders in Financial Futures (TFF), futures-only.

Leveraged-fund short positions in UST futures are the public fingerprint of
the basis/RV complex; asset-manager longs are the other side of the trade.
Weekly (Tuesday positions, published Friday) — honest T+3 provenance.
"""

from __future__ import annotations

import httpx
import pandas as pd

from seiche import store
from seiche.config import (
    CFTC_START,
    CFTC_TTL_MIN,
    CROWD_EXTRA_CONTRACTS,
    TFF_DATASET,
    USER_AGENT,
    UST_CONTRACTS,
)
from seiche.sources.base import SourceFault, utcnow_iso

BASE = f"https://publicreporting.cftc.gov/resource/{TFF_DATASET}.json"

# NB: the TFF dataset drops the "_all" suffix on positioning fields.
FIELDS = [
    "report_date_as_yyyy_mm_dd",
    "market_and_exchange_names",
    "contract_market_name",
    "open_interest_all",
    "lev_money_positions_long",
    "lev_money_positions_short",
    "asset_mgr_positions_long",
    "asset_mgr_positions_short",
]


def _match_contract(name: str) -> str | None:
    up = (name or "").upper()
    for key in UST_CONTRACTS:
        if key in up:
            return key
    # Crowding-panel extras need EXACT matches: "FED FUNDS" as a substring
    # would also catch hypothetical variants, and "E-MINI S&P 500" must not
    # swallow "MICRO E-MINI S&P 500 INDEX".
    if up.strip() in CROWD_EXTRA_CONTRACTS:
        return up.strip()
    return None


async def fetch_tff_ust(client: httpx.AsyncClient, start: str = CFTC_START) -> dict:
    key = "cftc_tff_ust"
    cached = store.load_blob(key, CFTC_TTL_MIN)
    if cached is None:
        try:
            extra = " OR ".join(
                f"upper(contract_market_name) = '{c}'" for c in CROWD_EXTRA_CONTRACTS
            )
            params = {
                "$select": ",".join(FIELDS),
                "$where": (
                    f"report_date_as_yyyy_mm_dd >= '{start}T00:00:00.000' AND "
                    "(upper(contract_market_name) like '%UST%' OR "
                    f"upper(contract_market_name) like '%TREASURY%' OR {extra})"
                ),
                "$limit": 50000,
            }
            r = await client.get(BASE, params=params, headers={"User-Agent": USER_AGENT}, timeout=60)
            r.raise_for_status()
            cached = {"fetched_at": utcnow_iso(), "rows": r.json()}
            store.save_blob(key, cached)
        except Exception as exc:
            cached = store.load_blob(key)
            if cached is None:
                raise SourceFault("cftc", f"TFF: {exc}") from exc
    df = pd.DataFrame(cached["rows"])
    if df.empty:
        return {"fetched_at": cached["fetched_at"], "tff": df}
    df["contract"] = df["contract_market_name"].map(_match_contract)
    df = df.dropna(subset=["contract"])
    df["date"] = pd.to_datetime(df["report_date_as_yyyy_mm_dd"])
    for c in FIELDS[3:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # Normalize back to the engine-facing "_all" names.
    df = df.rename(
        columns={
            "lev_money_positions_long": "lev_money_positions_long_all",
            "lev_money_positions_short": "lev_money_positions_short_all",
            "asset_mgr_positions_long": "asset_mgr_positions_long_all",
            "asset_mgr_positions_short": "asset_mgr_positions_short_all",
        }
    )
    # A contract can appear under multiple market rows; keep the largest OI row
    # per (date, contract) to avoid double counting.
    df = (
        df.sort_values("open_interest_all", ascending=False)
        .drop_duplicates(["date", "contract"])
        .sort_values("date")
    )
    return {"fetched_at": cached["fetched_at"], "tff": df}
