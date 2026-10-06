"""Country funding desks over existing canonical observations only."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging

from seiche.domain.observation import RedistributionStatus, SemanticRole
from seiche.markets.funding_reference import BIS_AUTHORITIES, COUNTRIES, COUNTRY_MAP, CURVES, FRED_SERIES, SOURCE_REFERENCES
from seiche.markets.registry import default_registry
from seiche.repository import get_repository

SCHEMA = "seiche.country-funding-curve.v1"
CATALOG_SCHEMA = "seiche.country-funding-catalog.v1"
SPREAD_PAIRS = ((2, 5), (2, 10), (5, 10), (10, 20), (10, 30))
POLICY_ROLES = {SemanticRole.POLICY_TARGET, SemanticRole.POLICY_FLOOR,
                SemanticRole.POLICY_CEILING, SemanticRole.CENTRAL_BANK_FACILITY_RATE,
                SemanticRole.RESERVE_REQUIREMENT_RATIO}
FUNDING_ROLES = {SemanticRole.UNSECURED_OVERNIGHT, SemanticRole.SECURED_OVERNIGHT,
                 SemanticRole.TERM_1W, SemanticRole.TERM_1M, SemanticRole.TERM_3M,
                 SemanticRole.TBILL_3M, SemanticRole.TBILL_6M, SemanticRole.TBILL_12M,
                 SemanticRole.CP_3M, SemanticRole.CD_3M, SemanticRole.TERM_FUNDING_RATE}
CAVEATS = [
    "Reference observations, not a calibrated country-risk score, executable quote or trading recommendation.",
    "Countries sharing a currency can share monetary-policy references while issuing different sovereign debt.",
    "Compare only matching observation dates, frequencies and yield conventions. Missing tenors are not interpolated.",
    "Publication time, observation time and first capture are distinct. Unknown publication times remain unknown.",
    "Monthly averages and historical references are not current daily market quotes.",
    "A successful collection does not establish complete coverage or historical first-seen vintages.",
    "Curve movement describes the stated observations; it does not identify a cause or predict a return.",
]


def country_code(value):
    if not isinstance(value, str):
        raise ValueError("country must be a supported two-letter code")
    code = value.upper()
    if code == "UK":
        code = "GB"
    if code not in COUNTRY_MAP:
        raise ValueError("unsupported country; use the country-funding catalog")
    return code


def catalog():
    return {"schema": CATALOG_SCHEMA, "status": "structural", "context_only": True,
            "countries": [{"code": c.code, "name": c.name, "market_id": c.market_id,
                           "funding_market_id": c.funding_market_id,
                           "monetary_policy_scope": "Euro area" if c.funding_market_id == "EA-EUR" else c.name,
                           "endpoint": "/api/v2/country-funding/" + c.code}
                          for c in COUNTRIES],
            "caveat": "This is a coverage catalog. Open each country for dated observations, restrictions and gaps."}


def event_window_start(market_id, cutoff):
    """Match date-based collection windows across website, REST and MCP."""
    days = 3650 if market_id == "TW-TWD" else 400
    return cutoff.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days)


def eligible_rows(pack, rows, cutoff):
    """Validate row identity, conventions, rights and all evidence clocks."""
    from seiche.markets.atlas import _instrument_rows
    candidates = []
    start = event_window_start(pack.market_id, cutoff)
    for row in rows:
        spec = pack.instrument_map.get(row.instrument_id)
        if spec is None or row.market_id != pack.market_id:
            continue
        adapter = pack.adapter_map[spec.source_adapter_id]
        if (row.source != spec.source_adapter_id or row.semantic_role != spec.semantic_role
                or row.canonical_unit != spec.canonical_unit or row.currency != pack.currency
                or row.monetary_area_id != pack.monetary_area_id
                or row.jurisdiction_codes != pack.jurisdiction_codes
                or row.rate_compounding != spec.rate_compounding or row.day_count != spec.day_count
                or row.connector_classification != adapter.classification
                or adapter.redistribution_status is not RedistributionStatus.ALLOWED
                or row.knowledge_time > cutoff or row.event_time > cutoff
                or row.event_time < start
                or row.source_publication_time is not None and row.source_publication_time > cutoff):
            continue
        candidates.append(row)
    # Resolve known revisions before filtering rights/quality. A withdrawn
    # revision must not resurrect an older usable value for the same date.
    return [row for values in _instrument_rows(candidates).values() for row in values
            if row.usable and row.redistribution_status is RedistributionStatus.ALLOWED]


def _projection(pack, rows, cutoff, include_history):
    from seiche.markets.atlas import _instrument_rows, _metric, _SOURCE_PUBLISHERS
    grouped = _instrument_rows(eligible_rows(pack, rows, cutoff))
    metrics = {}
    for spec in pack.instruments:
        if spec.source_adapter_id == "tenant_market_data":
            continue
        metric = _metric(pack, spec, grouped.get(spec.instrument_id, []), cutoff=cutoff)
        if include_history:
            metric["history"] = metric.get("history", [])[-90:]
        else:
            metric.pop("history", None)
        reference = SOURCE_REFERENCES.get(spec.source_adapter_id)
        if reference:
            metric["source_reference"] = dict(reference)
        else:
            metric["source_reference"] = {"publisher": _SOURCE_PUBLISHERS.get(spec.source_adapter_id, spec.source_adapter_id),
                                          "source_url": metric.get("source_url")}
        if spec.source_adapter_id == "bis_policy_reference":
            metric["source_reference"]["publisher"] = BIS_AUTHORITIES[pack.jurisdiction_codes[0]] + " via Bank for International Settlements"
        if metric["cadence"] == "P1M":
            metric["observation_period"] = metric["asof"][:7] if metric.get("asof") else None
            metric["event_date_basis"] = "calendar_month_end_label"
            metric["aggregation"] = "source_defined_monthly_reference"
        if spec.source_adapter_id == "oecd_fred_reference":
            series = next(key for key, (_, _, _, identifier) in FRED_SERIES.items() if identifier == spec.instrument_id)
            metric["source_url"] = "https://fred.stlouisfed.org/series/" + series
            metric["source_reference"]["source_url"] = metric["source_url"]
            metric["source_reference"]["accessed_at"] = metric.get("knowledge_time")
        if spec.source_adapter_id == "cbc_monthly_rates":
            metric["aggregation"] = "month_end" if "MONTH_END" in spec.instrument_id else "monthly_weighted_average"
        if spec.source_adapter_id == "ecb_sovereign_monthly":
            code = pack.jurisdiction_codes[0]
            metric["source_url"] = f"https://data.ecb.europa.eu/data/datasets/IRS/IRS.M.{code}.L.L40.CI.0000.EUR.N.Z"
            metric["observation_period"] = metric["asof"][:7] if metric.get("asof") else None
            metric["aggregation"] = "monthly_average"
            metric["source_reference"]["source_url"] = metric["source_url"]
        metrics[spec.instrument_id] = metric
    return grouped, metrics


def _spread(curve, short, long, grouped, metrics):
    from seiche.india_funding import classify_move
    identifiers = dict(curve.nodes)
    result = {"id": f"{short}s{long}s", "short_tenor_years": short, "long_tenor_years": long,
              "status": "unavailable", "asof": None, "previous_asof": None,
              "value_bp": None, "change_bp": None, "short_change_bp": None,
              "long_change_bp": None, "movement": "unavailable",
              "reason": "Two matching dated observations of each tenor are required."}
    if short not in identifiers or long not in identifiers:
        result["reason"] = "The source does not declare both tenors; no interpolation is used."
        return result
    left, right = (grouped.get(identifiers[t], []) for t in (short, long))
    if not left or not right:
        return result
    if left[-1].event_time != right[-1].event_time:
        return {**result, "status": "unaligned", "reason": "The latest source observation dates differ."}
    result.update(asof=left[-1].event_time.date().isoformat(),
                  value_bp=float(right[-1].value - left[-1].value),
                  status="dated_reference")
    if len(left) < 2 or len(right) < 2:
        return result
    if left[-2].event_time != right[-2].event_time:
        return {**result, "status": "unaligned", "reason": "Previous observation dates differ; no change is computed."}
    short_change, long_change = left[-1].value - left[-2].value, right[-1].value - right[-2].value
    latest_metrics = (metrics[identifiers[short]], metrics[identifiers[long]])
    current = all(m["status"] in {"FRESH", "AGING"} for m in latest_metrics)
    result.update(
        status="current_reference" if current and curve.cadence == "P1D" else "historical_reference",
        previous_asof=left[-2].event_time.date().isoformat(),
        change_bp=float(long_change - short_change), short_change_bp=float(short_change),
        long_change_bp=float(long_change), movement=classify_move(short_change, long_change),
        reason=(None if current and curve.cadence == "P1D" else
                "Change over the two stated observations only; current market freshness is not established."),
    )
    return result


def _germany_spread(code, observations, registry, cutoff, grouped):
    result = {"status": "unavailable", "asof": None, "value_bp": None,
              "definition": "National monthly ten-year reference minus Germany's matching ECB monthly reference.",
              "reason": "Matching national and German observations are required."}
    if code == "DE" or not any(c.country == code and c.adapter_id == "ecb_sovereign_monthly" for c in CURVES):
        return None
    from seiche.markets.atlas import _instrument_rows
    pack = registry.get("DE-EUR")
    german = _instrument_rows(eligible_rows(pack, observations.get("DE-EUR", ()), cutoff))
    identifier = code + ".ECB.LONG_TERM_10Y_MONTHLY"
    national_rows = grouped.get(identifier, [])
    german_rows = german.get("DE.ECB.LONG_TERM_10Y_MONTHLY", [])
    if not national_rows or not german_rows:
        return result
    row, national = german_rows[-1], national_rows[-1]
    if row.event_time != national.event_time:
        return {**result, "status": "unaligned", "reason": "Latest national and German reference months differ."}
    return {**result, "status": "historical_reference", "asof": national.event_time.date().isoformat(),
            "value_bp": float(national.value - row.value),
            "reason": "Monthly references; not a live tradable spread.",
            "germany_evidence_hash": row.evidence_hash, "national_evidence_hash": national.evidence_hash}


def build(country, observations_by_market, *, now, include_history=False, registry=None):
    code = country_code(country)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("country evaluation time must be timezone-aware")
    cutoff = now.astimezone(UTC).replace(microsecond=0)
    registry = registry or default_registry()
    profile = COUNTRY_MAP[code]
    funding_pack = registry.get(profile.funding_market_id)
    sovereign_pack = registry.get(profile.market_id)
    projections = {pack.market_id: _projection(pack, observations_by_market.get(pack.market_id, ()), cutoff, include_history)
                   for pack in {p.market_id: p for p in (funding_pack, sovereign_pack)}.values()}
    funding = projections[funding_pack.market_id][1]
    grouped, sovereign = projections[sovereign_pack.market_id]
    sections = []
    for section, roles in (("policy", POLICY_ROLES), ("money_market", FUNDING_ROLES)):
        sections.append({"id": section, "scope": "Euro area" if funding_pack.market_id == "EA-EUR" else profile.name,
                         "metrics": [funding[i.instrument_id] for i in funding_pack.instruments
                                     if i.semantic_role in roles and i.instrument_id in funding]})
    sections.append({"id": "liquidity", "scope": "Euro area" if funding_pack.market_id == "EA-EUR" else profile.name,
                     "metrics": [funding[i.instrument_id] for i in funding_pack.instruments
                                 if i.semantic_role not in POLICY_ROLES | FUNDING_ROLES | {SemanticRole.SOVEREIGN_YIELD}
                                 and i.instrument_id in funding]})
    curves = []
    for curve in CURVES:
        if curve.country != code:
            continue
        nodes = [{**sovereign[identifier], "tenor_years": tenor} for tenor, identifier in curve.nodes]
        curves.append({"id": curve.adapter_id, "kind": curve.kind, "cadence": curve.cadence,
                       "methodology": curve.method, "nodes": nodes, "complete_curve": False,
                       "source": dict(SOURCE_REFERENCES[curve.adapter_id]),
                       "spreads": [_spread(curve, a, b, grouped, sovereign) for a, b in SPREAD_PAIRS]})
    all_metrics = [m for s in sections for m in s["metrics"]] + [n for c in curves for n in c["nodes"]]
    available = sum(m["availability"] == "AVAILABLE" for m in all_metrics)
    dates = [m["asof"] for m in all_metrics if m.get("asof")]
    gaps = []
    if not curves:
        gaps.append("No public sovereign-yield series has been admitted for this country yet.")
    if not sections[1]["metrics"] or all(m["value"] is None for m in sections[1]["metrics"]):
        gaps.append("No publishable local money-market observation is available.")
    if not sections[2]["metrics"] or all(m["value"] is None for m in sections[2]["metrics"]):
        gaps.append("Central-bank liquidity or reserve observations are incomplete.")
    if any(c["kind"] == "monthly_long_term_reference" for c in curves):
        gaps.append("Only a monthly ten-year reference is declared; a daily multi-tenor sovereign curve is unavailable.")
    sources = {}
    for metric in all_metrics:
        reference = metric.get("source_reference") or {
            "publisher": metric.get("publisher"), "source_url": metric.get("source_url")}
        if reference.get("source_url"):
            sources[reference["source_url"]] = reference
    return {"schema": SCHEMA, "country": code, "country_name": profile.name,
            "market_id": profile.market_id, "funding_market_id": profile.funding_market_id,
            "monetary_area_id": funding_pack.monetary_area_id, "currency": funding_pack.currency,
            "generated_at": cutoff.isoformat(), "asof": max(dates, default=None),
            "status": "partial" if available else "unavailable", "context_only": True,
            "historically_validated": False, "headline": profile.name + " funding and sovereign references",
            "coverage": {"available": available, "current": sum(m["status"] in {"FRESH", "AGING"} for m in all_metrics),
                         "declared": len(all_metrics), "complete": False},
            "sections": sections, "curves": curves,
            "sovereign_spread_to_germany": _germany_spread(code, observations_by_market, registry, cutoff, grouped),
            "known_gaps": gaps, "caveats": CAVEATS, "sources": list(sources.values())}


def build_collection(packs, observations_by_market, *, now, include_history=True):
    from seiche.markets.registry import MarketRegistry
    registry = MarketRegistry(tuple(packs))
    present = {pack.market_id for pack in registry.list()}
    return {**catalog(), "generated_at": now.isoformat(), "desks": [
        build(c.code, observations_by_market, now=now, include_history=include_history, registry=registry)
        for c in COUNTRIES if {c.market_id, c.funding_market_id, "DE-EUR" if c.funding_market_id == "EA-EUR" else c.market_id} <= present
    ]}


def read(country=None, *, now=None, include_history=False):
    if country is None:
        return catalog()
    code = country_code(country)
    cutoff = now or datetime.now(UTC)
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("country evaluation time must be timezone-aware")
    profile = COUNTRY_MAP[code]
    registry = default_registry()
    market_ids = {profile.market_id, profile.funding_market_id}
    if profile.funding_market_id == "EA-EUR":
        market_ids.add("DE-EUR")
    observations, faults = {}, []
    repository = get_repository()
    for market_id in sorted(market_ids):
        pack = registry.get(market_id)
        identifiers = [i.instrument_id for i in pack.instruments
                       if pack.adapter_map[i.source_adapter_id].redistribution_status is RedistributionStatus.ALLOWED]
        try:
            observations[market_id] = repository.load_observations_as_of(
                market_id, cutoff, event_time=cutoff,
                event_time_from=event_window_start(market_id, cutoff),
                instrument_ids=identifiers,
            ) if identifiers else []
        except Exception:
            logging.getLogger(__name__).exception("Country funding canonical read unavailable for %s", market_id)
            observations[market_id] = []
            faults.append(market_id)
    result = build(code, observations, now=cutoff, include_history=include_history, registry=registry)
    if faults:
        result["read_faults"] = [{"market_id": market, "reason": "Canonical observations could not be read."} for market in faults]
    return result
