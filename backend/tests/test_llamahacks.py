"""DeFiLlama hacks collector: offline parsing on a recorded synthetic payload,
TTL/stale-serve semantics with a mocked fetch, and Time Machine truncation.
No network — the HTTP layer (llamahacks._get) and the blob store are mocked."""

from __future__ import annotations

import asyncio

import pandas as pd
import pytest

from seiche.sources import llamahacks
from seiche.sources.base import Series, SourceFault

pytestmark = pytest.mark.limit_memory("256 MB")

# Recorded shape of https://api.llama.fi/hacks (probed 2026-07-20), with
# planted ground truth: two hacks on 2026-07-10 (sum $7.5M), one on 2026-07-12.
PAYLOAD = [
    {
        "date": 1783036800,  # 2026-07-03T00:00:00Z
        "name": "Older Exploit",
        "classification": "Protocol Logic",
        "technique": "Reentrancy",
        "amount": 2_000_000,
        "chain": ["Ethereum"],
        "bridgeHack": False,
        "targetType": "DeFi Protocol",
        "source": "",
        "returnedFunds": None,
        "defillamaId": "1001",
        "language": "Solidity",
    },
    {
        "date": 1783641600,  # 2026-07-10T00:00:00Z
        "name": "Alpha Bridge",
        "classification": "Infrastructure",
        "technique": "Private Key Compromised",
        "amount": 5_000_000,
        "chain": ["Arbitrum", "Ethereum"],
        "bridgeHack": True,
        "targetType": "Bridge",
        "source": "",
        "returnedFunds": None,
        "defillamaId": "1002",
        "language": "Solidity",
    },
    {
        "date": 1783641600,  # same day -> the daily series must SUM, not dedupe
        "name": "Beta Vault",
        "classification": "Protocol Logic",
        "technique": "Oracle Manipulation",
        "amount": 2_500_000,
        "chain": ["Base"],
        "bridgeHack": False,
        "targetType": "DeFi Protocol",
        "source": "",
        "returnedFunds": None,
        "defillamaId": "1003",
        "language": "Solidity",
    },
    {
        "date": 1783814400,  # 2026-07-12T00:00:00Z
        "name": "Gamma Lend",
        "classification": "Protocol Logic",
        "technique": "Flash Loan Attack",
        "amount": 1_000_000,
        "chain": ["Ethereum"],
        "bridgeHack": False,
        "targetType": "DeFi Protocol",
        "source": "",
        "returnedFunds": None,
        "defillamaId": None,
        "language": "Solidity",
    },
]


def test_parse_daily_series_sums_same_day_and_zero_fills():
    daily, events = llamahacks.parse_hacks(PAYLOAD)
    assert daily.loc[pd.Timestamp("2026-07-10")] == pytest.approx(7_500_000.0)
    assert daily.loc[pd.Timestamp("2026-07-03")] == pytest.approx(2_000_000.0)
    assert daily.loc[pd.Timestamp("2026-07-12")] == pytest.approx(1_000_000.0)
    # zero-filled between event days — no-hack days are real zeros
    assert daily.loc[pd.Timestamp("2026-07-04")] == 0.0
    assert daily.loc[pd.Timestamp("2026-07-11")] == 0.0
    assert daily.index.min() == pd.Timestamp("2026-07-03")
    assert daily.index.max() == pd.Timestamp("2026-07-12")
    assert len(daily) == 10  # contiguous calendar days
    assert len(events) == 4


def test_parse_events_carry_the_engine_fields():
    _, events = llamahacks.parse_hacks(PAYLOAD)
    e = next(ev for ev in events if ev["name"] == "Alpha Bridge")
    assert set(e) == {"name", "date", "amount", "chain", "technique"}
    assert e["date"] == "2026-07-10"          # unix seconds -> ISO day
    assert e["amount"] == pytest.approx(5_000_000.0)
    assert e["chain"] == ["Arbitrum", "Ethereum"]
    assert e["technique"] == "Private Key Compromised"
    # ascending date order for the ledger
    assert [ev["date"] for ev in events] == sorted(ev["date"] for ev in events)


def test_parse_robust_to_junk_rows_and_null_amount():
    junk = [
        {"name": "No Date", "amount": 1.0},                    # dropped: no date
        {"date": "not-a-timestamp", "name": "Bad Date"},       # dropped
        {"date": 1783641600, "name": "Null Amount", "amount": None,
         "chain": None, "technique": None},                    # kept, no daily mass
    ]
    daily, events = llamahacks.parse_hacks(junk)
    assert [e["name"] for e in events] == ["Null Amount"]
    assert events[0]["amount"] is None and events[0]["chain"] == []
    # no dated amount anywhere -> no span to zero-fill; empty, never faked
    assert daily.empty


def test_parse_fails_loud_on_non_list_payload():
    with pytest.raises(ValueError):
        llamahacks.parse_hacks({"message": "throttled"})
    with pytest.raises(ValueError):
        llamahacks.parse_hacks(None)


def _patch_store(monkeypatch, blobs):
    """gdelt-test pattern: TTL'd loads consult the ttl flag, unttl'd serve stale."""
    saved = {}
    monkeypatch.setattr(
        llamahacks.store, "load_blob",
        lambda k, ttl=None: blobs.get(k) if (ttl is None or blobs.get(k + ":fresh")) else None,
    )
    monkeypatch.setattr(
        llamahacks.store, "save_blob", lambda k, v: saved.__setitem__(k, v))
    return saved


def test_fetch_fresh_blob_short_circuits_network(monkeypatch):
    blob = llamahacks._to_blob({
        "fetched_at": "2026-07-20T00:00:00+00:00",
        "daily": pd.Series([3.0], index=pd.DatetimeIndex(["2026-07-19"])),
        "events": [{"name": "X", "date": "2026-07-19", "amount": 3.0,
                    "chain": ["Ethereum"], "technique": "T"}],
    })
    blobs = {llamahacks.BLOB_KEY: blob, llamahacks.BLOB_KEY + ":fresh": True}
    _patch_store(monkeypatch, blobs)

    async def boom(client):  # any network touch would be a test failure
        raise AssertionError("network called despite fresh cache")

    monkeypatch.setattr(llamahacks, "_get", boom)
    out = asyncio.run(llamahacks.fetch_all(None, []))
    assert isinstance(out["daily"], Series)
    assert out["daily"].points.iloc[-1] == pytest.approx(3.0)
    assert out["daily"].staleness == "fresh"
    assert out["events"][0]["name"] == "X"


def test_fetch_cold_call_parses_envelopes_and_caches(monkeypatch):
    blobs = {}
    saved = _patch_store(monkeypatch, blobs)

    async def fake_get(client):
        return PAYLOAD

    monkeypatch.setattr(llamahacks, "_get", fake_get)
    out = asyncio.run(llamahacks.fetch_all(None, []))
    daily = out["daily"]
    assert isinstance(daily, Series)
    assert daily.mnemonic == llamahacks.MNEMONIC
    assert daily.source == "llamahacks" and daily.unit == "$" and daily.freq == "D"
    assert daily.points.loc[pd.Timestamp("2026-07-10")] == pytest.approx(7_500_000.0)
    assert len(out["events"]) == 4
    # the blob cache got the JSON-safe form (Series -> [date, value] pairs)
    assert llamahacks.BLOB_KEY in saved
    assert isinstance(saved[llamahacks.BLOB_KEY]["daily"][0][0], str)


def test_fetch_serves_stale_blob_on_upstream_failure(monkeypatch):
    blob = llamahacks._to_blob({
        "fetched_at": "2026-07-01T00:00:00+00:00",
        "daily": pd.Series([9.0], index=pd.DatetimeIndex(["2026-06-30"])),
        "events": [],
    })
    blobs = {llamahacks.BLOB_KEY: blob}  # present but NOT fresh
    _patch_store(monkeypatch, blobs)

    async def down(client):
        raise RuntimeError("upstream 503")

    monkeypatch.setattr(llamahacks, "_get", down)
    out = asyncio.run(llamahacks.fetch_all(None, []))
    assert out["daily"].points.iloc[-1] == pytest.approx(9.0)
    assert out["fetched_at"] == "2026-07-01T00:00:00+00:00"  # true age, not hidden


def test_fetch_fail_loud_with_no_cache(monkeypatch):
    _patch_store(monkeypatch, {})

    async def down(client):
        raise RuntimeError("upstream 503")

    monkeypatch.setattr(llamahacks, "_get", down)
    with pytest.raises(SourceFault):
        asyncio.run(llamahacks.fetch_all(None, []))


def test_truncate_cuts_series_and_ledger_point_in_time():
    daily, events = llamahacks.parse_hacks(PAYLOAD)
    payload = {
        "fetched_at": "2026-07-20T00:00:00+00:00",
        "daily": llamahacks._envelope(daily, "2026-07-20T00:00:00+00:00"),
        "events": events,
    }
    cut = llamahacks.truncate(payload, pd.Timestamp("2026-07-10"))
    assert cut["daily"].points.index.max() == pd.Timestamp("2026-07-10")
    assert cut["daily"].points.loc[pd.Timestamp("2026-07-10")] == pytest.approx(7_500_000.0)
    assert pd.Timestamp("2026-07-12") not in cut["daily"].points.index
    assert [e["name"] for e in cut["events"]] == ["Older Exploit", "Alpha Bridge", "Beta Vault"]
    # pure: the live payload is untouched
    assert payload["daily"].points.index.max() == pd.Timestamp("2026-07-12")
    assert len(payload["events"]) == 4


def test_truncate_tolerates_empty_and_missing_payload():
    cut = llamahacks.truncate({}, pd.Timestamp("2026-07-10"))
    assert cut["daily"].points.empty and cut["events"] == []
    cut2 = llamahacks.truncate(None, pd.Timestamp("2026-07-10"))
    assert cut2["events"] == []
