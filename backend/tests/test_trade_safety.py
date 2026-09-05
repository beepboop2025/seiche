"""Fail-closed contracts for Seiche's cache-only Trade Safety projection."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seiche import api, assemble, mcp_server, trade_safety


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def _snapshot() -> dict:
    return {
        "generated_at": "2026-09-02T11:30:00Z",
        "version": "0.12.3 test",
        "engines": {
            "composite": {
                "ok": True,
                "regime": "STRAIN",
                "value": 71.2,
                "coverage_pct": 84.0,
            }
        },
        "faults": [{"source": "synthetic", "category": "transport"}],
        "provenance": [
            {
                "mnemonic": "FRESH",
                "asof": "2026-09-01",
                "staleness": "fresh",
                "freshness_grace_days": 1,
            },
            {
                "mnemonic": "STALE",
                "asof": "2026-08-29",
                "staleness": "stale",
                "freshness_grace_days": 1,
            },
            {
                "mnemonic": "DEAD",
                "asof": "2026-08-28",
                "staleness": "dead",
                "freshness_grace_days": 0,
            },
            {
                "mnemonic": "TABLE",
                "asof": None,
                "staleness": "fresh",
            },
            {
                "mnemonic": "UNCLASSIFIED",
                "asof": None,
            },
        ],
    }


def test_projection_is_deterministic_conservative_and_non_executable() -> None:
    snapshot = _snapshot()
    before = copy.deepcopy(snapshot)

    first = trade_safety.project(snapshot, evaluation_at=NOW)
    second = trade_safety.project(snapshot, evaluation_at=NOW)

    assert first == second
    assert snapshot == before
    assert first["schema"] == "seiche.risk-context.v1"
    assert first["status"] == "available"
    assert first["state"] == "context_only"
    assert first["evidence_class"] == "derived"
    assert first["rights_status"] == "metadata_only"
    assert first["regime"] == "STRAIN"
    assert first["stress_index"] == 71.2
    assert first["coverage_pct"] == 84.0
    assert first["fault_count"] == 1
    assert first["attestation_state"] == "not_evaluated"
    assert first["staleness"] == {
        "fresh": 1,
        "aging": 0,
        "stale": 1,
        "dead": 1,
        "unknown": 2,
        "total": 5,
    }
    assert first["clocks"]["snapshot_generated_at"] == "2026-09-02T11:30:00Z"
    assert first["clocks"]["evidence_as_of"] == "2026-08-28T00:00:00Z"
    assert first["clocks"]["evaluated_at"] == "2026-09-02T12:00:00Z"
    assert first["clocks"]["snapshot_age_seconds"] == 1_800
    assert first["clocks"]["evidence_age_seconds"] == 475_200
    for key in (
        "executable",
        "executable_quote",
        "real_money_eligible",
        "can_authorize_order",
        "request_time_collection",
        "request_time_model_fitting",
        "request_time_network",
        "request_time_notary",
        "request_time_broker",
    ):
        assert first[key] is False
    assert len(first["projection_sha256"]) == 64
    unsigned = {
        key: value for key, value in first.items() if key != "projection_sha256"
    }
    canonical = json.dumps(
        unsigned,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    assert first["projection_sha256"] == hashlib.sha256(canonical).hexdigest()
    assert first["attestation"] == {
        "status": "not_evaluated",
        "ed25519_status": "not_evaluated",
        "ots_status": "not_evaluated",
        "bitcoin_anchor_claimed": False,
        "ledger_read": False,
        "reason": "attestation_ledger_not_evaluated_by_this_projection",
        "disclosure": (
            "This cache-only projection does not read or evaluate Seiche's "
            "attestation ledger. Verify stream attestations separately; even a "
            "verified stream attestation is not per-order execution authority."
        ),
    }


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda snapshot: snapshot.update(generated_at="bad"),
            "invalid_snapshot_clock",
        ),
        (
            lambda snapshot: snapshot["engines"]["composite"].update(regime="UNKNOWN"),
            "invalid_composite_reading",
        ),
        (
            lambda snapshot: snapshot["engines"]["composite"].update(
                value=float("nan")
            ),
            "invalid_completed_snapshot",
        ),
        (
            lambda snapshot: snapshot["provenance"][0].update(asof="2026-09-03"),
            "invalid_evidence_clock",
        ),
        (
            lambda snapshot: [row.update(asof=None) for row in snapshot["provenance"]],
            "evidence_clock_unavailable",
        ),
    ],
)
def test_invalid_snapshots_return_typed_unavailable(mutate, reason) -> None:
    snapshot = _snapshot()
    mutate(snapshot)

    payload = trade_safety.project(snapshot, evaluation_at=NOW)

    assert payload["ok"] is False
    assert payload["status"] == "unavailable"
    assert payload["reason"] == reason
    assert payload["regime"] is None
    assert payload["stress_index"] is None
    assert payload["real_money_eligible"] is False
    assert payload["attestation"]["bitcoin_anchor_claimed"] is False


def test_next_day_date_only_effective_value_uses_prior_collection_clock() -> None:
    snapshot = _snapshot()
    snapshot["provenance"] = [
        {
            "source": "fred",
            "mnemonic": "IORB",
            "remote_id": "IORB",
            "freq": "D",
            "asof": "2026-09-03",
            "fetched_at": "2026-09-02T11:20:00Z",
            "staleness": "fresh",
            "freshness_grace_days": 4,
        }
    ]

    payload = trade_safety.project(snapshot, evaluation_at=NOW)

    assert payload["status"] == "available"
    assert payload["clocks"]["evidence_as_of"] == "2026-09-02T11:20:00Z"
    assert payload["clocks"]["evidence_age_seconds"] == 2_400
    assert (
        "bounded_iorb_forward_effective_values_use_their_prior_collection_clock"
        in payload["limitations"]
    )


def test_iorb_holiday_effective_date_uses_prior_collection_clock() -> None:
    snapshot = _snapshot()
    snapshot["generated_at"] = "2026-09-04T22:29:43Z"
    snapshot["provenance"] = [
        {
            "source": "fred",
            "mnemonic": "IORB",
            "remote_id": "IORB",
            "freq": "D",
            "asof": "2026-09-08",
            "fetched_at": "2026-09-04T22:09:26Z",
            "staleness": "fresh",
            "freshness_grace_days": 4,
        }
    ]

    payload = trade_safety.project(
        snapshot,
        evaluation_at=datetime(2026, 9, 4, 22, 30, tzinfo=UTC),
    )

    assert payload["status"] == "available"
    assert payload["clocks"]["evidence_as_of"] == "2026-09-04T22:09:26Z"
    assert payload["clocks"]["evidence_age_seconds"] == 1_234
    assert payload["clocks"]["snapshot_age_seconds"] == 17


def test_iorb_fourteen_day_effective_date_boundary_is_bounded() -> None:
    snapshot = _snapshot()
    snapshot["generated_at"] = "2026-09-04T22:29:43Z"
    snapshot["provenance"] = [
        {
            "source": "fred",
            "mnemonic": "IORB",
            "remote_id": "IORB",
            "freq": "D",
            "asof": "2026-09-18",
            "fetched_at": "2026-09-04T22:09:26Z",
            "staleness": "fresh",
            "freshness_grace_days": 4,
        }
    ]

    payload = trade_safety.project(
        snapshot,
        evaluation_at=datetime(2026, 9, 4, 22, 30, tzinfo=UTC),
    )

    assert payload["status"] == "available"
    assert payload["clocks"]["evidence_as_of"] == "2026-09-04T22:09:26Z"


@pytest.mark.parametrize(
    "updates",
    [
        {"asof": "2026-09-19"},
        {"source": "other"},
        {"mnemonic": "SOFR"},
        {"remote_id": "SOFR"},
        {"freq": "W"},
        {"asof": "2026-09-08T00:00:00Z"},
        {"asof": "2026-W37-2"},
        {"fetched_at": None},
        {"fetched_at": "bad"},
        {"fetched_at": "2026-09-04"},
        {"fetched_at": "2026-09-04T22:09:26"},
        {"fetched_at": "2026-09-04T22:29:44Z"},
        {"fetched_at": "2026-08-20T22:09:26Z"},
        {"source": "other", "clock_role": "scheduled_effective"},
    ],
)
def test_other_iorb_forward_effective_rows_still_fail_closed(
    updates: dict[str, str | None],
) -> None:
    snapshot = _snapshot()
    snapshot["generated_at"] = "2026-09-04T22:29:43Z"
    row = {
        "source": "fred",
        "mnemonic": "IORB",
        "remote_id": "IORB",
        "freq": "D",
        "asof": "2026-09-08",
        "fetched_at": "2026-09-04T22:09:26Z",
        "staleness": "fresh",
        "freshness_grace_days": 4,
    }
    row.update(updates)
    if row["fetched_at"] is None:
        row.pop("fetched_at")
    snapshot["provenance"] = [row]

    payload = trade_safety.project(
        snapshot,
        evaluation_at=datetime(2026, 9, 4, 22, 30, tzinfo=UTC),
    )

    assert payload["status"] == "unavailable"
    assert payload["reason"] == "invalid_evidence_clock"


@pytest.mark.parametrize(
    ("asof", "fetched_at"),
    [
        ("2026-09-04", "2026-09-02T11:20:00Z"),
        ("2026-09-03T00:00:00Z", "2026-09-02T11:20:00Z"),
        ("2026-09-03", None),
        ("2026-09-03", "bad"),
        ("2026-09-03", "2026-09-02"),
        ("2026-09-03", "2026-09-02T11:31:00Z"),
    ],
)
def test_other_future_effective_values_still_fail_closed(
    asof: str, fetched_at: str | None
) -> None:
    snapshot = _snapshot()
    row = snapshot["provenance"][0]
    row["asof"] = asof
    if fetched_at is None:
        row.pop("fetched_at", None)
    else:
        row["fetched_at"] = fetched_at

    payload = trade_safety.project(snapshot, evaluation_at=NOW)

    assert payload["status"] == "unavailable"
    assert payload["reason"] == "invalid_evidence_clock"


def test_rights_boundary_is_rechecked_and_poisoned_cache_is_quarantined() -> None:
    snapshot = _snapshot()
    snapshot["provenance"].append(
        {
            "source": "chinamoney",
            "mnemonic": "SHIBOR_ON",
            "asof": "2026-09-01",
            "staleness": "fresh",
        }
    )

    payload = trade_safety.project(snapshot, evaluation_at=NOW)

    assert payload["status"] == "unavailable"
    assert payload["reason"] == "snapshot_rights_validation_failed"
    assert payload["source_snapshot_version"] is None


def test_non_json_cache_container_fails_closed_before_rights_projection() -> None:
    class ConcealedMapping:
        pass

    snapshot = _snapshot()
    snapshot["concealed"] = ConcealedMapping()

    payload = trade_safety.project(snapshot, evaluation_at=NOW)

    assert payload["status"] == "unavailable"
    assert payload["reason"] == "invalid_completed_snapshot"


def test_staleness_is_advanced_from_native_grace_without_refreshing() -> None:
    snapshot = _snapshot()
    snapshot["provenance"][0].update(
        asof="2026-08-30",
        staleness="fresh",
        freshness_grace_days=1,
    )

    payload = trade_safety.project(snapshot, evaluation_at=NOW)

    assert payload["status"] == "available"
    assert payload["staleness"] == {
        "fresh": 0,
        "aging": 0,
        "stale": 2,
        "dead": 1,
        "unknown": 2,
        "total": 5,
    }


def test_cached_fresh_label_without_cadence_is_never_trusted() -> None:
    snapshot = _snapshot()
    row = snapshot["provenance"][0]
    row.update(asof="2020-01-01", staleness="fresh")
    row.pop("freshness_grace_days")

    payload = trade_safety.project(snapshot, evaluation_at=NOW)

    assert payload["status"] == "available"
    assert payload["staleness"]["fresh"] == 0
    assert payload["staleness"]["unknown"] == 3


def test_mcp_and_rest_read_only_the_completed_snapshot(monkeypatch) -> None:
    snapshot = _snapshot()
    reads = []

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 2, 12, 0, tzinfo=tz)

    def completed():
        reads.append("completed")
        return snapshot

    def forbidden_restore(*_args, **_kwargs):
        raise AssertionError(
            "Trade Safety risk context must never restore durable state"
        )

    async def forbidden_build(*_args, **_kwargs):
        raise AssertionError("Trade Safety risk context must never build the board")

    monkeypatch.setattr(assemble, "cached_snapshot", completed)
    monkeypatch.setattr(assemble, "restore_cached_snapshot", forbidden_restore)
    monkeypatch.setattr(assemble, "snapshot", forbidden_build)
    monkeypatch.setattr(mcp_server, "datetime", FrozenDateTime)
    monkeypatch.setattr(api, "datetime", FrozenDateTime)

    tool = mcp_server.tool_trade_safety_risk_context({}, True)
    with TestClient(api.app) as client:
        response = client.get("/api/trade-safety/risk-context")

    assert tool["status"] == "available"
    assert response.status_code == 200
    assert response.json()["projection_sha256"] == tool["projection_sha256"]
    assert response.headers["x-seiche-execution-authority"] == "none"
    assert response.headers["cache-control"].startswith("public, max-age=30")
    assert reads == ["completed", "completed"]


def test_rest_fails_closed_without_a_completed_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "_get_in_memory_completed_snapshot", lambda: None)

    with TestClient(api.app) as client:
        response = client.get("/api/trade-safety/risk-context")

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["retry-after"] == "30"
    assert response.headers["x-seiche-execution-authority"] == "none"
    assert response.json()["reason"] == "no_completed_snapshot"


def test_risk_context_is_in_public_rest_and_mcp_discovery() -> None:
    discovery = api.api_index()
    openapi = api._public_openapi_document()
    tools = mcp_server.dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, public=True
    )["result"]["tools"]

    assert discovery["rest"]["trade_safety_risk_context"] == (
        "/api/trade-safety/risk-context"
    )
    route = openapi["paths"]["/api/trade-safety/risk-context"]["get"]
    assert set(route["responses"]) == {"200", "503"}
    schema = route["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema is mcp_server.OUTPUT_SCHEMAS["trade_safety_risk_context"]
    descriptor = next(
        tool for tool in tools if tool["name"] == "trade_safety_risk_context"
    )
    assert descriptor["inputSchema"]["additionalProperties"] is False
    assert descriptor["annotations"]["readOnlyHint"] is True
    assert "not evaluate stream attestations" in descriptor["description"]


def test_risk_context_is_in_the_edge_exact_path_allowlist() -> None:
    caddy = (ROOT / "ops" / "Caddyfile").read_text(encoding="utf-8")
    public = caddy[caddy.index("    @public {") : caddy.index("    handle @public {")]

    assert "method GET HEAD" in public
    assert "/api/trade-safety/risk-context" in public


def test_output_schema_requires_every_authority_fence_and_rejects_extensions() -> None:
    schema = mcp_server.OUTPUT_SCHEMAS["trade_safety_risk_context"]
    available = trade_safety.project(_snapshot(), evaluation_at=NOW)
    authority_fields = (
        "executable",
        "executable_quote",
        "can_authorize_order",
        "request_time_collection",
        "request_time_model_fitting",
        "request_time_network",
        "request_time_notary",
        "request_time_broker",
    )
    success_arms = [
        arm
        for arm in schema["anyOf"]
        if arm.get("properties", {}).get("schema", {}).get("const")
        == "seiche.risk-context.v1"
    ]
    assert len(success_arms) == 2
    assert schema["additionalProperties"] is False
    for arm in success_arms:
        assert set(authority_fields) <= set(arm["required"])

    try:
        import jsonschema
    except ModuleNotFoundError:
        return
    validator = jsonschema.Draft202012Validator(schema)
    validator.validate(available)
    validator.validate(trade_safety.unavailable("no_completed_snapshot"))
    for field in authority_fields:
        incomplete = dict(available)
        incomplete.pop(field)
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(incomplete)

    widened = {**available, "can_execute": True}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(widened)
