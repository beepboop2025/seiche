"""Orchestrator: fetch everything, run all engines, assemble the payload.

One entry point (`snapshot`) so the API layer stays thin, plus
`snapshot_asof` — the Time Machine: every engine is a pure function of its
input series, so truncating the series and re-running replays the whole board
as it would have looked on any historical date.

Results are cached in-process for CACHE_MIN minutes; the heavy analytics
layer (history reconstruction, Tell, Turn, Playbook, PROOF backtest) is
additionally blob-cached per data-day. Each upstream failure degrades that
block to its stale copy (with true staleness shown) or to an explicit fault
entry — never a silent gap.

Every live snapshot also appends to the point-in-time record (pit:* blobs):
from today forward, Seiche accrues a TRUE as-published index history that no
backtest reconstruction can be accused of polishing.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import math
import os
import re
import subprocess
import time
import traceback
import unicodedata
import urllib.parse
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from seiche import rubric, store
from seiche.config import (
    ALL_SERIES,
    BALLAST_CFTC_RELEASE_LAG_DAYS,
    BALLAST_EIA_RELEASE_LAG_DAYS,
    BIS_SERIES,
    BOJ_SERIES,
    COMPOSITE_WEIGHTS,
    CROWD_LOOKBACK_WEEKS,
    CRYPTO_PRODUCTS,
    ECB_SERIES,
    EIA_INVENTORY_SERIES,
    ESTUARY_FRED_SERIES,
    FOMC_DECISION_DATES,
    FRED_CP_SERIES,
    FRED_CUSTODY_SERIES,
    FRED_SERIES,
    GLOBAL_FRED_SERIES,
    GLOBAL_MM_FRED_SERIES,
    INDIA_FRED_SERIES,
    MARKET_SERIES,
    OFR_GCF_SERIES,
    OFR_PD_SERIES,
    OFR_SERIES,
    OIL_FUNDING_EIA_SERIES,
    OIL_FUNDING_FRED_SERIES,
    PLAYBOOK_OUTCOMES,
    PRETRAIN_FRED_SERIES,
    REFEREE_SERIES,
    RUNWAY_QT_PACE_B_PER_MONTH,
    SWAP_LINE_OPS_N,
)
from seiche.domain.forward_record import (
    SNAPSHOT_HANDOFF_SCHEMA,
    validate_release_handoff_envelope,
    validate_release_product_bindings,
)
from seiche.public_faults import safe_failure_envelope, sanitize_fault_record
from seiche.engines import auctions as eng_auctions
from seiche.engines import backtest as eng_backtest
from seiche.engines import basins as eng_basins
from seiche.engines import bathymetry as eng_bathymetry
from seiche.engines import book as eng_book
from seiche.engines import breakwater as eng_breakwater
from seiche.engines import caesar as eng_caesar
from seiche.engines import communique as eng_communique
from seiche.engines import composite as eng_composite
from seiche.engines import cpsentinel as eng_cpsentinel
from seiche.engines import windfetch as eng_windfetch
from seiche.engines import echo as eng_echo
from seiche.engines import edetect as eng_edetect
from seiche.engines import farbasin as eng_farbasin
from seiche.engines import gyre as eng_gyre
from seiche.engines import harbors as eng_harbors
from seiche.engines import spillover as eng_spillover
from seiche.engines import history as eng_history
from seiche.engines import markov as eng_markov
from seiche.engines import montecarlo as eng_montecarlo
from seiche.engines import oujump as eng_oujump
from seiche.engines import hydrophone as eng_hydrophone
from seiche.engines import kink as eng_kink
from seiche.engines import leakaudit as eng_leakaudit
from seiche.engines import merian as eng_merian
from seiche.engines import microseism as eng_microseism
from seiche.engines import regatta as eng_regatta
from seiche.engines import scuttlebutt as eng_scuttlebutt
from seiche.engines import searoom as eng_searoom
from seiche.engines import seastate as eng_seastate
from seiche.engines import thermohaline as eng_thermohaline
from seiche.engines import market as eng_market
from seiche.engines import money_market as eng_money_market
from seiche.engines import mlpred as eng_mlpred
from seiche.engines import moorings as eng_moorings
from seiche.engines import navigator as eng_navigator
from seiche.engines import phasemap as eng_phasemap
from seiche.engines import playbook as eng_playbook
from seiche.engines import resonance as eng_resonance
from seiche.engines import funding_pop as eng_funding_pop
from seiche.engines import roguewave as eng_roguewave
from seiche.engines import ledger as eng_ledger
from seiche.engines import modelcourt as eng_modelcourt
from seiche.engines import officialbid as eng_officialbid
from seiche.engines import ballast as eng_ballast
from seiche.engines import oilfunding as eng_oilfunding
from seiche.engines import estuary as eng_estuary
from seiche.engines import rdenowcast as eng_rdenowcast
from seiche.engines import refereegli as eng_refereegli
from seiche.engines import reportcard as eng_reportcard
from seiche.engines import runway as eng_runway
from seiche.engines import rvxray as eng_rvxray
from seiche.engines import stigma as eng_stigma
from seiche.engines import supplydesk as eng_supplydesk
from seiche.engines import sonar as eng_sonar
from seiche.engines import stacker as eng_stacker
from seiche.engines import stationkeeping as eng_stationkeeping
from seiche.engines import swell as eng_swell
from seiche.engines import tails as eng_tails
from seiche.engines import tidetables as eng_tidetables
from seiche.engines import turn as eng_turn
from seiche.engines import undertow as eng_undertow
from seiche.engines import warehouse as eng_warehouse
from seiche.engines import weather as eng_weather
from seiche import editorial
from seiche.sources import bis, boj, cftc, crypto, ecb, eia_petroleum, fedtext, fiscaldata, fred, gdelt, llamahacks, nyfed, nyfed_rde, ofr, palimpsest, td_auctions, windfetch
from seiche.sources.base import Series, SourceFault, utcnow_iso

CACHE_MIN = 15
DEEP_TTL_MIN = 12 * 60
LAST_GOOD_SNAPSHOT_KEY = "live-snapshot:last-known-good:v1"
STATIC_SNAPSHOT_PATH = (
    Path(__file__).resolve().with_name("bootstrap_snapshot.json")
)
PALIMPSEST_NATIVE_ENGINE_SERIES = {
    "PALIMPSEST_FEAR": "ddti-history.jsonl:top_threat",
    "PALIMPSEST_NEW": "ddti-history.jsonl:n_new",
    "PALIMPSEST_GFI": "history.jsonl:gfi",
}
RESTRICTED_SNAPSHOT_IDENTIFIERS = frozenset({
    "china money",
    "chinamoney",
    "cfets",
    "cfets_rates",
    "shibor",
    "shibor_on",
    "shibor:on",
    "cn.cfets.shibor_on",
    "cn.cfets.fdr007",
    "cn.cfets.dr007",
    "cn_fdr007",
    "cn_parity",
    "usdcny_parity",
    "fdr007",
    "dr007",
    "china-econ-history.jsonl:fdr007",
    "china-econ-history.jsonl:usdcny_parity",
    "cn·rate",
})
RESTRICTED_SNAPSHOT_IDENTITY_FIELDS = frozenset({
    "adapter",
    "adapter_id",
    "benchmark",
    "columns",
    "id",
    "input_series",
    "instrument_id",
    "label",
    "metric",
    "mnemonic",
    "name",
    "node",
    "rate_label",
    "remote_id",
    "series",
    "series_id",
    "source",
    "source_id",
    "source_uri",
})
RESTRICTED_SNAPSHOT_PROSE_FIELDS = frozenset({
    "caveat",
    "caveats",
    "description",
    "detail",
    "explanation",
    "message",
    "method",
    "note",
    "notes",
    "reason",
    "summary",
    "term",
    "text",
    "title",
})
RESTRICTED_SNAPSHOT_IDENTITY_SUFFIXES = ("_id", "_ids", "_series")
RESTRICTED_SNAPSHOT_IDENTITY_CONTAINERS = frozenset({
    "benchmarks",
    "components",
    "covariates",
    "exposures",
    "factors",
    "features",
    "indicators",
    "inputs",
    "instruments",
    "measures",
    "metrics",
    "outputs",
    "parameters",
    "predictors",
    "regressors",
    "signals",
    "sources",
    "targets",
    "variables",
})
RESTRICTED_SNAPSHOT_DISPLAY_IDENTITY_FIELDS = frozenset({
    "label",
    "name",
    "rate_label",
})
RESTRICTED_SNAPSHOT_QUANTITATIVE_FIELDS = frozenset({
    "amount",
    "balance",
    "beta",
    "close",
    "contribution",
    "corr",
    "correlation",
    "fixing",
    "high",
    "index",
    "last",
    "last_pct",
    "latest_pct",
    "level",
    "low",
    "mid",
    "notional",
    "open",
    "pctl",
    "percentile",
    "price",
    "pressure",
    "quote",
    "rate",
    "rate2",
    "rate_pct",
    "raw_value",
    "score",
    "spread",
    "spread_bp",
    "stress",
    "value",
    "value_bp",
    "volume",
    "weight",
    "z",
    "zscore",
})
RESTRICTED_SNAPSHOT_OBSERVED_SERIES_FIELDS = frozenset({
    "data",
    "data_points",
    "history",
    "measurement",
    "measurements",
    "observations",
    "points",
    "quotes",
    "rate_rows",
    "rates",
    "records",
    "rows",
    "series",
    "values",
})
RESTRICTED_SNAPSHOT_OBSERVED_SERIES_SUFFIXES = (
    "_data",
    "_history",
    "_observations",
    "_points",
    "_quotes",
    "_records",
    "_rows",
    "_series",
)
RESTRICTED_SNAPSHOT_QUALITATIVE_NUMERIC_FIELDS = frozenset({
    "count",
    "mention_count",
    "n_mentions",
    "n_new",
    "n_obs",
    "n_terms",
    "rank",
    "status_code",
    "year",
})
RESTRICTED_SNAPSHOT_METRIC_PREFIXES = (
    "change_",
    "chg_",
    "delta_",
    "diff_",
)
RESTRICTED_SNAPSHOT_METRIC_SUFFIXES = (
    "_amount",
    "_b",
    "_balance",
    "_bp",
    "_bps",
    "_cny",
    "_index",
    "_level",
    "_levels",
    "_m",
    "_notional",
    "_pctl",
    "_percent",
    "_percentile",
    "_pct",
    "_price",
    "_prices",
    "_quote",
    "_quotes",
    "_rate",
    "_rates",
    "_score",
    "_spread",
    "_spreads",
    "_usd",
    "_value",
    "_values",
    "_volume",
    "_z",
)
FARBASIN_TARGET_PATH = ("engines", "farbasin", "top_targets")
FARBASIN_TARGET_FIELDS = frozenset({"domain", "is_new", "term", "threat"})
RESTRICTED_SNAPSHOT_URL_SUFFIXES = (
    "_url",
    "_urls",
    "_uri",
    "_uris",
    "_href",
    "_hrefs",
)
_cache: dict = {
    "at": 0.0,
    "payload": None,
    "source": None,
    "release_receipt": None,
    "release_handoff_id": None,
    "producer_sha": None,
}
_process_release_sha: str | None = None
_lock = asyncio.Lock()
_refreshing = False  # one background rebuild at a time; readers never wait on it

# Two audiences, two strings. VERSION is the machine-facing contract and must
# stay bare semver matching server.json and the MCP registry listing — it is
# what the MCP handshake hands an agent. RELEASE is the human-facing codename
# the board has carried since v0.2 (deep-water, forecast-layer, physics-layer,
# scenarios, microseism, tier1, estuary) and rides along on the citation footers, where
# it is worth something to a reader.
VERSION = "0.10.1"
RELEASE = "estuary"
VERSION_LABEL = f"{VERSION} {RELEASE}"


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

async def _gather_sources() -> tuple[dict, list[dict]]:
    faults: list[dict] = []
    out: dict = {}
    async with httpx.AsyncClient(follow_redirects=True) as client:

        async def guard(name: str, coro):
            try:
                out[name] = await coro
            except SourceFault as e:
                faults.append({"source": e.source, "detail": e.detail})
            except Exception as e:  # unexpected — still fail loud
                faults.append({"source": name, "detail": f"{type(e).__name__}: {e}"})

        fred_mnems = [
            s.mnemonic
            for s in FRED_SERIES + MARKET_SERIES + GLOBAL_FRED_SERIES + INDIA_FRED_SERIES
            + GLOBAL_MM_FRED_SERIES + OIL_FUNDING_FRED_SERIES
            + ESTUARY_FRED_SERIES
            + PRETRAIN_FRED_SERIES + REFEREE_SERIES
        ]
        await asyncio.gather(
            guard("fred", fred.fetch_many(client, fred_mnems, faults)),
            guard("fred_cp_rates", fred.fetch_many(client, [s.mnemonic for s in FRED_CP_SERIES], faults)),
            guard("fred_custody", fred.fetch_many(client, [s.mnemonic for s in FRED_CUSTODY_SERIES], faults)),
            guard("eia_petroleum", eia_petroleum.fetch_many(client, [s.mnemonic for s in OIL_FUNDING_EIA_SERIES], faults)),
            guard("ofr", ofr.fetch_many(client, [s.mnemonic for s in OFR_SERIES], faults)),
            guard("ofr_gcf", ofr.fetch_many(client, [s.mnemonic for s in OFR_GCF_SERIES], faults)),
            guard("ofr_pd_financing", ofr.fetch_many(client, [s.mnemonic for s in OFR_PD_SERIES], faults)),
            guard("llama_hacks", llamahacks.fetch_all(client, faults)),
            guard("windfetch", windfetch.fetch_all(client, faults)),
            guard("ecb", ecb.fetch_many(client, [s.mnemonic for s in ECB_SERIES], faults)),
            guard("boj", boj.fetch_many(client, [s.mnemonic for s in BOJ_SERIES], faults)),
            guard("bis", bis.fetch_many(client, [s.mnemonic for s in BIS_SERIES], faults)),
            guard("crypto", crypto.fetch_all(client, CRYPTO_PRODUCTS, faults)),
            guard("nyfed_rates", nyfed.fetch_secured_rates(client)),
            guard("nyfed_srf", nyfed.fetch_srf_ops(client)),
            guard("nyfed_pd", nyfed.fetch_pd_positions(client)),
            guard("nyfed_fxs", nyfed.fetch_fx_swaps(client, SWAP_LINE_OPS_N)),
            guard("nyfed_rde", nyfed_rde.fetch_rde(client)),
            guard("tga", fiscaldata.fetch_tga_daily(client)),
            guard("auctions", fiscaldata.fetch_auctions(client)),
            guard("upcoming", fiscaldata.fetch_upcoming_auctions(client)),
            guard("mspd", td_auctions.fetch_mspd_maturities(client)),
            guard("tff", cftc.fetch_tff_ust(client)),
            guard("commodity_cot", cftc.fetch_disaggregated_commodities(client)),
            guard("eia_inventory", eia_petroleum.fetch_many(
                client, [s.mnemonic for s in EIA_INVENTORY_SERIES], faults
            )),
            guard("palimpsest", palimpsest.fetch_all(client, faults)),
            guard("fedtext", fedtext.fetch_all(client, faults)),
            guard("gdelt", gdelt.fetch_all(client, faults)),
        )
    return out, faults


def _rights_eligible_sources(src: dict) -> dict:
    """Project collected data onto the redistribution-safe engine boundary.

    This deliberately leaves the durable source cache untouched.  ChinaMoney
    and non-native Palimpsest fields may remain available for a future licensed
    migration, but they cannot enter engines, provenance, snapshots, or replay
    products in the current public release.
    The Federal Reserve H.10 ``CNY`` series lives in ``fred`` and is preserved.
    """
    eligible = dict(src)
    eligible.pop("chinamoney", None)

    palimpsest_block = src.get("palimpsest")
    if isinstance(palimpsest_block, dict):
        safe_palimpsest = dict(palimpsest_block)
        series = palimpsest_block.get("series")
        safe_series: dict[str, Series] = {}
        if isinstance(series, dict):
            for mnemonic, value in series.items():
                expected_remote_id = PALIMPSEST_NATIVE_ENGINE_SERIES.get(mnemonic)
                if (
                    expected_remote_id is not None
                    and isinstance(value, Series)
                    and value.mnemonic == mnemonic
                    and value.source == "palimpsest"
                    and value.remote_id == expected_remote_id
                ):
                    safe_series[mnemonic] = value
        safe_palimpsest["series"] = safe_series
        eligible["palimpsest"] = safe_palimpsest
    return eligible


def _truncate_sources(src: dict, asof: pd.Timestamp) -> dict:
    """Time Machine: cut every series at the replay date. Pure copies — the
    cached live sources are never mutated."""
    src = _rights_eligible_sources(src)
    out: dict = {}
    for group in ("fred", "fred_cp_rates", "fred_custody", "ofr", "ofr_gcf",
                  "ofr_pd_financing", "ecb", "eia_petroleum", "eia_inventory",
                  "bis", "boj"):
        cut = {}
        for m, s in (src.get(group) or {}).items():
            cutoff = (
                asof - pd.Timedelta(days=BALLAST_EIA_RELEASE_LAG_DAYS)
                if group == "eia_inventory"
                else asof
            )
            pts = s.points[s.points.index <= cutoff]
            cut[m] = Series(s.mnemonic, s.source, s.remote_id, s.label, s.unit, s.freq, s.fetched_at, pts)
        out[group] = cut
    out["llama_hacks"] = llamahacks.truncate(src.get("llama_hacks") or {}, asof)
    # windfetch is a live-only pack (no archive): a replay stamps the marker
    # and the engine refuses to backfill wind that was never recorded
    out["windfetch"] = {**(src.get("windfetch") or {}),
                        "replay_asof": asof.date().isoformat()}
    fxs = (src.get("nyfed_fxs") or {}).get("ops", [])
    out["nyfed_fxs"] = {
        "fetched_at": (src.get("nyfed_fxs") or {}).get("fetched_at"),
        "ops": [o for o in fxs if (o.get("trade_date") or "") <= asof.date().isoformat()],
    }
    cr = src.get("crypto") or {}
    stable = cr.get("stable") or {}
    total = stable.get("total", pd.Series(dtype=float))
    out["crypto"] = {
        "fetched_at": cr.get("fetched_at"),
        "candles": {
            m: Series(s.mnemonic, s.source, s.remote_id, s.label, s.unit, s.freq,
                      s.fetched_at, s.points[s.points.index <= asof])
            for m, s in (cr.get("candles") or {}).items()
        },
        # peg board is a spot-only feed — a replay has no vintage for it
        "stable": {"board": [], "total": total[total.index <= asof] if not total.empty else total},
    }
    nr = src.get("nyfed_rates") or {}
    out["nyfed_rates"] = {
        "fetched_at": nr.get("fetched_at"),
        "frames": {k: df[df.index <= asof] for k, df in (nr.get("frames") or {}).items()},
    }
    ns = src.get("nyfed_srf") or {}
    daily = ns.get("daily")
    out["nyfed_srf"] = {
        "fetched_at": ns.get("fetched_at"),
        "daily": daily[daily.index <= asof] if daily is not None and not daily.empty else pd.DataFrame(),
    }
    npd = src.get("nyfed_pd") or {}
    out["nyfed_pd"] = {
        "fetched_at": npd.get("fetched_at"),
        "positions": {k: s[s.index <= asof] for k, s in (npd.get("positions") or {}).items()},
    }
    tga = (src.get("tga") or {}).get(
        "tga", pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    )
    out["tga"] = {"fetched_at": (src.get("tga") or {}).get("fetched_at"), "tga": tga[tga.index <= asof]}
    au = (src.get("auctions") or {}).get("auctions", pd.DataFrame())
    if not au.empty:
        mask = pd.to_datetime(au["auction_date"], errors="coerce") <= asof
        au = au[mask]
    out["auctions"] = {"fetched_at": (src.get("auctions") or {}).get("fetched_at"), "auctions": au}
    out["upcoming"] = {"upcoming": pd.DataFrame()}  # current-state feed: no historical vintage
    out["mspd"] = {"mspd": pd.DataFrame()}  # monthly current-state feed: no historical vintage
    fedtexts = (src.get("fedtext") or {}).get("texts", {})
    out["fedtext"] = {
        "fetched_at": (src.get("fedtext") or {}).get("fetched_at"),
        "texts": {d: t for d, t in fedtexts.items() if d <= asof.date().isoformat()},
    }
    tff = (src.get("tff") or {}).get("tff", pd.DataFrame())
    if not tff.empty:
        tff = tff[tff["date"] <= asof]
    out["tff"] = {"fetched_at": (src.get("tff") or {}).get("fetched_at"), "tff": tff}
    commodity_cot = (src.get("commodity_cot") or {}).get(
        "positions", pd.DataFrame()
    )
    if not commodity_cot.empty:
        if "available_date" in commodity_cot.columns:
            available = pd.to_datetime(
                commodity_cot["available_date"], errors="coerce"
            )
        else:
            available = pd.to_datetime(
                commodity_cot["date"], errors="coerce"
            ) + pd.Timedelta(days=BALLAST_CFTC_RELEASE_LAG_DAYS)
        commodity_cot = commodity_cot[available <= asof]
    out["commodity_cot"] = {
        "fetched_at": (src.get("commodity_cot") or {}).get("fetched_at"),
        "positions": commodity_cot,
    }
    pal = src.get("palimpsest") or {}
    out["palimpsest"] = {
        "fetched_at": pal.get("fetched_at"),
        "series": {
            m: Series(s.mnemonic, s.source, s.remote_id, s.label, s.unit, s.freq,
                      s.fetched_at, s.points[s.points.index <= asof])
            for m, s in (pal.get("series") or {}).items()
        },
        "latest": {},  # spot board — a replay has no vintage for it
    }
    return out


# ---------------------------------------------------------------------------
# Shared derived series
# ---------------------------------------------------------------------------

def _pts(d: dict, key: str) -> pd.Series:
    s = d.get(key)
    return s.points.dropna() if s is not None else pd.Series(dtype=float)


def _vol_b(s: pd.Series) -> pd.Series:
    """OFR volume mnemonics are raw dollars — scale to $B when they look it."""
    x = s.dropna()
    # A latest legitimate zero must not make earlier dollar-scale rows look as
    # though they were already billions. The series-wide magnitude is a unit
    # boundary check, not a market-state inference.
    if not x.empty and float(x.abs().max()) > 1e6:
        return x / 1e9
    return x


def _derived(src: dict) -> dict:
    """Series every layer shares, computed once per snapshot."""
    fred_s = src.get("fred", {})
    ofr_s = src.get("ofr", {})
    frames = (src.get("nyfed_rates") or {}).get("frames", {})

    d: dict = {}
    d["iorb"] = None
    if "IORB" in fred_s and "IOER" in fred_s:
        d["iorb"] = fred.splice_iorb(fred_s["IORB"], fred_s["IOER"]).points.dropna()

    sofr = _pts(fred_s, "SOFR")
    if d["iorb"] is not None and not sofr.empty:
        d["spread_bp"] = ((sofr - d["iorb"].reindex(sofr.index).ffill()) * 100.0).dropna()
    else:
        d["spread_bp"] = pd.Series(dtype=float)

    sofr_f = frames.get("SOFR")
    if sofr_f is not None and "percentPercentile99" in sofr_f.columns:
        d["tail_bp"] = ((sofr_f["percentPercentile99"] - sofr_f["percentRate"]) * 100.0).dropna()
    else:
        d["tail_bp"] = pd.Series(dtype=float)

    # 3m AA nonfinancial CP − 3m Treasury, in bp (CP Sentinel's spread leg)
    cp_s = src.get("fred_cp_rates", {})
    cp3m, dgs3m = _pts(cp_s, "CP_NONFIN_3M"), _pts(cp_s, "DGS3M")
    if not cp3m.empty and not dgs3m.empty:
        d["cp_spread_bp"] = ((cp3m - dgs3m.reindex(cp3m.index).ffill()) * 100.0).dropna()
    else:
        d["cp_spread_bp"] = pd.Series(dtype=float)

    # DeFi exploit losses, daily USD (DeFiLlama hacks; zeros are real quiet days)
    hacks = (src.get("llama_hacks") or {}).get("daily")
    d["hacks_usd"] = hacks.points.dropna() if hacks is not None else pd.Series(dtype=float)

    res = _pts(fred_s, "WRESBAL") / 1000.0            # $B weekly
    gdp = _pts(fred_s, "GDP")                          # $B quarterly
    if not res.empty and not gdp.empty:
        g = gdp.sort_index().reindex(res.index, method="ffill")
        d["res_gdp"] = (res / g).dropna()
    else:
        d["res_gdp"] = pd.Series(dtype=float)
    d["res_gdp_pctl"] = (
        d["res_gdp"].expanding(60).rank(pct=True).dropna() if not d["res_gdp"].empty else pd.Series(dtype=float)
    )

    srf_daily = (src.get("nyfed_srf") or {}).get("daily", pd.DataFrame())
    d["srf"] = srf_daily["accepted"] if isinstance(srf_daily, pd.DataFrame) and not srf_daily.empty else pd.Series(dtype=float)
    d["srf_daily"] = srf_daily if isinstance(srf_daily, pd.DataFrame) else pd.DataFrame()

    d["dw_b"] = _pts(fred_s, "DISCOUNT_WINDOW") / 1000.0   # $M -> $B
    d["rrp"] = _pts(fred_s, "RRPONTSYD")
    d["tga"] = (src.get("tga") or {}).get("tga", pd.Series(dtype=float))
    d["dvp_vol_b"] = _vol_b(_pts(ofr_s, "DVP_VOL"))
    d["tri_vol_b"] = _vol_b(_pts(ofr_s, "TRI_VOL"))

    candles = (src.get("crypto") or {}).get("candles", {})
    usdt = candles.get("USDT_USD")
    d["usdt_peg_bp"] = (
        ((usdt.points.dropna() - 1.0) * 10_000.0) if usdt is not None else pd.Series(dtype=float)
    )
    btc = candles.get("BTC_USD")
    d["btc"] = btc.points.dropna() if btc is not None else pd.Series(dtype=float)
    return d


# ---------------------------------------------------------------------------
# Engines (the light layer — sub-second, replayable)
# ---------------------------------------------------------------------------

def _run_engines(src: dict, drv: dict, faults: list[dict], asof: pd.Timestamp | None = None) -> dict:
    """`asof` is set only on Time Machine replays. Most engines infer their own
    present from the truncated series they are handed, but a forward calendar
    has no series to infer it from: without this, a replayed board would carry
    a settlement table built around the wall clock and blob-cache it forever."""
    src = _rights_eligible_sources(src)
    fred_s = src.get("fred", {})
    ofr_s = src.get("ofr", {})
    frames = (src.get("nyfed_rates") or {}).get("frames", {})
    iorb = drv["iorb"]
    evaluation_asof = (
        asof if asof is not None
        else pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    )

    results: dict = {}

    def run(name: str, fn):
        try:
            results[name] = fn()
        except Exception as e:
            results[name] = safe_failure_envelope(e)
            faults.append({"source": f"engine:{name}", "detail": traceback.format_exc(limit=2)})

    # --- Institutional USD money-market desk ------------------------------
    # Unit boundaries are explicit here: the pure engine receives every
    # balance/volume in $B and every rate in percentage points. It performs
    # no source-specific guessing and never reaches back into collectors.
    cp_s = src.get("fred_cp_rates", {})
    gcf_s = src.get("ofr_gcf", {})
    run("money_market", lambda: eng_money_market.analyze(
        sofr=_pts(fred_s, "SOFR"),
        effr=_pts(fred_s, "EFFR"),
        iorb=iorb if iorb is not None else pd.Series(dtype=float),
        nyfed_sofr=frames.get("SOFR", pd.DataFrame()),
        nyfed_tgcr=frames.get("TGCR", pd.DataFrame()),
        nyfed_bgcr=frames.get("BGCR", pd.DataFrame()),
        bgcr=_pts(ofr_s, "BGCR"),
        tgcr=_pts(ofr_s, "TGCR"),
        dvp_rate=_pts(ofr_s, "DVP_RATE_OO"),
        dvp_volume=_vol_b(_pts(ofr_s, "DVP_VOL")),
        tri_rate=_pts(ofr_s, "TRI_RATE_OO"),
        tri_volume=_vol_b(_pts(ofr_s, "TRI_VOL")),
        gcf_rate=_pts(gcf_s, "GCF_RATE_OO"),
        gcf_volume=_vol_b(_pts(gcf_s, "GCF_VOL_OO")),
        mmf_total=_vol_b(_pts(ofr_s, "MMF_TOT")),
        mmf_repo_ficc=_vol_b(_pts(ofr_s, "MMF_REPO_FICC")),
        mmf_repo_fed=_vol_b(_pts(ofr_s, "MMF_REPO_FED")),
        mmf_repo_total=_vol_b(_pts(ofr_s, "MMF_REPO_TOT")),
        cp_nonfinancial_3m=_pts(cp_s, "CP_NONFIN_3M"),
        cp_financial_3m=_pts(cp_s, "CP_FIN_3M"),
        treasury_3m=_pts(cp_s, "DGS3M"),
        bill_4w=_pts(fred_s, "TB4W"),
        bill_3m=_pts(fred_s, "TB3M"),
        reserves=_pts(fred_s, "WRESBAL") / 1000.0,
        tga=drv["tga"],
        on_rrp=drv["rrp"],
        srf=drv["srf"],
        discount_window=drv["dw_b"],
        evaluation_asof=evaluation_asof,
    ))

    # --- Kink ---
    if iorb is not None:
        run("kink", lambda: eng_kink.fit_kink(
            (drv["spread_bp"] / 100.0), _pts(fred_s, "WRESBAL"), _pts(fred_s, "GDP")))
    else:
        results["kink"] = {"ok": False, "reason": "IORB/SOFR unavailable"}
    kink_b = results["kink"].get("kink_reserves_b") if results["kink"].get("ok") else None

    # --- RDE Nowcast (our kink fit vs the NY Fed official print) ---
    run("rdenowcast", lambda: eng_rdenowcast.nowcast(
        results["kink"],
        (src.get("nyfed_rde") or {}).get("rde", pd.DataFrame()),
        (drv["spread_bp"] / 100.0) if iorb is not None else None,
        _pts(fred_s, "WRESBAL"),
        _pts(fred_s, "GDP")))

    # --- Weather (with auction settlement overlay) ---
    settlements = eng_weather.settlement_calendar(
        (src.get("upcoming") or {}).get("upcoming", pd.DataFrame()))
    run("weather", lambda: eng_weather.forecast(
        _pts(fred_s, "WRESBAL"), _pts(fred_s, "WALCL"), drv["tga"], kink_b, settlements))

    # --- Supply Desk (net-new-cash forward table, Wrightson style) ---
    if asof is not None:
        # Replay blanks the current-state upcoming feed, so a forward supply
        # table cannot be reconstructed for a past date. Refusing is the
        # honest answer; guessing one from wall-clock data is not.
        results["supplydesk"] = {
            "ok": False,
            "reason": "forward supply table needs the current-state auction feed, "
                      "which has no historical vintage; not reconstructable point-in-time",
        }
    else:
        run("supplydesk", lambda: eng_supplydesk.forward_table(
            (src.get("upcoming") or {}).get("upcoming", pd.DataFrame()),
            (src.get("auctions") or {}).get("auctions", pd.DataFrame()),
            (src.get("mspd") or {}).get("mspd", pd.DataFrame()),
        ))

    # --- Reserve Runway (13-week kink-crossing projection) ---
    run("runway", lambda: eng_runway.project(
        _pts(fred_s, "WRESBAL"),
        drv["rrp"],
        drv["tga"],
        results["kink"],
        [{"date": d.date().isoformat(), "amount_b": round(float(v), 1)}
         for d, v in settlements.items()],
        RUNWAY_QT_PACE_B_PER_MONTH,
    ))

    # --- Tails ---
    run("tails", lambda: eng_tails.analyze(
        frames, iorb if iorb is not None else pd.Series(dtype=float)))

    # --- Stigma (SRF ceiling leak: repo paying up while the backstop sits idle) ---
    run("stigma", lambda: eng_stigma.gauge(
        frames.get("SOFR", pd.DataFrame()),
        _pts(fred_s, "SRF_CEILING"),
        drv["srf"],
        iorb,
    ))

    # --- Echo ---
    def _echo():
        comps = {
            "sofr_iorb": drv["spread_bp"] / 100.0,
            "effr_iorb": (_pts(fred_s, "EFFR") - iorb.reindex(_pts(fred_s, "EFFR").index).ffill()),
            "bgcr_sofr": (_pts(ofr_s, "BGCR") - _pts(fred_s, "SOFR")),
            "rrp": drv["rrp"],
            "tga_chg5": drv["tga"].diff(5),
            "reserves_chg4w": _pts(fred_s, "WRESBAL").diff(4),
            "srf": drv["srf"],
        }
        z = eng_echo.build_state({k: v for k, v in comps.items() if not v.dropna().empty})
        return eng_echo.match(z)
    if iorb is not None:
        run("echo", _echo)
    else:
        results["echo"] = {"ok": False, "reason": "state components unavailable"}

    # --- RV X-Ray + crowding ---
    tff = (src.get("tff") or {}).get("tff", pd.DataFrame())
    run("rvxray", lambda: eng_rvxray.analyze(tff, _pts(ofr_s, "DVP_VOL")))
    run("crowding", lambda: eng_rvxray.crowding(tff, CROWD_LOOKBACK_WEEKS))

    # --- Foreign Official Bid (custody vs foreign RRP: rotation or retreat) ---
    custody_s = src.get("fred_custody", {})
    run("officialbid", lambda: eng_officialbid.analyze(
        custody_tsy_weekly=_pts(custody_s, "CUSTODY_TSY"),
        foreign_rrp_weekly=_pts(fred_s, "FOREIGN_RRP"),
        fima_repo_weekly=_pts(custody_s, "FIMA_REPO"),
    ))

    # --- Auctions ---
    run("auctions", lambda: eng_auctions.analyze(
        (src.get("auctions") or {}).get("auctions", pd.DataFrame())))

    # --- Auction Report Card (the event study behind each auction grade) ---
    run("reportcard", lambda: eng_reportcard.report_cards(
        results["auctions"],
        drv["spread_bp"],
        drv["srf"],
        _pts(fred_s, "WRESBAL"),
        auctions_frame=(src.get("auctions") or {}).get("auctions", pd.DataFrame()),
    ))

    # --- Where the Dollars Sit (the H.4.1 identity, reconciled to the dollar) ---
    run("ledger", lambda: eng_ledger.reconcile(
        _pts(fred_s, "WALCL"),
        _pts(fred_s, "WCURCIR"),
        _pts(fred_s, "WRESBAL"),
        _pts(fred_s, "WTREGEN"),
        drv["rrp"],
        _pts(fred_s, "FOREIGN_RRP"),
    ))

    # --- Resonance ---
    run("resonance", lambda: eng_resonance.analyze(drv["spread_bp"]))

    # --- Undertow (free decay — the other half of the resonance physics) ---
    run("undertow", lambda: eng_undertow.analyze(drv["spread_bp"], drv["tail_bp"]))

    # --- Phase Map (calendar-phase-resolved serial dependence of the shared
    #     pop statistic: the calendar assumptions Resonance and the Swell/
    #     Microseism buckets hard-code, graded in public. Display-only:
    #     context, never composite) ---
    run("phasemap", lambda: eng_phasemap.analyze(
        drv["spread_bp"],
        auctions=(src.get("auctions") or {}).get("auctions", pd.DataFrame()),
    ))

    # --- E-Detector (regime-break tripwire on the two funding streams, with a
    #     nonasymptotic false-alarm warranty; context, never composite) ---
    run("edetect", lambda: eng_edetect.analyze(
        spread_bp=drv["spread_bp"], tail_bp=drv["tail_bp"]))

    # --- Communiqué (the policy text read as data; vintage-stamped) ---
    run("communique", lambda: eng_communique.analyze(
        (src.get("fedtext") or {}).get("texts", {})))

    # --- Scuttlebutt (press attention on the plumbing; context, like
    #     Communiqué: narrative is never weighted into the composite) ---
    run("scuttlebutt", lambda: eng_scuttlebutt.analyze(src.get("gdelt") or {}))

    # --- The Breakwater (the rescuer's revealed reaction function) ---
    run("breakwater", lambda: eng_breakwater.analyze(drv["spread_bp"], drv["srf"]))

    # --- The plumbing panel (shared by Hydrophone and Merian Modes) ---
    def _panel() -> dict[str, pd.Series]:
        sofr = _pts(fred_s, "SOFR")
        effr = _pts(fred_s, "EFFR")
        panel = {
            "SOFR-IORB": drv["spread_bp"],
            "EFFR-IORB": ((effr - iorb.reindex(effr.index).ffill()) * 100.0) if iorb is not None else pd.Series(dtype=float),
            "BGCR-SOFR": ((_pts(ofr_s, "BGCR") - sofr) * 100.0),
            "TGCR-SOFR": ((_pts(ofr_s, "TGCR") - sofr) * 100.0),
            "DVP-TRI rate": ((_pts(ofr_s, "DVP_RATE_OO") - _pts(ofr_s, "TRI_RATE_OO")) * 100.0),
            "SOFR tail": drv["tail_bp"],
            "SRF": drv["srf"],
            "RRP": drv["rrp"],
            "TGA": drv["tga"],
            "DVP vol": drv["dvp_vol_b"],
            "TRI vol": drv["tri_vol_b"],
        }
        return {k: v for k, v in panel.items() if not v.dropna().empty}

    # --- Hydrophone ---
    run("hydrophone", lambda: eng_hydrophone.analyze(_panel()))

    # --- Physics layer (v2.6): the basin's modes and tail law --------------
    # (Bathymetry — the fourth physics engine — lives in the DEEP layer: its
    # state variable is the PROOF pop statistic and its first-passage
    # probability joins the Stack as a forecast member.)
    # Merian Modes — the seiche eigenmodes via Hankel-DMD (Koopman spectrum)
    run("merian", lambda: eng_merian.analyze(_panel()))

    # Rogue Wave — the tail law (POT/GPD on the shared pop statistic)
    run("roguewave", lambda: eng_roguewave.analyze(drv["spread_bp"]))

    # CAESar — tomorrow's (VaR, ES) bands from the pop statistic's own CAViaR
    # dynamics (the operational sibling of Rogue Wave's static tail law; context)
    run("caesar", lambda: eng_caesar.analyze(spread_bp=drv["spread_bp"]))

    # --- Warehouse ---
    run("warehouse", lambda: eng_warehouse.analyze(
        (src.get("nyfed_pd") or {}).get("positions", {})))

    # --- Global basins ---
    ecb_s = src.get("ecb", {})
    boj_s = src.get("boj", {})
    run("basins", lambda: eng_basins.analyze(
        spread_us_bp=drv["spread_bp"],
        estr=_pts(ecb_s, "ESTR"),
        ecb_dfr=_pts(fred_s, "ECB_DFR"),
        sonia=_pts(fred_s, "SONIA"),
        dxy=_pts(fred_s, "DXY_BROAD"),
        swap_lines_m=_pts(fred_s, "SWAP_LINES"),
        foreign_rrp_m=_pts(fred_s, "FOREIGN_RRP"),
        fx_ops=(src.get("nyfed_fxs") or {}).get("ops", []),
        inr=_pts(fred_s, "INR"),
        usdt_peg_bp=drv["usdt_peg_bp"],
        tona=_pts(boj_s, "TONA"),
        cny=_pts(fred_s, "CNY"),
        jpy=_pts(fred_s, "JPY"),
        krw=_pts(fred_s, "KRW"),
    ))

    # --- Thermohaline (BIS global liquidity — the deep circulation) ---
    run("thermohaline", lambda: eng_thermohaline.analyze(src.get("bis") or {}))

    # --- Harbors (national money markets — the holistic world view) ---
    eurusd = _pts(fred_s, "EURUSD")
    run("harbors", lambda: eng_harbors.analyze(
        {
            "EURO AREA": {
                "rate": _pts(ecb_s, "ESTR"), "rate_label": "€STR", "cadence": "daily",
                "fx": (1.0 / eurusd.replace(0, np.nan)).dropna(), "fx_label": "EUR per USD",
            },
            "CHINA": {
                "cadence": "FX daily; local rate unavailable",
                "fx": _pts(fred_s, "CNY"), "fx_label": "CNY per USD",
            },
            "INDIA": {
                "rate": _pts(fred_s, "CALL_IN"), "rate_label": "call money (OECD MEI)",
                "cadence": "monthly ~2mo lag",
                "fx": _pts(fred_s, "INR"), "fx_label": "INR per USD",
            },
            "JAPAN": {
                "rate": _pts(src.get("boj", {}), "TONA"), "rate_label": "TONA (BOJ)",
                "cadence": "daily",
                "fx": _pts(fred_s, "JPY"), "fx_label": "JPY per USD",
            },
            "KOREA": {
                "rate": _pts(fred_s, "CALL_KR"), "rate_label": "o/n call (OECD MEI)",
                "cadence": "monthly ~2mo lag",
                "fx": _pts(fred_s, "KRW"), "fx_label": "KRW per USD",
            },
        },
        effr=_pts(fred_s, "EFFR"),
    ))

    # --- Spillover (Diebold-Yilmaz directional connectedness across harbors) ---
    # Daily nodes only: daily-cadence anchor rates + the H.10 FX legs (daily even
    # where the local RATE is a monthly OECD mirror). Monthly rates are excluded
    # from the VAR by construction, never interpolated to pad the panel.
    run("spillover", lambda: eng_spillover.analyze({
        "US·rate": _pts(fred_s, "EFFR"),
        "EUR·rate": _pts(ecb_s, "ESTR"),
        "JP·rate": _pts(src.get("boj", {}), "TONA"),
        "EUR·fx": (1.0 / eurusd.replace(0, np.nan)).dropna(),
        "CNY·fx": _pts(fred_s, "CNY"),
        "JPY·fx": _pts(fred_s, "JPY"),
        "INR·fx": _pts(fred_s, "INR"),
        "KRW·fx": _pts(fred_s, "KRW"),
    }))

    # --- Station-Keeping (maneuver detection) ---
    run("stationkeeping", lambda: eng_stationkeeping.analyze(
        tga_daily=drv["tga"],
        rrp_daily=drv["rrp"],
        walcl_weekly=_pts(fred_s, "WALCL"),
    ))

    # --- Far Basin (Palimpsest policy-fear channel) ---
    pal = src.get("palimpsest") or {}
    pal_series = pal.get("series") or {}

    def _pal_pts(m: str) -> pd.Series | None:
        s = pal_series.get(m)
        return s.points.dropna() if s is not None else None

    run("farbasin", lambda: eng_farbasin.analyze(
        fear=_pal_pts("PALIMPSEST_FEAR"),
        n_new=_pal_pts("PALIMPSEST_NEW"),
        gfi=_pal_pts("PALIMPSEST_GFI"),
        latest=pal.get("latest"),
    ))

    # --- Stablecoin moorings ---
    stable = (src.get("crypto") or {}).get("stable", {})
    run("moorings", lambda: eng_moorings.analyze(
        board=stable.get("board", []),
        usdt_usd=(src.get("crypto") or {}).get("candles", {}).get("USDT_USD").points
        if (src.get("crypto") or {}).get("candles", {}).get("USDT_USD") is not None
        else pd.Series(dtype=float),
        stable_total_b=stable.get("total", pd.Series(dtype=float)),
        btc_usd=drv["btc"],
    ))

    # --- CP Sentinel (do major DeFi exploits narrow CP spreads? cross-market
    #     channel context; a missing leg degrades to an honest ok=False) ---
    run("cpsentinel", lambda: eng_cpsentinel.analyze(
        hacks_usd=drv["hacks_usd"], cp_spread_bp=drv["cp_spread_bp"]))

    # --- Oil × Funding (bidirectional physical-barrel / money-market context;
    #     rich research surface, deliberately never a composite component) ---
    cp_s = src.get("fred_cp_rates", {})
    run("oilfunding", lambda: eng_oilfunding.analyze(
        wti=_pts(fred_s, "WTI_SPOT"),
        brent=_pts(fred_s, "BRENT_SPOT"),
        sofr=_pts(fred_s, "SOFR"),
        iorb=iorb if iorb is not None else pd.Series(dtype=float),
        cp_nonfinancial_3m=_pts(cp_s, "CP_NONFIN_3M"),
        cp_financial_3m=_pts(cp_s, "CP_FIN_3M"),
        treasury_3m=_pts(cp_s, "DGS3M"),
        inr_per_usd=_pts(fred_s, "INR"),
        energy_cpi=_pts(fred_s, "ENERGY_CPI"),
        core_cpi=_pts(fred_s, "CORE_CPI"),
        foreign_treasury_custody=_pts(src.get("fred_custody", {}), "CUSTODY_TSY"),
        foreign_official_rrp=_pts(fred_s, "FOREIGN_RRP"),
        cushing_stocks=_pts(src.get("eia_petroleum", {}), "CUSHING_STOCKS"),
    ))

    # --- Ballast (energy-futures cash displacement and physical inventory;
    #     a context-only child of Oil × Funding, never a composite input) ---
    run("ballast", lambda: eng_ballast.analyze(
        commodity_positions=(src.get("commodity_cot") or {}).get(
            "positions", pd.DataFrame()
        ),
        prices={
            "WTI_SPOT": _pts(fred_s, "WTI_SPOT"),
            "HENRY_HUB_SPOT": _pts(fred_s, "HENRY_HUB_SPOT"),
        },
        crude_stocks_ex_spr=_pts(
            src.get("eia_inventory", {}), "CRUDE_STOCKS_EX_SPR"
        ),
        sofr=_pts(fred_s, "SOFR"),
        iorb=iorb if iorb is not None else pd.Series(dtype=float),
        cp_nonfinancial_3m=_pts(cp_s, "CP_NONFIN_3M"),
        treasury_3m=_pts(cp_s, "DGS3M"),
    ))

    # --- The Estuary (FX + materials -> price of cash).  The daily Passage
    #     and monthly breadth are explicitly separate; context only, never a
    #     composite component. ---
    run("estuary", lambda: eng_estuary.analyze(
        fx={
            "EUR": {
                "label": "Euro", "bucket": "AFE", "series": _pts(fred_s, "EURUSD"),
                "quote": "usd_per_local", "source_id": "DEXUSEU",
                "rate": _pts(ecb_s, "ESTR"), "rate_label": "€STR", "rate_cadence": "daily",
            },
            "GBP": {
                "label": "Sterling", "bucket": "AFE", "series": _pts(fred_s, "GBP"),
                "quote": "usd_per_local", "source_id": "DEXUSUK",
                "rate": _pts(fred_s, "SONIA"), "rate_label": "SONIA", "rate_cadence": "daily",
            },
            "JPY": {
                "label": "Japanese yen", "bucket": "AFE", "series": _pts(fred_s, "JPY"),
                "quote": "local_per_usd", "source_id": "DEXJPUS",
                "rate": _pts(boj_s, "TONA"), "rate_label": "TONA", "rate_cadence": "daily",
            },
            "AUD": {
                "label": "Australian dollar", "bucket": "AFE", "series": _pts(fred_s, "AUD"),
                "quote": "usd_per_local", "source_id": "DEXUSAL",
            },
            "CAD": {
                "label": "Canadian dollar", "bucket": "AFE", "series": _pts(fred_s, "CAD"),
                "quote": "local_per_usd", "source_id": "DEXCAUS",
            },
            "CHF": {
                "label": "Swiss franc", "bucket": "AFE", "series": _pts(fred_s, "CHF"),
                "quote": "local_per_usd", "source_id": "DEXSZUS",
            },
            "CNY": {
                "label": "Chinese yuan", "bucket": "EM", "series": _pts(fred_s, "CNY"),
                "quote": "local_per_usd", "source_id": "DEXCHUS",
            },
            "INR": {
                "label": "Indian rupee", "bucket": "EM", "series": _pts(fred_s, "INR"),
                "quote": "local_per_usd", "source_id": "DEXINUS",
                "rate": _pts(fred_s, "CALL_IN"), "rate_label": "call money", "rate_cadence": "monthly ~2mo lag",
            },
            "KRW": {
                "label": "Korean won", "bucket": "EM", "series": _pts(fred_s, "KRW"),
                "quote": "local_per_usd", "source_id": "DEXKOUS",
                "rate": _pts(fred_s, "CALL_KR"), "rate_label": "o/n call", "rate_cadence": "monthly ~2mo lag",
            },
            "MXN": {
                "label": "Mexican peso", "bucket": "EM", "series": _pts(fred_s, "MXN"),
                "quote": "local_per_usd", "source_id": "DEXMXUS",
            },
            "BRL": {
                "label": "Brazilian real", "bucket": "EM", "series": _pts(fred_s, "BRL"),
                "quote": "local_per_usd", "source_id": "DEXBZUS",
            },
            "ZAR": {
                "label": "South African rand", "bucket": "EM", "series": _pts(fred_s, "ZAR"),
                "quote": "local_per_usd", "source_id": "DEXSFUS",
            },
            "NZD": {
                "label": "New Zealand dollar", "bucket": "AFE", "series": _pts(fred_s, "NZD"),
                "quote": "usd_per_local", "source_id": "DEXUSNZ",
            },
            "DKK": {
                "label": "Danish krone", "bucket": "AFE", "series": _pts(fred_s, "DKK"),
                "quote": "local_per_usd", "source_id": "DEXDNUS",
            },
            "HKD": {
                "label": "Hong Kong dollar", "bucket": "AFE", "series": _pts(fred_s, "HKD"),
                "quote": "local_per_usd", "source_id": "DEXHKUS",
            },
            "MYR": {
                "label": "Malaysian ringgit", "bucket": "EM", "series": _pts(fred_s, "MYR"),
                "quote": "local_per_usd", "source_id": "DEXMAUS",
            },
            "NOK": {
                "label": "Norwegian krone", "bucket": "AFE", "series": _pts(fred_s, "NOK"),
                "quote": "local_per_usd", "source_id": "DEXNOUS",
            },
            "SEK": {
                "label": "Swedish krona", "bucket": "AFE", "series": _pts(fred_s, "SEK"),
                "quote": "local_per_usd", "source_id": "DEXSDUS",
            },
            "SGD": {
                "label": "Singapore dollar", "bucket": "AFE", "series": _pts(fred_s, "SGD"),
                "quote": "local_per_usd", "source_id": "DEXSIUS",
            },
            "TWD": {
                "label": "New Taiwan dollar", "bucket": "EM", "series": _pts(fred_s, "TWD"),
                "quote": "local_per_usd", "source_id": "DEXTAUS",
            },
            "THB": {
                "label": "Thai baht", "bucket": "EM", "series": _pts(fred_s, "THB"),
                "quote": "local_per_usd", "source_id": "DEXTHUS",
            },
            "LKR": {
                "label": "Sri Lankan rupee", "bucket": "EM", "series": _pts(fred_s, "LKR"),
                "quote": "local_per_usd", "source_id": "DEXSLUS",
            },
        },
        broad_dollar=_pts(fred_s, "DXY_BROAD"),
        afe_dollar=_pts(fred_s, "DXY_AFE"),
        eme_dollar=_pts(fred_s, "DXY_EME"),
        commodities={
            "WTI": {
                "label": "WTI crude", "category": "energy", "series": _pts(fred_s, "WTI_SPOT"),
                "cadence": "D", "change_kind": "diff", "unit": "$/bbl", "source_id": "DCOILWTICO",
            },
            "BRENT": {
                "label": "Brent crude", "category": "energy", "series": _pts(fred_s, "BRENT_SPOT"),
                "cadence": "D", "change_kind": "diff", "unit": "$/bbl", "source_id": "DCOILBRENTEU",
            },
            "NATGAS": {
                "label": "Henry Hub gas", "category": "energy", "series": _pts(fred_s, "NATGAS_SPOT"),
                "cadence": "D", "unit": "$/MMBtu", "source_id": "DHHNGSP",
            },
            "COAL": {
                "label": "Australian coal", "category": "energy", "series": _pts(fred_s, "COAL"),
                "cadence": "M", "unit": "$/metric ton", "source_id": "PCOALAUUSDM",
            },
            "ALL": {
                "label": "All commodities", "category": "broad", "series": _pts(fred_s, "COMMODITY_ALL"),
                "cadence": "M", "unit": "2016=100", "source_id": "PALLFNFINDEXM",
            },
            "COPPER": {
                "label": "Copper", "category": "industrial", "series": _pts(fred_s, "COPPER"),
                "cadence": "M", "unit": "$/metric ton", "source_id": "PCOPPUSDM",
            },
            "ALUMINUM": {
                "label": "Aluminum", "category": "industrial", "series": _pts(fred_s, "ALUMINUM"),
                "cadence": "M", "unit": "$/metric ton", "source_id": "PALUMUSDM",
            },
            "NICKEL": {
                "label": "Nickel", "category": "industrial", "series": _pts(fred_s, "NICKEL"),
                "cadence": "M", "unit": "$/metric ton", "source_id": "PNICKUSDM",
            },
            "WHEAT": {
                "label": "Wheat", "category": "agriculture", "series": _pts(fred_s, "WHEAT"),
                "cadence": "M", "unit": "$/metric ton", "source_id": "PWHEAMTUSDM",
            },
            "CORN": {
                "label": "Corn", "category": "agriculture", "series": _pts(fred_s, "CORN"),
                "cadence": "M", "unit": "$/metric ton", "source_id": "PMAIZMTUSDM",
            },
        },
        sofr=_pts(fred_s, "SOFR"),
        iorb=iorb if iorb is not None else pd.Series(dtype=float),
        effr=_pts(fred_s, "EFFR"),
        cp_nonfinancial_3m=_pts(cp_s, "CP_NONFIN_3M"),
        cp_financial_3m=_pts(cp_s, "CP_FIN_3M"),
        treasury_3m=_pts(cp_s, "DGS3M"),
        swap_lines_m=_pts(fred_s, "SWAP_LINES"),
        foreign_rrp_m=_pts(fred_s, "FOREIGN_RRP"),
        fima_repo_m=_pts(src.get("fred_custody", {}), "FIMA_REPO"),
        offshore_usd_credit_m=_pts(src.get("bis", {}), "GLI_OFFSHORE_USD"),
    ))

    # --- Windfetch (the lab's current-affairs wind read back from the
    #     Undertow FETCH pack; overlay only — never enters the composite) ---
    run("windfetch", lambda: eng_windfetch.analyze(src.get("windfetch")))

    # --- SONAR ---
    def _sonar():
        series_map: dict[str, tuple[str, str, pd.Series]] = {}
        for group in ("fred", "ofr"):
            for m, s in (src.get(group) or {}).items():
                spec = ALL_SERIES.get(m)
                # OFR dollar series arrive as raw dollars; _vol_b leaves
                # percent-scale series untouched.
                pts = _vol_b(s.points) if group == "ofr" else s.points.dropna()
                series_map[m] = (spec.label if spec else m, spec.unit if spec else "", pts)
        series_map["SOFR-IORB"] = ("SOFR-IORB spread", "bp", drv["spread_bp"])
        series_map["SOFR_TAIL"] = ("SOFR P99-P50 tail", "bp", drv["tail_bp"])
        series_map["SRF"] = ("SRF accepted", "$B", drv["srf"])
        series_map["TGA"] = ("Treasury General Account", "$B", drv["tga"])
        for m, s in ((src.get("crypto") or {}).get("candles") or {}).items():
            spec = ALL_SERIES.get(m)
            series_map[m] = (spec.label if spec else m, spec.unit if spec else "", s.points.dropna())
        stable_total = ((src.get("crypto") or {}).get("stable") or {}).get("total")
        if stable_total is not None and not stable_total.dropna().empty:
            series_map["STABLE_TOTAL"] = ("Total stablecoin circulation", "$B", stable_total.dropna())
        for m, s in ((src.get("palimpsest") or {}).get("series") or {}).items():
            spec = ALL_SERIES.get(m)
            if s is not None and not s.points.dropna().empty:
                series_map[m] = (spec.label if spec else m, spec.unit if spec else "", s.points.dropna())
        return eng_sonar.sweep(series_map)
    run("sonar", _sonar)

    # --- Composite ---
    subs = {
        "tails": eng_tails.tails_score(results["tails"]) if results["tails"].get("ok") else None,
        "kink": eng_kink.kink_score(results["kink"]) if results["kink"].get("ok") else None,
        "weather": eng_weather.weather_score(results["weather"], kink_b) if results["weather"].get("ok") else None,
        "confession": (
            eng_composite.confession_score(drv["srf_daily"], drv["dw_b"])
            if (not drv["srf_daily"].empty or not drv["dw_b"].dropna().empty)
            else None
        ),
        "rvxray": eng_rvxray.rvxray_score(results["rvxray"]) if results["rvxray"].get("ok") else None,
        "resonance": eng_resonance.resonance_score(results["resonance"]) if results["resonance"].get("ok") else None,
        "hydrophone": eng_hydrophone.hydrophone_score(results["hydrophone"]) if results["hydrophone"].get("ok") else None,
        "undertow": eng_undertow.undertow_score(results["undertow"]) if results["undertow"].get("ok") else None,
        "auctions": eng_auctions.auctions_score(results["auctions"]) if results["auctions"].get("ok") else None,
        "warehouse": eng_warehouse.warehouse_score(results["warehouse"]) if results["warehouse"].get("ok") else None,
        "buffers": eng_composite.buffers_score(float(drv["rrp"].iloc[-1])) if not drv["rrp"].empty else None,
    }
    results["composite"] = {
        **eng_composite.compose(subs),
        "subscores": {k: round(v, 1) if v is not None else None for k, v in subs.items()},
    }
    return results


# ---------------------------------------------------------------------------
# Deep layer (history reconstruction + Tell + Turn + Playbook + PROOF)
# ---------------------------------------------------------------------------

def _odds_ledger() -> list | None:
    """The as-published forward odds, appended daily by the dispatch CI and
    committed, one JSON object per line. None (not []) when the file is
    missing, so the court reports 'no ledger' rather than 'empty ledger'."""
    path = Path(__file__).resolve().parent / "dispatches" / "odds_ledger.jsonl"
    if not path.exists():
        return None
    rows = []
    for line in path.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows or None


def _bind_deep_history_boundary(deep: dict, evidence: dict | None = None) -> dict:
    """Make every extractable deep block retain the parent evidence boundary.

    This also upgrades legacy day-cache blobs in memory, so `/api/book` and a
    consumer extracting one nested block cannot lose the final-vintage warning.
    Eligibility is never inferred here: engine-level validation remains false
    unless a separate, explicit gate proves it.
    """
    boundary = dict(evidence or eng_history.vintage_evidence(None))
    deep["historical_evidence"] = boundary
    for name, block in deep.items():
        if name == "historical_evidence" or not isinstance(block, dict):
            continue
        block.setdefault("historical_evidence", dict(boundary))
        block.setdefault("validated_backtest", False)
        block.setdefault("real_money_eligible", False)
    return deep


def _deep_layer(src: dict, drv: dict, engines: dict, faults: list[dict]) -> dict:
    spread = drv["spread_bp"]
    if spread.empty:
        return _bind_deep_history_boundary({"ok": False, "reason": "no spread history"})
    # VERSION in the key: a release that adds deep blocks (tidetables/swell/
    # stacker/book in v2.3) must not serve a pre-upgrade blob for up to 12h.
    cache_key = f"deep:{VERSION}:{spread.index[-1].date().isoformat()}"
    # Failure-aware cache: a blob computed with any failed layer only lives 30
    # minutes, so a transient fault can't poison the whole data-day (bit us
    # twice during the v2 build).
    cached = store.load_blob(cache_key)
    if cached is not None and _snapshot_contains_restricted_cfets({"deep": cached}):
        logging.getLogger("seiche.assemble").warning(
            "ignored deep cache containing restricted CFETS-derived data"
        )
        cached = None
    if cached is not None:
        ttl_min = DEEP_TTL_MIN if cached.get("_all_ok") else 30
        ts = cached.get("_computed_at")
        try:
            from datetime import datetime, timedelta, timezone
            fresh = ts is not None and (
                datetime.now(timezone.utc) - datetime.fromisoformat(ts)
                < timedelta(minutes=ttl_min)
            )
        except (TypeError, ValueError):
            fresh = False
        if fresh:
            nested = cached.get("history", {})
            evidence = cached.get("historical_evidence") or (
                nested.get("vintage_evidence") if isinstance(nested, dict) else None
            )
            return _bind_deep_history_boundary(cached, evidence)

    fred_s = src.get("fred", {})
    out: dict = {"ok": True}
    try:
        pair_full = engines.get("rvxray", {}).get("_pair_full", pd.Series(dtype=float))
        dig_full = engines.get("auctions", {}).get("_index_full", pd.Series(dtype=float))
        hist_kwargs = dict(
            spread_bp=spread,
            tail_bp=drv["tail_bp"],
            srf_accepted=drv["srf"],
            dw_b=drv["dw_b"],
            rrp_b=drv["rrp"],
            res_gdp=drv["res_gdp"],
            pair_b=pair_full,
            digestion=dig_full,
        )
        hist = eng_history.build(**hist_kwargs)
        idx, pctl = hist["index"], hist["pctl"]
        out["history"] = {
            "ok": True,
            "current": {
                "value": round(float(idx.iloc[-1]), 1),
                "pctl": round(float(pctl.dropna().iloc[-1]), 0) if not pctl.dropna().empty else None,
                "regime": str(hist["regime_series"].iloc[-1]),
            },
            "weights": hist["weights"],
            "excluded": hist["excluded"],
            "vintage_evidence": hist["vintage_evidence"],
            "series": [
                [d.date().isoformat(), round(float(v), 1),
                 round(float(pctl.loc[d]), 0) if pd.notna(pctl.loc[d]) else None]
                for d, v in idx.iloc[::2].items()
            ],
            "method": hist["method"],
        }
    except Exception as e:
        faults.append({"source": "deep:history", "detail": f"{type(e).__name__}: {e}"})
        out["history"] = safe_failure_envelope(e)
        _bind_deep_history_boundary(out)
        _assert_snapshot_rights({"deep": out})
        store.save_blob(cache_key, out)
        return out

    candles = (src.get("crypto") or {}).get("candles", {})

    def _outcome_series(m: str) -> pd.Series:
        if m in candles:
            return candles[m].points.dropna()
        return _pts(fred_s, m)

    outcomes = {m: _outcome_series(m) for m in PLAYBOOK_OUTCOMES}

    def run(name: str, fn):
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = safe_failure_envelope(e)
            faults.append({"source": f"deep:{name}", "detail": traceback.format_exc(limit=2)})

    run("tell", lambda: eng_market.tell(
        idx, _pts(fred_s, "VIX"), _pts(fred_s, "HY_OAS"),
        _pts(fred_s, "IG_OAS"), _pts(fred_s, "DGS10")))

    # Full-overlap Tell series, shared by Playbook, the Stack and the Book
    # (the payload's tell series is tail-limited).
    try:
        mkt_stress, _ = eng_market.market_stress(
            _pts(fred_s, "VIX"), _pts(fred_s, "HY_OAS"),
            _pts(fred_s, "IG_OAS"), _pts(fred_s, "DGS10"))
        plumb_p = eng_market._rpctl(idx.dropna())
        _both = pd.concat({"p": plumb_p, "m": mkt_stress}, axis=1).dropna()
        full_tell = _both["p"] - _both["m"]
    except Exception:
        full_tell = pd.Series(dtype=float)

    def _playbook():
        tell_rows = out.get("tell", {}).get("series") or []
        tell_series = pd.Series(
            [r[1] for r in tell_rows],
            index=pd.DatetimeIndex([r[0] for r in tell_rows]),
            dtype=float,
        )
        return eng_playbook.analyze(idx, full_tell if not full_tell.empty else tell_series, outcomes)
    run("playbook", _playbook)

    run("turn", lambda: eng_turn.analyze(spread, drv["rrp"], drv["tail_bp"], drv["res_gdp_pctl"]))
    if hist["vintage_evidence"]["validated_backtest_eligible"]:
        run("backtest", lambda: eng_backtest.run(pctl, spread, outcomes))
    else:
        out["backtest"] = {
            "ok": False,
            "status": "UNVERIFIED",
            "reason": (
                "historical inputs are construction-PIT/current-vintage; "
                "a validated replay requires ALFRED or as-published captures"
            ),
            "vintage_evidence": hist["vintage_evidence"],
        }

    # Microseism — calendar-gated Hawkes on the shared pop statistic. Lives in
    # the deep layer for the same reason Bathymetry does: its state variable
    # is the PROOF event's own pop, and the expanding refits are heavy enough
    # to want the per-data-day cache. Context, never composite.
    run("microseism", lambda: eng_microseism.analyze(spread))

    # Leak Audit — the one-switch leakage protocol run against our own lite
    # index (deliberately leaky variants scored on PROOF's events; the gains
    # we refuse to claim, published). Uses the exact history.build kwargs.
    run("leakaudit", lambda: eng_leakaudit.run(hist_kwargs, spread))

    # Tide Tables — analog forecast over the same plumbing state Echo matches
    # on, but expanding-z (no look-ahead) and against ALL history.
    def _tidetables():
        iorb = drv["iorb"]
        if iorb is None:
            return {"ok": False, "reason": "IORB/SOFR unavailable"}
        ofr_s = src.get("ofr", {})
        effr = _pts(fred_s, "EFFR")
        comps = {
            "sofr_iorb": spread,
            "effr_iorb": ((effr - iorb.reindex(effr.index).ffill()) * 100.0),
            "bgcr_sofr": ((_pts(ofr_s, "BGCR") - _pts(fred_s, "SOFR")) * 100.0),
            "tail": drv["tail_bp"],
            "rrp": drv["rrp"],
            "tga_chg5": drv["tga"].diff(5),
            "reserves_chg4w": _pts(fred_s, "WRESBAL").diff(4),
            "srf": drv["srf"],
        }
        return eng_tidetables.analyze(
            {k: v for k, v in comps.items() if not v.dropna().empty}, spread)
    run("tidetables", _tidetables)
    # (_hindcast stays on the result until the Stack consumes it; all nested
    # private keys are popped together before the blob cache below.)

    # Swell Forecast — the funding-stress forward curve: calendar-bucket
    # exceedance hazards + Undertow damping state + coupon settlements,
    # compounded over the next 42bd and walk-forward validated.
    def _swell():
        res = eng_swell.analyze(
            spread_bp=spread,
            damping_pctl=engines.get("undertow", {}).get("_damping_pctl"),
            auctions=(src.get("auctions") or {}).get("auctions", pd.DataFrame()),
            upcoming=(src.get("upcoming") or {}).get("upcoming", pd.DataFrame()),
        )
        # _p5_series stays on the result until the Stack consumes it as a
        # member; the private-key sweep below pops it before the blob cache.
        return res
    run("swell", _swell)

    # Bathymetry — the basin floor mapped: empirical Langevin potential,
    # the quantum-dual relaxation spectrum, entropy production, and the
    # first-passage event forecast (joins the Stack as its own member).
    run("bathymetry", lambda: eng_bathymetry.analyze(spread))

    # Stochastic scenarios on the reconstructed index (0-100): a Markov regime
    # chain, an OU+jump analytic marginal, and a Monte Carlo path fan. Three
    # different views of "where does the index go from here" — discrete-regime,
    # analytic-endpoint, and simulated-path-max.
    _comp = engines.get("composite", {})
    _cval, _creg = _comp.get("value"), _comp.get("regime")
    run("markov", lambda: eng_markov.analyze(idx, hist.get("regime_series"), current_regime=_creg))
    run("oujump", lambda: eng_oujump.analyze(idx, current_value=_cval))
    run("montecarlo", lambda: eng_montecarlo.analyze(idx, current_value=_cval))

    # Funding Pop: the pop prognosis, chop or current? (speaks on live pops)
    # Published under BOTH keys. `funding_pop` is canonical from 2026-08-04;
    # `riptide` is the original wire key and stays for compatibility, because
    # /api/overview consumers read deep.riptide and the resolved-pop history was
    # recorded under it. Do not drop the alias without a deprecation window.
    run("funding_pop", lambda: eng_funding_pop.analyze(
        spread_bp=spread,
        rrp_b=drv["rrp"],
        damping_pctl=engines.get("undertow", {}).get("_damping_pctl"),
    ))
    out["riptide"] = out["funding_pop"]

    # The Gyre — Takens/EDM: is the basin deterministic enough to predict at
    # all, how fast does predictability decay, and is it state-dependent?
    # Deep-layer citizen: its full hindcast is the expensive part and its
    # output is forecast-context, never composite evidence.
    run("gyre", lambda: eng_gyre.analyze(spread))

    # Referee GLI — the "global liquidity" headline claims tested on their
    # publicly reconstructible layer (G3 balance sheets in USD). Deep-layer
    # citizen for the bootstrap cost; context, never composite. Published
    # standalone at referee.html.
    run("refereegli", lambda: eng_refereegli.analyze(
        fed_assets=_pts(fred_s, "FED_ASSETS_LONG"),
        ecb_assets=_pts(fred_s, "ECB_ASSETS"),
        boj_assets=_pts(fred_s, "BOJ_ASSETS"),
        usd_per_eur=_pts(fred_s, "EURUSD_LONG"),
        jpy_per_usd=_pts(fred_s, "JPY_LONG"),
        equity=_pts(fred_s, "NASDAQ"),
        indpro=_pts(fred_s, "INDPRO"),
        tga=_pts(fred_s, "TGA_LONG"),
        rrp=_pts(fred_s, "RRP_LONG"),
    ))

    # The Rubric: the coded evidence matrix (arXiv:2606.08285, re-coded for
    # a terminal that trades nothing) applied to Seiche itself FIRST, then to
    # the GL case above, and shipped inside the refereegli block so the two
    # matrices publish side by side. Repo facts plus the block above; the
    # grades ride on the dark block too. Display-only, like every referee
    # surface.
    try:
        out["refereegli"]["rubric"] = rubric.build(out.get("refereegli"))
    except Exception as e:
        faults.append({"source": "deep:rubric", "detail": f"{type(e).__name__}: {e}"})
        out["refereegli"]["rubric"] = safe_failure_envelope(e)

    # Orthogonal signal test: rebuild the index WITHOUT the tails component
    # (which contains the spread/tail variables the event is defined on) and
    # rerun event capture. If this still leads events, the claim is causal
    # structure, not autocorrelation.
    def _orthogonal():
        hist_o = eng_history.build(
            spread_bp=spread,
            tail_bp=drv["tail_bp"],
            srf_accepted=drv["srf"],
            dw_b=drv["dw_b"],
            rrp_b=drv["rrp"],
            res_gdp=drv["res_gdp"],
            pair_b=engines.get("rvxray", {}).get("_pair_full", pd.Series(dtype=float)),
            digestion=engines.get("auctions", {}).get("_index_full", pd.Series(dtype=float)),
            exclude=("tails",),
        )
        cap = eng_backtest.capture(hist_o["pctl"], spread)
        if cap.get("ok"):
            cap["weights"] = hist_o["weights"]
            cap["excluded_components"] = hist_o["excluded"]
            cap["why"] = (
                "same event-capture test with the target's own variable family removed "
                "from the signal (no spread, no tails) — kink-proxy/confession/rvxray/"
                "auctions/buffers only"
            )
        return cap
    if out.get("backtest", {}).get("ok"):
        try:
            out["backtest"]["orthogonal"] = _orthogonal()
        except Exception as e:
            faults.append({"source": "deep:orthogonal", "detail": f"{type(e).__name__}: {e}"})
            out["backtest"]["orthogonal"] = safe_failure_envelope(e)

    def _ml():
        stable = (src.get("crypto") or {}).get("stable", {})
        X, y = eng_mlpred.build_features(
            spread_bp=spread,
            tail_bp=drv["tail_bp"],
            srf=drv["srf"],
            dw_b=drv["dw_b"],
            rrp_b=drv["rrp"],
            res_gdp_pctl=drv["res_gdp_pctl"],
            pair_b=engines.get("rvxray", {}).get("_pair_full", pd.Series(dtype=float)),
            digestion=engines.get("auctions", {}).get("_index_full", pd.Series(dtype=float)),
            lite_index=idx,
            lite_pctl=pctl,
            vix=_pts(fred_s, "VIX"),
            hy_oas=_pts(fred_s, "HY_OAS"),
            dgs10=_pts(fred_s, "DGS10"),
            inr=_pts(fred_s, "INR"),
            usdt_peg_bp=drv["usdt_peg_bp"],
            stable_total_b=stable.get("total", pd.Series(dtype=float)),
        )
        # Transfer learning: TED-era rows (1990–2018) as down-weighted extra
        # training mass — same funding-spread feature slots, older clothes.
        # The gain vs the SOFR-only model is reported either way; the
        # orthogonal run below deliberately gets NO pretrain (its rows are
        # the spread family being excluded).
        pre = eng_mlpred.build_pretrain_rows(_pts(fred_s, "TED"))
        res = eng_mlpred.walk_forward(X, y, pre=pre)
        if res.get("ok") and pre is not None:
            solo = eng_mlpred.walk_forward(X, y, full_report=False)
            if solo.get("ok"):
                gain = round(res["validation"]["auroc"] - solo["validation"]["auroc"], 3)
                res["transfer"] = {
                    "auroc_pooled": res["validation"]["auroc"],
                    "auroc_solo": solo["validation"]["auroc"],
                    "brier_pooled": res["validation"]["brier"],
                    "brier_solo": solo["validation"]["brier"],
                    "gain_auroc": gain,
                    "pretrain_rows": res["validation"].get("pretrain_rows"),
                    "pretrain_events": res["validation"].get("pretrain_events"),
                    "verdict": (
                        f"TED-era pretraining helps out-of-sample (+{gain} AUROC) — "
                        "the funding-stress grammar generalizes across eras"
                        if gain > 0.005 else
                        f"TED-era pretraining does NOT help ({gain:+} AUROC) — "
                        "the SOFR era speaks for itself; pretraining kept for robustness only"
                    ),
                }
        # Orthogonal ML: drop the target's variable family from the features
        # and re-evaluate. The honest AUROC for "the model knows something
        # beyond spread autocorrelation" is THIS one.
        if res.get("ok"):
            keep = [c for c in X.columns if c not in eng_mlpred.ORTHOGONAL_DROP]
            orth = eng_mlpred.walk_forward(X[keep], y, full_report=False)
            res["orthogonal"] = (
                {
                    "auroc": orth["validation"]["auroc"],
                    "brier": orth["validation"]["brier"],
                    "brier_climatology": orth["validation"]["brier_climatology"],
                    "p_event_5bd": orth["p_event_5bd"],
                    "verdict": orth["verdict"],
                    "utility": orth.get("utility"),
                    "dropped_features": [c for c in eng_mlpred.ORTHOGONAL_DROP if c in X.columns],
                }
                if orth.get("ok")
                else orth
            )
        return res
    run("ml", _ml)

    # --- The Stack + The Book (the signal made accountable) -----------------
    def _stacker():
        ml_blk = out.get("ml", {})
        tide_blk = out.get("tidetables", {})
        M = eng_stacker.build_member_matrix(
            rule_pctl=pctl,
            ml_p=ml_blk.get("_p_daily") if ml_blk.get("ok") else None,
            tide_p=tide_blk.get("_hindcast") if tide_blk.get("ok") else None,
            swell_p=(out.get("swell") or {}).get("_p5_series")
            if (out.get("swell") or {}).get("ok") else None,
            bathy_p=(out.get("bathymetry") or {}).get("_p5_series")
            if (out.get("bathymetry") or {}).get("ok") else None,
            tell=full_tell if not full_tell.empty else None,
        )
        yv = eng_stacker.event_labels(spread, M.index)
        return eng_stacker.walk_forward_stack(M, yv, regime=hist["regime_series"])
    run("stacker", _stacker)

    # Regatta + Sea Room consume the Stack's own OOS streams (private keys,
    # read here BEFORE the strip below): the fleet raced snoop-corrected, and
    # the published probability wrapped in coverage-guaranteed sets.
    def _regatta():
        stk = out.get("stacker", {})
        if not stk.get("ok"):
            return {"ok": False, "reason": f"stacker unavailable: {stk.get('reason')}"}
        return eng_regatta.analyze(stk["_cal"], stk["_p_pub"], stk["_y"])
    run("regatta", _regatta)

    def _searoom():
        stk = out.get("stacker", {})
        if not stk.get("ok"):
            return {"ok": False, "reason": f"stacker unavailable: {stk.get('reason')}"}
        return eng_searoom.analyze(stk["_p_pub"], stk["_y"],
                                   regime=hist["regime_series"])
    run("searoom", _searoom)

    # Sea State — the statistical regime gauge (filtered 2-state HMM).
    run("seastate", lambda: eng_seastate.analyze(spread))

    def _book():
        stk = out.get("stacker", {})
        if not stk.get("ok"):
            return {"ok": False, "reason": f"stacker unavailable: {stk.get('reason')}"}
        rets = eng_book.build_returns(
            dgs2=_pts(fred_s, "DGS2"),
            dgs10=_pts(fred_s, "DGS10"),
            sp500=_pts(fred_s, "SP500"),
            btc=drv["btc"],
            tb3m=_pts(fred_s, "TB3M"),
        )
        return eng_book.run(
            stk["_p"], stk["_member_probs"], stk["_dispersion"],
            full_tell, rets, pit_records=store.load_pit_records(),
        )
    run("book", _book)

    # Nested private keys are pandas objects — json blob cache would crash on
    # them, and the API strips them anyway. Top-level _all_ok/_computed_at stay.
    for key in ("ml", "tidetables", "stacker", "swell", "bathymetry", "gyre", "microseism", "seastate"):
        blk = out.get(key)
        if isinstance(blk, dict):
            for k in [k for k in blk if str(k).startswith("_")]:
                blk.pop(k)

    _bind_deep_history_boundary(out, hist["vintage_evidence"])
    out["_all_ok"] = all(
        isinstance(v, dict) and v.get("ok")
        for k, v in out.items()
        if k not in ("ok", "historical_evidence") and not str(k).startswith("_")
    )
    out["_computed_at"] = utcnow_iso()
    _assert_snapshot_rights({"deep": out})
    store.save_blob(cache_key, out)
    return out


# ---------------------------------------------------------------------------
# Headline, calendar, provenance
# ---------------------------------------------------------------------------

def _headline(src: dict, drv: dict) -> dict:
    fred_s = src.get("fred", {})

    def last(key, scale=1.0, digits=3):
        s = fred_s.get(key)
        if s is None or s.points.dropna().empty:
            return None
        p = s.points.dropna()
        return {"value": round(float(p.iloc[-1]) * scale, digits), "asof": p.index[-1].date().isoformat()}

    tga = drv["tga"]
    srf = drv["srf_daily"]
    dw = drv["dw_b"].dropna()
    return {
        "sofr_pct": last("SOFR"),
        "effr_pct": last("EFFR"),
        "iorb_pct": last("IORB"),
        "reserves_b": last("WRESBAL", 1e-3),
        "rrp_b": last("RRPONTSYD"),
        "tga_b": {"value": round(float(tga.iloc[-1]), 1), "asof": tga.index[-1].date().isoformat()} if not tga.empty else None,
        "srf_accepted_b": {"value": round(float(srf["accepted"].iloc[-1]), 2), "asof": srf.index[-1].date().isoformat()} if not srf.empty else None,
        "dw_b": {"value": round(float(dw.iloc[-1]), 1), "asof": dw.index[-1].date().isoformat()} if not dw.empty else None,
        "vix": last("VIX", 1.0, 2),
        "hy_oas_pct": last("HY_OAS", 1.0, 2),
    }


def _bill_desk(src: dict) -> list[dict]:
    """Latest auction high rate per bill tenor + next auction date — the
    'if you must park cash' pane."""
    au = (src.get("auctions") or {}).get("auctions", pd.DataFrame())
    up = (src.get("upcoming") or {}).get("upcoming", pd.DataFrame())
    if au.empty:
        return []
    df = au.copy()
    df = df[df["security_type"].str.contains("Bill", case=False, na=False)]
    if df.empty:
        return []
    df["auction_date"] = pd.to_datetime(df["auction_date"], errors="coerce")
    df["rate"] = pd.to_numeric(df["high_discnt_rate"], errors="coerce")
    df = df.dropna(subset=["auction_date", "rate"])
    nxt: dict[str, str] = {}
    if not up.empty and "security_term" in up.columns:
        u = up.copy()
        u["auction_date"] = pd.to_datetime(u.get("auction_date"), errors="coerce")
        u = u.dropna(subset=["auction_date"])
        for term, grp in u.groupby(u["security_term"].str.strip()):
            nxt[term] = grp["auction_date"].min().date().isoformat()
    rows = []
    for term, grp in df.groupby(df["security_term"].str.strip()):
        g = grp.sort_values("auction_date")
        rows.append(
            {
                "tenor": term,
                "last_high_rate_pct": round(float(g["rate"].iloc[-1]), 3),
                "last_auction": g["auction_date"].iloc[-1].date().isoformat(),
                "next_auction": nxt.get(term),
            }
        )
    order = {"4-Week": 0, "8-Week": 1, "13-Week": 2, "17-Week": 3, "26-Week": 4, "52-Week": 5}
    rows.sort(key=lambda r: order.get(r["tenor"], 99))
    return rows


def _calendar(src: dict, engines: dict, deep: dict, drv: dict) -> dict:
    today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    fomc = []
    for d in FOMC_DECISION_DATES:
        ts = pd.Timestamp(d)
        delta = int((ts - today).days)
        if 0 <= delta <= 90:
            fomc.append({"date": d, "days_until": delta})
    tax = []
    for m, day in sorted({(3, 15), (4, 15), (6, 15), (9, 15), (12, 15)}):
        for y in (today.year, today.year + 1):
            ts = pd.Timestamp(year=y, month=m, day=day)
            delta = int((ts - today).days)
            if 0 <= delta <= 90:
                tax.append({"date": ts.date().isoformat(), "days_until": delta})
    tax.sort(key=lambda r: r["days_until"])
    weather = engines.get("weather", {})
    turn = (deep or {}).get("turn", {})
    return {
        "fomc_next_90d": fomc,
        "corporate_tax_next_90d": tax[:4],
        "upcoming_settlements": weather.get("upcoming_settlements", []),
        "crunch_windows": weather.get("crunch_windows", []),
        "next_turn": turn.get("next_turn") if isinstance(turn, dict) else None,
        "bill_desk": _bill_desk(src),
    }


def _provenance(src: dict) -> list[dict]:
    src = _rights_eligible_sources(src)
    prov = []
    for group in ("fred", "ofr", "ecb", "eia_petroleum", "eia_inventory"):
        for s in (src.get(group) or {}).values():
            prov.append(s.provenance())
    for s in ((src.get("crypto") or {}).get("candles") or {}).values():
        prov.append(s.provenance())
    for s in ((src.get("palimpsest") or {}).get("series") or {}).values():
        prov.append(s.provenance())
    st = store.load_series("STABLE_TOTAL")
    if st is not None:
        prov.append(st.provenance())
    for key, label in (
        ("nyfed_rates", "NY Fed secured rates"),
        ("nyfed_srf", "NY Fed repo ops"),
        ("nyfed_pd", "NY Fed primary dealer stats"),
        ("nyfed_fxs", "NY Fed USD swap operations"),
        ("tga", "Treasury DTS/TGA"),
        ("auctions", "Treasury auctions"),
        ("upcoming", "Treasury upcoming auctions"),
        ("tff", "CFTC TFF"),
        ("commodity_cot", "CFTC commodity futures positioning"),
    ):
        blk = src.get(key)
        if blk:
            # These envelopes contain heterogeneous tables rather than one
            # cadence-bearing Series.  A recent HTTP fetch proves transport
            # health, not that every observation in the table is current.
            # Keep the fetch clock, but do not manufacture an observation-
            # freshness claim that the envelope cannot support.
            prov.append({
                "mnemonic": key,
                "source": key.split("_")[0],
                "label": label,
                "asof": None,
                "fetched_at": blk.get("fetched_at"),
                "staleness": "unknown",
                "age_days": None,
                "freshness_grace_days": None,
                "freshness_basis": (
                    "fetch clock only; this table contains heterogeneous observation dates"
                ),
            })
    return prov


def _strip_private(obj):
    """Remove '_'-prefixed keys (internal pandas objects) before serializing."""
    if isinstance(obj, dict):
        return {k: _strip_private(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, list):
        return [_strip_private(v) for v in obj]
    return obj


def _record_pit(engines: dict, deep: dict, navigator: dict | None = None) -> None:
    """Forward-accruing as-published record: today's index, subscores, tell,
    every forecast view (the Navigator included), and the Book's positions —
    the primitives the live track record replays; no reconstruction can
    polish them."""
    comp = engines.get("composite", {})
    if not comp.get("ok"):
        return
    day = utcnow_iso()[:10]
    stk = (deep or {}).get("stacker", {})
    book_today = ((deep or {}).get("book") or {}).get("today") or {}
    views = dict(stk.get("members_now") or {}) if stk.get("ok") else {}
    if navigator and navigator.get("ok"):
        views["navigator"] = navigator.get("p_event_5bd")
    record = {
            "date": day,
            "value": comp.get("value"),
            "regime": comp.get("regime"),
            "coverage_pct": comp.get("coverage_pct"),
            "subscores": comp.get("subscores"),
            # the weight vector that produced this value — without it a future
            # rebalance puts an undetectable structural break in the record
            "weights": dict(COMPOSITE_WEIGHTS),
            "tell": (deep or {}).get("tell", {}).get("tell"),
            "forecasts": {
                "p_ensemble": stk.get("p_now") if stk.get("ok") else None,
                "dispersion": stk.get("dispersion_now") if stk.get("ok") else None,
                "views": views,
            } if views else None,
            "book": {
                "stance": book_today.get("stance"),
                "p_ensemble": book_today.get("p_ensemble"),
                "dispersion": book_today.get("dispersion"),
                "positions": [
                    [p.get("sleeve"), p.get("weight")]
                    for p in book_today.get("positions", [])
                ],
            } if book_today else None,
    }
    store.save_blob(f"pit:{day}", record)
    _notarize(day, record)
    _attest(day, record)


def _notarize(day: str, record: dict) -> None:
    """Commit the as-published reading to the tamper-evident notary chain.
    Best-effort and fail-loud-in-logs: a notary error must never stop the board
    from updating."""
    try:
        from seiche import notary
        notary.commit(day, record)
    except Exception as exc:  # pragma: no cover - defensive
        logging.getLogger("seiche.assemble").warning(
            "notary commit failed for %s: %s", day, exc)


def _attest(day: str, record: dict) -> None:
    """Signed layer over the record (seiche/attest.py): commit the day's
    aggregate reading to the 'stress_readings' PIT stream and Ed25519-sign it.
    Off by default (SEICHE_ATTEST=1 enables); best-effort and
    fail-loud-in-logs, same contract as the notary — attestation must never
    stop the board from updating."""
    if os.getenv("SEICHE_ATTEST", "0") != "1":
        return
    try:
        from seiche import attest
        attest.attest_stress_reading(day, record)
    except Exception as exc:  # pragma: no cover - defensive
        logging.getLogger("seiche.assemble").warning(
            "attest failed for %s: %s", day, exc)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def _snapshot_contains_restricted_cfets(payload: object) -> bool:
    """Recursively detect restricted data identities and derived engine rows.

    Ordinary prose leaves are exempt unless their containing mapping is
    structurally quantitative. This validator follows typed identifiers (for
    example ``mnemonic`` and ``source``), exact restricted keys, and the known
    legacy engine shapes. It therefore finds a poisoned row under any wrapper
    without rejecting lawful editorial or exact-schema Palimpsest target text
    that happens to discuss a benchmark.
    """

    def folded_text(value: str) -> str:
        return unicodedata.normalize("NFKC", value).strip().casefold()

    normalized_identifiers = {
        re.sub(r"[^a-z0-9]+", "_", folded_text(value)).strip("_")
        for value in RESTRICTED_SNAPSHOT_IDENTIFIERS
    }
    compact_identifiers = {
        re.sub(r"[^a-z0-9]+", "", folded_text(value))
        for value in RESTRICTED_SNAPSHOT_IDENTIFIERS
    }
    restricted_tokens = {"cfets", "chinamoney", "shibor", "fdr007", "dr007"}
    identity_qualifiers = {
        "1d",
        "7d",
        "benchmark",
        "china",
        "cn",
        "cny",
        "column",
        "columns",
        "feature",
        "fixing",
        "id",
        "input",
        "instrument",
        "market",
        "metric",
        "money",
        "on",
        "observed",
        "overnight",
        "parity",
        "rate",
        "rates",
        "repo",
        "secured",
        "series",
        "unsecured",
        "usdcny",
    }

    def restricted_identifier(
        value: object,
        *,
        typed: bool = False,
        strict: bool = False,
    ) -> bool:
        if not isinstance(value, str):
            return False
        folded = folded_text(value)
        normalized = re.sub(r"[^a-z0-9]+", "_", folded).strip("_")
        compact = re.sub(r"[^a-z0-9]+", "", folded)
        if (
            folded in RESTRICTED_SNAPSHOT_IDENTIFIERS
            or normalized in normalized_identifiers
            or compact in compact_identifiers
            or re.fullmatch(r"cn\.cfets\.[a-z0-9_.:-]+", folded)
        ):
            return True
        if not typed:
            return False
        tokens = set(re.findall(r"[a-z0-9]+", folded))
        if strict:
            return bool(restricted_tokens & tokens) or any(
                marker in compact for marker in restricted_tokens
            )
        return bool(restricted_tokens & tokens) and tokens <= (
            restricted_tokens | identity_qualifiers
        )

    def restricted_mirror_url(value: object) -> bool:
        if not isinstance(value, str):
            return False
        try:
            parsed = urllib.parse.urlsplit(unicodedata.normalize("NFKC", value).strip())
        except ValueError:
            return False
        host = (parsed.hostname or "").casefold().rstrip(".")
        path = urllib.parse.unquote(parsed.path).casefold()
        if host == "chinamoney.com.cn" or host.endswith(".chinamoney.com.cn"):
            return True
        if host in {"palimpsest.info", "www.palimpsest.info"}:
            return "/readings/china-econ" in path
        if host == "raw.githubusercontent.com":
            return "/palimpsest/" in path and "/china-econ" in path
        return False

    def identity_field(field: str) -> bool:
        return (
            field in RESTRICTED_SNAPSHOT_IDENTITY_FIELDS
            or field in RESTRICTED_SNAPSHOT_IDENTITY_CONTAINERS
            or field.endswith(RESTRICTED_SNAPSHOT_IDENTITY_SUFFIXES)
        )

    def strict_identity_field(field: str) -> bool:
        return identity_field(field) and (
            field not in RESTRICTED_SNAPSHOT_DISPLAY_IDENTITY_FIELDS
        )

    def url_field(field: str) -> bool:
        return (
            field in {"href", "hrefs", "mirror", "mirrors", "url", "urls", "uri", "uris"}
            or field.endswith(RESTRICTED_SNAPSHOT_URL_SUFFIXES)
        )

    def restricted_engine_shape(value: dict) -> bool:
        if str(value.get("harbor", "")).upper() == "CHINA" and (
            value.get("rate") is not None
            or value.get("rate2") is not None
            or value.get("regime") is not None
        ):
            return True
        if (
            str(value.get("basin", "")).upper() == "CHINA"
            and {"anchor", "value_bp", "z", "asof"} & set(value)
        ):
            return True
        if str(value.get("key", "")).upper() == "CNY" and any(
            value.get(field) is not None
            for field in (
                "policy_diff_vs_effr_bp",
                "policy_rate_label",
                "policy_rate_cadence",
                "policy_asof",
            )
        ):
            return True
        rate_labels = value.get("rate_labels")
        if (
            isinstance(rate_labels, list)
            and ("harbors" in value or "rate_rows" in value)
            and any(str(label).upper() == "CHINA" for label in rate_labels)
        ):
            return True
        return False

    def restricted_quantitative_identity(
        value: object,
        *,
        strict: bool = False,
    ) -> bool:
        if isinstance(value, (list, tuple)):
            return any(
                restricted_quantitative_identity(item, strict=strict) for item in value
            )
        if isinstance(value, dict):
            return any(
                restricted_quantitative_identity(item, strict=strict)
                for item in value.values()
            )
        return restricted_mirror_url(value) or restricted_identifier(
            value,
            typed=True,
            strict=strict,
        )

    def restricted_quantitative_member(field: str, value: object) -> bool:
        """Scan one quantitative-row member using its declared semantic role."""

        if isinstance(value, dict):
            return any(
                restricted_quantitative_member(folded_text(str(key)), nested)
                for key, nested in value.items()
            )
        if isinstance(value, (list, tuple)):
            return any(restricted_quantitative_member(field, item) for item in value)
        return restricted_mirror_url(value) or restricted_identifier(
            value,
            typed=True,
            strict=field not in RESTRICTED_SNAPSHOT_PROSE_FIELDS,
        )

    def restricted_target_identity(value: object) -> bool:
        if isinstance(value, dict):
            return any(restricted_target_identity(item) for item in value.values())
        return restricted_quantitative_identity(value)

    def quantitative_scalar(value: object) -> bool:
        return not isinstance(value, (bool, np.bool_)) and isinstance(
            value,
            (int, float, np.integer, np.floating),
        )

    def numeric_observation(value: object) -> bool:
        if quantitative_scalar(value):
            return True
        if not isinstance(value, str):
            return False
        try:
            return math.isfinite(float(unicodedata.normalize("NFKC", value).strip()))
        except ValueError:
            return False

    def observed_series_has_data(value: object, *, field: str) -> bool:
        if (
            field in RESTRICTED_SNAPSHOT_QUALITATIVE_NUMERIC_FIELDS
            and not isinstance(value, (dict, list, tuple))
        ):
            return False
        if isinstance(value, dict):
            return any(
                observed_series_has_data(
                    nested,
                    field=folded_text(str(key)),
                )
                for key, nested in value.items()
            )
        if isinstance(value, (list, tuple)):
            if not value:
                return False
            if any(
                observed_series_has_data(item, field=field)
                for item in value
                if isinstance(item, (dict, list, tuple))
            ):
                return True
            return any(numeric_observation(item) for item in value)
        return numeric_observation(value)

    def quantitative_field(field: str, value: object) -> bool:
        if value is None:
            return False
        if (
            field in RESTRICTED_SNAPSHOT_OBSERVED_SERIES_FIELDS
            or field.endswith(RESTRICTED_SNAPSHOT_OBSERVED_SERIES_SUFFIXES)
        ):
            return observed_series_has_data(value, field=field)
        if field in RESTRICTED_SNAPSHOT_QUANTITATIVE_FIELDS:
            return True
        if field.startswith(RESTRICTED_SNAPSHOT_METRIC_PREFIXES):
            return True
        if field.endswith(RESTRICTED_SNAPSHOT_METRIC_SUFFIXES):
            return True
        return False

    def lawful_farbasin_target(
        value: dict,
        path: tuple[str, ...],
        sequence_depth: int,
    ) -> bool:
        """Admit only the real Palimpsest target schema at its exact path."""

        if (
            path != FARBASIN_TARGET_PATH
            or sequence_depth != 1
            or set(value) != FARBASIN_TARGET_FIELDS
        ):
            return False
        threat = value["threat"]
        threat_ok = threat is None or (
            not isinstance(threat, bool)
            and isinstance(threat, (int, float))
            and (not isinstance(threat, float) or math.isfinite(threat))
        )
        return (
            isinstance(value["term"], str)
            and (value["domain"] is None or isinstance(value["domain"], str))
            and threat_ok
            and (value["is_new"] is None or isinstance(value["is_new"], bool))
        )

    def quantitative_subtree(field: str, value: object) -> bool:
        if quantitative_scalar(value):
            return field not in RESTRICTED_SNAPSHOT_QUALITATIVE_NUMERIC_FIELDS
        if quantitative_field(field, value):
            return True
        return False

    def restricted_quantitative_mapping(value: dict) -> bool:
        """Detect restricted identities carried by a quantitative record.

        Quantitative meaning is structural: a numeric sibling, a named or
        patterned metric, or a non-empty observed-series container is enough.
        Once established, every direct string/list sibling is interpreted as
        typed identity rather than prose, so novel aliases cannot reopen the
        boundary.
        """

        has_quantitative_value = any(
            quantitative_subtree(folded_text(str(key)), nested)
            for key, nested in value.items()
        )
        if not has_quantitative_value:
            return False
        return any(
            restricted_quantitative_member(folded_text(str(key)), nested)
            for key, nested in value.items()
        )

    def walk(
        value: object,
        *,
        path: tuple[str, ...] = (),
        typed_identity: bool = False,
        strict_identity: bool = False,
        prose_context: bool = False,
        sequence_depth: int = 0,
    ) -> bool:
        if isinstance(value, dict):
            # Prose authority is leaf-only. A mapping beneath an audited prose
            # key is a new structural object: discard inherited prose context
            # and require each of its exact keys to establish its own role.
            # Thus {"text": "SHIBOR"} remains lawful prose, while
            # {"source": "chinamoney"} is a restricted identity.
            prose_context = False
            if typed_identity and restricted_quantitative_identity(
                value,
                strict=strict_identity,
            ):
                return True
            target_is_lawful = lawful_farbasin_target(value, path, sequence_depth)
            if (
                path == FARBASIN_TARGET_PATH
                and restricted_target_identity(value.get("term"))
                and not target_is_lawful
            ):
                return True
            if restricted_engine_shape(value) or (
                not target_is_lawful and restricted_quantitative_mapping(value)
            ):
                return True
            for key, nested in value.items():
                key_text = str(key)
                field = folded_text(key_text)
                # Trust exceptions compare the producer's raw schema keys.
                # Normalization is reserved for deny classification below.
                child_path = (*path, key_text)
                if restricted_identifier(key_text, typed=True, strict=True):
                    return True
                if (
                    not prose_context
                    and url_field(field)
                    and restricted_mirror_url(nested)
                ):
                    return True
                if (
                    not prose_context
                    and field == "nodes"
                    and isinstance(nested, (list, tuple))
                    and any(restricted_identifier(item, typed=True) for item in nested)
                ):
                    return True
                child_prose = (
                    prose_context or field in RESTRICTED_SNAPSHOT_PROSE_FIELDS
                )
                child_identity = (
                    False if child_prose else typed_identity or identity_field(field)
                )
                child_strict_identity = (
                    False
                    if child_prose
                    else strict_identity or strict_identity_field(field)
                )
                if walk(
                    nested,
                    path=child_path,
                    typed_identity=child_identity,
                    strict_identity=child_strict_identity,
                    prose_context=child_prose,
                    sequence_depth=0,
                ):
                    return True
            return False
        if isinstance(value, (list, tuple)):
            return any(
                walk(
                    item,
                    path=path,
                    typed_identity=typed_identity,
                    strict_identity=strict_identity,
                    prose_context=prose_context,
                    sequence_depth=sequence_depth + 1,
                )
                for item in value
            )
        if prose_context:
            return False
        if restricted_mirror_url(value):
            return True
        return restricted_identifier(value) or (
            typed_identity
            and restricted_identifier(value, typed=True, strict=strict_identity)
        )

    return walk(payload)


def _assert_snapshot_rights(payload: object) -> None:
    if _snapshot_contains_restricted_cfets(payload):
        raise ValueError("snapshot contains restricted CFETS-derived data")


def _safe_memory_snapshot() -> dict | None:
    """Return the in-process payload or quarantine it before any public read."""

    payload = _cache.get("payload")
    if payload is None:
        return None
    if not isinstance(payload, dict) or _snapshot_contains_restricted_cfets(payload):
        logging.getLogger("seiche.assemble").error(
            "quarantined invalid in-process snapshot before public cache read"
        )
        _cache.update(
            at=0.0,
            payload=None,
            source=None,
            release_receipt=None,
            release_handoff_id=None,
            producer_sha=None,
        )
        return None
    return payload


def _servable_snapshot(payload: object) -> bool:
    """Whether a saved payload can safely cover the public boot window.

    The snapshot contract is intentionally structural rather than tied to the
    current release number.  A deployment may add a section, but the previous
    release's completed gauge is still a better, timestamped answer than seven
    minutes of timeouts while the new process trains its deep layer.
    """
    if not isinstance(payload, dict) or _snapshot_contains_restricted_cfets(payload):
        return False
    engines = payload.get("engines")
    composite = engines.get("composite") if isinstance(engines, dict) else None
    deep = payload.get("deep")
    tell = deep.get("tell") if isinstance(deep, dict) else None
    stacker = deep.get("stacker") if isinstance(deep, dict) else None
    modelcourt = deep.get("modelcourt") if isinstance(deep, dict) else None
    backtest = deep.get("backtest") if isinstance(deep, dict) else None
    calendar = payload.get("calendar")
    navigator = payload.get("navigator")
    provenance = payload.get("provenance")

    def mapping_or_none(value: object) -> bool:
        return value is None or isinstance(value, dict)

    return (
        isinstance(payload.get("generated_at"), str)
        and bool(payload["generated_at"])
        and isinstance(payload.get("version"), str)
        and isinstance(composite, dict)
        and isinstance(composite.get("regime"), str)
        and isinstance(composite.get("value"), (int, float))
        and isinstance(deep, dict)
        and isinstance(tell, dict)
        and (
            not tell.get("ok")
            or (
                isinstance(tell.get("tell"), (int, float))
                and isinstance(tell.get("plumbing_pctl"), (int, float))
                and isinstance(tell.get("market_pctl"), (int, float))
                and isinstance(tell.get("reading"), str)
            )
        )
        and mapping_or_none(stacker)
        and (
            not isinstance(stacker, dict)
            or mapping_or_none(stacker.get("members_now"))
        )
        and mapping_or_none(modelcourt)
        and (
            not isinstance(modelcourt, dict)
            or mapping_or_none(modelcourt.get("ensemble"))
        )
        and mapping_or_none(backtest)
        and (
            not isinstance(backtest, dict)
            or (
                mapping_or_none(backtest.get("event_capture"))
                and (
                    backtest.get("episodes") is None
                    or (
                        isinstance(backtest.get("episodes"), list)
                        and all(
                            isinstance(row, dict) for row in backtest["episodes"]
                        )
                    )
                )
            )
        )
        and mapping_or_none(calendar)
        and (
            not isinstance(calendar, dict)
            or calendar.get("crunch_windows") is None
            or isinstance(calendar.get("crunch_windows"), list)
        )
        and mapping_or_none(navigator)
        and isinstance(payload.get("faults"), list)
        and all(isinstance(row, dict) for row in payload["faults"])
        and (
            (
                isinstance(provenance, list)
                and all(isinstance(row, dict) for row in provenance)
            )
            or (
                isinstance(provenance, dict)
                and all(isinstance(row, dict) for row in provenance.values())
            )
        )
    )


def restore_cached_snapshot() -> str | None:
    """Hydrate the in-process cache without fetching or running an engine.

    The repository's controller-accepted handoff is preferred.  The legacy
    SQLite copy remains a rollback bridge, and the backend-packaged snapshot
    is a disaster-recovery seed for the first rollout or a lost database.
    Restored payloads are marked stale so the normal background owner rebuilds
    immediately while readers continue to receive the dated prior reading.
    Returns the source name for startup logging, or ``None`` when no safe
    snapshot exists.
    """
    if _safe_memory_snapshot() is not None:
        return "memory"

    log = logging.getLogger("seiche.assemble")
    durable = None
    try:
        from seiche.repository import get_repository

        active = get_repository().load_active_release_handoff()
        if active is not None:
            durable, _, _, _ = _validated_handoff(active)
    except Exception:  # noqa: BLE001 - a broken handoff must fall through
        log.exception("could not load active repository snapshot handoff")
    if durable is None:
        try:
            durable = store.load_blob(LAST_GOOD_SNAPSHOT_KEY)
        except Exception:  # noqa: BLE001 - a broken handoff must fall through
            log.exception("could not load legacy last-known-good snapshot")
    if _servable_snapshot(durable):
        _cache.update(
            at=0.0,
            payload=durable,
            source="durable",
            release_receipt=None,
            release_handoff_id=None,
            producer_sha=None,
        )
        return "durable"
    if durable is not None:
        log.warning("ignored invalid durable last-known-good snapshot")

    try:
        static = json.loads(STATIC_SNAPSHOT_PATH.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        static = None
    if _servable_snapshot(static):
        _cache.update(
            at=0.0,
            payload=static,
            source="static",
            release_receipt=None,
            release_handoff_id=None,
            producer_sha=None,
        )
        return "static"
    if static is not None:
        log.warning("ignored invalid static last-known-good snapshot")
    return None


def _snapshot_digest(payload: dict) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _handoff_digest(body: dict) -> str:
    canonical = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _valid_release_sha(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _release_sha() -> str:
    checkout = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    resolved = result.stdout.strip()
    if not _valid_release_sha(resolved):
        raise ValueError("could not resolve a canonical release SHA")
    resolved = resolved.lower()
    explicit = os.getenv("SEICHE_RELEASE_SHA")
    if explicit is not None:
        explicit = explicit.strip()
        if not _valid_release_sha(explicit):
            raise ValueError("SEICHE_RELEASE_SHA is not a canonical commit SHA")
        explicit = explicit.lower()
        if explicit != resolved:
            raise ValueError("SEICHE_RELEASE_SHA does not match the checkout HEAD")
    return resolved


def capture_process_release_sha() -> str:
    """Resolve the release identity once, before a mutable checkout can move."""
    global _process_release_sha
    if _process_release_sha is None:
        _process_release_sha = _release_sha()
    return _process_release_sha


def _release_receipt_snapshot_ids(receipt: object, payload: dict) -> tuple[str, str]:
    if not isinstance(receipt, dict) or set(receipt) != {
        "generated_at",
        "producer",
        "products",
    }:
        raise ValueError("release receipt has an invalid top-level contract")
    if receipt.get("generated_at") != payload.get("generated_at"):
        raise ValueError("release receipt does not bind its payload")
    if receipt.get("producer") != (
        "seiche.markets.us_usd.materialize.seal_legacy_snapshot"
    ):
        raise ValueError("release receipt producer is invalid")
    bindings = validate_release_product_bindings(
        receipt.get("products"),
        required_products=("overview", "gauge"),
    )
    snapshot_ids = tuple(binding[1] for binding in bindings)
    return snapshot_ids[0], snapshot_ids[1]


def _handoff_body(payload: dict, release_receipt: dict, producer_sha: str) -> dict:
    return {
        "schema": SNAPSHOT_HANDOFF_SCHEMA,
        "producer_sha": producer_sha,
        "payload_sha256": _snapshot_digest(payload),
        "release_receipt": release_receipt,
        "payload": payload,
    }


def _build_handoff(payload: dict, release_receipt: dict, producer_sha: str) -> dict:
    _assert_snapshot_rights(payload)
    body = _handoff_body(payload, release_receipt, producer_sha)
    _release_receipt_snapshot_ids(release_receipt, payload)
    return {**body, "handoff_id": _handoff_digest(body)}


def _validated_handoff(
    envelope: object,
    *,
    expected_release_sha: str | None = None,
    expected_handoff_id: str | None = None,
) -> tuple[dict, dict, str, str]:
    if not isinstance(envelope, dict):
        raise ValueError("snapshot handoff envelope is incomplete")
    validate_release_handoff_envelope(
        envelope,
        expected_handoff_id=expected_handoff_id,
        expected_producer_sha=expected_release_sha,
    )
    if not isinstance(envelope, dict) or set(envelope) != {
        "schema",
        "producer_sha",
        "payload_sha256",
        "release_receipt",
        "payload",
        "handoff_id",
    }:
        raise ValueError("snapshot handoff envelope is incomplete")
    if envelope.get("schema") != SNAPSHOT_HANDOFF_SCHEMA:
        raise ValueError("snapshot handoff schema is invalid")
    producer_sha = envelope.get("producer_sha")
    handoff_id = envelope.get("handoff_id")
    payload = envelope.get("payload")
    receipt = envelope.get("release_receipt")
    if not _valid_release_sha(producer_sha):
        raise ValueError("snapshot handoff producer SHA is invalid")
    producer_sha = producer_sha.lower()
    if expected_release_sha is not None and producer_sha != expected_release_sha:
        raise ValueError("snapshot handoff belongs to another release")
    if not isinstance(handoff_id, str) or len(handoff_id) != 64:
        raise ValueError("snapshot handoff ID is invalid")
    if expected_handoff_id is not None and not hmac.compare_digest(
        handoff_id, expected_handoff_id
    ):
        raise ValueError("snapshot handoff ID changed after health verification")
    if not _servable_snapshot(payload) or not isinstance(receipt, dict):
        raise ValueError("snapshot handoff payload is not safely servable")
    payload_digest = envelope.get("payload_sha256")
    expected_payload_digest = _snapshot_digest(payload)
    if not isinstance(payload_digest, str) or not hmac.compare_digest(
        payload_digest, expected_payload_digest
    ):
        raise ValueError("snapshot handoff payload digest mismatch")
    _release_receipt_snapshot_ids(receipt, payload)
    body = {key: value for key, value in envelope.items() if key != "handoff_id"}
    expected_digest = _handoff_digest(body)
    if not hmac.compare_digest(handoff_id, expected_digest):
        raise ValueError("snapshot handoff receipt or envelope digest mismatch")
    return payload, receipt, producer_sha, handoff_id


def _accepted_release(repository, producer_sha: str) -> bool:
    active = repository.load_active_release_handoff()
    if active is None:
        return False
    try:
        _, _, active_sha, _ = _validated_handoff(active)
    except ValueError:
        logging.getLogger("seiche.assemble").exception(
            "active release handoff failed validation"
        )
        return False
    return active_sha == producer_sha


def _persist_pending_snapshot(payload: dict, release_receipt: dict) -> str | None:
    """Stage a verified handoff; only the deploy controller may activate it."""
    try:
        from seiche.repository import get_repository

        repository = get_repository()
        producer_sha = capture_process_release_sha()
        envelope = _build_handoff(payload, release_receipt, producer_sha)
        handoff_id = envelope["handoff_id"]
        repository.stage_release_handoff(handoff_id, producer_sha, envelope)
        if _accepted_release(repository, producer_sha) and not activate_pending_snapshot(
            producer_sha, handoff_id, repository=repository
        ):
            raise RuntimeError("accepted release could not advance active handoff")
    except Exception:  # noqa: BLE001 - memory cache remains authoritative
        logging.getLogger("seiche.assemble").exception(
            "could not stage pending release snapshot"
        )
        return None
    return handoff_id


def verify_pending_snapshot(
    expected_release_sha: str, expected_handoff_id: str
) -> bool:
    """Read-only controller preflight for the exact health-returned handoff."""
    try:
        from seiche.repository import get_repository

        if not _valid_release_sha(expected_release_sha):
            raise ValueError("expected release SHA is invalid")
        repository = get_repository()
        envelope = repository.load_release_handoff(expected_handoff_id)
        _validated_handoff(
            envelope,
            expected_release_sha=expected_release_sha.lower(),
            expected_handoff_id=expected_handoff_id,
        )
    except Exception:  # noqa: BLE001 - caller treats False as a failed preflight
        logging.getLogger("seiche.assemble").exception(
            "pending release handoff failed exact verification"
        )
        return False
    return True


def activate_pending_snapshot(
    expected_release_sha: str,
    expected_handoff_id: str,
    *,
    repository=None,
) -> bool:
    """Atomically activate the exact market bundle and full-board handoff."""
    try:
        from seiche.markets.us_usd.materialize import verify_release_receipt
        from seiche.repository import get_repository

        if not _valid_release_sha(expected_release_sha):
            raise ValueError("expected release SHA is invalid")
        expected_release_sha = expected_release_sha.lower()
        repository = repository or get_repository()
        envelope = repository.load_release_handoff(expected_handoff_id)
        payload, receipt, _, handoff_id = _validated_handoff(
            envelope,
            expected_release_sha=expected_release_sha,
            expected_handoff_id=expected_handoff_id,
        )
        snapshot_bindings = verify_release_receipt(repository, receipt)
        repository.activate_release_handoff(
            handoff_id,
            expected_release_sha,
            snapshot_bindings,
        )
        # Best-effort bridge for pre-handoff binaries. This is deliberately
        # outside the atomic repository commit and never changes its verdict.
        try:
            store.save_blob(LAST_GOOD_SNAPSHOT_KEY, payload)
        except Exception:  # noqa: BLE001 - accepted repository state is canonical
            logging.getLogger("seiche.assemble").exception(
                "could not mirror accepted handoff to legacy SQLite"
            )
    except Exception:  # noqa: BLE001 - controller reconciles this exact token
        logging.getLogger("seiche.assemble").exception(
            "could not activate exact pending release snapshot"
        )
        return False
    return True


def cached_snapshot() -> dict | None:
    """Return the last completed snapshot without refreshing or waiting.

    This is the cache-status seam for readiness checks.  Unlike
    :func:`snapshot`, it never acquires the build lock, schedules a refresh,
    or turns a cold-cache read into a full board build.
    """
    return _safe_memory_snapshot()


def cached_snapshot_was_rebuilt() -> bool:
    """True only after this process completed the full assembly pipeline."""
    return _safe_memory_snapshot() is not None and _cache.get("source") == "rebuilt"


def cached_snapshot_release_receipt() -> dict | None:
    """Return proof that this process rebuilt and sealed its market products.

    A completed v1 board remains readable when v2 sealing fails, but that
    degraded state must not satisfy a deployment gate.  The receipt is kept
    in process memory so a restored handoff can never impersonate work done by
    the candidate process.
    """
    if not cached_snapshot_was_rebuilt():
        return None
    receipt = _cache.get("release_receipt")
    return receipt if isinstance(receipt, dict) else None


def cached_snapshot_release_handoff() -> dict | None:
    """Return the exact staged generation accepted by the strict health gate."""
    if cached_snapshot_release_receipt() is None:
        return None
    handoff_id = _cache.get("release_handoff_id")
    producer_sha = _cache.get("producer_sha")
    if (
        not isinstance(handoff_id, str)
        or len(handoff_id) != 64
        or not _valid_release_sha(producer_sha)
    ):
        return None
    return {
        "producer_sha": producer_sha,
        "activation_token": handoff_id,
    }


def _seal_release_evidence(payload: dict) -> dict | None:
    """Seal both US market products and issue an all-or-nothing build receipt."""
    try:
        from seiche.markets.us_usd.materialize import seal_legacy_snapshot

        products = seal_legacy_snapshot(payload)
        if set(products) != {"overview", "gauge"} or not all(
            isinstance(binding, dict)
            and set(binding)
            == {"snapshot_id", "forward_record_id", "snapshot_row_sha256"}
            and all(isinstance(value, str) and value for value in binding.values())
            for binding in products.values()
        ):
            raise ValueError("US-USD materializer returned an incomplete receipt")
    except Exception:  # noqa: BLE001 — v2 cannot take ordinary v1 reads down
        logging.getLogger("seiche.assemble").exception(
            "US-USD v2 snapshot materialization failed"
        )
        return None
    return {
        "generated_at": payload["generated_at"],
        "producer": "seiche.markets.us_usd.materialize.seal_legacy_snapshot",
        "products": products,
    }


async def _publish_rebuilt_snapshot(
    payload: dict,
    release_receipt: dict | None,
) -> None:
    """Publish live v1 reads; stage only a cycle that passed evidence sealing.

    This is an in-place deployment, not a blue/green public-read switch. The
    controller boundary governs canonical v2 products and restart-durable LKG
    state; ordinary v1 memory/PIT publication remains the live process's
    established behavior while the candidate health gate runs.
    """
    _assert_snapshot_rights(payload)
    _cache.update(
        at=time.time(),
        payload=payload,
        source="rebuilt",
        release_receipt=None,
        release_handoff_id=None,
        producer_sha=None,
    )
    handoff_id = None
    if release_receipt is not None:
        handoff_id = await asyncio.to_thread(
            _persist_pending_snapshot, payload, release_receipt
        )
    if handoff_id is not None:
        _cache["release_receipt"] = release_receipt
        _cache["release_handoff_id"] = handoff_id
        _cache["producer_sha"] = capture_process_release_sha()


async def snapshot(force: bool = False) -> dict:
    """The live board, cache-first and never slow for a reader.

    Fresh cache → returned without touching the build lock. Stale cache →
    returned instantly while ONE background rebuild refreshes it (a reader
    must never pay the assembly bill — sources, 25 engines, the Navigator).
    Cold start / force → build inline; boot warming makes cold rare.
    """
    global _refreshing
    cached = _safe_memory_snapshot()
    if not force and cached is not None:
        if time.time() - _cache["at"] < CACHE_MIN * 60:
            return cached
        if not _refreshing:
            _refreshing = True
            asyncio.get_running_loop().create_task(_refresh_stale())
        return cached
    async with _lock:
        locked_cached = _safe_memory_snapshot()
        if not force and locked_cached is not None \
                and time.time() - _cache["at"] < CACHE_MIN * 60:
            return locked_cached
        return await _build_snapshot()


async def _refresh_stale() -> None:
    global _refreshing
    try:
        async with _lock:
            if time.time() - _cache["at"] >= CACHE_MIN * 60:
                await _build_snapshot()
    except Exception:  # noqa: BLE001 — a failed refresh keeps serving stale
        logging.getLogger("seiche.assemble").exception("background snapshot refresh failed")
    finally:
        _refreshing = False


async def _build_snapshot() -> dict:
    """Assemble the full payload. Caller holds `_lock`."""
    src, faults = await _gather_sources()
    src = _rights_eligible_sources(src)
    drv = _derived(src)
    # The engine + deep stages are synchronous CPU work (the deep layer
    # trains sklearn models in mlpred.walk_forward — minutes, not ms).
    # Run them on a worker thread: executed inline they starve the event
    # loop and every HTTP request hangs until the fit finishes (observed
    # live 2026-07-17: /, /docs and all /api/* timing out while the
    # keep-warm cycle trained). One rebuild at a time is still guaranteed
    # by the caller holding `_lock`.
    engines = await asyncio.to_thread(_run_engines, src, drv, faults)
    # Engine results are the first completed value-bearing projection. Reject
    # restricted rows before the deep layer can cache or otherwise consume them.
    _assert_snapshot_rights({"engines": _strip_private(engines)})
    deep = await asyncio.to_thread(_deep_layer, src, drv, engines, faults)
    # Model Court sits on the finished deep layer (published payloads, never
    # raw series) and re-reads the as-published odds ledger on every rebuild,
    # so it stays OUTSIDE _deep_layer's per-day blob cache. The ledger is the
    # git-tracked JSONL the dispatch CI appends to, which makes the court's
    # record auditable in history rather than a box-local file.
    deep["modelcourt"] = eng_modelcourt.convene(deep, odds_ledger=_odds_ledger())
    # Source adapters predate the typed failure contract and may still append
    # raw exception details to the shared list.  Normalize the complete list
    # before it can enter editorial output, release handoffs, or blob storage.
    faults = [sanitize_fault_record(item) for item in faults]
    generated_at = utcnow_iso()
    headline = _headline(src, drv)
    calendar = _calendar(src, engines, deep, drv)
    provenance = _provenance(src)
    payload = {
        "generated_at": generated_at,
        "version": VERSION_LABEL,
        "historical_evidence": deep.get(
            "historical_evidence", eng_history.vintage_evidence(None)
        ),
        "headline": headline,
        "engines": _strip_private(engines),
        "deep": _strip_private(deep),
        "calendar": calendar,
        "faults": faults,
        "provenance": provenance,
        "editorial": editorial.build_editorial(
            generated_at=generated_at,
            engines=engines,
            deep=deep,
            headline=headline,
            calendar=calendar,
            faults=faults,
        ),
        "data_quality": editorial.build_data_quality(
            generated_at=generated_at,
            provenance=provenance,
            headline=headline,
        ),
    }
    # The complete internal board must clear the same boundary before the
    # Navigator sees it or PIT/notary/materialization performs any side effect.
    _assert_snapshot_rights(payload)
    # The Navigator commits AFTER the board is assembled (its whole world
    # is the context pack of this payload), once per data-day, cached —
    # a re-run must never let the model revise the morning's number.
    nav: dict = {"ok": False, "reason": "no spread data-day to commit against"}
    if not drv["spread_bp"].empty:
        try:
            from seiche import ai as _ai
            nav = await eng_navigator.commit(
                _ai.context_pack(payload),
                drv["spread_bp"].index[-1].date().isoformat(),
            )
        except Exception as e:  # noqa: BLE001 — fail loud, never block the board
            nav = safe_failure_envelope(e)
        if nav.get("ok"):
            nav = {**nav, "record": eng_navigator.score_record(
                store.load_pit_records(), drv["spread_bp"])}
    payload["navigator"] = nav
    _assert_snapshot_rights(payload)
    _record_pit(engines, deep, nav)
    # v2 never collects at request time. The existing US cycle is the first
    # producer during migration: once its payload is complete, a pack-local
    # adapter seals independent market products for read-only v2 routes.
    # Failure is isolated from ordinary v1 reads but makes the candidate
    # ineligible for promotion through the strict deployment health gate.
    release_receipt = await asyncio.to_thread(_seal_release_evidence, payload)
    # Publish to memory first: a slow or locked SQLite handoff must never make
    # an already-completed reading wait. Stage only after the evidence seal;
    # the root deploy controller activates it after every remaining gate.
    await _publish_rebuilt_snapshot(payload, release_receipt)
    return payload


async def snapshot_asof(date: str) -> dict:
    """Time Machine: construction-PIT reconstruction for `date`.

    Engines are pure functions of their inputs, so the reconstruction truncates
    to observations dated on or before `date`. Inputs are final/current-vintage,
    not the publication vintage visible then; the payload states that boundary.
    The deep layer is excluded because its percentile bases are defined against
    the live sample. Replays are blob-cached per date.
    """
    asof = pd.Timestamp(date).normalize()
    key = f"asof:{asof.date().isoformat()}"
    cached = store.load_blob(key)
    if cached is not None and not _snapshot_contains_restricted_cfets(cached):
        if "historical_evidence" not in cached:
            cached = {
                **cached,
                "historical_evidence": eng_history.vintage_evidence(None),
            }
        return cached
    if cached is not None:
        logging.getLogger("seiche.assemble").warning(
            "ignored replay cache containing restricted CFETS-derived data"
        )

    async with _lock:
        src, faults = await _gather_sources()
    tsrc = _truncate_sources(src, asof)
    drv = _derived(tsrc)
    if drv["spread_bp"].empty or drv["spread_bp"].index[-1] < asof - pd.Timedelta(days=30):
        return {"ok": False, "reason": f"no data near {date} (coverage starts ~2018-06)"}
    # off the event loop: a cold wrecks rebuild chains many replays and the
    # engine stage would otherwise starve every HTTP request (same class as
    # the keep-warm fit incident)
    engines = await asyncio.to_thread(_run_engines, tsrc, drv, faults, asof)
    payload = {
        "ok": True,
        "generated_at": utcnow_iso(),
        "version": VERSION_LABEL,
        "replay": True,
        "asof": asof.date().isoformat(),
        "vintage_note": "replayed on final-vintage data; weekly H.4.1 aggregates are lightly revised vs what was on screens that day",
        "historical_evidence": eng_history.vintage_evidence(None),
        "headline": _headline(tsrc, drv),
        "engines": _strip_private(engines),
        "faults": faults,
    }
    _assert_snapshot_rights(payload)
    store.save_blob(key, payload)
    return payload
