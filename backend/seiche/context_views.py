"""Compact cross-market views shared by REST and delivery clients.

The full Oil x Funding and Estuary engines carry chart histories intended for
the browser. Bots and agents need the current evidence, scenario boundary, and
caveats instead. These adapters keep that smaller machine contract in one place
so each delivery surface cannot quietly reinterpret the engines.
"""

from __future__ import annotations

from typing import Any


def _object(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _rows(value: Any, limit: int) -> list:
    return value[:limit] if isinstance(value, list) else []


def _engine(snapshot: dict, name: str, schema: str) -> tuple[dict | None, dict]:
    envelope = {
        "schema": schema,
        "generated_at": snapshot.get("generated_at"),
        "context_only": True,
    }
    engine = _object(snapshot.get("engines")).get(name)
    if not isinstance(engine, dict):
        return None, {
            **envelope,
            "ok": False,
            "reason": f"{name} is unavailable in this snapshot",
        }
    if engine.get("ok") is not True:
        return None, {
            **envelope,
            "ok": False,
            "reason": engine.get("reason") or f"{name} did not produce a reading",
        }
    return engine, envelope


def oil_funding(snapshot: dict) -> dict[str, Any]:
    """Chartless Oil x Funding contract with observed and scenario layers."""

    engine, out = _engine(snapshot, "oilfunding", "seiche.oil-funding.v1")
    if engine is None:
        return out

    live = _object(engine.get("live"))
    scatter = _object(_object(engine.get("charts")).get("scatter"))
    structure_engine = engine.get("market_structure")
    if isinstance(structure_engine, dict) and structure_engine:
        market_structure = {
            "ok": True,
            "evidence_mode": structure_engine.get("evidence_mode"),
            "cushing": {
                **_object(structure_engine.get("cushing")),
                "live": _object(live.get("cushing")),
            },
            "brent_wti_spread": _object(live.get("brent_wti_spread")),
            "benchmark_architecture": (
                structure_engine.get("benchmark_architecture") or []
            ),
            "hub_taxonomy": structure_engine.get("hub_taxonomy") or [],
            "control_stack": structure_engine.get("control_stack") or [],
            "transmission_order": structure_engine.get("transmission_order") or [],
            "chokepoints": _object(structure_engine.get("chokepoints")),
            "india": _object(structure_engine.get("india")),
            "principles": structure_engine.get("principles") or [],
        }
    else:
        market_structure = {
            "ok": False,
            "reason": "Oil market structure is unavailable in this snapshot",
        }

    ballast_engine = _object(snapshot.get("engines")).get("ballast")
    if isinstance(ballast_engine, dict) and ballast_engine.get("ok") is True:
        ballast = {
            "ok": True,
            "schema": ballast_engine.get("schema"),
            "as_of": ballast_engine.get("asof"),
            "headline": _object(ballast_engine.get("headline")),
            "contracts": [
                {
                    key: contract.get(key)
                    for key in (
                        "key",
                        "label",
                        "report_asof",
                        "available_asof",
                        "report_lag",
                        "price_proxy",
                        "open_interest",
                        "cash_transfer_scale",
                        "positioning",
                    )
                }
                for contract in (ballast_engine.get("contracts") or [])
                if isinstance(contract, dict)
            ],
            "inventory": _object(ballast_engine.get("inventory")),
            "funding": _object(ballast_engine.get("funding")),
            "pressure_ledger": ballast_engine.get("pressure_ledger") or [],
            "coverage": _object(ballast_engine.get("coverage")),
            "handoffs": _object(ballast_engine.get("handoffs")),
            "sources": ballast_engine.get("sources") or [],
            "caveats": ballast_engine.get("caveats") or [],
        }
    else:
        ballast = {
            "ok": False,
            "reason": (
                ballast_engine.get("reason")
                if isinstance(ballast_engine, dict)
                else "Ballast is unavailable in this snapshot"
            ),
        }

    return {
        **out,
        "ok": True,
        "as_of": engine.get("asof"),
        "oil": {
            "wti": _object(live.get("wti")),
            "brent": _object(live.get("brent")),
        },
        "funding": {
            "cp_nonfinancial": _object(live.get("cp_nonfinancial")),
            "cp_financial": _object(live.get("cp_financial")),
            "sofr_iorb": _object(live.get("sofr_iorb")),
        },
        "india": {"inr": _object(live.get("inr"))},
        "inflation_policy": _object(live.get("inflation_policy")),
        "official_dollar_parking": _object(live.get("official_dollar_parking")),
        "coupling": {
            "fit": _object(scatter.get("fit")),
            "x_label": scatter.get("x_label"),
            "y_label": scatter.get("y_label"),
            "interpretation": (
                "non-overlapping five-business-day association; not a causal "
                "estimate, forecast, or executable trade"
            ),
        },
        "scenario": {
            **_object(engine.get("scenario")),
            "status": "scenario_only",
        },
        "market_structure": market_structure,
        "ballast": ballast,
        "channel_directions": _object(engine.get("channel_directions")),
        "sources": engine.get("sources") or [],
        "caveats": engine.get("caveats") or [],
        "reading": (
            "Oil x Funding is bidirectional context: observed spot and funding "
            "rows stay separate from editable cargo, margin and India scenario "
            "arithmetic. Market structure keeps live Cushing stocks separate "
            "from dated capacity and chokepoint references. The combined "
            "context never enters the Seiche funding-stress composite."
        ),
    }


def estuary(snapshot: dict) -> dict[str, Any]:
    """Chartless Estuary contract with its full holdout Passage ledger."""

    engine, out = _engine(snapshot, "estuary", "seiche.estuary.v1")
    if engine is None:
        return out

    fx = _object(engine.get("fx"))
    materials = _object(engine.get("materials"))
    funding = _object(engine.get("funding"))
    return {
        **out,
        "ok": True,
        "as_of": engine.get("asof"),
        "headline": _object(engine.get("headline")),
        "leaders": {
            "fx": _rows(fx.get("currencies"), 5),
            "materials": _rows(materials.get("instruments"), 5),
            "funding": _rows(funding.get("markets"), 3),
        },
        "fx_breadth": {
            "broad": _object(fx.get("broad")),
            "advanced": _object(fx.get("advanced")),
            "emerging": _object(fx.get("emerging")),
            "median_pair_depreciation_percentile": fx.get(
                "median_pair_depreciation_percentile"
            ),
            "median_pair_volatility_percentile": fx.get(
                "median_pair_volatility_percentile"
            ),
        },
        "materials_breadth": {
            "categories": materials.get("categories") or [],
            "higher_pct": materials.get("breadth_higher_pct"),
        },
        "passage": _object(engine.get("passage")),
        "analogs": _object(engine.get("analogs")),
        "dollar_system": _object(engine.get("dollar_system")),
        "settlement_structure": _object(engine.get("settlement_structure")),
        "scenario": {
            **_object(engine.get("scenario")),
            "status": "scenario_only",
        },
        "coverage_matrix": engine.get("coverage_matrix") or [],
        "sources": engine.get("sources") or [],
        "caveats": engine.get("caveats") or [],
        "reading": (
            "The Estuary compares upstream FX and physical-material cash "
            "pressure with funding already priced. Passage links are earned "
            "only on untouched holdout history; the gap is context, not a "
            "probability, forecast, trade signal, or composite input."
        ),
    }
