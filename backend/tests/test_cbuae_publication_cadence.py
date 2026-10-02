"""CBUAE reference ages never invent a verified publication calendar."""

from datetime import datetime

import pytest

from seiche.sources.publication import cbuae_fx_freshness, publication_freshness


@pytest.mark.parametrize("asof,status,age", [
    ("2026-10-02", "fresh", 0), ("2026-10-01", "aging", 1),
    ("2026-09-30", "stale", 2), ("2026-01-01", "stale", 274),
    (None, "dead", None), ("invalid", "unknown", None),
    ("2026-10-03", "unknown", None), ("2017-01-01", "unknown", None),
])
def test_daily_calendar_age(asof, status, age):
    result = cbuae_fx_freshness(asof, now=datetime.fromisoformat("2026-10-02T18:00:00+00:00"))
    assert result["staleness"] == status
    assert result["age_days"] == age
    assert result["publication_schedule"]["actual_published_at"] is None
    assert result["publication_schedule"]["latest_due_at"] is None
    assert result["publication_schedule"]["missed_publication_opportunities"] is None
    assert result["publication_schedule"]["clock_precision"] == "unknown"


def test_dubai_midnight_controls_age_without_weekend_exemption():
    # Friday 20:00 UTC is Saturday midnight in Dubai.
    result = cbuae_fx_freshness("2026-10-02", now=datetime.fromisoformat("2026-10-02T20:00:00+00:00"))
    assert result["staleness"] == "aging"
    assert result["age_days"] == 1


@pytest.mark.parametrize("currency", ["USD", "INR", "EUR", "GBP", "JPY", "CHF", "SGD"])
def test_registered_series_use_same_source_policy(currency):
    now = datetime.fromisoformat("2026-10-02T18:00:00+00:00")
    assert publication_freshness("cbuae_fx", f"VAT/AED_PER_{currency}", "D", "2026-10-02", now=now) == cbuae_fx_freshness("2026-10-02", now=now)


@pytest.mark.parametrize("source,remote,freq", [
    ("cbuae_fx", "VAT/AED_PER_INR", "W"), ("cbuae_fx", "DONIA", "D"),
    ("other", "VAT/AED_PER_INR", "D"), ("cbuae_fx", "VAT/AED_PER_CNY", "D"),
])
def test_policy_does_not_leak_to_unrelated_identities(source, remote, freq):
    assert publication_freshness(source, remote, freq, "2026-10-02", now=datetime.fromisoformat("2026-10-02T18:00:00+00:00")) is None


def test_naive_evaluation_clock_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        cbuae_fx_freshness("2026-10-02", now=datetime(2026, 10, 2))
