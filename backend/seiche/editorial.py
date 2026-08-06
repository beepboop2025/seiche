"""Deterministic editorial synthesis for the live board.

The engines answer narrow questions.  This module answers the editorial one:
what is the argument today, what evidence carries it, and what would make it
wrong?  It deliberately contains no I/O and no language model call, so the
published framing is reproducible from the same point-in-time snapshot.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any


SCHEMA = "seiche.editorial.v1"
QUALITY_SCHEMA = "seiche.data_quality.v1"

_DISPLAY = {
    "weather": "the dated reserve path",
    "resonance": "calendar amplification",
    "rvxray": "leveraged Treasury positioning",
    "kink": "reserve scarcity",
    "confession": "official-sector dollar demand",
    "buffers": "the remaining cash buffers",
    "undertow": "market damping",
    "warehouse": "dealer balance-sheet capacity",
    "tails": "repo tail pressure",
    "auctions": "auction digestion",
    "hydrophone": "repo-network stress",
}


def house_priority(component: str) -> float:
    """Return the house editorial priority used to break evidence ties.

    The neutral default keeps the publication fully deterministic.  This is
    intentionally a small owner-editable seam: the desk can decide whether a
    balance-sheet identity should outrank a market-price confirmation without
    rewriting the synthesis machinery.

    TODO(editorial-owner): encode Seiche's durable 5-10 line priority map here.
    """
    return 1.0


def _f(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _fmt(value: Any, digits: int = 1) -> str:
    number = _f(value)
    if number is None:
        return "unavailable"
    return f"{number:,.{digits}f}"


def _signed(value: Any, digits: int = 1) -> str:
    number = _f(value)
    if number is None:
        return "unavailable"
    return f"{number:+,.{digits}f}"


def _asof(block: dict | None) -> str | None:
    if not isinstance(block, dict):
        return None
    return block.get("asof") or block.get("date")


def _top_component(composite: dict) -> dict:
    rows = [r for r in composite.get("decomposition", []) if isinstance(r, dict)]
    if not rows:
        return {}
    return max(
        rows,
        key=lambda row: (
            _f(row.get("contribution")) or 0.0,
            house_priority(str(row.get("component") or "")),
        ),
    )


def _confidence(composite: dict, engines: dict, faults: list[dict]) -> tuple[str, str]:
    coverage = _f(composite.get("coverage_pct")) or 0.0
    tails = _f((engines.get("tails") or {}).get("score")) or 0.0
    hydro = _f((engines.get("hydrophone") or {}).get("score")) or 0.0
    spread = _f((engines.get("kink") or {}).get("observed_spread_now_bp"))
    srf = _f(((engines.get("stigma") or {}).get("srf") or {}).get("accepted_b"))
    confirmations = sum((tails >= 60, hydro >= 60, (spread or -999) > 0, (srf or 0) >= 1))
    top = _top_component(composite)
    saturated = bool(top.get("saturated"))

    if faults or coverage < 90:
        return (
            "low",
            "A source or engine fault reduces the evidence set; confidence is cut before the index is interpreted.",
        )
    if (_f(composite.get("value")) or 0) >= 60 and confirmations >= 2:
        return (
            "high",
            "At least two independent tape confirmations agree with the composite, with full source coverage.",
        )
    if confirmations >= 1 and not saturated:
        return (
            "medium",
            "The composite has one independent tape confirmation, but the evidence is not broad enough for a high-conviction call.",
        )
    return (
        "guarded",
        "The read is led by modelled or slow-moving structure while current market plumbing has not broadly confirmed it.",
    )


def _thesis(composite: dict, engines: dict, deep: dict) -> tuple[str, str]:
    value = _f(composite.get("value"))
    regime = str(composite.get("regime") or "UNRATED").upper()
    top = _top_component(composite)
    top_key = str(top.get("component") or "the composite")
    top_name = _DISPLAY.get(top_key, top_key.replace("_", " "))
    contribution = _f(top.get("contribution"))
    spread = _f((engines.get("kink") or {}).get("observed_spread_now_bp"))
    tell = _f((deep.get("tell") or {}).get("tell"))
    p5 = _f((deep.get("stacker") or {}).get("p_now"))

    if regime in {"STRESS", "CRISIS"}:
        thesis = "Funding stress is no longer only a forecast: the plumbing and the composite are confirming one another."
    elif top_key == "weather" and bool(top.get("saturated")) and spread is not None and spread < 0:
        thesis = "The calendar is carrying the strain call; the price of overnight cash still says abundance."
    elif tell is not None and tell >= 25:
        thesis = "The plumbing looks tighter than markets price, but divergence is a warning to investigate, not proof of a squeeze."
    elif spread is not None and spread > 0:
        thesis = "Overnight funding is beginning to price the reserve constraint that the structural engines have been flagging."
    else:
        thesis = f"{top_name.capitalize()} is the largest reason the board is not calm; the rest of the tape is mixed."

    pieces = [f"The board reads {_fmt(value, 0)} out of 100, {regime}"]
    if contribution is not None:
        pieces.append(f"{top_name} contributes {_fmt(contribution)} points")
    if p5 is not None:
        pieces.append(f"the pooled five-business-day event read is {p5:.1%}")
    if tell is not None:
        pieces.append(f"plumbing leads market pricing by {_signed(tell, 0)} percentile points")
    return thesis, "; ".join(pieces) + "."


def _evidence(engines: dict, deep: dict, headline: dict, calendar: dict) -> list[dict]:
    rows: list[dict] = []
    ledger = engines.get("ledger") or {}
    if ledger.get("ok") and ledger.get("letter_line"):
        rows.append({
            "label": "Balance-sheet identity",
            "claim": str(ledger["letter_line"]),
            "engine": "ledger",
            "asof": _asof(ledger) or _asof(headline.get("reserves_b")),
            "source": "Federal Reserve H.4.1",
        })

    official = engines.get("officialbid") or {}
    if official.get("ok") and official.get("letter_line"):
        rows.append({
            "label": "Official-sector footprint",
            "claim": str(official["letter_line"]),
            "engine": "officialbid",
            "asof": _asof(official),
            "source": "Federal Reserve custody and foreign RRP",
        })

    kink = engines.get("kink") or {}
    if kink.get("ok"):
        distance = _f(kink.get("distance_b"))
        side = "below" if distance is not None and distance < 0 else "above"
        rows.append({
            "label": "Reserve-demand curve",
            "claim": (
                f"Reserves are ${_fmt(abs(distance) if distance is not None else None, 0)}B {side} the fitted kink, "
                f"but SOFR is {_signed(kink.get('observed_spread_now_bp'))}bp versus IORB; "
                f"fit R-squared is {_fmt(kink.get('r2'), 2)}."
            ),
            "engine": "kink",
            "asof": _asof(kink),
            "source": "FRED, NY Fed secured rates, BEA GDP",
        })

    crunches = calendar.get("crunch_windows") or []
    if crunches:
        first = crunches[0]
        rows.append({
            "label": "Dated forcing",
            "claim": str(first.get("reason") or "A flagged funding window is approaching.") + ".",
            "engine": "weather",
            "asof": first.get("date"),
            "source": "Treasury auction calendar and Seiche reserve path",
        })

    court = deep.get("modelcourt") or {}
    if court.get("ok") and court.get("adjudication"):
        rows.append({
            "label": "Forecast disagreement",
            "claim": str(court["adjudication"]),
            "engine": "modelcourt",
            "asof": None,
            "source": "As-published Seiche forecast ledger",
        })
    return rows[:4]


def _countercase(engines: dict, deep: dict, headline: dict) -> list[dict]:
    rows: list[dict] = []
    kink = engines.get("kink") or {}
    spread = _f(kink.get("observed_spread_now_bp"))
    if spread is not None and spread < 0:
        rows.append({
            "claim": f"SOFR is {abs(spread):.1f}bp below IORB, a current abundance signal rather than a scarcity print.",
            "asof": _asof(kink) or _asof(headline.get("sofr_pct")),
            "source": "NY Fed secured rates and Federal Reserve IORB",
        })

    srf = headline.get("srf_accepted_b") or {}
    if _f(srf.get("value")) is not None and (_f(srf.get("value")) or 0) < 1:
        rows.append({
            "claim": f"Standing Repo Facility take-up is ${_fmt(srf.get('value'), 2)}B; the backstop is not being tested in size.",
            "asof": _asof(srf),
            "source": "New York Fed operations",
        })

    tell = deep.get("tell") or {}
    if tell.get("ok") and _f(tell.get("market_pctl")) is not None:
        rows.append({
            "claim": f"Market stress sits at only the {_fmt(tell.get('market_pctl'), 0)}th percentile of its own history.",
            "asof": _asof(tell),
            "source": "VIX, credit spreads and rates volatility",
        })
    return rows[:3]


def _watch(calendar: dict, deep: dict) -> list[dict]:
    rows: list[dict] = []
    for event in (calendar.get("crunch_windows") or [])[:3]:
        rows.append({
            "date": event.get("date"),
            "label": event.get("reason") or "flagged funding window",
            "worst_case_reserves_b": event.get("worst_case_b"),
            "settlement_b": event.get("settlement_b"),
        })
    turn = calendar.get("next_turn") or {}
    if turn.get("date") and all(row.get("date") != turn.get("date") for row in rows):
        rows.append({
            "date": turn.get("date"),
            "label": f"{str(turn.get('mode') or 'calendar turn').replace('_', ' ')}: {turn.get('forecast_bp')}bp published forecast",
            "forecast_bp": turn.get("forecast_bp"),
            "band_bp": turn.get("band_bp"),
        })
    return rows[:4]


def build_editorial(
    *,
    generated_at: str,
    engines: dict,
    deep: dict,
    headline: dict,
    calendar: dict,
    faults: list[dict],
) -> dict:
    """Build the versioned, testable editorial front page."""
    composite = engines.get("composite") or {}
    thesis, standfirst = _thesis(composite, engines, deep)
    confidence, confidence_note = _confidence(composite, engines, faults)
    top = _top_component(composite)
    return {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "thesis": thesis,
        "standfirst": standfirst,
        "confidence": confidence,
        "confidence_note": confidence_note,
        "dominant_driver": {
            "engine": top.get("component"),
            "label": _DISPLAY.get(str(top.get("component") or ""), str(top.get("component") or "")),
            "score": top.get("score"),
            "contribution": top.get("contribution"),
            "saturated": bool(top.get("saturated")),
        },
        "evidence": _evidence(engines, deep, headline, calendar),
        "countercase": _countercase(engines, deep, headline),
        "watch": _watch(calendar, deep),
        "method": (
            "Deterministic synthesis over the published point-in-time engines. "
            "Confidence measures agreement across independent signals, not the probability of a market outcome."
        ),
    }


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if len(text) == 10:
        text += "T00:00:00+00:00"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def build_data_quality(*, generated_at: str, provenance: list[dict], headline: dict) -> dict:
    """Summarise freshness without pretending unlike publication lags match."""
    statuses = Counter(str(row.get("staleness") or "unknown") for row in provenance)
    classified_statuses = ("fresh", "aging", "stale", "dead")
    classified_count = sum(statuses.get(status, 0) for status in classified_statuses)
    classified_active = sum(statuses.get(status, 0) for status in ("fresh", "aging", "stale"))
    fresh = statuses.get("fresh", 0)
    fetched = [_parse_datetime(row.get("fetched_at")) for row in provenance]
    fetched = [value for value in fetched if value is not None]
    generated = _parse_datetime(generated_at) or datetime.now(timezone.utc)

    headline_rows = []
    for key, block in headline.items():
        if not isinstance(block, dict) or block.get("asof") is None:
            continue
        asof = _parse_datetime(block.get("asof"))
        headline_rows.append({
            "series": key,
            "asof": block.get("asof"),
            "age_days": None if asof is None else max(0, (generated.date() - asof.date()).days),
        })

    return {
        "schema": QUALITY_SCHEMA,
        "generated_at": generated_at,
        "source_count": len(provenance),
        "status_counts": dict(sorted(statuses.items())),
        "classified_source_count": classified_count,
        "unclassified_source_count": len(provenance) - classified_count,
        "classification_coverage_pct": (
            round(100 * classified_count / len(provenance), 1) if provenance else None
        ),
        "fresh_share_pct": round(100 * fresh / classified_active, 1) if classified_active else None,
        "last_fetch_at": max(fetched).isoformat() if fetched else None,
        "oldest_fetch_at": min(fetched).isoformat() if fetched else None,
        "headline_ages": headline_rows,
        "realtime": {
            "available": True,
            "endpoint": "https://api.seiche.info/undertow/live/quotes.json",
            "schema": "undertow.live_relay.v1",
            "declared_cadence_ms": 1500,
            "scope": "crypto venue microstructure only",
        },
        "publication_note": (
            "Official macro series update on their publishers' clocks. "
            "Venue data is shown separately and never used to disguise a weekly or daily macro print as real time."
        ),
    }
