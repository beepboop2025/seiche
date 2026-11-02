"""The allowed BoE policy instruments must carry observations, not empty columns."""
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from seiche import store
from seiche.domain.observation import QualityState
from seiche.markets.registry import default_registry
from seiche.repository import SQLiteMarketRepository
from seiche.sources.canonical import FetchedDocument
from seiche.sources.official import BOE_POLICY_XML_SERIES, build_official_adapters, parse_boe_xml

FIXTURES = Path(__file__).parent / "fixtures" / "boe"
BOE = "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp"


def xml(label, payload=None):
    code = BOE_POLICY_XML_SERIES[label][0]
    return FetchedDocument(BOE, "text/xml", payload or (FIXTURES / f"{code}.xml").read_bytes(), label)


def test_every_declared_boe_policy_instrument_has_a_source_series():
    pack = default_registry().get("UK-GBP")
    declared = {i.instrument_id for i in pack.instruments if i.source_adapter_id == "boe_policy"}
    assert declared == {"GB.BOE.BANK_RATE", *BOE_POLICY_XML_SERIES}
    assert pack.adapter_map["boe_policy"].redistribution_status.value == "allowed"
    cadences = {i: pack.instrument_cadence(pack.instrument_map[i]) for i in BOE_POLICY_XML_SERIES}
    assert cadences == {"GB.BOE.OSF_DEPOSIT": "P7W", "GB.BOE.OSF_LENDING": "P7W", "GB.BOE.RESERVE_BALANCES": "P1M"}


@pytest.mark.parametrize("label,event,value", [
    ("GB.BOE.OSF_DEPOSIT", date(2026, 7, 30), Decimal("3.6")),
    ("GB.BOE.OSF_LENDING", date(2026, 7, 30), Decimal("3.9")),
    ("GB.BOE.RESERVE_BALANCES", date(2026, 9, 30), Decimal("642235")),
])
def test_xml_exports_keep_source_dates_values_and_release_clocks(label, event, value):
    points = parse_boe_xml(xml(label))
    latest = points[-1]
    assert (latest.instrument_id, latest.event_time, latest.raw_value) == (label, event, value)
    # LAST_UPDATED is the Bank's own clock, read in London time, estimated.
    assert all(p.source_publication_time.utcoffset() is not None for p in points)
    assert all(p.quality is QualityState.ESTIMATED for p in points)
    assert all(p.source_publication_time.date() > p.event_time for p in points)


def test_osf_rates_keep_the_published_fifteen_basis_point_spread():
    deposit = {p.event_time: p.raw_value for p in parse_boe_xml(xml("GB.BOE.OSF_DEPOSIT"))}
    lending = {p.event_time: p.raw_value for p in parse_boe_xml(xml("GB.BOE.OSF_LENDING"))}
    assert lending[date(2026, 7, 30)] - deposit[date(2026, 7, 30)] == Decimal("0.3")
    # 09:30 in London on 21 September 2026 is 08:30 UTC (British Summer Time).
    latest = parse_boe_xml(xml("GB.BOE.OSF_LENDING"))[-1]
    assert latest.source_publication_time.astimezone(UTC) == datetime(2026, 9, 21, 8, 30, tzinfo=UTC)


@pytest.mark.parametrize("mutate,message", [
    (lambda text: text.replace(b'SCODE="YWMB83C"', b'SCODE="YWMB82C"'), "identity"),
    (lambda text: text.replace(b"Operational Standing Facility rate - deposit", b"Something else"), "identity"),
    (lambda text: text.replace(b'OBS_CONF="N"', b'OBS_CONF="Y"', 1), "public"),
    (lambda text: text.replace(b'LAST_UPDATED="2026-09-21 09:30:00"', b'LAST_UPDATED=""'), "incomplete"),
    (lambda text: b'<!DOCTYPE x [<!ENTITY a "b">]>' + text.split(b"?>", 1)[1], "document type"),
])
def test_series_identity_rights_flag_and_clock_are_validated(mutate, message):
    payload = mutate((FIXTURES / "YWMB83C.xml").read_bytes())
    with pytest.raises(ValueError, match=message):
        parse_boe_xml(xml("GB.BOE.OSF_DEPOSIT", payload))


def test_an_empty_request_window_is_not_a_fault():
    # The Bank's real response for a window after the latest maintenance period.
    payload = (FIXTURES / "YWMB82C-empty-window.xml").read_bytes()
    assert b"LAST_OBS" in payload and b"TIME=" not in payload
    assert parse_boe_xml(xml("GB.BOE.OSF_LENDING", payload)) == ()


@pytest.mark.asyncio
async def test_boe_policy_requests_bank_rate_and_the_three_series_then_collects_them(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "boe-policy.sqlite")
    capture = datetime(2026, 11, 2, 12, tzinfo=UTC)
    adapter = next(item for item in build_official_adapters(repository=SQLiteMarketRepository(), clock=lambda: capture)
                   if item.adapter_id == "boe_policy")
    bank_rate = b"DATE,IUDBEDR\n29 Oct 2026,3.75\n30 Oct 2026,3.75\n"
    requests = []

    def handler(request):
        requests.append(request)
        code = request.url.params["SeriesCodes"]
        if code == "IUDBEDR":
            return httpx.Response(200, content=bank_rate, headers={"content-type": "text/csv"})
        return httpx.Response(200, content=(FIXTURES / f"{code}.xml").read_bytes(), headers={"content-type": "text/xml"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        documents = tuple(await adapter.fetcher(client))

    params = [dict(request.url.params) for request in requests]
    assert [p["SeriesCodes"] for p in params] == ["IUDBEDR", "YWMB83C", "YWMB82C", "LPMBL22"]
    assert params[0]["csv.x"] == "yes" and params[0]["Datefrom"] == "18/Sep/2026"
    # Per-period and monthly series need a window longer than their lag.
    assert all(p["xml.x"] == "yes" and p["Datefrom"] == "28/Sep/2025" for p in params[1:])

    async def fetcher(_client):
        return documents

    adapter.fetcher = fetcher
    batch = await adapter.collect()
    latest = {}
    for row in batch.observations:
        if row.instrument_id not in latest or row.event_time > latest[row.instrument_id].event_time:
            latest[row.instrument_id] = row
    assert {key: str(row.value) for key, row in latest.items()} == {
        "GB.BOE.BANK_RATE": "375.00", "GB.BOE.OSF_DEPOSIT": "360.0",
        "GB.BOE.OSF_LENDING": "390.0", "GB.BOE.RESERVE_BALANCES": "642235",
    }
    assert latest["GB.BOE.RESERVE_BALANCES"].source_publication_time == datetime(2026, 10, 7, 8, 30, tzinfo=UTC)
