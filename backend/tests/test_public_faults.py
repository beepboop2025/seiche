from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from seiche import api, store
from seiche.collectors import CollectorRunStatus, CollectorSupervisor
from seiche.markets.materialize import PUBLIC_SNAPSHOT_VISIBILITY
from seiche.public_faults import (
    project_public_fault,
    safe_failure_envelope,
    sanitize_fault,
    sanitize_public_fault_payload,
)
from seiche.sources.base import ObservationBatch


_SECRET = "issued-secret-7UqQ9"
_CREDENTIAL_URL = f"https://operator:{_SECRET}@official.example/data?api_key={_SECRET}"
_HTML = f"<script>Bearer {_SECRET}</script>"


def _assert_redacted(value) -> None:
    serialized = json.dumps(value, default=str, sort_keys=True)
    assert _SECRET not in serialized
    assert "operator:" not in serialized
    assert "api_key" not in serialized
    assert "Bearer" not in serialized
    assert "<script>" not in serialized
    assert "/Users/" not in serialized


def test_sanitizer_never_formats_exception_text_or_follows_its_chain() -> None:
    class HostileTimeout(TimeoutError):
        def __str__(self) -> str:
            raise AssertionError("sanitizer formatted an arbitrary exception")

    cause = RuntimeError(f"{_CREDENTIAL_URL} {_HTML}")
    fault = HostileTimeout()
    fault.__cause__ = cause
    fault.__context__ = cause

    assert sanitize_fault(fault, status="FAILED") == (
        "TIMEOUT: source collection timed out"
    )
    _assert_redacted(sanitize_fault(fault, status="FAILED"))


def test_typed_failure_envelope_uses_exception_type_without_diagnostics() -> None:
    fault = TimeoutError(f"{_CREDENTIAL_URL} /Users/operator/private.env {_HTML}")

    envelope = safe_failure_envelope(fault)

    assert envelope == {
        "ok": False,
        "status": "FAILED",
        "category": "TIMEOUT",
        "reason": "source collection timed out",
    }
    _assert_redacted(envelope)


def test_recursive_boundary_contains_hostile_nested_reasons_and_details() -> None:
    payload = {
        "engines": {
            "nested": {
                "ok": False,
                "reason": f"RuntimeError: {_CREDENTIAL_URL} {_HTML}",
            },
            "legacy": {
                "status": "FAILED",
                "detail": f"/Users/operator/private.env?token={_SECRET}",
            },
        }
    }

    sanitized = sanitize_public_fault_payload(payload)

    for failure in sanitized["engines"].values():
        assert failure["ok"] is False
        assert failure["status"] == "FAILED"
        assert failure["category"] == "INTERNAL_ERROR"
        assert failure["reason"] == "collector failed"
    assert sanitized["engines"]["legacy"]["detail"] == "collector failed"
    _assert_redacted(sanitized)


def test_public_projection_allowlists_ids_timestamps_and_fixed_details() -> None:
    projected = project_public_fault(
        {
            "market_id": "US-USD",
            "source": _CREDENTIAL_URL,
            "status": "FAILED<script>",
            "detail": f"RuntimeError: {_CREDENTIAL_URL} {_HTML}",
            "finished_at": "2026-08-21T10:00:00+00:00",
            "traceback": _HTML,
            "response_body": _HTML,
        }
    )

    assert projected == {
        "source": "unknown_source",
        "status": "FAILED",
        "category": "INTERNAL_ERROR",
        "detail": "collector failed",
        "market_id": "US-USD",
        "finished_at": "2026-08-21T10:00:00+00:00",
    }
    _assert_redacted(projected)


def test_recursive_public_boundary_strips_fault_diagnostics_and_credential_urls() -> (
    None
):
    payload = {
        "faults": [
            {
                "market_id": "US-USD",
                "source": "fred_daily",
                "status": "FAILED",
                "detail": f"RuntimeError: {_CREDENTIAL_URL} {_HTML}",
                "traceback": _HTML,
            }
        ],
        "adapters": [
            {
                "adapter_id": "fred_daily",
                "classification": "official_open",
                "redistribution_status": "allowed",
                "expected_cadence": "P1D",
                "last_run_status": "FAILED",
                "fault": f"RuntimeError: {_CREDENTIAL_URL} {_HTML}",
                "source_url": _CREDENTIAL_URL,
                "debug": _HTML,
            }
        ],
    }

    sanitized = sanitize_public_fault_payload(payload)

    assert sanitized["faults"] == [
        {
            "source": "fred_daily",
            "status": "FAILED",
            "category": "INTERNAL_ERROR",
            "detail": "collector failed",
            "market_id": "US-USD",
        }
    ]
    adapter = sanitized["adapters"][0]
    assert adapter["adapter_id"] == "fred_daily"
    assert adapter["expected_cadence"] == "P1D"
    assert adapter["fault"] == "INTERNAL_ERROR: collector failed"
    assert "source_url" not in adapter
    assert "debug" not in adapter
    _assert_redacted(sanitized)


@pytest.mark.asyncio
async def test_collector_sanitizes_before_run_persistence() -> None:
    persisted: list[dict] = []

    class FailingAdapter:
        market_id = "US-USD"
        adapter_id = "fred_daily"

        async def collect(self) -> ObservationBatch:
            try:
                raise ValueError(f"{_CREDENTIAL_URL} {_HTML}")
            except ValueError as cause:
                raise RuntimeError(f"{_CREDENTIAL_URL} {_HTML}") from cause

    async def no_sleep(_seconds: float) -> None:
        return None

    supervisor = CollectorSupervisor(
        run_writer=persisted.append,
        sleep=no_sleep,
    )
    supervisor.register(FailingAdapter())

    run = (
        await supervisor.run_due(
            now=datetime(2026, 8, 21, 10, tzinfo=UTC),
            force=True,
        )
    )[0]

    assert run.status is CollectorRunStatus.FAILED
    assert run.fault == "INTERNAL_ERROR: collector failed"
    assert persisted[0]["fault"] == "INTERNAL_ERROR: collector failed"
    _assert_redacted(run.to_dict())
    _assert_redacted(persisted)


def test_api_resanitizes_legacy_snapshot_and_collector_fault_rows(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "fault-boundary.sqlite")
    now = datetime.now(UTC).replace(microsecond=0)
    store.save_collector_run(
        {
            "market_id": "US-USD",
            "adapter_id": "fred_daily",
            "status": "FAILED",
            "started_at": now.isoformat(),
            "finished_at": now.isoformat(),
            "observations_written": 0,
            "attempts": 1,
            "next_due": (now + timedelta(days=1)).isoformat(),
            "fault": f"RuntimeError: {_CREDENTIAL_URL} {_HTML}",
        }
    )
    legacy_snapshot = {
        "payload": {
            "visibility": PUBLIC_SNAPSHOT_VISIBILITY,
            "faults": [
                {
                    "market_id": "US-USD",
                    "source": "fred_daily",
                    "status": "FAILED",
                    "detail": f"RuntimeError: {_CREDENTIAL_URL} {_HTML}",
                }
            ],
        }
    }

    snapshot_payload = api._public_snapshot_payload(legacy_snapshot)
    coverage = api.coverage_v2(api.Response())
    us = next(item for item in coverage["markets"] if item["market_id"] == "US-USD")

    assert snapshot_payload["faults"][0]["category"] == "INTERNAL_ERROR"
    assert us["faults"][0]["source"] == "fred_daily"
    assert us["faults"][0]["category"] == "INTERNAL_ERROR"
    _assert_redacted(snapshot_payload)
    _assert_redacted(coverage)


def test_atlas_repository_failures_do_not_leak_exception_text_to_logs_or_wire(
    monkeypatch, caplog
) -> None:
    class FailingRepository:
        def load_observations_as_of(self, *_args, **_kwargs):
            raise RuntimeError(f"{_CREDENTIAL_URL} {_HTML}")

        def latest_collector_runs(self):
            raise RuntimeError(f"{_CREDENTIAL_URL} {_HTML}")

    monkeypatch.setattr(api, "get_repository", lambda: FailingRepository())
    caplog.set_level("ERROR", logger="seiche.api")

    payload = api.global_money_markets_v2(api.Response())

    assert payload["status"] == "PARTIAL"
    assert payload["read_faults"]
    _assert_redacted(payload)
    _assert_redacted(caplog.text)
