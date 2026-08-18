"""Desk-ask sibling reads must carry LiquiLens eligibility flags."""

from __future__ import annotations

import asyncio

import pytest

from seiche import ai


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, payloads: dict[str, dict]) -> None:
        self._payloads = payloads

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def post(self, url: str, json: dict | None = None) -> _FakeResponse:
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


def test_mcp_payload_accepts_text_fallback() -> None:
    payload = ai._mcp_payload({
        "content": [{"type": "text", "text": '{"rows": []}', }],
    })
    assert payload == {"rows": []}
    assert ai._mcp_payload({"isError": True}) is None
    assert ai._mcp_payload({"structuredContent": {"regime": "STRAIN"}}) == {
        "regime": "STRAIN"
    }


def test_ask_attaches_liquilens_evidence_markets(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = {
        "failure_radar_board": {
            "result": {"structuredContent": {"rows": [{"slug": "esaf"}], "as_of": "2026-08-18"}}
        },
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
        },
    }

    def _client(*_args: object, **_kwargs: object) -> _FakeClient:
        return _FakeClient(payloads)

    monkeypatch.setattr(ai.httpx, "AsyncClient", _client)
    out = asyncio.run(ai.ask("why is this bank on the radar?", _snap()))
    assert out["ok"] is False
    pack = out["context_pack"]
    assert pack["liquilens_failure_radar"]["rows"][0]["slug"] == "esaf"
    evidence = pack["liquilens_evidence_markets"]["markets"][0]["historical_evidence"]
    assert evidence["validated_backtest_eligible"] is False
    assert evidence["real_money_eligible"] is False
    assert "eligibility flags" in pack["liquilens_evidence_note"]
