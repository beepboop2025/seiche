"""MSPD marketable-maturity collector (FiscalData, keyless JSON).

Why a second Treasury feed: the auctions_query history starts at
AUCTIONS_START (2018), so any coupon issued before that window is invisible
to a maturity schedule built from auction results alone. A 10y note issued
in 2016 or a 30y bond issued in 1996 maturing on the mid-month refunding
date is exactly the money the Supply Desk table must not drop. The Monthly
Statement of the Public Debt (table 3, marketable detail) carries every
outstanding CUSIP with its maturity date and outstanding amount, so it
backfills the pre-window coupons.

Monthly publication: amounts are as of the latest month end. Fine for old
coupons (their outstanding stock does not move); NOT fine for recent bills,
which is why the engine only takes pre-window issues from this frame.
"""

from __future__ import annotations

import httpx
import pandas as pd

from seiche import store
from seiche.config import USER_AGENT
from seiche.sources.base import SourceFault, utcnow_iso

# Same FiscalData plumbing as the auction collectors.
from seiche.sources.fiscaldata import BASE, _get_all_pages

MSPD_TTL_MIN = 24 * 60  # monthly dataset: one refresh a day is generous

_FIELDS = ",".join([
    "record_date", "security_type_desc", "security_class1_desc",
    "security_class2_desc", "issue_date", "maturity_date",
    "issued_amt", "outstanding_amt",
])


async def fetch_mspd_maturities(client: httpx.AsyncClient, horizon_days: int = 70) -> dict:
    """Latest-month MSPD marketable detail, maturities inside the horizon.

    outstanding_amt is stated once per CUSIP (continuation tranche rows carry
    'null'); amounts are $M. Returned raw: the engine owns unit conversion
    and the pre-window filter.
    """
    key = "fiscal_mspd_maturities"
    cached = store.load_blob(key, MSPD_TTL_MIN)
    if cached is None:
        try:
            # Latest publication month first: the dataset holds every month
            # back to 2002 and only the newest vintage is the current stock.
            r = await client.get(
                f"{BASE}/v1/debt/mspd/mspd_table_3_market",
                params={"sort": "-record_date", "page[size]": 1, "fields": "record_date"},
                headers={"User-Agent": USER_AGENT},
                timeout=40,
            )
            r.raise_for_status()
            data = r.json().get("data", [])
            if not data:
                raise SourceFault("mspd", "empty table 3 response")
            latest = data[0]["record_date"]
            lo = pd.Timestamp(latest).date().isoformat()
            hi = (pd.Timestamp.now().normalize() + pd.Timedelta(days=horizon_days)).date().isoformat()
            rows = await _get_all_pages(
                client,
                "/v1/debt/mspd/mspd_table_3_market",
                {
                    "filter": f"record_date:eq:{latest},maturity_date:gte:{lo},maturity_date:lte:{hi}",
                    "fields": _FIELDS,
                    "sort": "maturity_date",
                },
                max_pages=4,
            )
            cached = {"fetched_at": utcnow_iso(), "rows": rows}
            store.save_blob(key, cached)
        except SourceFault:
            cached = store.load_blob(key)
            if cached is None:
                raise
        except Exception as exc:
            cached = store.load_blob(key)
            if cached is None:
                raise SourceFault("mspd", f"table 3: {exc}") from exc
    return {"fetched_at": cached["fetched_at"], "mspd": pd.DataFrame(cached["rows"])}
