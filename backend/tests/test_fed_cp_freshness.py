"""The Fed CP release suppresses thin tenors; that gap is not a dead collector."""
from datetime import UTC, datetime

from seiche.sources import publication

NOW = datetime(2026, 10, 31, 18, tzinfo=UTC)


def test_suppressed_cp_tenor_ages_honestly_but_is_not_dead_before_ninety_days():
    result = publication.publication_freshness("fred", "DCPN3M", "D", "2026-10-13", now=NOW)
    assert result["freshness_policy"] == "fed-cp-intermittent-v1"
    assert result["age_days"] == 18 and result["staleness"] == "stale"
    assert "not a collector fault" in result["freshness_basis"]
    assert publication.publication_freshness("fred", "DCPF3M", "D", "2026-10-29", now=NOW)["staleness"] == "fresh"
    assert publication.publication_freshness("fred", "DCPN3M", "D", "2026-07-31", now=NOW)["staleness"] == "dead"


def test_other_daily_fred_series_do_not_take_the_cp_policy():
    other = publication.publication_freshness("fred", "DCPN30", "D", "2026-10-13", now=NOW)
    assert other is None or other.get("freshness_policy") != "fed-cp-intermittent-v1"
