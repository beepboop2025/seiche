"""Public alert recording cannot send or consume subscriber notifications."""
import sqlite3

import pytest

from seiche import alerts


def snapshot(regime="STRAIN", minute="00"):
    return {"generated_at": f"2026-10-26T16:{minute}:00+00:00",
            "engines": {"composite": {"regime": regime, "value": 50}},
            "deep": {}, "headline": {}}


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "alerts.sqlite"
    monkeypatch.setattr(alerts, "DB_PATH", path)
    monkeypatch.setattr(alerts, "ALERT_RULES", {"regime_change": True})
    def forbidden(*_args):
        raise AssertionError("Public history must never notify")
    for name in ("_notify_macos", "_notify_webhook", "_notify_telegram", "_notify_subscribers"):
        monkeypatch.setattr(alerts, name, forbidden)
    return path


def test_public_history_records_real_states_once_with_original_clock(ledger):
    first = alerts.snapshot_log(snapshot())
    assert first["status"] == "ready"
    assert len(first["alerts"]) == 1
    assert first["alerts"][0]["state"] == "STRAIN"
    again = alerts.snapshot_log(snapshot(minute="15"))
    assert again["alerts"] == first["alerts"]
    assert again["evaluated_at"] == "2026-10-26T16:15:00+00:00"
    changed = alerts.snapshot_log(snapshot("STRESS", "30"))
    assert len(changed["alerts"]) == 2
    assert changed["alerts"][1]["fired_at"] == "2026-10-26T16:00:00+00:00"


def test_public_record_does_not_consume_notification_dedupe(ledger, monkeypatch):
    alerts.snapshot_log(snapshot())
    delivered = []
    for name in ("_notify_macos", "_notify_webhook", "_notify_telegram", "_notify_subscribers"):
        monkeypatch.setattr(alerts, name, lambda *args: delivered.append(args))
    assert len(alerts.evaluate(snapshot())) == 1
    assert len(delivered) == 4
    assert alerts.evaluate(snapshot()) == []
    assert len(delivered) == 4


def test_preview_cannot_create_or_advance_history(ledger):
    assert alerts.snapshot_log(snapshot(), record=False)["status"] == "unavailable"
    assert not ledger.exists()
    first = alerts.snapshot_log(snapshot())
    assert alerts.snapshot_log(snapshot("STRESS", "30"), record=False) == first


def test_no_matching_rule_is_ready_empty_not_unavailable(ledger, monkeypatch):
    monkeypatch.setattr(alerts, "ALERT_RULES", {})
    result = alerts.snapshot_log(snapshot())
    assert result["status"] == "ready"
    assert result["alerts"] == []
    assert result["evaluated_at"] == snapshot()["generated_at"]


@pytest.mark.parametrize("change", [{"replay": True}, {"generated_at": "2026-10-26T16:00:00"}])
def test_undated_or_replayed_snapshot_cannot_create_public_events(ledger, change):
    with pytest.raises(ValueError):
        alerts.snapshot_log({**snapshot(), **change})
    assert not ledger.exists()


def test_storage_errors_are_not_reported_as_an_empty_success(ledger):
    ledger.write_bytes(b"not a sqlite database")
    with pytest.raises(sqlite3.DatabaseError):
        alerts.snapshot_log(snapshot())
