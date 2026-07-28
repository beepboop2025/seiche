"""NY Fed Reserve Demand Elasticity collector (keyless, stable medialibrary URL).

The NY Fed publishes RDE monthly (Afonso, Giannone, La Spada, Williams):
the estimated effect, in basis points, of a 1 percent change in total
reserves on the federal funds-IORB spread, with 68 and 95 percent bands.
Updates land around 10:00 ET on the third Thursday of each month.

The interactive's data file sits at a fixed path and, despite the .xlsx
filename, is served as plain CSV (verified 2026-07-28): Date plus the
50th/2.5th/97.5th/16th/84th percentile columns. We parse CSV first and fall
back to a real Excel read in case they ever make the name honest.
"""

from __future__ import annotations

import io

import httpx
import pandas as pd

from seiche import store
from seiche.config import USER_AGENT
from seiche.sources.base import SourceFault, utcnow_iso

DATA_URL = (
    "https://www.newyorkfed.org/medialibrary/Research/Interactives/"
    "Data/elasticity/elasticity-data.xlsx"
)
RDE_TTL_MIN = 12 * 60  # monthly release; twice a day is already generous

COLUMNS = ["median", "p2_5", "p16", "p84", "p97_5"]


def _shape(df: pd.DataFrame) -> pd.DataFrame:
    """Header-keyword mapping so column reordering upstream cannot break us.

    Order matters: '97.5' must be tested before '2.5' (substring collision).
    """
    ren = {}
    for c in df.columns:
        lc = str(c).lower()
        if "date" in lc:
            ren[c] = "date"
        elif "50th" in lc or "main" in lc:
            ren[c] = "median"
        elif "97.5" in lc:
            ren[c] = "p97_5"
        elif "2.5" in lc:
            ren[c] = "p2_5"
        elif "16th" in lc:
            ren[c] = "p16"
        elif "84th" in lc:
            ren[c] = "p84"
    df = df.rename(columns=ren)
    if "date" not in df.columns or "median" not in df.columns:
        raise ValueError(f"unrecognized RDE layout: {list(df.columns)}")
    keep = ["date"] + [c for c in COLUMNS if c in df.columns]
    out = df[keep].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    for c in keep[1:]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.dropna(subset=["date", "median"]).set_index("date").sort_index()
    return out[~out.index.duplicated(keep="last")]


def parse_rde(content: bytes) -> pd.DataFrame:
    """bytes -> DataFrame indexed by date with columns median/p2_5/p16/p84/p97_5."""
    try:
        df = pd.read_csv(io.BytesIO(content))
    except Exception:
        df = pd.read_excel(io.BytesIO(content))
    return _shape(df)


def _to_rows(df: pd.DataFrame) -> list[list]:
    rows = []
    for dt, r in df.iterrows():
        rows.append(
            [dt.date().isoformat()]
            + [None if pd.isna(r.get(c)) else float(r.get(c)) for c in COLUMNS]
        )
    return rows


def _from_rows(rows: list[list]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame(rows, columns=["date"] + COLUMNS)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index()


async def fetch_rde(client: httpx.AsyncClient) -> dict:
    """NY Fed RDE estimates as {'fetched_at': iso, 'rde': DataFrame}."""
    key = "nyfed_rde"
    cached = store.load_blob(key, RDE_TTL_MIN)
    if cached is None:
        try:
            r = await client.get(
                DATA_URL, headers={"User-Agent": USER_AGENT}, timeout=45,
                follow_redirects=True,
            )
            r.raise_for_status()
            # The site 302s bad medialibrary paths to an HTML shell instead of
            # 404ing; an HTML content type means the file moved. Fail loud.
            ctype = r.headers.get("content-type", "")
            if "html" in ctype:
                raise ValueError(f"RDE URL redirected to HTML ({ctype}); file moved?")
            df = parse_rde(r.content)
            if df.empty:
                raise ValueError("RDE file parsed to zero rows")
            cached = {"fetched_at": utcnow_iso(), "rows": _to_rows(df)}
            store.save_blob(key, cached)
        except Exception as exc:  # serve stale on failure, fail-loud via staleness
            cached = store.load_blob(key)
            if cached is None:
                raise SourceFault("nyfed_rde", f"{type(exc).__name__}: {exc}") from exc
    return {"fetched_at": cached["fetched_at"], "rde": _from_rows(cached["rows"])}
