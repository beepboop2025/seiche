"""Rates and funding research reads over stored canonical observations only.

A request never starts collection. Every value keeps three clocks: the
publisher's observation date (``asof``), the publication time (``published_at``,
null when the publisher gives none) and Seiche's first capture
(``knowledge_time``). Nothing is interpolated or forward filled: a leg that is
absent or on another date gives a status and a reason, never a number. Every
analysis value carries its formula. Descriptive context only: no forecast, no
score, no ranking of raw levels across currencies.
"""
from __future__ import annotations

import copy
import logging
import statistics
import threading
import time as _monotonic
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from seiche.domain.observation import RATE_ROLES, QualityState, RedistributionStatus, SemanticRole
from seiche.markets.rates_reference import (
    CURVE_MAP, EXISTING_SOURCE_TERMS, FORWARDS, INSTRUMENT_LABELS, INSTRUMENT_TENORS, RATE_CURVES, RATE_SPREADS,
    RATES_SOURCES, SPREAD_MAP,
)
from seiche.markets.registry import default_registry
from seiche.repository import get_repository

LOGGER = logging.getLogger(__name__)

CATALOG_SCHEMA = "seiche.rates.catalog.v1"
SERIES_SCHEMA = "seiche.rates.series.v1"
CURVES_SCHEMA = "seiche.rates.curves.v1"
CURVE_SCHEMA = "seiche.rates.curve.v1"
SPREADS_SCHEMA = "seiche.rates.spreads.v1"
SPREAD_SCHEMA = "seiche.rates.spread.v1"
FUNDING_SCHEMA = "seiche.rates.funding-conditions.v1"

DEFAULT_DAYS = 400
MAX_DAYS = 20000
DEFAULT_WINDOW_DAYS = 1095
MIN_WINDOW_DAYS = 250
MAX_WINDOW_DAYS = 7300
MINIMUM_REGIME_OBSERVATIONS = 60
CURVE_LOOKBACK_DAYS = 40

PERCENTILE_FORMULA = ("percentile = 100 * (count below latest + 0.5 * count equal to latest) / count, over the "
                      "series' own dated values inside the stated window")
ROBUST_Z_FORMULA = "robust_z = (latest - median) / (1.4826 * median absolute deviation); 0 when the sample is flat at the latest value"
FORWARD_FORMULA = ("(s_end * t_end - s_start * t_start) / (t_end - t_start) with continuously compounded spot "
                   "rates s in percent and tenors t in years, both observed on the same date")
CAVEATS = [
    "Official observations as published; spreads, slopes, forwards and percentiles are Seiche calculations from them.",
    "Missing tenors and missing dates are not interpolated or filled.",
    "A percentile describes where the latest value sits in its own past inside the stated window. It is not a forecast.",
    "Raw rate levels in different currencies are shown side by side, never ranked against each other.",
    "Publication time, observation date and first capture are separate clocks; unknown publication times stay unknown.",
    "Rows imported from a current historical file are provisional: they were not captured as past vintages.",
]

_R = SemanticRole
FAMILY_BY_ROLE = {
    **dict.fromkeys((_R.POLICY_FLOOR, _R.POLICY_TARGET, _R.POLICY_CEILING, _R.POLICY_RANGE_LOWER,
                     _R.POLICY_RANGE_UPPER, _R.STANDING_REPO_RATE, _R.CENTRAL_BANK_FACILITY_RATE,
                     _R.RETIRED_ADMINISTERED_RATE), "policy_rate"),
    _R.UNSECURED_OVERNIGHT: "overnight_unsecured",
    _R.SECURED_OVERNIGHT: "overnight_secured",
    **dict.fromkeys((_R.RATE_P01, _R.RATE_P25, _R.RATE_MEDIAN, _R.RATE_P75, _R.RATE_P99, _R.REPO_VOLUME,
                     _R.UNSECURED_FUNDING_VOLUME, _R.BENCHMARK_TRANSACTIONS, _R.BENCHMARK_ACTIVE_BANKS, _R.BENCHMARK_SUBMITTERS,
                     _R.BENCHMARK_TOP5_VOLUME_SHARE), "overnight_distribution"),
    **dict.fromkeys((_R.COMPOUNDED_OVERNIGHT_AVERAGE_30D, _R.COMPOUNDED_OVERNIGHT_AVERAGE_90D,
                     _R.COMPOUNDED_OVERNIGHT_AVERAGE_180D, _R.COMPOUNDED_OVERNIGHT_RATE_INDEX,
                     _R.COMPOUNDED_OVERNIGHT_AVERAGE_TENOR), "compounded_average"),
    **dict.fromkeys((_R.TERM_1W, _R.TERM_1M, _R.TERM_3M, _R.TERM_FUNDING_RATE, _R.CP_3M, _R.CD_3M),
                    "term_money_market"),
    **dict.fromkeys((_R.TBILL_3M, _R.TBILL_6M, _R.TBILL_12M, _R.TBILL_DISCOUNT_RATE), "bills"),
    _R.SOVEREIGN_YIELD: "government_curve",
    **dict.fromkeys((_R.SYSTEM_LIQUIDITY, _R.CENTRAL_BANK_TOTAL_ASSETS, _R.CENTRAL_BANK_BALANCE_SHEET_ITEM,
                     _R.RESERVE_BALANCES, _R.DURABLE_LIQUIDITY, _R.NET_LIQUIDITY_OPERATIONS,
                     _R.GOVERNMENT_CASH_BALANCE, _R.CENTRAL_BANK_ASSET_TRANSACTIONS, _R.RESERVE_REQUIREMENT,
                     _R.RESERVE_REQUIREMENT_RATIO, _R.TERM_FUNDING_OUTSTANDING, _R.TERM_FUNDING_ISSUANCE),
                    "central_bank_balance_sheet"),
    _R.CENTRAL_BANK_FACILITY_TAKEUP: "central_bank_facility",
}
FAMILY_LABELS = {
    "policy_rate": "Policy rates",
    "overnight_unsecured": "Overnight unsecured benchmarks",
    "overnight_secured": "Overnight secured benchmarks",
    "overnight_distribution": "Overnight distribution: percentiles, volumes, counts",
    "compounded_average": "Compounded averages and indices",
    "term_money_market": "Term money market",
    "bills": "Bills",
    "government_curve": "Government curves",
    "central_bank_balance_sheet": "Central bank balance sheet and liquidity",
    "central_bank_facility": "Central bank facility usage",
    "auction_result": "Auction results",
}

# Funding legs per market, in preference order; the first declared public leg with
# stored observations is used and named in the payload.
FUNDING_LEGS: dict[str, dict[str, tuple[str, ...]]] = {
    "US-USD": {"overnight": ("US.NYFED.SOFR_MEDIAN",), "anchor": ("US.FED.IORB_DIRECT",),
               "floor": ("US.NYFED.FFR_TARGET_RANGE_LOWER",), "ceiling": ("US.NYFED.FFR_TARGET_RANGE_UPPER",),
               "volume": ("US.NYFED.SOFR_VOLUME",), "p25": ("US.NYFED.SOFR_P25",), "p75": ("US.NYFED.SOFR_P75",)},
    "EA-EUR": {"overnight": ("EA.ECB.ESTR",), "anchor": ("EA.ECB.DFR",), "floor": ("EA.ECB.DFR",),
               "ceiling": ("EA.ECB.MLF",), "volume": ("EA.ECB.ESTR_VOLUME",), "p25": ("EA.ECB.ESTR_P25",),
               "p75": ("EA.ECB.ESTR_P75",), "transactions": ("EA.ECB.ESTR_TRANSACTIONS",),
               "active_banks": ("EA.ECB.ESTR_ACTIVE_BANKS",), "top5_share": ("EA.ECB.ESTR_TOP5_SHARE",)},
    "UK-GBP": {"overnight": ("GB.BOE.SONIA",), "anchor": ("GB.BOE.BANK_RATE",), "floor": ("GB.BOE.OSF_DEPOSIT",),
               "ceiling": ("GB.BOE.OSF_LENDING",)},
    "JP-JPY": {"overnight": ("JP.BOJ.TONA",), "anchor": ("JP.BOJ.POLICY_TARGET", "JP.BOJ.BASIC_LOAN"),
               "floor": ("JP.BOJ.COMPLEMENTARY_DEPOSIT",), "ceiling": ("JP.BOJ.BASIC_LOAN",)},
    "CA-CAD": {"overnight": ("CA.BOC.CORRA",), "anchor": ("CA.BOC.POLICY_RATE",), "floor": ("CA.BOC.DEPOSIT_RATE",),
               "ceiling": ("CA.BOC.BANK_RATE",), "volume": ("CA.BOC.CORRA_VOLUME",), "p25": ("CA.BOC.CORRA_P25",),
               "p75": ("CA.BOC.CORRA_P75",)},
    "AU-AUD": {"overnight": ("AU.RBA.AONIA",), "anchor": ("AU.RBA.CASH_TARGET",), "floor": ("AU.RBA.ES_FLOOR",),
               "ceiling": ("AU.RBA.STANDING_CEILING",)},
    "SG-SGD": {"overnight": ("SG.MAS.SORA",), "anchor": (), "floor": ("SG.MAS.STANDING_DEPOSIT",),
               "ceiling": ("SG.MAS.STANDING_BORROWING",)},
    "IN-INR": {"overnight": ("IN.MARKET.CALL_WAR",), "anchor": ("IN.RBI.POLICY_REPO",), "floor": ("IN.RBI.SDF",),
               "ceiling": ("IN.RBI.MSF",)},
    "TW-TWD": {"overnight": ("TW.CBC.OVERNIGHT_CALL",), "anchor": ("TW.CBC.DISCOUNT_DECISION",)},
    "HK-HKD": {"overnight": (), "anchor": ("HK.HKMA.BASE_RATE",)},
}
FUNDING_NOTES = {
    "CA-CAD": "CORRA methodology changed on 12 June 2020. Policy band rows are published on change dates; no daily band values are filled. Publication receipts and a validated Bank calendar are unavailable, so freshness remains UNKNOWN.",
    "SG-SGD": "Monetary policy works through the exchange rate; no policy interest rate anchor exists.",
    "HK-HKD": "The overnight benchmark is not redistributable under its terms; only the base rate is public.",
}
DISPLAY_LABELS = {
    "US.NYFED.SOFR_MEDIAN": "SOFR (secured overnight financing rate, volume-weighted median)",
    "US.FED.IORB_DIRECT": "Interest on reserve balances",
    "US.NYFED.FFR_TARGET_RANGE_LOWER": "Federal funds target range, lower bound",
    "US.NYFED.FFR_TARGET_RANGE_UPPER": "Federal funds target range, upper bound",
    "US.NYFED.SOFR_VOLUME": "SOFR transaction volume",
    "US.NYFED.SOFR_P25": "SOFR 25th percentile", "US.NYFED.SOFR_P75": "SOFR 75th percentile",
    "EA.ECB.ESTR": "Euro short-term rate (€STR)", "EA.ECB.DFR": "ECB deposit facility rate",
    "EA.ECB.MRO": "ECB main refinancing operations rate", "EA.ECB.MLF": "ECB marginal lending facility rate",
    "EA.ECB.ESTR_VOLUME": "€STR total volume",
    "GB.BOE.SONIA": "SONIA", "GB.BOE.BANK_RATE": "Bank Rate",
    "GB.BOE.OSF_DEPOSIT": "Bank of England operational standing deposit facility rate",
    "GB.BOE.OSF_LENDING": "Bank of England operational standing lending facility rate",
    "JP.BOJ.TONA": "TONA (uncollateralized overnight call rate)", "JP.BOJ.POLICY_TARGET": "Bank of Japan policy rate target",
    "JP.BOJ.BASIC_LOAN": "Bank of Japan basic loan rate", "JP.BOJ.COMPLEMENTARY_DEPOSIT": "Bank of Japan complementary deposit rate",
    "AU.RBA.AONIA": "AONIA (interbank overnight cash rate)", "AU.RBA.CASH_TARGET": "RBA cash rate target",
    "AU.RBA.ES_FLOOR": "RBA exchange settlement balance rate", "AU.RBA.STANDING_CEILING": "RBA standing facility rate",
    "SG.MAS.SORA": "SORA", "SG.MAS.STANDING_DEPOSIT": "MAS standing deposit facility rate",
    "SG.MAS.STANDING_BORROWING": "MAS standing loan facility rate",
    "IN.MARKET.CALL_WAR": "Call money weighted average rate", "IN.RBI.POLICY_REPO": "RBI policy repo rate",
    "IN.RBI.SDF": "RBI standing deposit facility rate", "IN.RBI.MSF": "RBI marginal standing facility rate",
    "TW.CBC.OVERNIGHT_CALL": "Interbank overnight call loan rate", "TW.CBC.DISCOUNT_DECISION": "CBC discount rate",
    "HK.HKMA.BASE_RATE": "HKMA base rate",
}
_STATE_ORDER = ("DEAD", "STALE", "UNAVAILABLE", "UNKNOWN", "RESTRICTED", "AGING", "FRESH")
_CURRENT = frozenset({"FRESH", "AGING"})

_CACHE: dict[tuple, tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()
CACHE_SECONDS = 60.0


def _cached(key: tuple, build):
    now = _monotonic.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is not None and hit[0] > now:
            return copy.deepcopy(hit[1])
    value = build()
    with _CACHE_LOCK:
        _CACHE[key] = (now + CACHE_SECONDS, value)
    return copy.deepcopy(value)


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


# ----------------------------------------------------------------------------- helpers

def _cutoff(now: datetime | None) -> datetime:
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("evaluation time must be timezone-aware")
    return moment.astimezone(UTC).replace(microsecond=0)


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(float(value), digits)


def worst_state(states) -> str | None:
    present = [state for state in states if state]
    if not present:
        return None
    return min(present, key=lambda state: _STATE_ORDER.index(state) if state in _STATE_ORDER else 0)


def percentile_rank(values: list[float]) -> float | None:
    """Where the last value sits in its own sample, mid-rank for ties."""
    if not values:
        return None
    latest = values[-1]
    below = sum(value < latest for value in values)
    tied = sum(value == latest for value in values)
    return 100.0 * (below + 0.5 * tied) / len(values)


def robust_z(values: list[float]) -> float | None:
    if not values:
        return None
    center = statistics.median(values)
    scale = 1.4826 * statistics.median(abs(value - center) for value in values)
    if scale <= 0:
        return 0.0 if values[-1] == center else None
    return (values[-1] - center) / scale


def continuous_forward(start_rate: float, start_years: float, end_rate: float, end_years: float) -> float:
    """Forward rate between two continuously compounded spot rates (same units in and out)."""
    if not end_years > start_years >= 0:
        raise ValueError("forward tenors must satisfy 0 <= start < end")
    return (end_rate * end_years - start_rate * start_years) / (end_years - start_years)


def source_reference(adapter_id: str) -> dict[str, Any]:
    from seiche.markets.atlas import market_source_reference
    from seiche.markets.funding_reference import SOURCE_REFERENCES
    reference = dict(market_source_reference(adapter_id))
    extra = RATES_SOURCES.get(adapter_id) or SOURCE_REFERENCES.get(adapter_id) or EXISTING_SOURCE_TERMS.get(adapter_id) or {}
    for key in ("publisher", "source_url"):
        if extra.get(key):
            reference[key] = extra[key]
    if not reference.get("terms_url"):
        reference["terms_url"] = extra.get("terms_url") or extra.get("rights_url")
    if not reference.get("attribution"):
        reference["attribution"] = extra.get("attribution") or (
            f"Source: {reference['publisher']}." if reference.get("publisher") else None)
    return reference


def _is_public(pack, spec) -> bool:
    from seiche.markets.atlas import _instrument_is_public
    return spec.source_adapter_id != "tenant_market_data" and _instrument_is_public(pack, spec)


def _validated(pack, rows, cutoff) -> dict[str, list]:
    """Identity, convention, rights and clock checks; latest vintage per date."""
    from seiche.markets.atlas import _instrument_rows
    kept = []
    for row in rows:
        spec = pack.instrument_map.get(row.instrument_id)
        if spec is None or row.market_id != pack.market_id:
            continue
        adapter = pack.adapter_map[spec.source_adapter_id]
        if (row.source != spec.source_adapter_id or row.semantic_role != spec.semantic_role
                or row.canonical_unit != spec.canonical_unit or row.currency != pack.currency
                or row.rate_compounding != spec.rate_compounding or row.day_count != spec.day_count
                or row.connector_classification != adapter.classification
                or adapter.redistribution_status is not RedistributionStatus.ALLOWED
                or row.knowledge_time > cutoff or row.event_time > cutoff
                or row.source_publication_time is not None and row.source_publication_time > cutoff):
            continue
        kept.append(row)
    # Resolve revisions first so a withdrawn vintage never resurrects an older value.
    return {identifier: [row for row in values
                         if row.usable and row.redistribution_status is RedistributionStatus.ALLOWED]
            for identifier, values in _instrument_rows(kept).items()}


def _rows(repository, pack, cutoff, identifiers, *, event_from=None, event_to=None) -> dict[str, list]:
    identifiers = sorted(set(identifiers))
    if not identifiers:
        return {}
    upper = min(event_to, cutoff) if event_to is not None else cutoff
    rows = repository.load_observations_as_of(
        pack.market_id, cutoff, event_time=upper, event_time_from=event_from, instrument_ids=identifiers)
    return _validated(pack, rows, cutoff)


def _display(spec, value) -> float:
    return float(value) / 100.0 if spec.semantic_role in RATE_ROLES else float(value)


def _metric_block(pack, spec, rows, cutoff) -> dict[str, Any]:
    from seiche.markets.atlas import _metric
    metric = _metric(pack, spec, rows, cutoff=cutoff)
    metric.pop("history", None)
    tenor, years = INSTRUMENT_TENORS.get(spec.instrument_id, (None, None))
    reference = source_reference(spec.source_adapter_id)
    metric.update(
        label=INSTRUMENT_LABELS.get(spec.instrument_id) or DISPLAY_LABELS.get(spec.instrument_id) or metric["label"],
        family=FAMILY_BY_ROLE.get(spec.semantic_role), tenor=tenor, tenor_years=years,
        market_id=pack.market_id, currency=pack.currency, publisher=reference.get("publisher"),
    )
    if not metric.get("attribution"):
        metric["attribution"] = reference.get("attribution")
    if not metric.get("terms_url"):
        metric["terms_url"] = reference.get("terms_url")
    if not metric.get("source_url"):
        metric["source_url"] = reference.get("source_url")
    return metric


def provenance(metrics) -> list[dict[str, Any]]:
    """One entry per source that contributed an available value to the payload."""
    grouped: dict[str, list[dict]] = {}
    for metric in metrics:
        if metric and metric.get("availability") == "AVAILABLE" and metric.get("source"):
            grouped.setdefault(metric["source"], []).append(metric)
    entries = []
    for source, items in sorted(grouped.items()):
        latest_asof = max(item["asof"] for item in items)
        published = [item["published_at"] for item in items if item["asof"] == latest_asof and item.get("published_at")]
        reference = source_reference(source)
        entries.append({
            "source": source,
            "publisher": reference.get("publisher"),
            "source_url": reference.get("source_url") or items[0].get("source_url"),
            "terms_url": reference.get("terms_url"),
            "attribution": reference.get("attribution"),
            "redistribution_status": items[0].get("redistribution_status"),
            "cadence": items[0].get("cadence"),
            "asof": latest_asof,
            "published_at": max(published) if published else None,
            "fetched_at": max(item["knowledge_time"] for item in items if item.get("knowledge_time")),
            "staleness": (worst_state(item.get("status") for item in items) or "UNKNOWN").lower(),
            "instruments": len(items),
        })
    return entries


def _locate(registry, instrument_id: str):
    if not isinstance(instrument_id, str) or not instrument_id.strip() or len(instrument_id) > 96:
        raise ValueError("instrument must be a nonblank id of at most 96 characters; see /api/v2/rates")
    for pack in registry.list():
        spec = pack.instrument_map.get(instrument_id)
        if spec is not None:
            if not _is_public(pack, spec) or FAMILY_BY_ROLE.get(spec.semantic_role) is None:
                break
            return pack, spec
    raise ValueError("unknown or non-public rates instrument; see /api/v2/rates")


def _envelope(schema: str, cutoff: datetime, status: str, **body) -> dict[str, Any]:
    return {"schema": schema, "generated_at": cutoff.isoformat(), "status": status, "context_only": True, **body}


# ----------------------------------------------------------------------------- catalog

def catalog(*, repository=None, registry=None, now=None) -> dict[str, Any]:
    if repository is None and registry is None and now is None:
        return _cached(("catalog",), lambda: _catalog(get_repository(), default_registry(), _cutoff(None)))
    return _catalog(repository or get_repository(), registry or default_registry(), _cutoff(now))


def _catalog(repository, registry, cutoff) -> dict[str, Any]:
    counter = getattr(repository, "instrument_observation_counts", None)
    markets, metrics_all, declared_empty, faults = [], [], [], []
    for pack in registry.list():
        specs = [spec for spec in pack.instruments if _is_public(pack, spec) and FAMILY_BY_ROLE.get(spec.semantic_role)]
        if not specs:
            continue
        try:
            counts = counter(pack.market_id, cutoff, instrument_ids=[s.instrument_id for s in specs]) if callable(counter) else {}
            with_data = [s for s in specs if (s.instrument_id, s.source_adapter_id) in counts]
            recent_floor = cutoff - timedelta(days=10)
            current = [s for s in with_data
                       if _parse_time(counts[(s.instrument_id, s.source_adapter_id)]["event_end"]) >= recent_floor]
            rows = _rows(repository, pack, cutoff, [s.instrument_id for s in current], event_from=recent_floor)
            for spec in with_data:
                if spec in current:
                    continue
                last = _parse_time(counts[(spec.instrument_id, spec.source_adapter_id)]["event_end"])
                rows.update(_rows(repository, pack, cutoff, [spec.instrument_id], event_from=last))
        except Exception:  # noqa: BLE001: one market's read fault never hides the others
            LOGGER.exception("rates catalog read failed for %s", pack.market_id)
            faults.append({"market_id": pack.market_id, "reason": "Canonical observations could not be read."})
            continue
        instruments = []
        for spec in with_data:
            count = counts[(spec.instrument_id, spec.source_adapter_id)]
            metric = _metric_block(pack, spec, rows.get(spec.instrument_id, []), cutoff)
            metrics_all.append(metric)
            instruments.append({
                "instrument_id": spec.instrument_id, "label": metric["label"], "family": metric["family"],
                "semantic_role": spec.semantic_role.value, "tenor": metric["tenor"], "tenor_years": metric["tenor_years"],
                "unit": metric["unit"], "cadence": metric["cadence"], "source": spec.source_adapter_id,
                "publisher": metric["publisher"], "first_observation": str(count["event_start"])[:10],
                "last_observation": str(count["event_end"])[:10], "observations": count["observations"],
                "value": metric["value"], "asof": metric["asof"], "published_at": metric["published_at"],
                "knowledge_time": metric["knowledge_time"], "status": metric["status"],
                "revision_status": metric["revision_status"],
                "redistribution_status": metric["redistribution_status"], "source_url": metric["source_url"],
                "series_url": "/api/v2/rates/series/" + spec.instrument_id,
            })
        empty = [s.instrument_id for s in specs if s not in with_data]
        if empty:
            declared_empty.append({"market_id": pack.market_id, "instruments": len(empty)})
        if instruments:
            markets.append({"market_id": pack.market_id, "currency": pack.currency, "display_name": pack.display_name,
                            "instruments": instruments, "declared_without_observations": len(empty)})
    families: dict[str, dict] = {}
    for market in markets:
        for item in market["instruments"]:
            family = families.setdefault(item["family"], {"family": item["family"], "label": FAMILY_LABELS[item["family"]],
                                                          "instruments": 0, "observations": 0, "markets": set()})
            family["instruments"] += 1
            family["observations"] += item["observations"]
            family["markets"].add(market["market_id"])
    family_list = [{**value, "markets": sorted(value["markets"])} for _, value in sorted(families.items())]
    present = {market["market_id"] for market in markets}
    sources = {item["source"] for market in markets for item in market["instruments"]}
    return _envelope(
        CATALOG_SCHEMA, cutoff, "ok" if markets and not faults else "partial" if markets else "unavailable",
        totals={"markets": len(markets), "instruments": sum(len(m["instruments"]) for m in markets),
                "observations": sum(f["observations"] for f in family_list), "sources": len(sources)},
        counting_rule=("observations are distinct stored publisher dates per instrument and source, read from "
                       "the store index; revisions of a date count once"),
        families=family_list, markets=markets,
        curves=[{"curve_id": c.curve_id, "label": c.label, "market_id": c.market_id, "kind": c.kind,
                 "tenors": [t for t, _, _ in c.nodes], "url": "/api/v2/rates/curves/" + c.curve_id}
                for c in RATE_CURVES if c.market_id in present],
        spreads=[{"spread_id": s.spread_id, "label": s.label, "market_id": s.market_id, "kind": s.kind,
                  "formula": s.formula, "url": "/api/v2/rates/spreads/" + s.spread_id}
                 for s in RATE_SPREADS if s.market_id in present],
        declared_without_observations=declared_empty, read_faults=faults,
        provenance=provenance(metrics_all), caveats=CAVEATS,
    )


def _parse_time(value) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# ----------------------------------------------------------------------------- series

def series(instrument_id: str, *, days: int = DEFAULT_DAYS, repository=None, registry=None, now=None) -> dict[str, Any]:
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= MAX_DAYS:
        raise ValueError(f"days must be an integer from 1 to {MAX_DAYS}")
    registry = registry or default_registry()
    repository = repository or get_repository()
    cutoff = _cutoff(now)
    pack, spec = _locate(registry, instrument_id)
    rows = _rows(repository, pack, cutoff, [spec.instrument_id]).get(spec.instrument_id, [])
    metric = _metric_block(pack, spec, rows, cutoff)
    window: list = []
    start = None
    if rows:
        start = rows[-1].event_time.date() - timedelta(days=days)
        window = [row for row in rows if row.event_time.date() >= start]
    return _envelope(
        SERIES_SCHEMA, cutoff, "ok" if rows else "unavailable",
        instrument=metric, observation_unit=metric["unit"],
        observations=[[row.event_time.date().isoformat(), _round(_display(spec, row.value), 6)] for row in window],
        window={"days": days, "from": window[0].event_time.date().isoformat() if window else None,
                "through": window[-1].event_time.date().isoformat() if window else None,
                "observations": len(window), "stored_observations": len(rows),
                "first_stored": rows[0].event_time.date().isoformat() if rows else None,
                "rule": "calendar days back from the latest stored observation; one value per publisher date"},
        revisions={"revised_points_in_window": sum(row.quality is QualityState.REVISED for row in window),
                   "provisional_points_in_window": sum(row.quality is QualityState.PROVISIONAL for row in window)},
        provenance=provenance([metric]), caveats=CAVEATS,
    )


# ----------------------------------------------------------------------------- curves

def _curve_specs(registry, curve):
    pack = registry.get(curve.market_id)
    specs = {}
    for tenor, years, identifier in curve.nodes:
        spec = pack.instrument_map.get(identifier)
        if spec is not None and _is_public(pack, spec):
            specs[identifier] = spec
    return pack, specs


def _curve_date(rows: dict[str, list], on: date | None) -> date | None:
    counts: dict[date, int] = {}
    for values in rows.values():
        for row in values:
            day = row.event_time.date()
            if on is None or day <= on:
                counts[day] = counts.get(day, 0) + 1
    eligible = [day for day, count in counts.items() if count >= 2]
    return max(eligible) if eligible else None


def _value_on(rows: list, day: date | None):
    if day is None:
        return None, None
    previous = None
    for row in rows:
        current = row.event_time.date()
        if current == day:
            return row, previous
        if current > day:
            break
        previous = row
    return None, None


def _curve_snapshot(repository, pack, specs, cutoff, on: date | None):
    """Rows near the requested date and the curve date they support."""
    floor = (on or cutoff.date()) - timedelta(days=CURVE_LOOKBACK_DAYS)
    rows = _rows(repository, pack, cutoff, specs, event_from=_day_start(floor),
                 event_to=_day_start(on) if on else None)
    day = _curve_date(rows, on)
    if day is None and on is None:
        rows = _rows(repository, pack, cutoff, specs, event_from=_day_start(cutoff.date() - timedelta(days=400)))
        day = _curve_date(rows, None)
    return rows, day


def curves(*, repository=None, registry=None, now=None) -> dict[str, Any]:
    registry = registry or default_registry()
    repository = repository or get_repository()
    cutoff = _cutoff(now)
    listed, metrics_all = [], []
    present = {pack.market_id for pack in registry.list()}
    for curve in RATE_CURVES:
        if curve.market_id not in present:
            continue
        pack, specs = _curve_specs(registry, curve)
        rows, day = _curve_snapshot(repository, pack, specs, cutoff, None)
        metrics = [_metric_block(pack, spec, rows.get(identifier, []), cutoff) for identifier, spec in specs.items()]
        metrics_all.extend(metrics)
        listed.append({
            "curve_id": curve.curve_id, "label": curve.label, "market_id": curve.market_id, "currency": curve.currency,
            "kind": curve.kind, "compounding": curve.compounding, "cadence": curve.cadence,
            "tenors": [t for t, _, _ in curve.nodes], "asof": day.isoformat() if day else None,
            "nodes_on_curve_date": sum(1 for values in rows.values() if _value_on(values, day)[0] is not None),
            "status": worst_state(m["status"] for m in metrics) or "UNAVAILABLE",
            "url": "/api/v2/rates/curves/" + curve.curve_id,
        })
    return _envelope(CURVES_SCHEMA, cutoff, "ok" if any(c["asof"] for c in listed) else "unavailable",
                     curves=listed, provenance=provenance(metrics_all), caveats=CAVEATS)


def curve(curve_id: str, *, on: date | None = None, compare: date | None = None,
          repository=None, registry=None, now=None) -> dict[str, Any]:
    if curve_id not in CURVE_MAP:
        raise ValueError("unknown curve; see /api/v2/rates/curves")
    definition = CURVE_MAP[curve_id]
    registry = registry or default_registry()
    repository = repository or get_repository()
    cutoff = _cutoff(now)
    if on is not None and on > cutoff.date():
        raise ValueError("date cannot be in the future")
    if compare is not None and compare > cutoff.date():
        raise ValueError("compare cannot be in the future")
    pack, specs = _curve_specs(registry, definition)
    current_rows, current_day = _curve_snapshot(repository, pack, specs, cutoff, None)
    metrics = {identifier: _metric_block(pack, spec, current_rows.get(identifier, []), cutoff)
               for identifier, spec in specs.items()}
    if on is None or (current_day is not None and on >= current_day):
        rows, day = current_rows, current_day
    else:
        rows, day = _curve_snapshot(repository, pack, specs, cutoff, on)
    compare_rows, compare_day = (_curve_snapshot(repository, pack, specs, cutoff, compare)
                                 if compare is not None else ({}, None))
    values: dict[str, float] = {}
    compare_values: dict[str, float] = {}
    nodes = []
    for tenor, years, identifier in definition.nodes:
        spec = specs.get(identifier)
        metric = metrics.get(identifier)
        node = {"tenor": tenor, "tenor_years": _round(years, 6), "instrument_id": identifier,
                "label": metric["label"] if metric else identifier, "unit": "%", "value": None,
                "canonical_value": None, "asof": None, "status": metric["status"] if metric else "UNAVAILABLE",
                "published_at": None, "knowledge_time": None, "revision_status": None,
                "previous": None, "comparison": None, "reason": None}
        if spec is None:
            node["reason"] = "Not declared as a public instrument in this market."
            nodes.append(node)
            continue
        row, previous = _value_on(rows.get(identifier, []), day)
        if row is None:
            node["reason"] = "No observation on the curve date; nothing is filled."
            nodes.append(node)
            continue
        bp = float(row.value)
        values[tenor] = bp
        node.update(value=_round(bp / 100.0, 6), canonical_value=_round(bp, 4), asof=day.isoformat(),
                    published_at=row.source_publication_time.isoformat() if row.source_publication_time else None,
                    knowledge_time=row.knowledge_time.isoformat(), revision_status=row.quality.value)
        if previous is not None:
            node["previous"] = {"asof": previous.event_time.date().isoformat(),
                                "value": _round(float(previous.value) / 100.0, 6),
                                "change_bp": _round(bp - float(previous.value), 4)}
        compare_row, _ = _value_on(compare_rows.get(identifier, []), compare_day)
        if compare_row is not None:
            compare_values[tenor] = float(compare_row.value)
            node["comparison"] = {"asof": compare_day.isoformat(),
                                  "value": _round(float(compare_row.value) / 100.0, 6),
                                  "change_bp": _round(bp - float(compare_row.value), 4)}
        nodes.append(node)
    slopes = []
    for spread in RATE_SPREADS:
        if spread.kind != "curve_slope" or spread.market_id != definition.market_id:
            continue
        identifiers = {identifier for _, _, identifier in definition.nodes}
        (long_id, _), (short_id, _) = spread.legs
        if long_id not in identifiers or short_id not in identifiers:
            continue
        long_tenor, short_tenor = INSTRUMENT_TENORS[long_id][0], INSTRUMENT_TENORS[short_id][0]
        item = {"id": spread.spread_id.rsplit("_", 1)[-1].lower(), "spread_id": spread.spread_id,
                "short": short_tenor, "long": long_tenor, "value_bp": None,
                "asof": day.isoformat() if day else None, "status": "unavailable",
                "comparison_value_bp": None, "change_bp": None,
                "formula": "long tenor rate minus short tenor rate on the same observation date, in basis points",
                "reason": None}
        if long_tenor in values and short_tenor in values:
            item.update(value_bp=_round(values[long_tenor] - values[short_tenor], 4), status="dated")
            if long_tenor in compare_values and short_tenor in compare_values:
                item["comparison_value_bp"] = _round(compare_values[long_tenor] - compare_values[short_tenor], 4)
                item["change_bp"] = _round(item["value_bp"] - item["comparison_value_bp"], 4)
        else:
            item["reason"] = "A leg has no observation on the curve date."
        slopes.append(item)
    forwards = []
    gaps = ["Tenors the publisher does not print are not interpolated."]
    exact = definition.kind == "zero_coupon_spot" and definition.compounding == "continuous"
    if exact:
        by_years = {years: tenor for tenor, years, _ in definition.nodes}
        for forward_id, start_years, end_years in FORWARDS:
            start_tenor, end_tenor = by_years.get(start_years), by_years.get(end_years)
            if start_tenor not in values or end_tenor not in values:
                continue
            value = continuous_forward(values[start_tenor], start_years, values[end_tenor], end_years) / 100.0
            item = {"id": forward_id, "start_years": start_years, "end_years": end_years, "value": _round(value, 6),
                    "unit": "%", "asof": day.isoformat(), "comparison_value": None, "change_bp": None,
                    "formula": FORWARD_FORMULA}
            if start_tenor in compare_values and end_tenor in compare_values:
                previous_value = continuous_forward(compare_values[start_tenor], start_years,
                                                    compare_values[end_tenor], end_years) / 100.0
                item["comparison_value"] = _round(previous_value, 6)
                item["change_bp"] = _round((value - previous_value) * 100.0, 4)
            forwards.append(item)
    else:
        gaps.append("No forwards: the publisher's convention for this curve does not make continuous-compounding "
                    "forward arithmetic exact.")
    available = sum(1 for node in nodes if node["value"] is not None)
    return _envelope(
        CURVE_SCHEMA, cutoff, "ok" if available >= 2 else "unavailable",
        curve_id=definition.curve_id, market_id=definition.market_id, currency=definition.currency,
        label=definition.label, kind=definition.kind, compounding=definition.compounding,
        cadence=definition.cadence, methodology=definition.methodology,
        asof=day.isoformat() if day else None, requested_date=on.isoformat() if on else None,
        date_rule="latest date on or before the requested date on which at least two nodes are observed",
        comparison=({"requested": compare.isoformat(), "used": compare_day.isoformat() if compare_day else None,
                     "rule": "latest observation date on or before the requested date"} if compare else None),
        nodes=nodes, slopes=slopes, forwards=forwards, known_gaps=gaps,
        provenance=provenance(metrics.values()), caveats=CAVEATS,
    )


# ----------------------------------------------------------------------------- spreads

def _aligned(leg_rows: list[tuple[list, int]]) -> list[tuple[date, float]]:
    maps = [{row.event_time.date(): float(row.value) for row in rows} for rows, _ in leg_rows]
    if not maps:
        return []
    common = set(maps[0]).intersection(*maps[1:])
    return [(day, sum(sign * values[day] for values, (_, sign) in zip(maps, leg_rows))) for day in sorted(common)]


def regime(points: list[tuple[date, float]], window_days: int) -> dict[str, Any]:
    base = {"window_days": window_days, "minimum_observations": MINIMUM_REGIME_OBSERVATIONS,
            "formula": PERCENTILE_FORMULA + "; " + ROBUST_Z_FORMULA}
    if not points:
        return {**base, "status": "unavailable", "window": None, "percentile": None, "robust_z": None,
                "median_bp": None, "min": None, "max": None}
    last = points[-1][0]
    start = last - timedelta(days=window_days)
    window = [(day, value) for day, value in points if day >= start]
    values = [value for _, value in window]
    stats = {"window": {"requested_from": start.isoformat(), "from": window[0][0].isoformat(),
                        "through": last.isoformat(), "observations": len(window)}}
    if len(values) < MINIMUM_REGIME_OBSERVATIONS:
        return {**base, **stats, "status": "insufficient_history", "percentile": None, "robust_z": None,
                "median_bp": None, "min": None, "max": None}
    low = min(window, key=lambda item: (item[1], item[0]))
    high = max(window, key=lambda item: (item[1], item[0]))
    return {**base, **stats, "status": "ok", "percentile": _round(percentile_rank(values), 1),
            "robust_z": _round(robust_z(values), 3), "median_bp": _round(statistics.median(values), 4),
            "min": {"value_bp": _round(low[1], 4), "date": low[0].isoformat()},
            "max": {"value_bp": _round(high[1], 4), "date": high[0].isoformat()}}


def _spread_inputs(spread, registry, repository, cutoff, *, event_from=None):
    pack = registry.get(spread.market_id)
    specs = {identifier: pack.instrument_map.get(identifier) for identifier, _ in spread.legs}
    missing = [identifier for identifier, spec in specs.items() if spec is None or not _is_public(pack, spec)]
    if missing:
        return pack, specs, {}, missing
    rows = _rows(repository, pack, cutoff, specs, event_from=event_from)
    return pack, specs, rows, []


def spread(spread_id: str, *, window_days: int = DEFAULT_WINDOW_DAYS, days: int = DEFAULT_DAYS,
           repository=None, registry=None, now=None) -> dict[str, Any]:
    if spread_id not in SPREAD_MAP:
        raise ValueError("unknown spread; see /api/v2/rates/spreads")
    _check_window(window_days)
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= MAX_DAYS:
        raise ValueError(f"days must be an integer from 1 to {MAX_DAYS}")
    definition = SPREAD_MAP[spread_id]
    registry = registry or default_registry()
    repository = repository or get_repository()
    cutoff = _cutoff(now)
    pack, specs, rows, missing = _spread_inputs(definition, registry, repository, cutoff)
    metrics = [_metric_block(pack, spec, rows.get(identifier, []), cutoff)
               for identifier, spec in specs.items() if spec is not None and identifier not in missing]
    points = _aligned([(rows.get(identifier, []), sign) for identifier, sign in definition.legs]) if not missing else []
    latest = None
    if points:
        latest = {"value_bp": _round(points[-1][1], 4), "asof": points[-1][0].isoformat(),
                  "status": worst_state(m["status"] for m in metrics) or "UNKNOWN"}
    history_start = points[-1][0] - timedelta(days=days) if points else None
    full = None
    if points:
        full = {"from": points[0][0].isoformat(), "through": points[-1][0].isoformat(), "observations": len(points),
                "percentile": _round(percentile_rank([value for _, value in points]), 1)}
    return _envelope(
        SPREAD_SCHEMA, cutoff, "ok" if points else "unavailable",
        spread_id=definition.spread_id, label=definition.label, market_id=definition.market_id,
        kind=definition.kind, unit="bp", formula=definition.formula,
        legs=[{"instrument_id": identifier, "sign": sign} for identifier, sign in definition.legs],
        latest=latest, regime=regime(points, window_days), full_history=full,
        history=[[day.isoformat(), _round(value, 4)] for day, value in points if day >= history_start] if points else [],
        history_unit="bp",
        reason=("A leg is not a public instrument in this market." if missing else
                None if points else "No date carries both legs."),
        provenance=provenance(metrics), caveats=CAVEATS,
    )


def spreads(*, window_days: int = DEFAULT_WINDOW_DAYS, repository=None, registry=None, now=None) -> dict[str, Any]:
    _check_window(window_days)
    if repository is None and registry is None and now is None:
        return _cached(("spreads", window_days), lambda: _spreads(
            window_days, get_repository(), default_registry(), _cutoff(None)))
    return _spreads(window_days, repository or get_repository(), registry or default_registry(), _cutoff(now))


def _spreads(window_days, repository, registry, cutoff) -> dict[str, Any]:
    listed, metrics_all = [], []
    present = {pack.market_id for pack in registry.list()}
    for definition in RATE_SPREADS:
        if definition.market_id not in present:
            continue
        # The latest value and the default-window regime need only the window plus a margin.
        floor = _day_start(cutoff.date() - timedelta(days=window_days + 400))
        pack, specs, rows, missing = _spread_inputs(definition, registry, repository, cutoff, event_from=floor)
        metrics = [_metric_block(pack, spec, rows.get(identifier, []), cutoff)
                   for identifier, spec in specs.items() if spec is not None and identifier not in missing]
        metrics_all.extend(metrics)
        points = _aligned([(rows.get(identifier, []), sign) for identifier, sign in definition.legs]) if not missing else []
        context = regime(points, window_days)
        listed.append({
            "spread_id": definition.spread_id, "label": definition.label, "market_id": definition.market_id,
            "kind": definition.kind, "unit": "bp", "formula": definition.formula,
            "latest": ({"value_bp": _round(points[-1][1], 4), "asof": points[-1][0].isoformat(),
                        "status": worst_state(m["status"] for m in metrics) or "UNKNOWN"} if points else None),
            "regime": {key: context[key] for key in ("status", "window_days", "window", "percentile", "robust_z")},
            "url": "/api/v2/rates/spreads/" + definition.spread_id,
        })
    return _envelope(SPREADS_SCHEMA, cutoff, "ok" if any(s["latest"] for s in listed) else "unavailable",
                     spreads=listed, regime_formula=PERCENTILE_FORMULA, provenance=provenance(metrics_all),
                     caveats=CAVEATS)


def _check_window(window_days) -> None:
    if (not isinstance(window_days, int) or isinstance(window_days, bool)
            or not MIN_WINDOW_DAYS <= window_days <= MAX_WINDOW_DAYS):
        raise ValueError(f"window_days must be an integer from {MIN_WINDOW_DAYS} to {MAX_WINDOW_DAYS}")


# ----------------------------------------------------------------------------- funding conditions

FUNDING_FORMULAS = {
    "spread_to_policy_bp": ("overnight benchmark minus policy anchor on the latest observation date both legs "
                            "share, in basis points; no forward fill"),
    "corridor_position": ("(overnight minus floor) / (ceiling minus floor) on a date all three share; null unless "
                          "floor < ceiling; below 0 means under the floor, above 1 over the ceiling"),
    "dispersion_width_bp": "75th percentile minus 25th percentile of the benchmark's own transactions on the same date",
    "percentile": PERCENTILE_FORMULA,
}


def parse_markets(value: str | None, registry) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 200:
        raise ValueError("markets must be a comma list of market ids of at most 200 characters")
    identifiers = [item.strip().upper() for item in value.split(",") if item.strip()]
    if not identifiers or len(identifiers) > 20:
        raise ValueError("markets must name between 1 and 20 market ids")
    known = {pack.market_id for pack in registry.list()}
    unknown = [item for item in identifiers if item not in known]
    if unknown:
        raise ValueError("unknown market id(s): " + ", ".join(unknown))
    return list(dict.fromkeys(identifiers))


def funding_conditions(*, markets: str | None = None, window_days: int = DEFAULT_WINDOW_DAYS,
                       repository=None, registry=None, now=None) -> dict[str, Any]:
    _check_window(window_days)
    registry_ = registry or default_registry()
    selected = parse_markets(markets, registry_)
    if repository is None and registry is None and now is None:
        return _cached(("funding", tuple(selected or ()), window_days), lambda: _funding(
            selected, window_days, get_repository(), registry_, _cutoff(None)))
    return _funding(selected, window_days, repository or get_repository(), registry_, _cutoff(now))


def _pick(pack, rows, candidates) -> tuple[Any, list]:
    declared = [identifier for identifier in candidates
                if identifier in pack.instrument_map and _is_public(pack, pack.instrument_map[identifier])]
    for identifier in declared:
        if rows.get(identifier):
            return pack.instrument_map[identifier], rows[identifier]
    if declared:
        return pack.instrument_map[declared[0]], []
    return None, []


def _funding(selected, window_days, repository, registry, cutoff) -> dict[str, Any]:
    present = {pack.market_id for pack in registry.list()}
    market_ids = selected if selected is not None else [m for m in FUNDING_LEGS if m in present]
    results, metrics_all, asofs = [], [], []
    floor = _day_start(cutoff.date() - timedelta(days=window_days + 60))
    for market_id in market_ids:
        pack = registry.get(market_id)
        legs = FUNDING_LEGS.get(market_id, {})
        identifiers = [identifier for candidates in legs.values() for identifier in candidates
                       if identifier in pack.instrument_map]
        try:
            rows = _rows(repository, pack, cutoff, identifiers, event_from=floor)
        except Exception:  # noqa: BLE001: one market's read fault never hides the others
            LOGGER.exception("funding conditions read failed for %s", market_id)
            results.append({"market_id": market_id, "currency": pack.currency, "display_name": pack.display_name,
                            "status": "unavailable", "known_gaps": ["Canonical observations could not be read."]})
            continue
        picked = {role: _pick(pack, rows, legs.get(role, ())) for role in
                  ("overnight", "anchor", "floor", "ceiling", "volume", "p25", "p75",
                   "transactions", "active_banks", "top5_share")}
        metrics = {role: (_metric_block(pack, spec, values, cutoff) if spec is not None else None)
                   for role, (spec, values) in picked.items()}
        metrics_all.extend(metric for metric in metrics.values() if metric)
        gaps = []
        if market_id in FUNDING_NOTES:
            gaps.append(FUNDING_NOTES[market_id])
        overnight_rows, anchor_rows = picked["overnight"][1], picked["anchor"][1]
        spread_block = {"value_bp": None, "asof": None, "status": "unavailable", "legs": [
            spec.instrument_id for spec in (picked["overnight"][0], picked["anchor"][0]) if spec is not None],
            "percentile": None, "reason": None}
        if picked["overnight"][0] is None:
            gaps.append("No redistributable overnight benchmark is declared for this market.")
            spread_block["reason"] = "No public overnight benchmark."
        elif picked["anchor"][0] is None:
            spread_block["reason"] = "No public policy rate anchor is declared."
        elif not overnight_rows or not anchor_rows:
            spread_block["reason"] = "A leg has no stored observation inside the read window."
        else:
            points = _aligned([(overnight_rows, 1), (anchor_rows, -1)])
            if not points:
                spread_block.update(status="unaligned", reason="The legs share no observation date; nothing is filled.")
            else:
                context = regime(points, window_days)
                spread_block.update(
                    value_bp=_round(points[-1][1], 4), asof=points[-1][0].isoformat(), status="dated",
                    percentile={key: context[key] for key in ("status", "percentile", "robust_z", "window_days",
                                                              "window", "minimum_observations", "median_bp")})
        corridor = {"position": None, "width_bp": None, "asof": None, "status": "unavailable", "reason": None}
        if picked["floor"][0] is None or picked["ceiling"][0] is None:
            corridor["reason"] = "Floor or ceiling is not declared as a public instrument."
        elif overnight_rows and picked["floor"][1] and picked["ceiling"][1]:
            maps = [{row.event_time.date(): float(row.value) for row in values}
                    for values in (overnight_rows, picked["floor"][1], picked["ceiling"][1])]
            shared = sorted(set(maps[0]) & set(maps[1]) & set(maps[2]))
            if not shared:
                corridor.update(status="unaligned", reason="No date carries the benchmark, floor and ceiling together.")
            else:
                day = shared[-1]
                rate, low, high = (values[day] for values in maps)
                width = high - low
                corridor.update(asof=day.isoformat(), width_bp=_round(width, 4))
                if width > 0:
                    corridor.update(position=_round((rate - low) / width, 4), status="dated")
                else:
                    corridor.update(status="undefined", reason="Floor is not below ceiling on that date.")
        else:
            corridor["reason"] = "A corridor leg has no stored observation inside the read window."
        dispersion = None
        if picked["p25"][1] and picked["p75"][1]:
            points = _aligned([(picked["p75"][1], 1), (picked["p25"][1], -1)])
            if points:
                dispersion = {"p25": metrics["p25"], "p75": metrics["p75"], "width_bp": _round(points[-1][1], 4),
                              "asof": points[-1][0].isoformat()}
        elif picked["overnight"][0] is not None:
            gaps.append("No published distribution percentiles are stored for this benchmark.")
        overnight_metric, anchor_metric = metrics["overnight"], metrics["anchor"]
        if overnight_metric is None or overnight_metric["value"] is None:
            status = "unavailable"
        elif anchor_metric is None or anchor_metric["value"] is None:
            status = "partial"
        elif overnight_metric["status"] in _CURRENT and anchor_metric["status"] in _CURRENT:
            status = "ok"
        else:
            status = "stale"
        if overnight_metric and overnight_metric.get("asof"):
            asofs.append(overnight_metric["asof"])
        results.append({
            "market_id": market_id, "currency": pack.currency, "display_name": pack.display_name, "status": status,
            "policy_regime": pack.policy_regime.value,
            "overnight": overnight_metric, "policy_anchor": anchor_metric,
            "policy_floor": metrics["floor"], "policy_ceiling": metrics["ceiling"],
            "spread_to_policy": spread_block, "corridor": corridor, "volume": metrics["volume"],
            "dispersion": dispersion,
            "participation": ({key: metrics[key] for key in ("transactions", "active_banks", "top5_share")}
                              if any(metrics[key] for key in ("transactions", "active_banks", "top5_share")) else None),
            "known_gaps": gaps,
        })
    statuses = {item["status"] for item in results}
    overall = ("unavailable" if not results or statuses == {"unavailable"} else
               "ok" if statuses == {"ok"} else "partial")
    return _envelope(
        FUNDING_SCHEMA, cutoff, overall, asof=max(asofs) if asofs else None, window_days=window_days,
        formulas=FUNDING_FORMULAS, markets=results,
        reading_rule="Rows are shown side by side; raw levels in different currencies are not ranked.",
        provenance=provenance(metrics_all), caveats=CAVEATS,
    )
