"""Compact agent reads preserve the canonical India evidence and its boundaries."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json

import pytest

from seiche import assemble, india_context, india_funding, mcp_server
from seiche.domain.observation import (
    Observation, QualityState, RedistributionStatus, StalenessState, evidence_sha256,
)
from seiche.markets.india_inr.pack import PACK


NOW = datetime(2026, 10, 1, 17, tzinfo=UTC)


@pytest.fixture
def full_desk():
    """Every public instrument, populated with enough history for full statistics."""
    rows = []
    for spec in PACK.instruments:
        adapter = PACK.adapter_map[spec.source_adapter_id]
        if spec.source_adapter_id == "tenant_market_data" or adapter.redistribution_status is not RedistributionStatus.ALLOWED:
            continue
        for offset in range(25):
            event = NOW.replace(hour=0) - timedelta(days=26 - offset)
            rows.append(Observation(
                market_id="IN-INR", monetary_area_id="IN", jurisdiction_codes=("IN",), currency="INR",
                instrument_id=spec.instrument_id, semantic_role=spec.semantic_role,
                value=Decimal(600 + offset), canonical_unit=spec.canonical_unit,
                rate_compounding=spec.rate_compounding, day_count=spec.day_count,
                event_time=event, source_publication_time=event + timedelta(hours=19), knowledge_time=NOW,
                revision_id="synthetic:constant-security", source=adapter.adapter_id,
                evidence_hash=evidence_sha256(f"synthetic-{spec.instrument_id}-{offset}"),
                connector_classification=adapter.classification,
                redistribution_status=adapter.redistribution_status,
                quality=QualityState.VERIFIED, staleness=StalenessState.FRESH,
            ))
    return india_funding.build(rows, now=NOW, include_history=False)


def _cards(desk):
    return [card for section in desk["sections"] for card in section["metrics"]] + desk["curve"]["nodes"] + desk["monthly_curve"]["nodes"]


def _dispatch(arguments):
    return mcp_server.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "money_market_context", "arguments": arguments},
    }, public=True)


def test_summary_retains_findings_constraints_and_explicit_absence(full_desk):
    full_desk["read_fault"] = "Canonical India observations could not be read."
    before = deepcopy(full_desk)
    result = india_context.project(full_desk, view="summary")
    for key in (
        "status", "context_only", "historically_validated", "coverage", "missing_series",
        "caveats", "read_fault", "sources", "policy_comparison", "liquidity_summary", "spreads", "mechanism",
    ):
        assert result[key] == full_desk[key]
    for key in ("curve", "monthly_curve"):
        assert result[key] == {k: v for k, v in full_desk[key].items() if k != "nodes"}
    assert "sections" not in result
    assert all("nodes" not in result[key] for key in ("curve", "monthly_curve"))
    ids = [key for group in result["metric_catalog"] for key in group["metric_ids"]]
    assert ids == [card["id"] for card in _cards(full_desk)]
    assert len(ids) >= 80  # The fixture must exercise the full current desk.
    assert full_desk == before
    result["caveats"].append("caller mutation")
    assert full_desk == before


def test_freshness_does_not_promote_missing_unknown_stale_or_unrepresented(full_desk):
    cards = _cards(full_desk)
    for card in cards:
        card.update(status="FRESH", availability="AVAILABLE")
    for card, status in zip(cards, ("UNKNOWN", "STALE", "DEAD", "RESTRICTED", "UNAVAILABLE")):
        card["status"] = status
        if status in {"RESTRICTED", "UNAVAILABLE"}:
            card["availability"] = status
            card["value"] = None
    result = india_context.project(full_desk, view="summary")["freshness_summary"]
    assert result["status"] == "partial_or_unknown"
    assert result["represented_metrics"] == len(cards)
    assert result["unrepresented_declared_metrics"] == full_desk["coverage"]["declared"] - len(cards)
    assert sum(result["by_status"].values()) == len(cards)
    assert result["by_status"]["UNKNOWN"] == result["by_status"]["STALE"] == result["by_status"]["DEAD"] == 1
    assert result["by_availability"]["RESTRICTED"] == result["by_availability"]["UNAVAILABLE"] == 1
    # Even all represented cards being fresh cannot establish freshness for omitted series.
    for card in cards:
        card.update(status="FRESH", availability="AVAILABLE")
    assert india_context.project(full_desk, view="summary")["freshness_summary"]["status"] == "partial_or_unknown"


@pytest.mark.parametrize("group", ["policy", "money_market", "liquidity", "bond_auctions", "curve", "monthly_curve"])
def test_exact_card_preserves_all_fields_and_group_constraints(full_desk, group):
    source = (full_desk[group]["nodes"][0] if group in {"curve", "monthly_curve"}
              else next(section["metrics"][0] for section in full_desk["sections"] if section["id"] == group))
    before = deepcopy(full_desk)
    result = india_context.project(full_desk, view="metric", metric_id=source["id"])
    assert result["selection_status"] == "found"
    assert result["metric"] == source
    assert result["metric_location"] == group
    for key in ("redistribution_status", "event_time", "published_at", "knowledge_time", "statistics_window", "confidence", "status"):
        assert result["metric"][key] == source[key]
    if group == "monthly_curve":
        assert result["selection_context"]["historical_context_only"] is True
        assert result["selection_context"]["cadence"] == "P1M"
    if group == "curve":
        assert result["selection_context"]["interpolated"] is False
        assert result["selection_context"]["fixed_tenor_status"] == "unavailable"
    result["metric"]["statistics_window"]["basis"] = "changed by caller"
    assert full_desk == before


@pytest.mark.parametrize("status", ["STALE", "UNKNOWN", "RESTRICTED", "UNAVAILABLE"])
def test_metric_cannot_promote_stale_missing_or_restricted(full_desk, status):
    card = full_desk["sections"][0]["metrics"][0]
    card.update(status=status, availability=status if status in {"RESTRICTED", "UNAVAILABLE"} else "AVAILABLE")
    if status == "RESTRICTED":
        card.update(value=None, canonical_value=None, redistribution_status="prohibited")
    if status == "UNAVAILABLE":
        card.update(value=None, canonical_value=None)
    result = india_context.project(full_desk, view="metric", metric_id=card["id"])
    assert result["metric"] == card
    assert result["metric"]["status"] == status


def test_unknown_metric_is_explicit_and_does_not_fall_back(full_desk, monkeypatch):
    monkeypatch.setattr(india_funding, "read", lambda **kwargs: full_desk)
    result = _dispatch({"section": "india", "view": "metric", "metric_id": "IN.RBI.NOT_A_METRIC"})["result"]["structuredContent"]
    assert result["ok"] is False
    assert result["india"]["selection_status"] == "not_found"
    assert result["india"]["metric"] is None
    assert "metric catalog" in result["india"]["reason"]


@pytest.mark.parametrize("arguments", [
    {"view": "summary"}, {"section": "countries", "view": "full"},
    {"section": "india", "country": "IN"}, {"section": "india", "view": "other"},
    {"section": "india", "view": []}, {"section": "india", "view": "metric"},
    {"section": "india", "view": "metric", "metric_id": None},
    {"section": "india", "view": "metric", "metric_id": " "},
    {"section": "india", "view": "metric", "metric_id": "X" * 129},
    {"section": "india", "metric_id": "IN.RBI.CRR"},
    {"section": "india", "view": "summary", "metric_id": "IN.RBI.CRR"},
    {"section": "india", "view": "full", "metric_id": "IN.RBI.CRR"},
])
def test_invalid_combinations_rejected_before_canonical_io(monkeypatch, arguments):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid arguments must not read canonical observations or snapshots")
    monkeypatch.setattr(india_funding, "read", forbidden)
    monkeypatch.setattr(mcp_server, "_get_completed_snapshot", forbidden)
    with pytest.raises(mcp_server.ToolError):
        mcp_server.tool_money_market(arguments, True)


def test_full_legacy_parity_and_compact_wire_budgets(full_desk, monkeypatch):
    calls = []
    def read(**kwargs):
        calls.append(kwargs)
        return full_desk
    def forbidden(*args, **kwargs):
        pytest.fail("India projection must not collect, assemble or recompute a snapshot")
    monkeypatch.setattr(india_funding, "read", read)
    monkeypatch.setattr(mcp_server, "_get_snapshot", forbidden)
    monkeypatch.setattr(mcp_server, "_get_completed_snapshot", forbidden)
    monkeypatch.setattr(assemble, "snapshot", forbidden)
    original = deepcopy(full_desk)
    legacy = _dispatch({"section": "india"})
    assert legacy == _dispatch({"section": "india", "view": "full"})
    assert legacy["result"]["structuredContent"]["india"] == full_desk
    full_bytes = len(json.dumps(legacy).encode())
    assert full_bytes > 350_000
    summary = _dispatch({"section": "india", "view": "summary"})
    assert len(json.dumps(summary).encode()) <= 64 * 1024
    assert json.loads(summary["result"]["content"][0]["text"]) == summary["result"]["structuredContent"]
    # The budget counts both text and structured copies plus the JSON-RPC envelope.
    for card in _cards(full_desk):
        result = _dispatch({"section": "india", "view": "metric", "metric_id": card["id"]})
        assert len(json.dumps(result).encode()) <= 24 * 1024
        assert result["result"]["structuredContent"]["india"]["metric"] == card
    assert all(call == {"include_history": False} for call in calls)
    assert len(calls) == 3 + len(_cards(full_desk))
    assert full_desk == original
