"""Current auctions and swap results refresh hourly without altering as-ofs."""
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from seiche import store
from seiche.config import PD_POSITION_SERIES
from seiche.sources import base, fiscaldata, nyfed


@pytest.mark.parametrize("kind", ["auctions", "upcoming", "fx_swaps", "pd_positions"])
@pytest.mark.parametrize("age_minutes,status,requests", [(59, 200, 0), (61, 200, 1), (61, 503, 1)])
@pytest.mark.asyncio
async def test_hourly_expiry_uses_canonical_store_and_preserves_failure_clock(monkeypatch, tmp_path, kind, age_minutes, status, requests):
    now = datetime(2026, 10, 2, 20, tzinfo=UTC)
    instant = [now - timedelta(minutes=age_minutes)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant[0].astimezone(tz or UTC)

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "cache.sqlite")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "datetime", Clock)
    monkeypatch.setattr(base, "datetime", Clock)
    monkeypatch.setattr(nyfed, "datetime", Clock)
    keys = {"auctions": "fiscal_auctions", "upcoming": "fiscal_upcoming", "fx_swaps": "nyfed_fx_swaps", "pd_positions": "nyfed_pd_positions"}
    if kind == "pd_positions":
        payload = {"series": {key: [["2026-09-23", "1000"]] for key in PD_POSITION_SERIES}}
        body = {"pd": {"timeseries": [{"asofdate": "2026-09-23", "value": "1000"}]}}
        fetch = nyfed.fetch_pd_positions
    elif kind == "fx_swaps":
        payload = {"ops": [{"trade_date": "2026-09-30", "counterparty": "European Central Bank", "amount_m": 10.0}]}
        body = {"fxSwaps": {"operations": [{"tradeDate": "2026-09-30", "counterparty": "European Central Bank", "amount": "10000000"}]}}
        fetch = nyfed.fetch_fx_swaps
    else:
        # Treasury's future auction dates remain announcements, not a new
        # completed observation. Keep the same source row on successful check.
        auction_day = (datetime.now(UTC) + timedelta(days=7)).date().isoformat()
        issue_day = (datetime.now(UTC) + timedelta(days=9)).date().isoformat()
        row = {"auction_date": auction_day, "issue_date": issue_day, "security_type": "Bill"}
        payload = {"rows": [row]}
        body = {"data": [row], "meta": {"total-pages": 1}}
        fetch = fiscaldata.fetch_auctions if kind == "auctions" else fiscaldata.fetch_upcoming_auctions
    fetched = instant[0].isoformat(timespec="seconds")
    store.save_blob(keys[kind], payload | {"fetched_at": fetched})
    instant[0] = now
    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(status, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        result = await fetch(client)
    assert len(calls) == (8 if kind == "pd_positions" and requests and status == 200 else requests)
    assert result["fetched_at"] == (now.isoformat(timespec="seconds") if requests and status == 200 else fetched)
    persisted = store.load_blob(keys[kind])
    assert persisted["fetched_at"] == result["fetched_at"]
    if kind == "pd_positions":
        assert all(series.index.max().date().isoformat() == "2026-09-23" for series in result["positions"].values())
    elif kind == "fx_swaps":
        assert result["ops"][0]["trade_date"] == "2026-09-30"
    else:
        assert result[kind].iloc[0]["auction_date"] == auction_day
