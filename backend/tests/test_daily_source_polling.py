"""Slow source publication must not postpone a daily check for new releases."""
from seiche.config import BIS_SERIES, OFR_SERIES


def test_slow_official_sources_are_checked_daily_without_changing_observation_frequency():
    monthly = [spec for spec in OFR_SERIES if spec.mnemonic.startswith("MMF_")]
    assert len(monthly) == 4 and len(BIS_SERIES) == 6
    assert all(spec.ttl_minutes <= 1440 for spec in monthly + BIS_SERIES)
    assert all(spec.freq == "M" for spec in monthly)
    assert all(spec.freq == "QL" for spec in BIS_SERIES)
