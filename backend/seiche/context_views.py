"""Compact cross-market views shared by REST and delivery clients.

The full Oil x Funding and Estuary engines carry chart histories intended for
the browser. Bots and agents need the current evidence, scenario boundary, and
caveats instead. These adapters keep that smaller machine contract in one place
so each delivery surface cannot quietly reinterpret the engines.
"""

from __future__ import annotations

import os
from typing import Any

from seiche.markets.world import project_world_markets
from seiche.nbs_intake import (
    NBSMacroContext,
    load_public_context_from_public_dir,
)
from seiche.palimpsest_china_intake import (
    PalimpsestChinaEconomicContext,
    PalimpsestChinaIntakeError,
    load_accepted_export,
)


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
            "Oil x Funding is bidirectional context: observed spot, CFTC "
            "futures positioning, EIA inventory and funding rows stay separate "
            "from editable cargo, margin and India scenario arithmetic. Ballast "
            "estimates gross cash-transfer scale, not observed margin calls. "
            "Market structure keeps live Cushing stocks separate from dated "
            "capacity and chokepoint references. The combined context never "
            "enters the Seiche funding-stress composite."
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


def world_markets(
    snapshot: dict,
    *,
    selector: str = "all",
    evaluation_asof: Any = None,
    china_macro_context: NBSMacroContext | None = None,
    china_economic_context: PalimpsestChinaEconomicContext | None = None,
) -> dict[str, Any]:
    """Unified chartless catalog over one already completed board snapshot.

    The projection itself lives under ``seiche.markets`` so REST, MCP, static
    publication, and tests share the exact same evidence/status semantics.
    """

    return project_world_markets(
        snapshot,
        selector=selector,
        evaluation_asof=evaluation_asof,
        china_macro_context=china_macro_context,
        china_economic_context=china_economic_context,
    )


def public_china_macro_context() -> NBSMacroContext | None:
    """Load only the signed public projection configured for API/MCP reads."""

    public_dir = os.getenv("SEICHE_NBS_PUBLIC_DIR", "").strip()
    if not public_dir:
        return None
    context = load_public_context_from_public_dir(public_dir)
    return context if isinstance(context, NBSMacroContext) else None


def public_china_economic_context() -> PalimpsestChinaEconomicContext | None:
    """Load one exact, operator-accepted Palimpsest export from local files.

    An entirely absent configuration means the additive context is not
    onboarded. A partial or invalid configuration fails loud; silently dropping
    a configured rights or integrity failure would let clients mistake missing
    China evidence for a valid empty panel.
    """

    names = {
        "manifest": "SEICHE_PALIMPSEST_CHINA_MANIFEST_PATH",
        "artifact": "SEICHE_PALIMPSEST_CHINA_ARTIFACT_PATH",
        "input_ledger": "SEICHE_PALIMPSEST_CHINA_INPUT_LEDGER_PATH",
        "availability": "SEICHE_PALIMPSEST_CHINA_AVAILABILITY_PATH",
        "producer_commit_evidence": (
            "SEICHE_PALIMPSEST_CHINA_PRODUCER_COMMIT_EVIDENCE_PATH"
        ),
        "producer_main_evidence": (
            "SEICHE_PALIMPSEST_CHINA_PRODUCER_MAIN_EVIDENCE_PATH"
        ),
        "handoff": "SEICHE_PALIMPSEST_CHINA_HANDOFF_PATH",
        "checksums": "SEICHE_PALIMPSEST_CHINA_CHECKSUMS_PATH",
        "lineage_chain": "SEICHE_PALIMPSEST_CHINA_LINEAGE_CHAIN_PATH",
        "lineage_evidence": "SEICHE_PALIMPSEST_CHINA_LINEAGE_EVIDENCE_PATH",
        "acceptance": "SEICHE_PALIMPSEST_CHINA_ACCEPTANCE_PATH",
    }
    configured = {key: os.getenv(name, "").strip() for key, name in names.items()}
    if not any(configured.values()):
        return None
    missing = [names[key] for key, value in configured.items() if not value]
    if missing:
        raise PalimpsestChinaIntakeError(
            "Palimpsest China intake configuration is incomplete: "
            + ", ".join(sorted(missing))
        )
    return load_accepted_export(
        configured["manifest"],
        configured["artifact"],
        configured["acceptance"],
        input_ledger_path=configured["input_ledger"],
        availability_path=configured["availability"],
        producer_commit_evidence_path=configured["producer_commit_evidence"],
        producer_main_evidence_path=configured["producer_main_evidence"],
        handoff_path=configured["handoff"],
        checksums_path=configured["checksums"],
        lineage_chain_path=configured["lineage_chain"],
        lineage_evidence_path=configured["lineage_evidence"],
    )
