import asyncio
from unittest.mock import AsyncMock

import pytest

from seiche.sources import crypto


def test_failed_peg_storage_preserves_completed_candles(monkeypatch):
    candles = {"BTC-USD": object(), "ETH-USD": object()}

    async def read_candle(_client, product):
        return candles[product]

    monkeypatch.setattr(crypto, "fetch_candles", read_candle)
    monkeypatch.setattr(crypto, "fetch_stablecoins", AsyncMock(side_effect=RuntimeError("private storage path")))
    faults = []
    result = asyncio.run(crypto.fetch_all(None, list(candles), faults))

    assert result["candles"] == {"BTC_USD": candles["BTC-USD"], "ETH_USD": candles["ETH-USD"]}
    assert "stable" not in result
    assert faults == [{"source": "crypto", "component": "stable", "detail": "collector failed", "category": "INTERNAL_ERROR"}]


def test_failed_candle_preserves_other_product_and_stable_board(monkeypatch):
    async def read_candle(_client, product):
        if product == "BTC-USD":
            raise TimeoutError("private endpoint")
        return "dated ETH candle"

    monkeypatch.setattr(crypto, "fetch_candles", read_candle)
    monkeypatch.setattr(crypto, "fetch_stablecoins", AsyncMock(return_value={"fetched_at": "original clock"}))
    faults = []
    result = asyncio.run(crypto.fetch_all(None, ["BTC-USD", "ETH-USD"], faults))

    assert result["candles"] == {"ETH_USD": "dated ETH candle"}
    assert result["stable"] == {"fetched_at": "original clock"}
    assert len(faults) == 1 and faults[0]["component"] == "BTC_USD" and faults[0]["category"] == "TIMEOUT"
    assert "private endpoint" not in str(faults)


def test_unreported_unexpected_failure_still_raises(monkeypatch):
    monkeypatch.setattr(crypto, "fetch_stablecoins", AsyncMock(side_effect=RuntimeError("store")))
    with pytest.raises(RuntimeError, match="store"):
        asyncio.run(crypto.fetch_all(None, []))


def test_cancellation_propagates_with_a_fault_sink(monkeypatch):
    monkeypatch.setattr(crypto, "fetch_stablecoins", AsyncMock(side_effect=asyncio.CancelledError()))
    faults = []
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(crypto.fetch_all(None, [], faults))
    assert faults == []
