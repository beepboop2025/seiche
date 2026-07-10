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
import logging
import time
import traceback

import httpx
import numpy as np
import pandas as pd

from seiche import store
from seiche.config import (
    ALL_SERIES,
    BIS_SERIES,
    COMPOSITE_WEIGHTS,
    CROWD_LOOKBACK_WEEKS,
    CRYPTO_PRODUCTS,
    ECB_SERIES,
    FOMC_DECISION_DATES,
    FRED_SERIES,
    GLOBAL_FRED_SERIES,
    INDIA_FRED_SERIES,
    MARKET_SERIES,
    OFR_SERIES,
    PLAYBOOK_OUTCOMES,
    PRETRAIN_FRED_SERIES,
    SWAP_LINE_OPS_N,
)
from seiche.engines import auctions as eng_auctions
from seiche.engines import backtest as eng_backtest
from seiche.engines import basins as eng_basins
from seiche.engines import bathymetry as eng_bathymetry
from seiche.engines import book as eng_book
from seiche.engines import breakwater as eng_breakwater
from seiche.engines import communique as eng_communique
from seiche.engines import composite as eng_composite
from seiche.engines import echo as eng_echo
from seiche.engines import farbasin as eng_farbasin
from seiche.engines import gyre as eng_gyre
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
from seiche.engines import searoom as eng_searoom
from seiche.engines import seastate as eng_seastate
from seiche.engines import thermohaline as eng_thermohaline
from seiche.engines import market as eng_market
from seiche.engines import mlpred as eng_mlpred
from seiche.engines import moorings as eng_moorings
from seiche.engines import navigator as eng_navigator
from seiche.engines import playbook as eng_playbook
from seiche.engines import resonance as eng_resonance
from seiche.engines import riptide as eng_riptide
from seiche.engines import roguewave as eng_roguewave
from seiche.engines import rvxray as eng_rvxray
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
from seiche.sources import bis, cftc, crypto, ecb, fedtext, fiscaldata, fred, nyfed, ofr, palimpsest
from seiche.sources.base import Series, SourceFault, utcnow_iso

CACHE_MIN = 15
DEEP_TTL_MIN = 12 * 60
_cache: dict = {"at": 0.0, "payload": None}
_lock = asyncio.Lock()

VERSION = "0.7.0 tier1"


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
            + PRETRAIN_FRED_SERIES
        ]
        await asyncio.gather(
            guard("fred", fred.fetch_many(client, fred_mnems, faults)),
            guard("ofr", ofr.fetch_many(client, [s.mnemonic for s in OFR_SERIES], faults)),
            guard("ecb", ecb.fetch_many(client, [s.mnemonic for s in ECB_SERIES], faults)),
            guard("bis", bis.fetch_many(client, [s.mnemonic for s in BIS_SERIES], faults)),
            guard("crypto", crypto.fetch_all(client, CRYPTO_PRODUCTS, faults)),
            guard("nyfed_rates", nyfed.fetch_secured_rates(client)),
            guard("nyfed_srf", nyfed.fetch_srf_ops(client)),
            guard("nyfed_pd", nyfed.fetch_pd_positions(client)),
            guard("nyfed_fxs", nyfed.fetch_fx_swaps(client, SWAP_LINE_OPS_N)),
            guard("tga", fiscaldata.fetch_tga_daily(client)),
            guard("auctions", fiscaldata.fetch_auctions(client)),
            guard("upcoming", fiscaldata.fetch_upcoming_auctions(client)),
            guard("tff", cftc.fetch_tff_ust(client)),
            guard("palimpsest", palimpsest.fetch_all(client, faults)),
            guard("fedtext", fedtext.fetch_all(client, faults)),
        )
    return out, faults


def _truncate_sources(src: dict, asof: pd.Timestamp) -> dict:
    """Time Machine: cut every series at the replay date. Pure copies — the
    cached live sources are never mutated."""
    out: dict = {}
    for group in ("fred", "ofr", "ecb", "bis"):
        cut = {}
        for m, s in (src.get(group) or {}).items():
            pts = s.points[s.points.index <= asof]
            cut[m] = Series(s.mnemonic, s.source, s.remote_id, s.label, s.unit, s.freq, s.fetched_at, pts)
        out[group] = cut
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
    tga = (src.get("tga") or {}).get("tga", pd.Series(dtype=float))
    out["tga"] = {"fetched_at": (src.get("tga") or {}).get("fetched_at"), "tga": tga[tga.index <= asof]}
    au = (src.get("auctions") or {}).get("auctions", pd.DataFrame())
    if not au.empty:
        mask = pd.to_datetime(au["auction_date"], errors="coerce") <= asof
        au = au[mask]
    out["auctions"] = {"fetched_at": (src.get("auctions") or {}).get("fetched_at"), "auctions": au}
    out["upcoming"] = {"upcoming": pd.DataFrame()}  # current-state feed: no historical vintage
    fedtexts = (src.get("fedtext") or {}).get("texts", {})
    out["fedtext"] = {
        "fetched_at": (src.get("fedtext") or {}).get("fetched_at"),
        "texts": {d: t for d, t in fedtexts.items() if d <= asof.date().isoformat()},
    }
    tff = (src.get("tff") or {}).get("tff", pd.DataFrame())
    if not tff.empty:
        tff = tff[tff["date"] <= asof]
    out["tff"] = {"fetched_at": (src.get("tff") or {}).get("fetched_at"), "tff": tff}
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
    if not x.empty and float(x.iloc[-1]) > 1e6:
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

def _run_engines(src: dict, drv: dict, faults: list[dict]) -> dict:
    fred_s = src.get("fred", {})
    ofr_s = src.get("ofr", {})
    frames = (src.get("nyfed_rates") or {}).get("frames", {})
    iorb = drv["iorb"]

    results: dict = {}

    def run(name: str, fn):
        try:
            results[name] = fn()
        except Exception as e:
            results[name] = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
            faults.append({"source": f"engine:{name}", "detail": traceback.format_exc(limit=2)})

    # --- Kink ---
    if iorb is not None:
        run("kink", lambda: eng_kink.fit_kink(
            (drv["spread_bp"] / 100.0), _pts(fred_s, "WRESBAL"), _pts(fred_s, "GDP")))
    else:
        results["kink"] = {"ok": False, "reason": "IORB/SOFR unavailable"}
    kink_b = results["kink"].get("kink_reserves_b") if results["kink"].get("ok") else None

    # --- Weather (with auction settlement overlay) ---
    settlements = eng_weather.settlement_calendar(
        (src.get("upcoming") or {}).get("upcoming", pd.DataFrame()))
    run("weather", lambda: eng_weather.forecast(
        _pts(fred_s, "WRESBAL"), _pts(fred_s, "WALCL"), drv["tga"], kink_b, settlements))

    # --- Tails ---
    run("tails", lambda: eng_tails.analyze(
        frames, iorb if iorb is not None else pd.Series(dtype=float)))

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

    # --- Auctions ---
    run("auctions", lambda: eng_auctions.analyze(
        (src.get("auctions") or {}).get("auctions", pd.DataFrame())))

    # --- Resonance ---
    run("resonance", lambda: eng_resonance.analyze(drv["spread_bp"]))

    # --- Undertow (free decay — the other half of the resonance physics) ---
    run("undertow", lambda: eng_undertow.analyze(drv["spread_bp"], drv["tail_bp"]))

    # --- Communiqué (the policy text read as data; vintage-stamped) ---
    run("communique", lambda: eng_communique.analyze(
        (src.get("fedtext") or {}).get("texts", {})))

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

    # --- Warehouse ---
    run("warehouse", lambda: eng_warehouse.analyze(
        (src.get("nyfed_pd") or {}).get("positions", {})))

    # --- Global basins ---
    ecb_s = src.get("ecb", {})
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
    ))

    # --- Thermohaline (BIS global liquidity — the deep circulation) ---
    run("thermohaline", lambda: eng_thermohaline.analyze(src.get("bis") or {}))

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

def _deep_layer(src: dict, drv: dict, engines: dict, faults: list[dict]) -> dict:
    spread = drv["spread_bp"]
    if spread.empty:
        return {"ok": False, "reason": "no spread history"}
    # VERSION in the key: a release that adds deep blocks (tidetables/swell/
    # stacker/book in v2.3) must not serve a pre-upgrade blob for up to 12h.
    cache_key = f"deep:{VERSION}:{spread.index[-1].date().isoformat()}"
    # Failure-aware cache: a blob computed with any failed layer only lives 30
    # minutes, so a transient fault can't poison the whole data-day (bit us
    # twice during the v2 build).
    cached = store.load_blob(cache_key)
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
            return cached

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
            "series": [
                [d.date().isoformat(), round(float(v), 1),
                 round(float(pctl.loc[d]), 0) if pd.notna(pctl.loc[d]) else None]
                for d, v in idx.iloc[::2].items()
            ],
            "method": hist["method"],
        }
    except Exception as e:
        faults.append({"source": "deep:history", "detail": f"{type(e).__name__}: {e}"})
        out["history"] = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
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
            out[name] = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
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
    run("backtest", lambda: eng_backtest.run(pctl, spread, outcomes))

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

    # Riptide — the pop prognosis: chop or current? (speaks on live pops)
    run("riptide", lambda: eng_riptide.analyze(
        spread_bp=spread,
        rrp_b=drv["rrp"],
        damping_pctl=engines.get("undertow", {}).get("_damping_pctl"),
    ))

    # The Gyre — Takens/EDM: is the basin deterministic enough to predict at
    # all, how fast does predictability decay, and is it state-dependent?
    # Deep-layer citizen: its full hindcast is the expensive part and its
    # output is forecast-context, never composite evidence.
    run("gyre", lambda: eng_gyre.analyze(spread))

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
            out["backtest"]["orthogonal"] = {"ok": False, "reason": f"{type(e).__name__}: {e}"}

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
        return eng_searoom.analyze(stk["_p_pub"], stk["_y"])
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

    out["_all_ok"] = all(
        isinstance(v, dict) and v.get("ok")
        for k, v in out.items()
        if k not in ("ok",) and not str(k).startswith("_")
    )
    out["_computed_at"] = utcnow_iso()
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
    prov = []
    for group in ("fred", "ofr", "ecb"):
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
    ):
        blk = src.get(key)
        if blk:
            prov.append({"mnemonic": key, "source": key.split("_")[0], "label": label,
                         "fetched_at": blk.get("fetched_at"), "staleness": "fresh"})
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


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

async def snapshot(force: bool = False) -> dict:
    async with _lock:
        if not force and _cache["payload"] and time.time() - _cache["at"] < CACHE_MIN * 60:
            return _cache["payload"]
        src, faults = await _gather_sources()
        drv = _derived(src)
        engines = _run_engines(src, drv, faults)
        deep = _deep_layer(src, drv, engines, faults)
        payload = {
            "generated_at": utcnow_iso(),
            "version": VERSION,
            "headline": _headline(src, drv),
            "engines": _strip_private(engines),
            "deep": _strip_private(deep),
            "calendar": _calendar(src, engines, deep, drv),
            "faults": faults,
            "provenance": _provenance(src),
        }
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
                nav = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
            if nav.get("ok"):
                nav = {**nav, "record": eng_navigator.score_record(
                    store.load_pit_records(), drv["spread_bp"])}
        payload["navigator"] = nav
        _record_pit(engines, deep, nav)
        _cache.update(at=time.time(), payload=payload)
        return payload


async def snapshot_asof(date: str) -> dict:
    """Time Machine: the whole light board as it would have looked on `date`.

    Engines are pure functions of their inputs, so truncated inputs replay the
    past faithfully (with final-vintage data — stated in the payload). The
    deep layer is excluded: its percentile bases are defined against the live
    sample. Replays are blob-cached per date.
    """
    asof = pd.Timestamp(date).normalize()
    key = f"asof:{asof.date().isoformat()}"
    cached = store.load_blob(key)
    if cached is not None:
        return cached

    async with _lock:
        src, faults = await _gather_sources()
    tsrc = _truncate_sources(src, asof)
    drv = _derived(tsrc)
    if drv["spread_bp"].empty or drv["spread_bp"].index[-1] < asof - pd.Timedelta(days=30):
        return {"ok": False, "reason": f"no data near {date} (coverage starts ~2018-06)"}
    engines = _run_engines(tsrc, drv, faults)
    payload = {
        "ok": True,
        "generated_at": utcnow_iso(),
        "version": VERSION,
        "replay": True,
        "asof": asof.date().isoformat(),
        "vintage_note": "replayed on final-vintage data; weekly H.4.1 aggregates are lightly revised vs what was on screens that day",
        "headline": _headline(tsrc, drv),
        "engines": _strip_private(engines),
        "faults": faults,
    }
    store.save_blob(key, payload)
    return payload
