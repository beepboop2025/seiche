"""Far Basin — the policy-fear channel (Palimpsest).

Seiche's global-basin engine tracks the dollar system's connected waters
through RATES (€STR, SONIA), FX (broad dollar, INR) and the crypto moorings.
This engine adds a channel none of those carry: what the Chinese state is
actively deleting. Censorship intensity is a confession — the same logic as
the SRF/discount-window confession channel, one basin further out. A spike in
deletion-threat or in the Generative Firewall (state-aligned LLMs refusing
what they answered last week) is policy fear you cannot buy from a market
data vendor, arriving before it becomes a market print.

HONEST SCOPE, stated on the card: these feeds are days old as public series.
They accrue in the local store on every fetch and remain quarantined —
never in the composite, never a model feature — until they clear
FARBASIN_MIN_OBS daily observations. Until then the engine publishes level,
short-window trend, and the countdown to backtestability. A channel this
young earns context status, nothing more.
"""

from __future__ import annotations

import pandas as pd

from seiche.config import FARBASIN_MIN_OBS


def _block(s: pd.Series | None, label: str, unit: str) -> dict | None:
    if s is None:
        return None
    pts = s.dropna()
    if pts.empty:
        return None
    out = {
        "label": label,
        "unit": unit,
        "last": round(float(pts.iloc[-1]), 2),
        "asof": pts.index[-1].date().isoformat(),
        "n_obs": int(len(pts)),
    }
    if len(pts) >= 5:
        prior = pts.iloc[:-1].tail(10)
        out["chg_vs_prior10"] = round(float(pts.iloc[-1] - prior.median()), 2)
    if len(pts) >= 2:
        out["series"] = [
            [d.date().isoformat(), round(float(v), 2)] for d, v in pts.tail(120).items()
        ]
    return out


def analyze(
    fear: pd.Series | None,
    n_new: pd.Series | None,
    gfi: pd.Series | None,
    latest: dict | None,
) -> dict:
    blocks = {
        "fear": _block(fear, "Deletion-threat (top-term score)", "score"),
        "n_new": _block(n_new, "Newly censor-targeted terms", "terms/day"),
        "gfi": _block(gfi, "Generative Firewall Index", "0-100"),
    }
    live = {k: v for k, v in blocks.items() if v}
    if not live:
        return {"ok": False, "reason": "no palimpsest readings available"}

    n_max = max(v["n_obs"] for v in live.values())
    backtestable = n_max >= FARBASIN_MIN_OBS
    top = (latest or {}).get("top") or []
    return {
        "ok": True,
        "asof": max(v["asof"] for v in live.values()),
        "channels": blocks,
        "top_targets": top,
        "status": {
            "backtestable": backtestable,
            "n_obs": n_max,
            "min_obs": FARBASIN_MIN_OBS,
            "note": (
                "channel cleared for model entry"
                if backtestable else
                f"ACCRUING — {n_max}/{FARBASIN_MIN_OBS} daily obs; context only until then "
                "(never in the composite, never a model feature)"
            ),
        },
        "why": (
            "censorship intensity is a confession channel: what a state rushes to delete "
            "is policy fear readable before it becomes a market print — no market data "
            "vendor carries it"
        ),
        "method": (
            "DDTI deletion-threat (daily max of 3h prints), newly-targeted term count, "
            "and the Generative Firewall Index from palimpsest.info CI-published readings "
            "(GitHub raw mirror fallback); history accrues locally on every fetch; "
            f"quarantined from all models until {FARBASIN_MIN_OBS} daily obs"
        ),
    }
