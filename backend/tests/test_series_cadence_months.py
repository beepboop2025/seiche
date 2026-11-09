"""Every native cadence a market pack declares ages without error (monthly packs used to 500)."""
import pytest

from seiche.markets import atlas
from seiche.markets.registry import default_registry


@pytest.mark.parametrize("value, seconds", [
    ("PT1H", 3600), ("P1D", 86400), ("P1W", 7 * 86400), ("P2W", 14 * 86400),
    ("P1M", 31 * 86400), ("P3M", 93 * 86400), ("P1Y", 366 * 86400),
])
def test_cadence_seconds_cover_every_iso_unit_in_use(value, seconds):
    assert atlas._cadence_seconds(value) == seconds


def test_every_registered_adapter_cadence_is_understood():
    packs = default_registry().list()
    cadences = {adapter.expected_cadence for pack in packs for adapter in pack.adapter_map.values()}
    assert "P1M" in cadences  # the monthly country packs that used to fail
    for cadence in cadences:
        assert atlas._cadence_seconds(cadence) > 0, cadence
