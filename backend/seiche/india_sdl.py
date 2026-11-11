"""State government borrowing in India: RBI's weekly State Government Securities (SDL) auctions, state by state.

Every Tuesday (and on other notified days) RBI auctions state development loans for the states and union territories
that borrow that week, and publishes a full result: the amount notified, competitive bids received, the cut-off
yield or price, the weighted average yield and the amount allotted, per security. Read together these releases are
the cost and demand of state borrowing: what co-operative and small banks holding state securities in their SLR books,
and anyone pricing state credit, watch.

This desk is descriptive. The latest auction day, a weekly series since April 2021 and a state table are computed
from RBI's printed figures; bid cover is competitive bids received divided by the notified amount (both printed).
A security with no cut-off (RBI prints '-' or 0 when a state accepts no bid) is shown as not accepted, never as a
yield. No spread to central government bonds is computed here: it needs a dated benchmark yield for the same
residual maturity, which this pack does not carry.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from functools import lru_cache
import json
from pathlib import Path

PACK = Path(__file__).resolve().parent / "india_sdl_auctions.json"
SCHEMA = "seiche.india-sdl-desk.v1"
WINDOW_DAYS = 364
HISTORY_POINTS = 120
CAVEATS = [
    "Primary auction results as RBI prints them, attributed to RBI; not secondary-market yields or executable quotes.",
    "Bid cover is competitive bids received over the notified amount, both as printed; RBI may accept more than "
    "notified (a greenshoe) or less.",
    "A cut-off printed as '-' or 0 means no bid was accepted for that security; it is counted as not accepted, never as "
    "a yield.",
    "Residual tenor is the printed maturity year minus the auction year, an approximation used only to group securities.",
    "No spread to central government securities is computed: this pack carries no benchmark yield for the same date and "
    "maturity.",
]


@lru_cache(maxsize=1)
def _pack() -> dict:
    pack = json.loads(PACK.read_text())
    if pack.get("schema") != "seiche.india-sdl-auctions.v1":
        raise ValueError("unexpected SDL pack schema")
    cols = pack["columns"]
    pack["_rows"] = [dict(zip(cols, row)) for row in pack["rows"]]
    return pack


def _wavg(rows: list[dict], value: str, weight: str) -> float | None:
    pairs = [(r[value], r[weight]) for r in rows if r.get(value) is not None and r.get(weight)]
    total = sum(w for _, w in pairs)
    return round(sum(v * w for v, w in pairs) / total, 4) if total else None


def _day(rows: list[dict]) -> dict:
    notified = sum(r["notified_cr"] or 0 for r in rows)
    bids = sum(r["competitive_bids_cr"] or 0 for r in rows)
    return {"securities": len(rows), "states": len({r["state"] for r in rows}),
            "notified_cr": round(notified, 3), "allotted_cr": round(sum(r["allotted_cr"] or 0 for r in rows), 3),
            "bid_cover": round(bids / notified, 3) if notified else None,
            "weighted_avg_cutoff_yield_pct": _wavg(rows, "cutoff_yield_pct", "allotted_cr"),
            "not_accepted": sum(1 for r in rows if r.get("cutoff_yield_pct") is None and not r.get("allotted_cr"))}


def _security(r: dict) -> dict:
    return {"auction_date": r["auction_date"], "security": r["security"], "tenor_as_printed": r["tenor_as_printed"],
            "residual_years": (r["maturity_year"] - int(r["auction_date"][:4])) if r.get("maturity_year") else None,
            "notified_cr": r["notified_cr"], "competitive_bids_cr": r["competitive_bids_cr"],
            "bid_cover": round(r["competitive_bids_cr"] / r["notified_cr"], 3)
            if r.get("competitive_bids_cr") is not None and r.get("notified_cr") else None,
            "cutoff_yield_pct": r["cutoff_yield_pct"], "cutoff_price_rs": r["cutoff_price_rs"],
            "weighted_avg_yield_pct": r["weighted_avg_yield_pct"], "allotted_cr": r["allotted_cr"],
            "release": f"https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid={r['prid']}"}


def states() -> list[str]:
    return sorted({r["state"] for r in _pack()["_rows"] if r.get("state")})


def read(state: str | None = None, include_history: bool = False) -> dict:
    pack = _pack()
    rows = pack["_rows"]
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["auction_date"]].append(r)
    days = sorted(by_day)
    latest = days[-1]
    since = (date.fromisoformat(latest) - timedelta(days=WINDOW_DAYS)).isoformat()
    head = {"schema": SCHEMA, "status": "available", "asof": latest, "source": pack["source"], "caveats": CAVEATS,
            "redistribution": "allowed with attribution to RBI"}
    if state is not None:
        wanted = state.strip().upper()
        if wanted not in states():
            raise ValueError("state must be one of: " + ", ".join(states()))
        mine = [r for r in rows if r["state"] == wanted]
        recent = [r for r in mine if r["auction_date"] > since]
        return {**head, "state": wanted, "last_52_weeks": {**_day(recent), "auction_days": len({r["auction_date"] for r in recent})},
                "securities": [_security(r) for r in sorted(mine, key=lambda r: r["auction_date"], reverse=True)
                               [: (len(mine) if include_history else 40)]],
                "securities_total": len(mine)}
    table = []
    for name in states():
        mine = [r for r in rows if r["state"] == name]
        recent = [r for r in mine if r["auction_date"] > since]
        last = max(mine, key=lambda r: (r["auction_date"], r["allotted_cr"] or 0))
        table.append({"state": name, "last_auction": last["auction_date"], "last_security": last["security"],
                      "last_cutoff_yield_pct": last["cutoff_yield_pct"],
                      "issued_52w_cr": round(sum(r["allotted_cr"] or 0 for r in recent), 3),
                      "weighted_avg_cutoff_52w_pct": _wavg(recent, "cutoff_yield_pct", "allotted_cr"),
                      "bid_cover_52w": _day(recent)["bid_cover"] if recent else None,
                      "not_accepted_52w": _day(recent)["not_accepted"] if recent else 0})
    table.sort(key=lambda t: -(t["issued_52w_cr"] or 0))
    series = [{"auction_date": d, **_day(by_day[d])} for d in days]
    return {**head, "latest_auction": {"auction_date": latest, **_day(by_day[latest])},
            "states": table, "weekly": series if include_history else series[-HISTORY_POINTS:],
            "weekly_points": len(series), "state_lookup": "/api/v2/india-sdl?state=<state>"}
