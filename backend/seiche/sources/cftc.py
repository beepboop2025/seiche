"""CFTC Socrata collector: Traders in Financial Futures (TFF), futures-only.

Leveraged-fund short positions in UST futures are the public fingerprint of
the basis/RV complex; asset-manager longs are the other side of the trade.
Weekly (Tuesday positions, published Friday) — honest T+3 provenance.
"""

from __future__ import annotations

import asyncio

import httpx
import pandas as pd

from seiche import store
from seiche.config import (
    BALLAST_CONTRACTS,
    BALLAST_CFTC_RELEASE_LAG_DAYS,
    CFTC_START,
    CFTC_TTL_MIN,
    CROWD_EXTRA_CONTRACTS,
    DISAGG_FUTURES_DATASET,
    TFF_DATASET,
    USER_AGENT,
    UST_CONTRACTS,
)
from seiche.sources.base import SourceFault, utcnow_iso

BASE = f"https://publicreporting.cftc.gov/resource/{TFF_DATASET}.json"
DISAGG_BASE = (
    f"https://publicreporting.cftc.gov/resource/{DISAGG_FUTURES_DATASET}.json"
)

# Socrata occasionally drops or times out an otherwise valid cold-CI query.
# Keep the two CFTC datasets from competing with each other, and retry only
# transport failures, throttling, and server errors. Query/schema errors stay
# fail-loud on the first attempt.
_RETRY_DELAYS_S = (1.0, 3.0)
_CLIENT_SEMAPHORE_ATTR = "_seiche_cftc_request_semaphore"

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

DISAGG_FIELDS = [
    "report_date_as_yyyy_mm_dd",
    "market_and_exchange_names",
    "contract_market_name",
    "cftc_contract_market_code",
    "commodity_name",
    "open_interest_all",
    "prod_merc_positions_long",
    "prod_merc_positions_short",
    "swap_positions_long_all",
    "swap__positions_short_all",
    "m_money_positions_long_all",
    "m_money_positions_short_all",
    "other_rept_positions_long",
    "other_rept_positions_short",
    "traders_tot_all",
    "conc_gross_le_4_tdr_long",
    "conc_gross_le_4_tdr_short",
    "conc_gross_le_8_tdr_long",
    "conc_gross_le_8_tdr_short",
]

_BALLAST_BY_CODE = {
    str(spec["cftc_code"]): key for key, spec in BALLAST_CONTRACTS.items()
}


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return False


def _exception_detail(exc: Exception) -> str:
    """Never let exceptions with an empty ``str()`` erase the diagnosis."""

    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def _request_semaphore(client: httpx.AsyncClient) -> asyncio.Semaphore:
    """Return a limiter whose lifetime cannot outlive the client's event loop."""

    semaphore = getattr(client, _CLIENT_SEMAPHORE_ATTR, None)
    if semaphore is None:
        semaphore = asyncio.Semaphore(1)
        setattr(client, _CLIENT_SEMAPHORE_ATTR, semaphore)
    return semaphore


async def _fetch_rows(
    client: httpx.AsyncClient, url: str, params: dict[str, object]
) -> list[dict]:
    last_exc: Exception | None = None
    async with _request_semaphore(client):
        for attempt in range(len(_RETRY_DELAYS_S) + 1):
            try:
                response = await client.get(
                    url,
                    params=params,
                    headers={"User-Agent": USER_AGENT},
                    timeout=60,
                )
                response.raise_for_status()
                rows = response.json()
                if not isinstance(rows, list):
                    raise ValueError("CFTC response must be a JSON array")
                return rows
            except Exception as exc:
                last_exc = exc
                if attempt >= len(_RETRY_DELAYS_S) or not _retryable(exc):
                    raise
                await asyncio.sleep(_RETRY_DELAYS_S[attempt])
    raise last_exc or RuntimeError("CFTC request failed without an exception")


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
            rows = await _fetch_rows(client, BASE, params)
            cached = {"fetched_at": utcnow_iso(), "rows": rows}
            store.save_blob(key, cached)
        except Exception as exc:
            cached = store.load_blob(key)
            if cached is None:
                raise SourceFault("cftc", f"TFF: {_exception_detail(exc)}") from exc
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


async def fetch_disaggregated_commodities(
    client: httpx.AsyncClient, start: str = CFTC_START
) -> dict:
    """Fetch futures-only physical-commodity positioning for Ballast.

    Stable CFTC contract codes select the canonical WTI and Henry Hub rows;
    display-name matching would silently drift as exchanges rename products.
    """

    key = "cftc_disagg_ballast"
    cached = store.load_blob(key, CFTC_TTL_MIN)
    if cached is None:
        try:
            code_filter = " OR ".join(
                f"cftc_contract_market_code = '{code}'"
                for code in sorted(_BALLAST_BY_CODE)
            )
            params = {
                "$select": ",".join(DISAGG_FIELDS),
                "$where": (
                    f"report_date_as_yyyy_mm_dd >= '{start}T00:00:00.000' "
                    f"AND ({code_filter})"
                ),
                "$limit": 50000,
            }
            rows = await _fetch_rows(client, DISAGG_BASE, params)
            cached = {"fetched_at": utcnow_iso(), "rows": rows}
            store.save_blob(key, cached)
        except Exception as exc:
            cached = store.load_blob(key)
            if cached is None:
                raise SourceFault(
                    "cftc",
                    f"Disaggregated commodities: {_exception_detail(exc)}",
                ) from exc

    frame = pd.DataFrame(cached["rows"])
    if frame.empty:
        return {"fetched_at": cached["fetched_at"], "positions": frame}
    frame["contract"] = frame["cftc_contract_market_code"].map(_BALLAST_BY_CODE)
    frame = frame.dropna(subset=["contract"])
    frame["date"] = pd.to_datetime(frame["report_date_as_yyyy_mm_dd"])
    frame["available_date"] = frame["date"] + pd.Timedelta(
        days=BALLAST_CFTC_RELEASE_LAG_DAYS
    )
    for column in DISAGG_FIELDS[5:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = (
        frame.sort_values("open_interest_all", ascending=False)
        .drop_duplicates(["date", "contract"])
        .sort_values(["date", "contract"])
        .reset_index(drop=True)
    )
    return {"fetched_at": cached["fetched_at"], "positions": frame}
