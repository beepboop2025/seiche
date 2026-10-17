"""Pure, opt-in projections of the existing India funding evidence contract.

This module never reads observations or recomputes financial findings. Freshness
counts describe only exposed cards, not every series declared by the country pack.
"""

from collections import Counter
from copy import deepcopy


_CONTEXT_KEYS = (
    "schema", "market_id", "generated_at", "asof", "status", "context_only",
    "historically_validated", "headline", "coverage", "caveats", "read_fault",
    "sources",
)
_FINDING_KEYS = (
    "missing_series", "policy_comparison", "liquidity_summary", "spreads", "mechanism",
)


def _groups(india: dict):
    for section in india.get("sections", []):
        yield section["id"], section.get("metrics", []), {
            key: value for key, value in section.items() if key != "metrics"
        }
    for key in ("curve", "monthly_curve"):
        curve = india.get(key, {})
        yield key, curve.get("nodes", []), {
            name: value for name, value in curve.items() if name != "nodes"
        }


def _freshness(cards: dict, declared: int | None) -> dict:
    statuses = Counter(card.get("status", "UNKNOWN") for card in cards.values())
    availability = Counter(card.get("availability", "UNKNOWN") for card in cards.values())
    unrepresented = max(0, declared - len(cards)) if isinstance(declared, int) else None
    return {
        "status": "current" if cards and unrepresented == 0 and all(
            card.get("status") in {"FRESH", "AGING"}
            and card.get("availability") == "AVAILABLE" for card in cards.values()
        ) else "partial_or_unknown",
        "represented_metrics": len(cards),
        "unrepresented_declared_metrics": unrepresented,
        "by_status": dict(sorted(statuses.items())),
        "by_availability": dict(sorted(availability.items())),
        "basis": (
            "Existing card statuses at generated_at, without re-evaluation. Counts cover "
            "exposed cards only. asof is the newest observation date, not whole-desk "
            "freshness; daily and monthly references remain separate."
        ),
    }


def project(india: dict, *, view: str, metric_id: str | None = None) -> dict:
    """Copy a summary or one exact card without modifying the canonical payload."""
    if not isinstance(view, str) or view not in {"summary", "metric"}:
        raise ValueError("India projection view must be summary or metric")
    if view == "metric" and (
        not isinstance(metric_id, str) or not metric_id.strip() or len(metric_id) > 128
    ):
        raise ValueError("metric_id must be a nonblank string of at most 128 characters")
    if view != "metric" and metric_id is not None:
        raise ValueError("metric_id is supported only for the metric view")

    out = {key: india[key] for key in _CONTEXT_KEYS if key in india}
    out["view"] = view
    groups = list(_groups(india))
    if view == "summary":
        out.update({key: india[key] for key in _FINDING_KEYS if key in india})
        out["metric_catalog"] = [
            {"id": group_id, "metric_ids": [card["id"] for card in cards]}
            for group_id, cards, _ in groups
        ]
        for key in ("curve", "monthly_curve"):
            out[key] = {name: value for name, value in india.get(key, {}).items() if name != "nodes"}
        unique = {card["id"]: card for _, cards, _ in groups for card in cards}
        out["freshness_summary"] = _freshness(unique, india.get("coverage", {}).get("declared"))
    else:
        out.update(metric_id=metric_id, selection_status="not_found", metric=None)
        for group_id, cards, context in groups:
            card = next((card for card in cards if card["id"] == metric_id), None)
            if card is not None:
                out.update(
                    selection_status="found", metric=card,
                    metric_location=group_id,
                    # Preserve cadence/curve constraints without returning unrelated spreads.
                    selection_context={key: value for key, value in context.items() if key != "spreads"},
                )
                break
        else:
            out["reason"] = "No exposed metric card has this exact ID. Use view='summary' for the metric catalog."
    return deepcopy(out)
