"""Treasury FiscalData collector (keyless JSON).

- Daily TGA opening balance (the Daily Treasury Statement): the single biggest
  reserve-drain lever now that ON RRP is empty.
- Auction results (bid-to-cover, dealer/indirect takedown): Auction Digestion.
- Upcoming auctions: settlement-calendar overlay for Liquidity Weather.
"""

from __future__ import annotations

import httpx
import pandas as pd

from seiche import store
from seiche.config import AUCTIONS_START, FISCAL_TTL_MIN, TGA_START, USER_AGENT
from seiche.sources.base import SourceFault, utcnow_iso

BASE = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"


async def _get_all_pages(client: httpx.AsyncClient, path: str, params: dict, max_pages: int = 12) -> list[dict]:
    out: list[dict] = []
    page = 1
    while page <= max_pages:
        p = dict(params)
        p["page[number]"] = page
        p["page[size]"] = 1000
        r = await client.get(f"{BASE}{path}", params=p, headers={"User-Agent": USER_AGENT}, timeout=40)
        r.raise_for_status()
        j = r.json()
        out.extend(j.get("data", []))
        total_pages = int(j.get("meta", {}).get("total-pages", 1))
        if page >= total_pages:
            break
        page += 1
    return out


async def fetch_tga_daily(client: httpx.AsyncClient, start: str = TGA_START) -> dict:
    """Daily TGA opening balance, $B."""
    key = "fiscal_tga_daily"
    cached = store.load_blob(key, FISCAL_TTL_MIN)
    if cached is None:
        try:
            # Pre-2021 the DTS labels the TGA row "Federal Reserve Account"
            # (verified live 2026-07-07) — all three labels are needed for a
            # continuous 2019+ history.
            rows = await _get_all_pages(
                client,
                "/v1/accounting/dts/operating_cash_balance",
                {
                    "filter": f"record_date:gte:{start},account_type:in:(Treasury General Account (TGA) Opening Balance,Treasury General Account (TGA),Federal Reserve Account)",
                    "fields": "record_date,account_type,open_today_bal",
                    "sort": "record_date",
                },
            )
            cached = {"fetched_at": utcnow_iso(), "rows": rows}
            store.save_blob(key, cached)
        except Exception as exc:
            cached = store.load_blob(key)
            if cached is None:
                raise SourceFault("fiscaldata", f"TGA: {exc}") from exc
    df = pd.DataFrame(cached["rows"])
    if df.empty:
        return {"fetched_at": cached["fetched_at"], "tga": pd.Series(dtype=float)}
    df["record_date"] = pd.to_datetime(df["record_date"])
    df["open_today_bal"] = pd.to_numeric(df["open_today_bal"], errors="coerce") / 1000.0  # $M -> $B
    # One row per date, preferring the explicit opening-balance label when
    # several account_type variants coexist around the 2021 format change.
    priority = {
        "Treasury General Account (TGA) Opening Balance": 0,
        "Treasury General Account (TGA)": 1,
        "Federal Reserve Account": 2,
    }
    df["prio"] = df["account_type"].map(priority).fillna(9)
    tga = (
        df.dropna(subset=["open_today_bal"])
        .sort_values(["record_date", "prio"])
        .drop_duplicates("record_date")
        .set_index("record_date")["open_today_bal"]
        .sort_index()
    )
    return {"fetched_at": cached["fetched_at"], "tga": tga}


async def fetch_auctions(client: httpx.AsyncClient, start: str = AUCTIONS_START) -> dict:
    """Historical auction results for notes/bonds/bills with allocation detail."""
    key = "fiscal_auctions"
    cached = store.load_blob(key, FISCAL_TTL_MIN)
    if cached is None:
        try:
            rows = await _get_all_pages(
                client,
                "/v1/accounting/od/auctions_query",
                {
                    "filter": f"auction_date:gte:{start}",
                    "fields": ",".join([
                        "cusip", "security_type", "security_term", "auction_date",
                        "issue_date", "maturity_date", "offering_amt", "total_accepted",
                        "bid_to_cover_ratio", "primary_dealer_accepted",
                        "direct_bidder_accepted", "indirect_bidder_accepted",
                        "high_yield", "high_discnt_rate",
                    ]),
                    "sort": "auction_date",
                },
            )
            cached = {"fetched_at": utcnow_iso(), "rows": rows}
            store.save_blob(key, cached)
        except Exception as exc:
            cached = store.load_blob(key)
            if cached is None:
                raise SourceFault("fiscaldata", f"auctions: {exc}") from exc
    df = pd.DataFrame(cached["rows"])
    return {"fetched_at": cached["fetched_at"], "auctions": df}


async def fetch_upcoming_auctions(client: httpx.AsyncClient) -> dict:
    key = "fiscal_upcoming"
    cached = store.load_blob(key, FISCAL_TTL_MIN)
    if cached is None:
        try:
            rows = await _get_all_pages(
                client, "/v1/accounting/od/upcoming_auctions", {"sort": "issue_date"}, max_pages=2
            )
            cached = {"fetched_at": utcnow_iso(), "rows": rows}
            store.save_blob(key, cached)
        except Exception as exc:
            cached = store.load_blob(key)
            if cached is None:
                raise SourceFault("fiscaldata", f"upcoming auctions: {exc}") from exc
    return {"fetched_at": cached["fetched_at"], "upcoming": pd.DataFrame(cached["rows"])}
