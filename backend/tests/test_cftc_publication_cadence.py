"""Weekly COT boundaries preserve source dates and bounded polling."""
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from seiche import store
from seiche.sources import base, cftc, publication


def clock(monkeypatch, value):
    instant = datetime.fromisoformat(value)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz or UTC)

    monkeypatch.setattr(cftc, "datetime", Clock)
    monkeypatch.setattr(base, "datetime", Clock)


@pytest.mark.parametrize("now,asof,fetched,due", [
    ("2026-10-02T19:29:59+00:00", "2026-09-22", "2026-10-02T19:00:00+00:00", False),
    ("2026-10-02T19:30:00+00:00", "2026-09-22", "2026-10-02T19:00:00+00:00", True),
    ("2026-10-02T20:29:59+00:00", "2026-09-22", "2026-10-02T19:30:00+00:00", False),
    ("2026-10-02T20:30:00+00:00", "2026-09-22", "2026-10-02T19:30:00+00:00", True),
    ("2026-10-03T07:00:00+00:00", "2026-09-29", "2026-10-02T19:30:00+00:00", False),
    ("2026-02-06T20:29:59+00:00", "2026-01-27", "2026-02-06T20:00:00+00:00", False),
    ("2026-02-06T20:30:00+00:00", "2026-01-27", "2026-02-06T20:00:00+00:00", True),
    ("2026-03-13T19:30:00+00:00", "2026-03-03", "2026-03-13T19:00:00+00:00", True),
    # CFTC's actual 2026 Thanksgiving release is Monday Nov 30. Nominal
    # Friday polling may see old data; it never asserts a Friday publication.
    ("2026-11-27T20:30:00+00:00", "2026-11-17", "2026-11-27T20:00:00+00:00", True),
    ("2026-11-27T21:00:00+00:00", "2026-11-17", "2026-11-27T20:30:00+00:00", False),
    ("2026-11-30T21:00:00+00:00", "2026-11-24", "2026-11-30T20:30:00+00:00", False),
])
def test_nominal_boundary_dst_and_delayed_release(now, asof, fetched, due):
    assert publication.cftc_positions_refresh_due([asof] * 10, fetched, now=datetime.fromisoformat(now)) is due


@pytest.mark.parametrize("bad", [None, "invalid", "2026-10-02T19:00:00", "2026-10-03T00:00:00+00:00"])
def test_bad_fetch_clock_cannot_hide_release(bad):
    assert publication.cftc_positions_refresh_due(["2026-09-29"], bad, now=datetime(2026, 10, 2, 20, tzinfo=UTC))


@pytest.mark.parametrize("bad", [None, "NaT", "2026-09-22", "2026-10-06"])
def test_each_configured_contract_needs_current_observation(bad):
    assert publication.cftc_positions_refresh_due(["2026-09-29", bad], "2026-10-02T19:00:00+00:00", now=datetime(2026, 10, 2, 20, tzinfo=UTC))


def test_naive_evaluation_time_rejected():
    with pytest.raises(ValueError):
        publication.cftc_positions_refresh_due(["2026-09-29"], "2026-10-02T19:00:00+00:00", now=datetime(2026, 10, 2, 20))


def rows(commodities, asof):
    identifiers = sorted(cftc._BALLAST_BY_CODE) if commodities else list(cftc.UST_CONTRACTS) + cftc.CROWD_EXTRA_CONTRACTS
    fields = cftc.DISAGG_FIELDS if commodities else cftc.FIELDS
    return [dict.fromkeys(fields, "100") | {
        "report_date_as_yyyy_mm_dd": asof + "T00:00:00.000",
        "contract_market_name": identifier,
        "cftc_contract_market_code": identifier,
    } for identifier in identifiers]


def install_cache(monkeypatch, commodities):
    cached = [{"fetched_at": "2026-10-02T19:00:00+00:00", "rows": rows(commodities, "2026-09-22")}]
    saved = []
    monkeypatch.setattr(store, "load_blob", lambda *args: cached[0])

    def save(key, value):
        assert key == ("cftc_disagg_ballast" if commodities else "cftc_tff_ust")
        saved.append(value)
        cached[0] = value

    monkeypatch.setattr(store, "save_blob", save)
    return cached, saved


@pytest.mark.parametrize("commodities", [False, True])
@pytest.mark.asyncio
async def test_boundary_refetches_all_contracts_and_reuses_current_cache(monkeypatch, commodities):
    clock(monkeypatch, "2026-10-02T19:31:00+00:00")
    cached, saved = install_cache(monkeypatch, commodities)
    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(200, json=rows(commodities, "2026-09-29"))

    fetch = cftc.fetch_disaggregated_commodities if commodities else cftc.fetch_tff_ust
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        result = await fetch(client)
        await fetch(client)
    assert len(calls) == len(saved) == 1
    frame = result["positions" if commodities else "tff"]
    assert frame["date"].min().date().isoformat() == "2026-09-29"
    assert set(frame["contract"]) == (set(cftc._BALLAST_BY_CODE.values()) if commodities else set(cftc.UST_CONTRACTS) | set(cftc.CROWD_EXTRA_CONTRACTS))
    assert result["fetched_at"] == "2026-10-02T19:31:00+00:00"


@pytest.mark.parametrize("commodities", [False, True])
@pytest.mark.asyncio
async def test_successful_delayed_release_preserves_dates_and_checks_hourly(monkeypatch, commodities):
    clock(monkeypatch, "2026-10-02T19:31:00+00:00")
    cached, saved = install_cache(monkeypatch, commodities)
    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(200, json=rows(commodities, "2026-09-22"))

    fetch = cftc.fetch_disaggregated_commodities if commodities else cftc.fetch_tff_ust
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        result = await fetch(client)
        await fetch(client)
        assert len(calls) == 1
        clock(monkeypatch, "2026-10-02T20:31:00+00:00")
        await fetch(client)
    assert len(calls) == len(saved) == 2
    assert result["positions" if commodities else "tff"]["date"].max().date().isoformat() == "2026-09-22"


@pytest.mark.parametrize("commodities", [False, True])
@pytest.mark.asyncio
async def test_failed_refresh_keeps_prior_fetch_clock_and_observations(monkeypatch, commodities):
    clock(monkeypatch, "2026-10-02T19:31:00+00:00")
    cached, saved = install_cache(monkeypatch, commodities)
    fetch = cftc.fetch_disaggregated_commodities if commodities else cftc.fetch_tff_ust
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(400))) as client:
        result = await fetch(client)
    assert not saved
    assert result["fetched_at"] == "2026-10-02T19:00:00+00:00"
    assert result["positions" if commodities else "tff"]["date"].max().date().isoformat() == "2026-09-22"


@pytest.mark.parametrize("commodities", [False, True])
@pytest.mark.parametrize("partial", ["missing", "old"])
def test_current_contract_cannot_mask_partial_response(monkeypatch, commodities, partial):
    clock(monkeypatch, "2026-10-02T19:31:00+00:00")
    data = rows(commodities, "2026-09-29")
    if partial == "missing":
        data.pop()
    else:
        data[-1]["report_date_as_yyyy_mm_dd"] = "2026-09-22T00:00:00.000"
    assert cftc._cache_crossed_release({"rows": data, "fetched_at": "2026-10-02T19:00:00+00:00"}, commodities=commodities)


@pytest.mark.parametrize("commodities", [False, True])
@pytest.mark.parametrize("age_minutes,status,requests", [(59, 200, 0), (61, 200, 1), (61, 400, 1)])
@pytest.mark.asyncio
async def test_hourly_ttl_also_bounds_early_or_unscheduled_releases(monkeypatch, tmp_path, commodities, age_minutes, status, requests):
    # Before nominal Friday, Sep 29 still satisfies that calendar. An earlier
    # actual source update must nevertheless be discovered within the TTL.
    now = datetime(2026, 10, 8, 20, tzinfo=UTC)
    fetched = (now - timedelta(minutes=age_minutes)).isoformat(timespec="seconds")
    clock(monkeypatch, fetched)
    monkeypatch.setattr(store, "datetime", cftc.datetime)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "cache.sqlite")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    key = "cftc_disagg_ballast" if commodities else "cftc_tff_ust"
    store.save_blob(key, {"fetched_at": fetched, "rows": rows(commodities, "2026-09-29")})
    clock(monkeypatch, now.isoformat())
    monkeypatch.setattr(store, "datetime", cftc.datetime)
    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(status, json=rows(commodities, "2026-10-06"))

    fetch = cftc.fetch_disaggregated_commodities if commodities else cftc.fetch_tff_ust
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        result = await fetch(client)
    assert len(calls) == requests
    refreshed = requests and status == 200
    assert result["fetched_at"] == (now.isoformat(timespec="seconds") if refreshed else fetched)
    assert result["positions" if commodities else "tff"]["date"].max().date().isoformat() == ("2026-10-06" if refreshed else "2026-09-29")


@pytest.mark.parametrize("name", ["ULTRA UST BOND", "ULTRA UST BOND FUTURES"])
def test_ultra_bond_does_not_collapse_into_standard_bond(name):
    assert cftc._match_contract(name) == "ULTRA UST BOND"
    assert cftc._match_contract("UST BOND") == "UST BOND"


def test_actual_primary_labels_remain_ten_distinct_contracts():
    # CFTC gpe5-46if contract_market_name labels read 2026-10-02; these are
    # source identities, separate from its market_and_exchange_names title.
    labels = ["ULTRA UST 10Y", "UST 2Y NOTE", "UST 10Y NOTE", "UST BOND",
              "SOFR-1M", "UST 5Y NOTE", "SOFR-3M", "ULTRA UST BOND",
              "FED FUNDS", "E-MINI S&P 500"]
    assert [cftc._match_contract(label) for label in labels] == labels
    assert len({cftc._match_contract(label) for label in labels}) == 10


def test_unrelated_ambiguous_contract_title_is_not_joined():
    assert cftc._match_contract("UST 2Y NOTE / UST 5Y NOTE") is None
