"""Source failures recorded by adapters must not be counted again by assembly."""
from unittest.mock import AsyncMock

import pytest

from seiche import assemble
from seiche.sources.base import SourceFault


CALLS = {
    "fred": ("fetch_many",), "eia_petroleum": ("fetch_many",),
    "ofr": ("fetch_many",), "llamahacks": ("fetch_all",),
    "windfetch": ("fetch_all",), "ecb": ("fetch_many",),
    "ecb_fx": ("fetch",), "cbuae_fx": ("fetch",),
    "cbuae_donia": ("fetch",), "boj": ("fetch_many",),
    "bis": ("fetch_many",), "crypto": ("fetch_all",),
    "nyfed": ("fetch_secured_rates", "fetch_srf_ops", "fetch_pd_positions", "fetch_fx_swaps"),
    "nyfed_rde": ("fetch_rde",), "fiscaldata": ("fetch_tga_daily", "fetch_auctions", "fetch_upcoming_auctions"),
    "td_auctions": ("fetch_mspd_maturities",), "cftc": ("fetch_tff_ust", "fetch_disaggregated_commodities"),
    "palimpsest": ("fetch_all",), "fedtext": ("fetch_all",), "gdelt": ("fetch_all",),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("already_recorded", [False, True])
@pytest.mark.parametrize("earlier_detail", [None, "a distinct earlier failure"])
async def test_adapter_fault_is_recorded_once_without_hiding_other_failures(
    monkeypatch, already_recorded, earlier_detail
):
    for module, names in CALLS.items():
        for name in names:
            monkeypatch.setattr(getattr(assemble, module), name, AsyncMock(return_value={}))
    detail = "HTTPError: HTTP Error 403: Forbidden"
    expected = []
    if earlier_detail is not None:
        expected.append({"source": "cbuae_donia", "detail": earlier_detail})
    expected.append({"source": "cbuae_donia", "detail": detail})

    async def fail(client, faults):
        if earlier_detail is not None:
            faults.append({"source": "cbuae_donia", "detail": earlier_detail})
        if already_recorded:
            faults.append({"source": "cbuae_donia", "detail": detail})
        raise SourceFault("cbuae_donia", detail)

    monkeypatch.setattr(assemble.cbuae_donia, "fetch", fail)
    sources, faults = await assemble._gather_sources()
    assert faults == expected
    assert "cbuae_donia" not in sources
    assert sources["ecb_fx"] == {}


@pytest.mark.asyncio
async def test_successful_adapter_keeps_its_reported_degradation(monkeypatch):
    for module, names in CALLS.items():
        for name in names:
            monkeypatch.setattr(getattr(assemble, module), name, AsyncMock(return_value={}))
    existing = {"source": "cbuae_donia", "detail": "fresh fetch failed; cached series retained"}

    async def cached(client, faults):
        faults.append(existing)
        return {"DONIA": "validated cached series"}

    monkeypatch.setattr(assemble.cbuae_donia, "fetch", cached)
    sources, faults = await assemble._gather_sources()
    assert faults == [existing]
    assert sources["cbuae_donia"] == {"DONIA": "validated cached series"}
