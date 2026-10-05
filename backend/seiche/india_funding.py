"""India funding and curve context over dated canonical observations.

This is a descriptive desk, not a calibrated forecasting engine. Reference
benchmarks, primary-auction references and fixed-tenor curves remain distinct.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from seiche.domain.observation import Observation, RedistributionStatus
from seiche.markets.india_inr.pack import PACK, BENCHMARK_TENORS, CURVE_TENORS
from seiche.repository import get_repository

SCHEMA = "seiche.india-funding-curve.v1"
SPREADS = ((2, 5), (2, 10), (5, 10), (10, 30))
CURRENT = {"FRESH", "AGING"}
CAVEATS = [
    "Reference context only; IN-INR is not promoted to validated historical or predictive support.",
    "RBI net injection is positive for injection and negative for absorption. It is not the same series as net durable liquidity.",
    "Benchmark G-Sec yields retain the security identity and approximate tenor; they are not constant-maturity or zero-coupon yields.",
    "CD/CP ranges cover issuance during the reported fortnight, not a three-month benchmark or executable quote.",
    "Treasury-bill references are weekly primary-auction yields; no daily secondary-market history is inferred.",
    "Concurrent absorption and curve movement support a possible mechanism, not a causal finding or prediction.",
    "Missing operation amounts are unknown, not zero. Missing tenors are not interpolated or extrapolated.",
    "Monthly SGL maturity yields are a separate RBI report, not daily benchmark yields or a daily trading signal. Archive capture does not establish historical first-seen vintages.",
    "Government-bond auction cut-off yields are primary-issuance references. The 40Y auction reference is not a daily secondary-market observation.",
]


def eligible_rows(rows, cutoff):
    """Recheck source identity, row rights and all three clocks at the boundary."""
    result = []
    for row in rows:
        spec = PACK.instrument_map.get(row.instrument_id)
        if spec is None or row.market_id != "IN-INR" or not row.usable:
            continue
        adapter = PACK.adapter_map[spec.source_adapter_id]
        if (row.source != spec.source_adapter_id or row.semantic_role != spec.semantic_role
                or row.canonical_unit != spec.canonical_unit
                or row.currency != PACK.currency or row.monetary_area_id != PACK.monetary_area_id
                or row.jurisdiction_codes != PACK.jurisdiction_codes
                or row.rate_compounding != spec.rate_compounding or row.day_count != spec.day_count
                or row.connector_classification != adapter.classification
                or row.redistribution_status is not RedistributionStatus.ALLOWED
                or adapter.redistribution_status is not RedistributionStatus.ALLOWED
                or row.knowledge_time > cutoff or row.event_time > cutoff
                or row.source_publication_time is not None and row.source_publication_time > cutoff):
            continue
        result.append(row)
    return result


def classify_move(short_change_bp: Decimal, long_change_bp: Decimal, *, tolerance_bp=Decimal("0.1")) -> str:
    """Classify two leg changes; mixed and one-leg moves remain explicit."""
    if not all(x.is_finite() for x in (short_change_bp, long_change_bp, tolerance_bp)) or tolerance_bp < 0:
        raise ValueError("finite changes and a non-negative tolerance are required")
    delta = long_change_bp - short_change_bp
    if abs(short_change_bp) <= tolerance_bp and abs(long_change_bp) <= tolerance_bp:
        return "unchanged"
    if abs(delta) <= tolerance_bp:
        return "parallel_selloff" if short_change_bp > 0 else "parallel_rally"
    shape = "steepening" if delta > 0 else "flattening"
    if short_change_bp > tolerance_bp and long_change_bp > tolerance_bp:
        return "bear_" + shape
    if short_change_bp < -tolerance_bp and long_change_bp < -tolerance_bp:
        return "bull_" + shape
    if abs(short_change_bp) <= tolerance_bp or abs(long_change_bp) <= tolerance_bp:
        return "one_leg_" + shape
    return "twist_" + shape


def _identity(row):
    return row.revision_id.split("@capture-", 1)[0] if row.revision_id.startswith("bond:") else None


def _spread(short: int, long: int, grouped, metrics, *, history: bool, monthly: bool = False):
    prefix = "IN.RBI.SGL_MONTHLY" if monthly else "IN.RBI.GSEC_BENCHMARK"
    short_id, long_id = (f"{prefix}_{t}Y" for t in (short, long))
    out = {"id": f"{short}s{long}s", "short_tenor_years": short, "long_tenor_years": long,
           "curve_kind": "monthly_sgl_ytm" if monthly else "benchmark_ytm", "formula": "long yield minus short yield, in basis points",
           "status": "unavailable", "asof": None, "previous_asof": None, "value_bp": None,
           "change_bp": None, "short_change_bp": None, "long_change_bp": None,
           "movement": "unavailable", "reason": "Both dated benchmark tenors are required.",
           "sources": []}
    if history:
        out["history"] = []
    left = {r.event_time: r for r in grouped.get(short_id, [])}
    right = {r.event_time: r for r in grouped.get(long_id, [])}
    common = sorted(left.keys() & right.keys())
    if not common:
        return out
    day = common[-1]
    # Do not quietly carry a stale leg into today's curve.
    if max(left) != day or max(right) != day:
        out.update(status="unaligned", reason="The latest tenor dates differ; no carried-forward curve is constructed.")
        return out
    a, b = left[day], right[day]
    spread = b.value - a.value
    out.update(asof=day.date().isoformat(), value_bp=float(spread), status="available", reason=None,
               sources=[{"instrument_id": r.instrument_id, "event_time": r.event_time.isoformat(),
                         "knowledge_time": r.knowledge_time.isoformat(), "evidence_hash": r.evidence_hash,
                         "security": _identity(r), "source_url": "https://data.rbi.org.in/" if monthly else "https://www.rbi.org.in/"} for r in (a, b)])
    if history:
        out["history"] = [[d.date().isoformat(), float(right[d].value - left[d].value)] for d in common[-90:]]
    if not monthly and any(metrics.get(i, {}).get("status") not in CURRENT for i in (short_id, long_id)):
        out.update(status="stale", reason="A curve leg is stale or has unknown freshness; current movement is withheld.")
        return out
    if len(common) < 2:
        out.update(reason="A second common observation is needed to classify movement.")
        return out
    previous = common[-2]
    if not monthly and not all(_identity(current) is not None and _identity(current) == _identity(old)
               for current, old in ((a, left[previous]), (b, right[previous]))):
        out.update(status="benchmark_roll", reason="A security changed or its identity is unavailable; yield changes are withheld.")
        return out
    da, db = a.value - left[previous].value, b.value - right[previous].value
    out.update(previous_asof=previous.date().isoformat(), short_change_bp=float(da), long_change_bp=float(db),
               change_bp=float(db - da), movement=classify_move(da, db),
               comparison="previous common month-end observation; historical context only" if monthly else "previous common observation; not necessarily the previous trading day")
    if monthly:
        out["status"] = "historical_reference"
    return out


def _policy_comparison(grouped, metrics):
    out = {"status": "unavailable", "value_bp": None, "asof": None, "policy_asof": None,
           "interpretation": "A current overnight observation and a dated policy reference are required.", "sources": []}
    calls = grouped.get("IN.MARKET.CALL_WAR", [])
    if not calls or metrics.get("IN.MARKET.CALL_WAR", {}).get("status") not in CURRENT:
        return out
    call = calls[-1]
    candidates = [r for key in ("IN.RBI.POLICY_REPO_WEEKLY", "IN.RBI.POLICY_REPO_DECISION")
                  for r in grouped.get(key, []) if r.event_time <= call.event_time]
    if not candidates:
        return out
    policy = max(candidates, key=lambda r: r.event_time)
    # A weekly rate snapshot may be used as the stated reference for at most
    # two weeks. An old decision alone cannot prove today's unchanged policy.
    if (call.event_time - policy.event_time).days > 14:
        return out
    spread = call.value - policy.value
    out.update(status="dated_reference", value_bp=float(spread), asof=call.event_time.date().isoformat(),
               policy_asof=policy.event_time.date().isoformat(),
               interpretation=("Call money is below the dated repo reference; this alone does not establish a system-wide surplus."
                               if spread < 0 else "Call money is at or above the dated repo reference."),
               sources=[{"instrument_id": r.instrument_id, "evidence_hash": r.evidence_hash,
                         "knowledge_time": r.knowledge_time.isoformat()} for r in (call, policy)])
    return out


def _liquidity_change(grouped, metrics):
    key = "IN.RBI.SYSTEM_LIQUIDITY"
    rows = grouped.get(key, [])
    out = {"status": "unavailable", "asof": None, "previous_asof": None, "net_injection_crore": None,
           "net_absorption_crore": None, "change_in_absorption_crore": None, "direction": "unknown"}
    if not rows:
        return out
    current = rows[-1]
    out.update(status="available" if metrics.get(key, {}).get("status") in CURRENT else "stale",
               asof=current.event_time.date().isoformat(), net_injection_crore=float(current.value / 10),
               net_absorption_crore=float(-current.value / 10),
               direction="net_absorption" if current.value < 0 else "net_injection" if current.value > 0 else "balanced")
    if len(rows) >= 2 and out["status"] == "available":
        previous = rows[-2]
        out.update(previous_asof=previous.event_time.date().isoformat(),
                   change_in_absorption_crore=float((previous.value - current.value) / 10))
    return out


def build(rows, *, now: datetime, include_history: bool = True) -> dict:
    from seiche.markets.atlas import _instrument_rows, _metric

    if now.tzinfo is None:
        raise ValueError("India evaluation time must be timezone-aware")
    now = now.astimezone(UTC).replace(microsecond=0)
    grouped = _instrument_rows(eligible_rows(rows, now))
    metrics = {}
    for instrument in PACK.instruments:
        if instrument.source_adapter_id == "tenant_market_data":
            continue
        metric = _metric(PACK, instrument, grouped.get(instrument.instrument_id, []), cutoff=now)
        if not include_history:
            metric.pop("history", None)
        elif isinstance(metric.get("history"), list):
            metric["history"] = metric["history"][-90:]
        if instrument.canonical_unit.value == "local_currency_millions":
            # Display RBI's native crore unit; canonical store remains millions.
            metric["value"] = metric["canonical_value"] / 10 if metric["canonical_value"] is not None else None
            metric["unit"] = "INR crore"
            metric["change_unit"] = "INR crore"
            for key in ("change_1_observation", "change_5_observations", "change_20_observations", "change_vol_20_annualized"):
                if metric.get(key) is not None:
                    metric[key] /= 10
            metric["change_vol_unit"] = "INR crore/year^0.5"
            metric["formula"] = "RBI observation in INR crore; canonical INR millions / 10"
            if isinstance(metric.get("history"), list):
                metric["history"] = [[day, value / 10] for day, value in metric["history"]]
        if instrument.instrument_id == "IN.RBI.CRR":
            metric["value"] = metric["canonical_value"] / 100 if metric["canonical_value"] is not None else None
            metric["unit"] = "%"
            metric["change_unit"] = "bp"
            metric["change_vol_unit"] = "bp/year^0.5"
            if isinstance(metric.get("history"), list):
                metric["history"] = [[day, value / 100] for day, value in metric["history"]]
        if instrument.instrument_id.startswith("IN.RBI.GSEC_BENCHMARK_"):
            metric["security"] = _identity(grouped[instrument.instrument_id][-1]) if grouped.get(instrument.instrument_id) else None
        if instrument.instrument_id.startswith("IN.RBI.GSEC_AUCTION_") and grouped.get(instrument.instrument_id):
            identity = grouped[instrument.instrument_id][-1].revision_id.split("@capture-", 1)[0]
            metric["security"] = identity.split(":prid:", 1)[0].removeprefix("auction:")
            if ":prid:" in identity and identity.rsplit(":prid:", 1)[-1].isdigit():
                metric["source_url"] = "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=" + identity.rsplit(":prid:", 1)[-1]
        metrics[instrument.instrument_id] = metric
    spreads = [_spread(a, b, grouped, metrics, history=include_history) for a, b in SPREADS]
    monthly_spreads = [_spread(a, b, grouped, metrics, history=include_history, monthly=True) for a, b in SPREADS]
    liquidity = _liquidity_change(grouped, metrics)
    policy = _policy_comparison(grouped, metrics)
    main = next(x for x in spreads if x["id"] == "5s10s")
    mechanism = {"status": "insufficient_evidence", "causal_claim": False,
                 "explanation": "Matched curve changes and liquidity changes over the same dates are needed to assess the withdrawal hypothesis.",
                 "alternative_explanations": ["Bond supply and auction composition", "Policy and inflation expectations", "Duration demand and global yields"]}
    balances = {r.event_time.date().isoformat(): r for r in grouped.get("IN.RBI.SYSTEM_LIQUIDITY", [])}
    if (main["movement"] == "bear_flattening"
            and main["asof"] in balances and main["previous_asof"] in balances):
        previous_balance, current_balance = balances[main["previous_asof"]], balances[main["asof"]]
        change = (previous_balance.value - current_balance.value) / 10
        mechanism["matched_liquidity_window"] = {
            "from": main["previous_asof"], "through": main["asof"],
            "change_in_absorption_crore": float(change),
            "evidence_hashes": [previous_balance.evidence_hash, current_balance.evidence_hash],
        }
        if change > 0:
            mechanism.update(status="consistent_with", explanation="Net RBI absorption increased while the intermediate benchmark yield rose more than the long benchmark. This is consistent with liquidity withdrawal contributing to repricing; it does not identify the cause.")
        else:
            mechanism.update(status="not_confirmed", explanation="The curve bear-flattened, but the matched RBI balance did not show increasing net absorption.")
    latest_dates = [m["asof"] for m in metrics.values() if m["asof"]]
    available = sum(m["availability"] == "AVAILABLE" for m in metrics.values())
    current = sum(m["status"] in CURRENT for m in metrics.values())
    headline = ("India — " + main["movement"].replace("_", " ") if main["movement"] != "unavailable"
                else "India funding and sovereign benchmarks — movement not yet established")
    groups = {
        "policy": ["IN.RBI.POLICY_REPO_WEEKLY", "IN.RBI.POLICY_REPO_DECISION", "IN.RBI.SDF", "IN.RBI.SDF_DECISION", "IN.RBI.MSF", "IN.RBI.MSF_DECISION", "IN.RBI.CRR"],
        "money_market": ["IN.MARKET.CALL_WAR", "IN.RBI.TREPS_WAR", "IN.RBI.MARKET_REPO_WAR", *[f"IN.RBI.TBILL_{d}D_WEEKLY" for d in (91, 182, 364)],
                         *[f"IN.RBI.{k}_RATE_{b}" for k in ("CD", "CP") for b in ("LOW", "HIGH")]],
        "liquidity": [key for key, m in metrics.items() if m["unit"] == "INR crore"],
        "bond_auctions": [f"IN.RBI.GSEC_AUCTION_{t}Y" for t in CURVE_TENORS],
    }
    return {"schema": SCHEMA, "market_id": "IN-INR", "generated_at": now.isoformat(),
            "asof": max(latest_dates, default=None), "status": "partial" if available else "unavailable",
            "context_only": True, "historically_validated": False, "headline": headline,
            "coverage": {"available": available, "current": current, "declared": len(metrics), "complete": False},
            "sections": [{"id": key, "metrics": [metrics[i] for i in ids]} for key, ids in groups.items()],
            "policy_comparison": policy, "liquidity_summary": liquidity,
            "curve": {"kind": "benchmark_ytm", "nodes": [{"tenor_years": t, **metrics[f"IN.RBI.GSEC_BENCHMARK_{t}Y"]} for t in BENCHMARK_TENORS],
                      "requested_tenors_years": list(CURVE_TENORS), "interpolated": False,
                      "fixed_tenor_status": "unavailable", "fixed_tenor_reason": "A daily fixed-tenor curve is unavailable. The separate monthly maturity view provides RBI's dated 1–30Y report."},
            "monthly_curve": {"kind": "monthly_sgl_ytm", "cadence": "P1M", "historical_context_only": True,
                              "nodes": [{"tenor_years": t, **metrics[f"IN.RBI.SGL_MONTHLY_{t}Y"]} for t in CURVE_TENORS],
                              "spreads": monthly_spreads,
                              "methodology": "RBI DBIE report 217: month-end SGL yields for labelled maturities. No mixing with daily benchmarks or interpolation by Seiche. The report stops at 30 years."},
            "spreads": spreads, "mechanism": mechanism, "caveats": CAVEATS,
            "missing_series": [{"id": m["id"], "status": m["availability"]} for m in metrics.values() if m["availability"] != "AVAILABLE"],
            "sources": [{"title": "RBI money-market operations", "url": "https://www.rbi.org.in/Scripts/BS_ViewMMO.aspx"},
                        {"title": "RBI government-securities benchmarks", "url": "https://www.rbi.org.in/"},
                        {"title": "RBI DBIE monthly maturity report via RBI Innovation Hub", "url": "https://github.com/Reserve-Bank-Innovation-Hub/dbie.rbihub.in"},
                        {"title": "RBI weekly ratios and rates", "url": "https://www.rbi.org.in/Scripts/BS_NSDPDisplay.aspx?param=4"},
                        {"title": "RBI monetary-policy decisions", "url": "https://www.rbi.org.in/Scripts/annualpolicy.aspx"},
                        {"title": "RBI weekly liquidity, CD and CP tables", "url": "https://www.rbi.org.in/Scripts/BS_ViewWssExtract.aspx"}]}


def read(*, now: datetime | None = None, include_history: bool = False) -> dict:
    now = now or datetime.now(UTC)
    failure = False
    try:
        rows = get_repository().load_observations_as_of("IN-INR", now, event_time=now,
            event_time_from=now - timedelta(days=400), instrument_ids=[i.instrument_id for i in PACK.instruments
                if PACK.adapter_map[i.source_adapter_id].redistribution_status is RedistributionStatus.ALLOWED])
    except Exception:
        logging.getLogger(__name__).exception("India funding canonical read unavailable")
        rows, failure = [], True
    result = build(rows, now=now, include_history=include_history)
    if failure:
        result["read_fault"] = "Canonical India observations could not be read."
    return result
