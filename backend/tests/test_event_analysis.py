"""Readings-only event analysis: compact grounding, joins, and API surface."""

import asyncio
import json

from fastapi.testclient import TestClient

from seiche import event_analysis


def _snapshot() -> dict:
    return {
        "generated_at": "2026-08-15T09:00:00+00:00",
        "version": "test",
        "headline": "Funding pressure is contained but not absent.",
        "engines": {
            "composite": {
                "value": 44.0,
                "regime": "EROSION",
                "coverage_pct": 94.0,
                "dead_inputs": ["one_input"],
                "decomposition": [],
            },
            "sonar": {"movers": []},
            "weather": {},
            "kink": {},
            "resonance": {},
            "warehouse": {},
            "echo": {},
            "basins": {},
            "moorings": {},
        },
        "deep": {
            "tell": {}, "turn": {}, "ml": {}, "playbook": {},
            "backtest": {},
        },
        "calendar": {},
        "faults": [],
        "provenance": [{"staleness": "fresh"}, {"staleness": "stale"}],
    }


def _raw_fleet() -> dict:
    return {
        "undertow": {
            "asof": "2026-08-14",
            "public_subset": True,
            "funding": {"regime": "EROSION"},
            "segments": {
                "BANKS": {
                    "tier": "PARTIAL",
                    "score": None,
                    "candidate_tier": "NORMAL",
                    "candidate_score": 0.34,
                    "n_measures": 2,
                    "n_qualifying": 1,
                    "score_withheld_reason": "validation incomplete",
                    "validation_replay": {
                        "status": "INCOMPLETE",
                        "score_eligible": False,
                        "failed_controls": ["minimum span"],
                    },
                    "measures": [{
                        "measure": "bank equity liquidity",
                        "stress_pctl": 0.62,
                        "obs": 90,
                        "asof": "2026-08-14",
                    }],
                }
            },
            "provenance": {"generated_at": "2026-08-15T08:30:00Z"},
        },
        "failure_radar": {
            "as_of": "2026-06-30",
            "tiers": {"WATCH": 2},
            "rows": [{
                "name": "JPMorgan Chase Bank, National Association",
                "slug": "jpmorgan-chase",
                "tier": "BASELINE",
                "score": 0.12,
                "as_of": "2026-06-30",
            }],
        },
        "rails": {
            "_error": "HTTP 503",
            "_status": 503,
            "detail": "stale upstream reading",
        },
    }


def test_compaction_preserves_official_partial_and_unavailable():
    raw = _raw_fleet()
    undertow = event_analysis.compact_undertow(raw["undertow"])
    cell = undertow["segments"]["BANKS"]
    assert cell["tier"] == "PARTIAL"
    assert cell["candidate_tier"] == "NORMAL"
    assert cell["validation_replay"]["score_eligible"] is False

    rails = event_analysis.compact_liquilens_layer(
        "rails", raw["rails"], "what happened at JPMorgan?"
    )
    assert rails["available"] is False
    assert rails["status"] == 503
    assert "stale" in rails["reason"]


def test_entity_join_is_specific_not_generic_or_numeric():
    row = {
        "name": "JPMorgan Chase Bank, National Association",
        "ticker": "JPM",
        "cert": 628,
    }
    assert event_analysis.row_matches_question(
        row, "JPMorgan ended a banking relationship"
    )
    assert event_analysis.row_matches_question(row, "What does JPM show?")
    assert not event_analysis.row_matches_question(
        row, "What do the readings say about bank regulatory concerns?"
    )
    assert not event_analysis.row_matches_question(
        row, "Does observation 628 change anything?"
    )


def test_context_is_bounded_and_matches_only_named_entities():
    pack = asyncio.run(event_analysis.event_context(
        "What can the readings say about JPMorgan?", _snapshot(), _raw_fleet()
    ))
    assert pack["event_text_status"].startswith("user-supplied, unverified")
    matches = pack["liquilens"]["layers"]["failure_radar"]["entity_matches"]
    assert [row["slug"] for row in matches] == ["jpmorgan-chase"]
    assert len(json.dumps(pack, default=str)) < 60_000


def test_analyze_sends_only_event_and_reading_pack(monkeypatch):
    captured = {}

    async def fake_router(messages):
        captured["messages"] = messages
        return "Current readings contextualize transmission; they do not prove cause."

    async def no_env(_messages):
        raise AssertionError("the second route should not run after success")

    monkeypatch.setattr(event_analysis.ai, "_via_router", fake_router)
    monkeypatch.setattr(event_analysis.ai, "_via_env", no_env)
    result = asyncio.run(event_analysis.analyze(
        "JPMorgan ended its relationship with Polymarket", _snapshot(),
        raw_fleet=_raw_fleet(),
    ))

    assert result["ok"] is True
    assert result["route"] == "free-llm-router"
    system, user = captured["messages"]
    assert "UNVERIFIED CLAIM" in system["content"]
    assert "only source" in system["content"]
    envelope = json.loads(user["content"])
    assert envelope["event_text_unverified"].startswith("JPMorgan ended")
    assert envelope["fleet_reading_pack"]["contract"] == \
        "readings_only_event_connection.v1"
    assert '"seiche"' in user["content"]
    assert '"undertow"' in user["content"]
    assert '"liquilens"' in user["content"]


def test_no_llm_returns_readings_without_inventing_connection(monkeypatch):
    async def unavailable(_messages):
        return None

    monkeypatch.setattr(event_analysis.ai, "_via_router", unavailable)
    monkeypatch.setattr(event_analysis.ai, "_via_env", unavailable)
    result = asyncio.run(event_analysis.analyze(
        "an unverified event", _snapshot(), raw_fleet=_raw_fleet()
    ))

    assert result["ok"] is False
    assert "do not establish" in result["answer"]
    assert "no semantic connection is being invented" in result["answer"]


def test_event_analysis_api_is_post_only_and_rate_limited_like_ask(monkeypatch):
    from seiche import api as api_mod

    seen = {}

    async def fake_snapshot():
        return _snapshot()

    async def fake_analyze(question, snapshot):
        seen.update(question=question, snapshot=snapshot)
        return {"ok": True, "answer": "grounded"}

    monkeypatch.setattr(api_mod.assemble, "snapshot", fake_snapshot)
    monkeypatch.setattr(event_analysis, "analyze", fake_analyze)
    monkeypatch.setattr(api_mod, "_ask_limiter", api_mod._RateLimiter(20))
    client = TestClient(api_mod.app)

    response = client.post(
        "/api/event-analysis",
        json={"question": "  What do the readings say?  "},
        headers={"x-forwarded-for": "198.51.100.81"},
    )
    assert response.status_code == 200
    assert seen == {"question": "What do the readings say?",
                    "snapshot": _snapshot()}
    assert client.get("/api/event-analysis").status_code == 405
    assert client.post("/api/event-analysis", json={"question": " "},
                       headers={"x-forwarded-for": "198.51.100.82"}).status_code == 422
