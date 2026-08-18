"""Desk-ask sibling reads reuse event_analysis fail-closed LiquiLens layers."""

from __future__ import annotations

import asyncio

import pytest

from seiche import ai, event_analysis
from seiche import mcp_server


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, payloads: dict[str, dict], *, fail: bool = False) -> None:
        self._payloads = payloads
        self._fail = fail

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def post(self, url: str, json: dict | None = None) -> _FakeResponse:
        if self._fail:
            raise RuntimeError("sibling MCP unreachable")
        name = (json or {}).get("params", {}).get("name")
        return _FakeResponse(self._payloads[name])


def _snap() -> dict:
    return {
        "generated_at": "2026-08-18T00:00:00+00:00",
        "version": "test",
        "engines": {"composite": {"value": 44.0, "regime": "EROSION",
                                  "coverage_pct": 94.0, "dead_inputs": []}},
        "deep": {},
        "headline": {},
        "calendar": {},
        "faults": [],
        "provenance": [{"staleness": "fresh"}],
    }


def _valid_layer(name: str) -> dict:
    payloads = {
        "failure_radar": {"rows": [{"slug": "esaf", "name": "ESAF"}], "tiers": {}},
        "rails": {"rows": [], "aggregate": {}},
        "bondholders": {"us": {}, "uk": {}},
        "deposit_migration": {
            "channels": {}, "coverage": {},
            "cannot_see": ["intra-day deposit switches"],
        },
        "bond_book": {
            "rows": [], "aggregate": {}, "counts": {}, "nowcast": {},
        },
        "short_pressure": {"us_rows": [], "uk_rows": [], "eu_rows": []},
        "market_makers": {"live": {}},
        "leverage": {"markets": [], "breadth": {}},
        "tbtf": {"rows": [], "flagged": {}},
        "crypto_exposure": {"rows": [], "compound_flags": []},
    }
    return {"as_of": "2026-08-18", "available": True, **payloads[name]}


def _fleet() -> dict[str, dict]:
    return {name: _valid_layer(name) for name in event_analysis._LIQUILENS_PATHS}


def _evidence_payloads() -> dict[str, dict]:
    return {
        "evidence_markets": {
            "result": {
                "structuredContent": {
                    "markets": [{
                        "name": "India",
                        "historical_evidence": {
                            "validated_backtest_eligible": False,
                            "real_money_eligible": False,
                        },
                    }]
                }
            }
        }
    }


def test_mcp_payload_accepts_text_fallback() -> None:
    payload = ai._mcp_payload({
        "content": [{"type": "text", "text": '{"rows": []}', }],
    })
    assert payload == {"rows": []}
    assert ai._mcp_payload({"isError": True}) is None
    assert ai._mcp_payload({"structuredContent": {"regime": "STRAIN"}}) == {
        "regime": "STRAIN"
    }


def test_wants_liquilens_sibling_for_institutions_and_tandem() -> None:
    assert ai.wants_liquilens_sibling("why is this bank on the radar?")
    assert ai.wants_liquilens_sibling("What is the tandem read?")
    assert not ai.wants_liquilens_sibling("why is SOFR elevated?")


def test_evidence_markets_schema_drift_is_unavailable() -> None:
    out = ai.compact_liquilens_evidence_markets({"detail": "ok"})
    assert out["available"] is False
    assert out["reading"] == "UNAVAILABLE"
    assert out["validated_backtest_eligible"] is False
    assert out["real_money_eligible"] is False
    assert out.get("regime") != "CALM"


def _patch_fleet(monkeypatch: pytest.MonkeyPatch, raw: dict | None = None) -> None:
    payload = raw if raw is not None else _fleet()

    async def _raw() -> dict:
        return payload

    monkeypatch.setattr(event_analysis, "_raw_fleet", _raw)


def test_ask_attaches_fail_closed_liquilens_layers(monkeypatch: pytest.MonkeyPatch) -> None:
    def _client(*_args: object, **_kwargs: object) -> _FakeClient:
        return _FakeClient(_evidence_payloads())

    _patch_fleet(monkeypatch)
    monkeypatch.setattr(ai.httpx, "AsyncClient", _client)
    out = asyncio.run(ai.ask("why is ESAF on the radar?", _snap()))
    assert out["ok"] is False
    pack = out["context_pack"]
    layers = pack["liquilens"]["layers"]
    assert set(layers) == set(event_analysis._LIQUILENS_PATHS)
    assert layers["failure_radar"]["entity_matches"][0]["slug"] == "esaf"
    assert layers["deposit_migration"]["cannot_see"] == ["intra-day deposit switches"]
    evidence = pack["liquilens_evidence_markets"]["markets"][0]["historical_evidence"]
    assert evidence["validated_backtest_eligible"] is False
    assert evidence["real_money_eligible"] is False
    assert "eligibility flags" in pack["liquilens_evidence_note"]
    assert "UNAVAILABLE" in pack["liquilens_note"]
    assert "joint score" not in pack
    assert "seiche_liquilens_undertow" not in pack


def test_ask_attaches_sibling_for_tandem_question(monkeypatch: pytest.MonkeyPatch) -> None:
    def _client(*_args: object, **_kwargs: object) -> _FakeClient:
        return _FakeClient(_evidence_payloads())

    _patch_fleet(monkeypatch)
    monkeypatch.setattr(ai.httpx, "AsyncClient", _client)
    out = asyncio.run(ai.ask("what is the tandem read?", _snap()))
    assert "liquilens" in out["context_pack"]
    assert out["context_pack"]["liquilens"]["source_status"]["product"] == "liquilens"


def test_ask_does_not_attach_sibling_for_funding_only_question() -> None:
    out = asyncio.run(ai.ask("why is SOFR elevated?", _snap()))
    pack = out["context_pack"]
    assert "liquilens" not in pack
    assert "liquilens_evidence_markets" not in pack


def test_ask_renders_schema_drift_as_unavailable_not_calm(
        monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _fleet()
    raw["rails"] = {
        "available": False,
        "reason": "board did not answer",
        "regime": "CALM",
        "as_of": "2026-08-18",
    }

    def _client(*_args: object, **_kwargs: object) -> _FakeClient:
        return _FakeClient({}, fail=True)

    _patch_fleet(monkeypatch, raw)
    monkeypatch.setattr(ai.httpx, "AsyncClient", _client)
    pack = asyncio.run(ai.ask("why is this bank on the radar?", _snap()))["context_pack"]
    rails = pack["liquilens"]["layers"]["rails"]
    assert rails["available"] is False
    assert rails["reading"] == "UNAVAILABLE"
    assert rails.get("regime") != "CALM"
    evidence = pack["liquilens_evidence_markets"]
    assert evidence["available"] is False
    assert evidence["reading"] == "UNAVAILABLE"
    assert evidence["validated_backtest_eligible"] is False
    assert evidence["real_money_eligible"] is False


def test_ask_desk_does_not_proxy_liquilens_into_public_tools() -> None:
    public = {name for name, tool in mcp_server.TOOLS.items() if tool[4]}
    assert "ask_desk" not in public
    assert not any("liquilens" in name for name in mcp_server.TOOLS)
    assert len(public) == 9
