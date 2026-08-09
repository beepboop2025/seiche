"""Compatibility materializer from the legacy board to sealed US products.

This adapter is intentionally pack-local: it is allowed to understand the v1
payload. Neither the observation domain nor the universal kernel imports it.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from seiche.markets.base import CapabilityStatus
from seiche.markets.us_usd.pack import PACK
from seiche.repository import get_repository


_FAULT_PREFIXES = ("fred", "nyfed", "ofr", "fiscaldata", "tga", "auctions")


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _event_cutoff(snapshot: dict) -> datetime:
    mapped = {item.mnemonic for item in PACK.instruments}
    dates: list[datetime] = []
    for provenance in snapshot.get("provenance") or []:
        if provenance.get("mnemonic") not in mapped or not provenance.get("asof"):
            continue
        try:
            dates.append(datetime.fromisoformat(provenance["asof"]).replace(tzinfo=UTC))
        except (TypeError, ValueError):
            continue
    generated = datetime.fromisoformat(snapshot["generated_at"].replace("Z", "+00:00"))
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=UTC)
    return min(max(dates), generated) if dates else generated


def _market_faults(snapshot: dict) -> list[dict]:
    faults = []
    for fault in snapshot.get("faults") or []:
        source = str(fault.get("source") or "").lower()
        if any(source == prefix or source.startswith(f"{prefix}_") for prefix in _FAULT_PREFIXES):
            faults.append({**fault, "market_id": PACK.market_id})
    return faults


def _stale_inputs(snapshot: dict) -> list[dict]:
    mapped = {item.mnemonic for item in PACK.instruments}
    return [
        {
            "instrument": item.get("mnemonic"),
            "staleness": item.get("staleness"),
            "asof": item.get("asof"),
            "fetched_at": item.get("fetched_at"),
        }
        for item in snapshot.get("provenance") or []
        if item.get("mnemonic") in mapped
        and item.get("staleness") not in {None, "fresh"}
    ]


def _capability_payload() -> tuple[dict[str, str], list[dict[str, str | None]]]:
    matrix = {
        capability.capability_id: capability.status.value
        for capability in PACK.capabilities
    }
    missing = [
        {
            "capability": capability.capability_id,
            "status": capability.status.value,
            "reason": capability.reason,
        }
        for capability in PACK.capabilities
        if capability.status is not CapabilityStatus.READY
    ]
    return matrix, missing


def build_products(snapshot: dict) -> tuple[dict, dict]:
    engines = snapshot.get("engines") or {}
    deep = snapshot.get("deep") or {}
    composite = engines.get("composite") or {}
    tell = deep.get("tell") or {}
    stack = deep.get("stacker") or {}
    calendar = snapshot.get("calendar") or {}
    capabilities, missing = _capability_payload()
    event_cutoff = _event_cutoff(snapshot).isoformat()
    knowledge_cutoff = snapshot.get("generated_at")
    market_faults = _market_faults(snapshot)
    stale = _stale_inputs(snapshot)
    status = "READY" if composite.get("value") is not None else "UNAVAILABLE"

    members: dict[str, Any] = dict(stack.get("members_now") or {})
    navigator = snapshot.get("navigator") or {}
    if navigator.get("ok") and navigator.get("p_event_5bd") is not None:
        members["navigator"] = navigator["p_event_5bd"]
    court = deep.get("modelcourt") or {}
    court_probability = (court.get("ensemble") or {}).get("p")
    if court.get("ok") and court_probability is not None:
        members["modelcourt"] = court_probability

    common = {
        "market_id": PACK.market_id,
        "monetary_area_id": PACK.monetary_area_id,
        "jurisdiction_codes": list(PACK.jurisdiction_codes),
        "currency": PACK.currency,
        "policy_regime": PACK.policy_regime.value,
        "support_status": PACK.support_status.value,
        "calibration_id": PACK.calibration_id,
        "data_coverage": {
            "component_coverage_pct": composite.get("coverage_pct"),
            "canonical_observations": get_repository().canonical_coverage(PACK.market_id),
        },
        "capabilities": capabilities,
        "missing_capabilities": missing,
        "evidence_eligibility": {
            "eligible": False,
            "mode": "legacy_final_vintage_bridge",
            "reason": (
                "v1 series captures do not preserve a source publication and "
                "knowledge clock for every historical row"
            ),
        },
        "event_cutoff": event_cutoff,
        "knowledge_cutoff": knowledge_cutoff,
        "faults": market_faults,
        "stale_inputs": stale,
    }
    gauge = {
        "schema": "seiche.local-gauge.v2",
        "product": "LOCAL_SEICHE_GAUGE",
        "status": status,
        **common,
        "reading": {
            "index": composite.get("value"),
            "regime": composite.get("regime"),
            "tell": tell.get("tell"),
            "p_event_5bd": stack.get("p_now") if stack.get("ok") else None,
            "p_event_5bd_dispersion": (
                stack.get("dispersion_now") if stack.get("ok") else None
            ),
            "p_event_5bd_members": members or None,
        },
        "components": composite.get("decomposition") or [],
        "calendar": {
            "calendar_id": PACK.settlement_calendar.calendar_id,
            "next_turn": calendar.get("next_turn"),
            "crunch_windows": (calendar.get("crunch_windows") or [])[:3],
        },
        "notes": (
            "compatibility materialization of the US v1 gauge; canonical "
            "point-in-time history remains forward-only"
        ),
    }
    overview = {
        "schema": "seiche.market-overview.v2",
        "product": "LOCAL_MARKET_OVERVIEW",
        "status": status,
        **common,
        "gauge": gauge["reading"],
        "headline": snapshot.get("headline") or {},
        "calendar": {
            "calendar_id": PACK.settlement_calendar.calendar_id,
            **calendar,
        },
        "data_quality": snapshot.get("data_quality") or {},
    }
    return _json_safe(overview), _json_safe(gauge)


def seal_legacy_snapshot(snapshot: dict) -> dict[str, str]:
    """Materialize US products after a collector/engine cycle, never in API."""

    overview, gauge = build_products(snapshot)
    repository = get_repository()
    event_cutoff = overview["event_cutoff"]
    knowledge_cutoff = overview["knowledge_cutoff"]
    ids = {
        "overview": repository.seal_market_snapshot(
            market_id=PACK.market_id,
            product="overview",
            event_cutoff=event_cutoff,
            knowledge_cutoff=knowledge_cutoff,
            calibration_id=PACK.calibration_id,
            evidence_eligible=False,
            payload=overview,
        ),
        "gauge": repository.seal_market_snapshot(
            market_id=PACK.market_id,
            product="gauge",
            event_cutoff=event_cutoff,
            knowledge_cutoff=knowledge_cutoff,
            calibration_id=PACK.calibration_id,
            evidence_eligible=False,
            payload=gauge,
        ),
    }
    for product, snapshot_id in ids.items():
        payload = overview if product == "overview" else gauge
        repository.append_forward_record(
            snapshot_id=snapshot_id,
            market_id=PACK.market_id,
            product=product,
            event_cutoff=event_cutoff,
            knowledge_cutoff=knowledge_cutoff,
            calibration_id=PACK.calibration_id,
            payload=payload,
        )
    return ids
