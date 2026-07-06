"""Orchestrator: fetch everything, run all engines, assemble the payload.

One entry point (`snapshot`) so the API layer stays thin. Results are cached
in-process for CACHE_MIN minutes; each upstream failure degrades that block
to its stale copy (with true staleness shown) or to an explicit fault entry —
never a silent gap.
"""

from __future__ import annotations

import asyncio
import time
import traceback

import httpx
import pandas as pd

from seiche.config import FRED_SERIES, OFR_SERIES
from seiche.engines import auctions as eng_auctions
from seiche.engines import composite as eng_composite
from seiche.engines import echo as eng_echo
from seiche.engines import kink as eng_kink
from seiche.engines import rvxray as eng_rvxray
from seiche.engines import tails as eng_tails
from seiche.engines import weather as eng_weather
from seiche.sources import cftc, fiscaldata, fred, nyfed, ofr
from seiche.sources.base import SourceFault, utcnow_iso

CACHE_MIN = 15
_cache: dict = {"at": 0.0, "payload": None}
_lock = asyncio.Lock()


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

        await asyncio.gather(
            guard("fred", fred.fetch_many(client, [s.mnemonic for s in FRED_SERIES], faults)),
            guard("ofr", ofr.fetch_many(client, [s.mnemonic for s in OFR_SERIES], faults)),
            guard("nyfed_rates", nyfed.fetch_secured_rates(client)),
            guard("nyfed_srf", nyfed.fetch_srf_ops(client)),
            guard("tga", fiscaldata.fetch_tga_daily(client)),
            guard("auctions", fiscaldata.fetch_auctions(client)),
            guard("tff", cftc.fetch_tff_ust(client)),
        )
    return out, faults


def _run_engines(src: dict, faults: list[dict]) -> dict:
    fred_s = src.get("fred", {})
    ofr_s = src.get("ofr", {})

    def pts(d, key):
        s = d.get(key)
        return s.points.dropna() if s is not None else pd.Series(dtype=float)

    iorb_spliced = None
    if "IORB" in fred_s and "IOER" in fred_s:
        iorb_spliced = fred.splice_iorb(fred_s["IORB"], fred_s["IOER"]).points.dropna()

    results: dict = {}

    def run(name: str, fn):
        try:
            results[name] = fn()
        except Exception as e:
            results[name] = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
            faults.append({"source": f"engine:{name}", "detail": traceback.format_exc(limit=2)})

    # --- Kink ---
    def _kink():
        spread = (pts(fred_s, "SOFR") - iorb_spliced.reindex(pts(fred_s, "SOFR").index).ffill()).dropna()
        return eng_kink.fit_kink(spread, pts(fred_s, "WRESBAL"), pts(fred_s, "GDP"))
    if iorb_spliced is not None:
        run("kink", _kink)
    else:
        results["kink"] = {"ok": False, "reason": "IORB/SOFR unavailable"}

    kink_b = results["kink"].get("kink_reserves_b") if results["kink"].get("ok") else None

    # --- Weather ---
    tga = src.get("tga", {}).get("tga", pd.Series(dtype=float))
    run("weather", lambda: eng_weather.forecast(pts(fred_s, "WRESBAL"), pts(fred_s, "WALCL"), tga, kink_b))

    # --- Tails ---
    frames = src.get("nyfed_rates", {}).get("frames", {})
    run("tails", lambda: eng_tails.analyze(frames, iorb_spliced if iorb_spliced is not None else pd.Series(dtype=float)))

    # --- Echo ---
    def _echo():
        srf_daily = src.get("nyfed_srf", {}).get("daily", pd.DataFrame())
        comps = {
            "sofr_iorb": (pts(fred_s, "SOFR") - iorb_spliced.reindex(pts(fred_s, "SOFR").index).ffill()),
            "effr_iorb": (pts(fred_s, "EFFR") - iorb_spliced.reindex(pts(fred_s, "EFFR").index).ffill()),
            "bgcr_sofr": (pts(fred_s, "BGCR") - pts(fred_s, "SOFR")),
            "rrp": pts(fred_s, "RRPONTSYD"),
            "tga_chg5": tga.diff(5),
            "reserves_chg4w": pts(fred_s, "WRESBAL").diff(4),
            "srf": srf_daily["accepted"] if not srf_daily.empty else pd.Series(dtype=float),
        }
        z = eng_echo.build_state({k: v for k, v in comps.items() if not v.dropna().empty})
        return eng_echo.match(z)
    if iorb_spliced is not None:
        run("echo", _echo)
    else:
        results["echo"] = {"ok": False, "reason": "state components unavailable"}

    # --- RV X-Ray ---
    tff = src.get("tff", {}).get("tff", pd.DataFrame())
    run("rvxray", lambda: eng_rvxray.analyze(tff, pts(ofr_s, "DVP_VOL")))

    # --- Auctions ---
    run("auctions", lambda: eng_auctions.analyze(src.get("auctions", {}).get("auctions", pd.DataFrame())))

    # --- Composite ---
    srf_daily = src.get("nyfed_srf", {}).get("daily", pd.DataFrame())
    rrp = pts(fred_s, "RRPONTSYD")
    subs = {
        "tails": eng_tails.tails_score(results["tails"]) if results["tails"].get("ok") else None,
        "kink": eng_kink.kink_score(results["kink"]) if results["kink"].get("ok") else None,
        "weather": eng_weather.weather_score(results["weather"], kink_b) if results["weather"].get("ok") else None,
        "srf": eng_composite.srf_score(srf_daily) if not srf_daily.empty else None,
        "rvxray": eng_rvxray.rvxray_score(results["rvxray"]) if results["rvxray"].get("ok") else None,
        "auctions": eng_auctions.auctions_score(results["auctions"]) if results["auctions"].get("ok") else None,
        "buffers": eng_composite.buffers_score(float(rrp.iloc[-1])) if not rrp.empty else None,
    }
    results["composite"] = {**eng_composite.compose(subs), "subscores": {k: round(v, 1) if v is not None else None for k, v in subs.items()}}
    return results


def _provenance(src: dict) -> list[dict]:
    prov = []
    for group in ("fred", "ofr"):
        for s in (src.get(group) or {}).values():
            prov.append(s.provenance())
    for key, label in (("nyfed_rates", "NY Fed secured rates"), ("nyfed_srf", "NY Fed repo ops"),
                       ("tga", "Treasury DTS/TGA"), ("auctions", "Treasury auctions"), ("tff", "CFTC TFF")):
        blk = src.get(key)
        if blk:
            prov.append({"mnemonic": key, "source": key.split("_")[0], "label": label,
                         "fetched_at": blk.get("fetched_at"), "staleness": "fresh"})
    return prov


def _headline(src: dict) -> dict:
    fred_s = src.get("fred", {})

    def last(key, scale=1.0):
        s = fred_s.get(key)
        if s is None or s.points.dropna().empty:
            return None
        p = s.points.dropna()
        return {"value": round(float(p.iloc[-1]) * scale, 3), "asof": p.index[-1].date().isoformat()}

    tga = src.get("tga", {}).get("tga", pd.Series(dtype=float))
    srf = src.get("nyfed_srf", {}).get("daily", pd.DataFrame())
    return {
        "sofr_pct": last("SOFR"),
        "effr_pct": last("EFFR"),
        "iorb_pct": last("IORB"),
        "reserves_b": last("WRESBAL", 1e-3),
        "rrp_b": last("RRPONTSYD"),
        "tga_b": {"value": round(float(tga.iloc[-1]), 1), "asof": tga.index[-1].date().isoformat()} if not tga.empty else None,
        "srf_accepted_b": {"value": round(float(srf["accepted"].iloc[-1]), 2), "asof": srf.index[-1].date().isoformat()} if not srf.empty else None,
    }


async def snapshot(force: bool = False) -> dict:
    async with _lock:
        if not force and _cache["payload"] and time.time() - _cache["at"] < CACHE_MIN * 60:
            return _cache["payload"]
        src, faults = await _gather_sources()
        engines = _run_engines(src, faults)
        payload = {
            "generated_at": utcnow_iso(),
            "headline": _headline(src),
            "engines": engines,
            "faults": faults,
            "provenance": _provenance(src),
        }
        _cache.update(at=time.time(), payload=payload)
        return payload
