"""Compact public contracts for Estuary and Oil x Funding."""

from __future__ import annotations

import json
from copy import deepcopy

from fastapi.testclient import TestClient

from seiche import api, context_views


def _snapshot() -> dict:
    return {
        "generated_at": "2026-08-08T05:00:00Z",
        "engines": {
            "oilfunding": {
                "ok": True,
                "asof": "2026-08-07",
                "live": {
                    "wti": {"price_usd_per_bbl": 81.5, "change_20d_pct": 9.0},
                    "cp_nonfinancial": {"spread_bp": 14.0, "change_20d_bp": 6.0},
                    "sofr_iorb": {"spread_bp": 2.0},
                },
                "charts": {
                    "scatter": {
                        "fit": {"correlation": 0.42, "r_squared": 0.18},
                        "rows": [["large", "browser", "history"]],
                    }
                },
                "scenario": {"outputs": {"margin": {"cash_usd": 12_000_000}}},
                "channel_directions": {"margin": "oil_to_same_day_cash"},
            },
            "estuary": {
                "ok": True,
                "asof": "2026-08-07",
                "headline": {
                    "regime": "TRANSMISSION BUILDING",
                    "upstream_pressure": 72.0,
                    "funding_priced": 50.0,
                    "transmission_gap": 22.0,
                    "context_only": True,
                },
                "fx": {"currencies": [{"key": "INR", "pressure": 89.0}]},
                "materials": {
                    "instruments": [{"key": "WTI", "pressure": 88.0}]
                },
                "funding": {"markets": [{"key": "cp_nonfinancial"}]},
                "passage": {
                    "earned": 1,
                    "tentative": 0,
                    "not_earned": 2,
                    "edges": [{
                        "source": "WTI",
                        "target": "Nonfinancial CP-bill",
                        "status": "earned",
                        "corr_holdout": 0.31,
                    }],
                },
                "charts": {"daily_gap": {"rows": [["large", "history"]]}},
            },
        },
    }


def _missing_sofr_snapshot() -> dict:
    snapshot = deepcopy(_snapshot())
    engine = snapshot["engines"]["oilfunding"]
    engine["live"]["sofr_iorb"] = {
        "spread_bp": None,
        "change_20d_bp": None,
        "percentile_3y": None,
        "asof": None,
    }
    engine["scenario"] = {
        "assumptions": {"funding_rate_pct": None},
        "funding_rate_evidence": {
            "value_pct": None,
            "basis": "unavailable",
            "asof": None,
        },
        "outputs": {
            "carry": {
                "financing_cost_usd_per_bbl": None,
                "required_contango_usd_per_bbl": None,
                "mechanical_headroom_usd_per_bbl": None,
            },
            "trade_finance": {"cargo_financing_cost_usd": None},
        },
    }
    return snapshot


def test_context_views_are_compact_versioned_and_context_only():
    snapshot = _snapshot()
    oil = context_views.oil_funding(snapshot)
    estuary = context_views.estuary(snapshot)

    assert oil["schema"] == "seiche.oil-funding.v1"
    assert oil["context_only"] is True
    assert oil["oil"]["wti"]["price_usd_per_bbl"] == 81.5
    assert oil["coupling"]["fit"]["correlation"] == 0.42
    assert "charts" not in oil
    assert oil["ballast"]["ok"] is False
    assert estuary["schema"] == "seiche.estuary.v1"
    assert estuary["context_only"] is True
    assert estuary["passage"]["edges"][0]["status"] == "earned"
    assert estuary["leaders"]["fx"][0]["key"] == "INR"
    assert "charts" not in estuary


def test_context_views_preserve_engine_failure_reason():
    snapshot = {
        "generated_at": "2026-08-08T05:00:00Z",
        "engines": {"estuary": {"ok": False, "reason": "daily tape stale"}},
    }
    payload = context_views.estuary(snapshot)
    assert payload["ok"] is False
    assert payload["reason"] == "daily tape stale"
    assert payload["context_only"] is True


def test_oil_view_exposes_bounded_ballast_and_market_structure(fake_snap):
    payload = context_views.oil_funding(fake_snap)

    assert payload["ballast"]["schema"] == "seiche.ballast.v1"
    assert payload["ballast"]["headline"]["state"] == "TIGHT"
    contract = payload["ballast"]["contracts"][0]
    assert contract["key"] == "WTI"
    assert contract["available_asof"] == "2026-07-10"
    assert "history" not in contract
    assert payload["market_structure"]["ok"] is True
    assert payload["market_structure"]["cushing"]["live"]["stocks_m_bbl"] == 21.0
    assert payload["market_structure"]["cushing"]["capacity_asof"] == "2024-03-31"
    assert "gross cash-transfer scale" in payload["reading"]


def test_missing_sofr_stays_nullable_in_the_versioned_view_and_rest(monkeypatch):
    snapshot = _missing_sofr_snapshot()
    payload = context_views.oil_funding(snapshot)

    assert payload["schema"] == "seiche.oil-funding.v1"
    assert payload["funding"]["sofr_iorb"]["spread_bp"] is None
    assert payload["scenario"]["assumptions"]["funding_rate_pct"] is None
    assert payload["scenario"]["funding_rate_evidence"] == {
        "value_pct": None,
        "basis": "unavailable",
        "asof": None,
    }
    assert payload["scenario"]["outputs"]["carry"] == {
        "financing_cost_usd_per_bbl": None,
        "required_contango_usd_per_bbl": None,
        "mechanical_headroom_usd_per_bbl": None,
    }
    assert json.dumps(payload, allow_nan=False)

    async def fake_snapshot():
        return snapshot

    monkeypatch.setattr(api.assemble, "snapshot", fake_snapshot)
    response = TestClient(api.app).get("/api/oil-funding")
    assert response.status_code == 200
    assert response.json()["scenario"]["funding_rate_evidence"]["basis"] == "unavailable"
    assert response.json()["scenario"]["outputs"]["trade_finance"][
        "cargo_financing_cost_usd"
    ] is None


def test_public_routes_serve_schema_identity(monkeypatch):
    async def fake_snapshot():
        return _snapshot()

    monkeypatch.setattr(api.assemble, "snapshot", fake_snapshot)
    client = TestClient(api.app)

    oil = client.get("/api/oil-funding")
    estuary = client.get("/api/estuary")
    assert oil.status_code == 200
    assert oil.json()["schema"] == "seiche.oil-funding.v1"
    assert estuary.status_code == 200
    assert estuary.json()["schema"] == "seiche.estuary.v1"
    assert "public" in oil.headers["cache-control"]
