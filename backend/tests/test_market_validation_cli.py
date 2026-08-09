from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from types import SimpleNamespace

import pytest

from seiche import cli
from seiche.markets.base import ValidationCheck


class _Registry:
    def __init__(self, *market_ids: str) -> None:
        self._packs = tuple(SimpleNamespace(market_id=item) for item in market_ids)

    def list(self):
        return self._packs


class _ValidationReport:
    def __init__(self, market_id: str, exit_code: int) -> None:
        self.market_id = market_id
        self.exit_code = exit_code

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "seiche.market-validation-report.v1",
            "market_id": self.market_id,
        }


def test_market_validate_parses_options_and_hard_failure_outranks_pending(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    from seiche.markets import registry as registry_module
    from seiche.markets import validation as validation_module

    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        registry_module,
        "default_registry",
        lambda: _Registry("US-USD", "IN-INR"),
    )

    def fake_validate(market_id: str, **kwargs):
        calls.append((market_id, kwargs))
        return _ValidationReport(market_id, 2 if market_id == "IN-INR" else 1)

    monkeypatch.setattr(validation_module, "validate_market", fake_validate)
    evidence_dir = tmp_path / "evidence"
    monkeypatch.setenv("SEICHE_VALIDATION_DIR", str(evidence_dir))
    monkeypatch.setattr(
        "sys.argv",
        [
            "seiche",
            "market-validate",
            "--check",
            "schema_and_units",
            "--as-of",
            "2026-08-09T12:00:00+05:30",
            "--minimum-forward-records",
            "250",
            "--minimum-forward-span-days",
            "365",
        ],
    )

    with pytest.raises(SystemExit) as stopped:
        cli.main()

    assert stopped.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["evidence_dir"] == str(evidence_dir)
    assert payload["summary"] == {
        "exit_code": 1,
        "failed": 1,
        "market_count": 2,
        "passed": 0,
        "pending": 1,
    }
    assert [item[0] for item in calls] == ["IN-INR", "US-USD"]
    for _, options in calls:
        assert options["checks"] == (ValidationCheck.SCHEMA_AND_UNITS,)
        assert options["as_of"] == datetime(2026, 8, 9, 6, 30, tzinfo=UTC)
        assert options["minimum_forward_records"] == 250
        assert options["minimum_forward_span_days"] == 365


def test_promotion_report_is_read_only_and_missing_evidence_is_pending(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    from seiche.markets import registry as registry_module
    from seiche.markets import validation as validation_module

    monkeypatch.setattr(
        registry_module,
        "default_registry",
        lambda: _Registry("US-USD"),
    )
    called: list[str] = []

    def fake_promotion_report(market_id: str, **_kwargs):
        called.append(market_id)
        return {
            "schema": "seiche.market-promotion-report.v1",
            "market_id": market_id,
            "eligible": False,
            "checks": {
                "schema_and_units": {"status": "PASS"},
                "forward_paper_record": {"status": "MISSING"},
            },
        }

    monkeypatch.setattr(
        validation_module,
        "promotion_report",
        fake_promotion_report,
    )
    monkeypatch.setattr(
        validation_module,
        "validate_market",
        lambda *_args, **_kwargs: pytest.fail("promotion report reran validation"),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "seiche",
            "market-validate",
            "--market",
            "us-usd",
            "--promotion-report",
            "--evidence-dir",
            str(tmp_path),
        ],
    )

    with pytest.raises(SystemExit) as stopped:
        cli.main()

    assert stopped.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "promotion_report"
    assert payload["summary"]["pending"] == 1
    assert called == ["US-USD"]


def test_one_market_exception_is_json_failure_and_siblings_continue(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    from seiche.markets import registry as registry_module
    from seiche.markets import validation as validation_module

    monkeypatch.setattr(
        registry_module,
        "default_registry",
        lambda: _Registry("JP-JPY", "IN-INR"),
    )

    def fake_validate(market_id: str, **_kwargs):
        if market_id == "JP-JPY":
            raise RuntimeError("collector evidence malformed")
        return _ValidationReport(market_id, 0)

    monkeypatch.setattr(validation_module, "validate_market", fake_validate)
    monkeypatch.setattr(
        "sys.argv",
        ["seiche", "market-validate", "--evidence-dir", str(tmp_path)],
    )

    with pytest.raises(SystemExit) as stopped:
        cli.main()

    assert stopped.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["passed"] == 1
    assert payload["summary"]["failed"] == 1
    errors = [
        item["report"]
        for item in payload["markets"]
        if item["report"]["schema"] == "seiche.market-validation-error.v1"
    ]
    assert errors == [
        {
            "schema": "seiche.market-validation-error.v1",
            "market_id": "JP-JPY",
            "error": {
                "type": "RuntimeError",
                "message": "market validation failed; inspect protected service logs",
            },
        }
    ]


def test_market_validation_error_redacts_a_dsn(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    from seiche.markets import registry as registry_module
    from seiche.markets import validation as validation_module

    secret_dsn = "postgresql://operator:do-not-log@db.internal/seiche"
    monkeypatch.setattr(
        registry_module,
        "default_registry",
        lambda: _Registry("US-USD"),
    )
    monkeypatch.setattr(
        validation_module,
        "validate_market",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError(f"connection failed for {secret_dsn}")
        ),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["seiche", "market-validate", "--evidence-dir", str(tmp_path)],
    )

    with pytest.raises(SystemExit) as stopped:
        cli.main()

    assert stopped.value.code == 1
    serialized = capsys.readouterr().out
    assert secret_dsn not in serialized
    assert "do-not-log" not in serialized
    assert "market validation failed" in serialized


def test_validation_timestamp_and_thresholds_fail_closed() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="explicit UTC offset"):
        cli._aware_iso_timestamp("2026-08-09T12:00:00")
    with pytest.raises(argparse.ArgumentTypeError, match="non-negative"):
        cli._nonnegative_int("-1")
    assert cli._aware_iso_timestamp("2026-08-09T12:00:00Z") == datetime(
        2026,
        8,
        9,
        12,
        tzinfo=UTC,
    )
