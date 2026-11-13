"""ECB yield curve adapter: pinned identity, units, clocks, revisions. Offline only."""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from seiche.domain.observation import QualityState, RedistributionStatus, SemanticRole
from seiche.markets.rates_reference import DECLARED_CURVES, ECB_YC_KEYS, EXISTING_CURVES, RATES_SOURCES
from seiche.markets.registry import default_registry
from seiche.sources import rates_sources
from seiche.sources.canonical import FetchedDocument, FunctionalCanonicalAdapter
from seiche.sources.official import PRODUCTION_ADAPTER_KEYS, build_official_adapters

FIXTURES = Path(__file__).parent / "fixtures" / "rates"
AAA_10Y = "EA.ECB.YC_AAA_SPOT_10Y"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def document(payload: bytes, label: str = AAA_10Y) -> FetchedDocument:
    return FetchedDocument("https://data-api.ecb.europa.eu/service/data/YC/fixture", "text/csv", payload, label)


def test_fixture_parses_to_published_values_without_rounding():
    points = rates_sources.parse_ecb_yield_curve(document(fixture("ecb-yc-aaa-sr10y-2026-11-09-to-12.csv")))
    assert [p.event_time for p in points] == [date(2026, 11, d) for d in (9, 10, 11, 12)]
    assert {p.instrument_id for p in points} == {AAA_10Y}
    # Values exactly as printed by the ECB on 2026-11-13.
    assert points[0].raw_value == Decimal("3.2719578055")
    assert points[-1].raw_value == Decimal("3.3672459164")
    # Stable evidence: the value-bearing fields only, never descriptive titles.
    assert b"TITLE" not in points[0].row_evidence and b"Svensson" not in points[0].row_evidence
    assert b'"key":"YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y"' in points[0].row_evidence
    assert all(p.quality is None for p in points)


def test_all_issuer_and_short_tenor_fixtures_keep_their_own_identity():
    all_10y = rates_sources.parse_ecb_yield_curve(
        document(fixture("ecb-yc-all-sr10y-2026-11-09-to-12.csv"), "EA.ECB.YC_ALL_SPOT_10Y"))
    assert all_10y[-1].raw_value == Decimal("3.9516211543")
    three_month = rates_sources.parse_ecb_yield_curve(
        document(fixture("ecb-yc-aaa-sr3m-2026-11-09-to-12.csv"), "EA.ECB.YC_AAA_SPOT_3M"))
    assert three_month[0].raw_value == Decimal("2.6117339061")
    # A document is accepted only under the label of its own series key.
    with pytest.raises(ValueError, match="series key"):
        rates_sources.parse_ecb_yield_curve(document(fixture("ecb-yc-aaa-sr2y-2026-11-09-to-12.csv"), AAA_10Y))


@pytest.mark.parametrize("old,new,message", [
    (b",PCPA,0", b",PC,0", "unit changed"),
    (b",PCPA,0", b",PCPA,3", "unit changed"),
    (b"3.2719578055,A,F", b"3.2719578055,A,C", "free for publication"),
    (b"3.2719578055,A,F", b"3.2719578055,F,F", "status"),
    (b"3.2719578055", b"327.19578055", "plausibility"),
    (b"3.2719578055", b"abc", "not numeric"),
    (b"SR_10Y,B,U2", b"SR_10Y,D,U2", "frequency"),
])
def test_identity_unit_and_permission_changes_fail_closed(old, new, message):
    payload = fixture("ecb-yc-aaa-sr10y-2026-11-09-to-12.csv").replace(old, new, 1)
    with pytest.raises(ValueError, match=message):
        rates_sources.parse_ecb_yield_curve(document(payload))


def test_duplicate_dates_fail_and_missing_values_stay_missing():
    lines = fixture("ecb-yc-aaa-sr10y-2026-11-09-to-12.csv").split(b"\n")
    duplicated = b"\n".join([*lines[:2], lines[1], *lines[2:]])
    with pytest.raises(ValueError, match="repeats"):
        rates_sources.parse_ecb_yield_curve(document(duplicated))
    blanked = fixture("ecb-yc-aaa-sr10y-2026-11-09-to-12.csv").replace(b"3.3308865271,A,F", b",M,F", 1)
    points = rates_sources.parse_ecb_yield_curve(document(blanked))
    assert [p.event_time.day for p in points] == [9, 11, 12]


def test_header_change_fails():
    payload = fixture("ecb-yc-aaa-sr10y-2026-11-09-to-12.csv").replace(b"OBS_CONF", b"OBS_CONFIDENTIALITY", 1)
    with pytest.raises(ValueError, match="columns changed"):
        rates_sources.parse_ecb_yield_curve(document(payload))


def test_declarations_are_complete_allowed_and_in_production_keys():
    pack = default_registry().get("EA-EUR")
    adapter = pack.adapter_map["ecb_yield_curve"]
    assert adapter.redistribution_status is RedistributionStatus.ALLOWED
    assert adapter.expected_cadence == "P1D"
    assert (adapter.publication_clock.timezone_name, adapter.publication_clock.local_time.hour,
            adapter.publication_clock.business_day_lag) == ("Europe/Berlin", 12, 1)
    curve_ids = [i.instrument_id for i in pack.instruments if i.source_adapter_id == "ecb_yield_curve"]
    assert len(curve_ids) == 22 == len(ECB_YC_KEYS)
    assert all(pack.instrument_map[i].semantic_role is SemanticRole.SOVEREIGN_YIELD for i in curve_ids)
    assert all(pack.instrument_map[i].value_multiplier == 100 for i in curve_ids)
    assert ("EA-EUR", "ecb_yield_curve") in PRODUCTION_ADAPTER_KEYS
    assert [c.curve_id for c in DECLARED_CURVES] == ["EA_AAA_SPOT", "EA_ALL_SPOT"]
    # Curves declared by their own packs are read, never re-declared or re-collected here.
    assert {c.adapter_id for c in EXISTING_CURVES} & {k[1] for k in rates_sources.PRODUCTION_KEYS} == set()
    assert rates_sources.PRODUCTION_KEYS == {("EA-EUR", "ecb_yield_curve"), ("EA-EUR", "ecb_compounded")}
    reference = RATES_SOURCES["ecb_yield_curve"]
    assert reference["terms_url"].startswith("https://www.ecb.europa.eu/services/using-our-site/disclaimer")
    assert reference["terms_read_on"] == "2026-11-13"
    assert "ECB must be cited as the source" in reference["terms_quote"]


def test_requests_cover_every_node_once_from_the_first_published_date():
    requests = list(rates_sources.ecb_yield_curve_requests(date(2004, 9, 6)))
    assert len(requests) == 22 and len({label for label, _, _ in requests}) == 22
    label, url, params = requests[7]
    assert label == "EA.ECB.YC_AAA_SPOT_10Y"
    assert url == "https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y"
    assert params == {"format": "csvdata", "startPeriod": "2004-09-06"}
    backfill = [a for a in build_official_adapters(backfill=True) if a.adapter_id == "ecb_yield_curve"]
    assert len(backfill) == 1 and backfill[0].historical_backfill


def _adapter(tmp_path, monkeypatch, documents, *, now, backfill=False):
    from seiche import store
    from seiche.repository import SQLiteMarketRepository
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "rates.sqlite")
    repository = SQLiteMarketRepository()

    async def fetch(_client):
        return documents

    adapter = FunctionalCanonicalAdapter(
        pack=default_registry().get("EA-EUR"), adapter_id="ecb_yield_curve", source="ecb_yield_curve",
        fetcher=fetch, parser=rates_sources.parse_ecb_yield_curve, repository=repository,
        clock=lambda: now, historical_backfill=backfill,
    )
    return adapter, repository


def test_canonical_run_keeps_three_clocks_and_converts_percent_to_basis_points(tmp_path, monkeypatch):
    now = datetime(2026, 11, 13, 19, 12, 44, tzinfo=UTC)
    adapter, repository = _adapter(tmp_path, monkeypatch, (
        document(fixture("ecb-yc-aaa-sr10y-2026-11-09-to-12.csv")),), now=now)
    batch = asyncio.run(adapter.collect())
    assert len(batch.observations) == 4
    latest = batch.observations[-1]
    assert latest.value == Decimal("336.72459164")
    assert latest.event_time == datetime(2026, 11, 12, tzinfo=UTC)
    # Scheduled publication: noon Frankfurt time on the next TARGET business day.
    assert latest.source_publication_time == datetime(2026, 11, 13, 11, 0, tzinfo=UTC)
    assert latest.knowledge_time == now
    assert latest.quality is QualityState.VERIFIED
    assert repository.save_observations(batch.observations) == 4


def test_rows_not_yet_published_are_withheld(tmp_path, monkeypatch):
    before_noon = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)
    adapter, _ = _adapter(tmp_path, monkeypatch, (
        document(fixture("ecb-yc-aaa-sr10y-2026-11-09-to-12.csv")),), now=before_noon)
    batch = asyncio.run(adapter.collect())
    assert [o.event_time.day for o in batch.observations] == [9, 10, 11]


def test_publisher_revision_appends_a_vintage_and_keeps_the_earlier_one(tmp_path, monkeypatch):
    first = datetime(2026, 11, 13, 19, 0, tzinfo=UTC)
    original = fixture("ecb-yc-aaa-sr10y-2026-11-09-to-12.csv")
    adapter, repository = _adapter(tmp_path, monkeypatch, (document(original),), now=first)
    repository.save_observations(asyncio.run(adapter.collect()).observations)

    second = datetime(2026, 11, 14, 19, 0, tzinfo=UTC)
    revised = original.replace(b"3.3400829714", b"3.3500000000", 1)
    adapter, _ = _adapter(tmp_path, monkeypatch, (document(revised),), now=second)
    batch = asyncio.run(adapter.collect())
    by_day = {o.event_time.day: o for o in batch.observations}
    assert by_day[11].quality is QualityState.REVISED and "@capture-20261114T190000Z" in by_day[11].revision_id
    assert by_day[11].value == Decimal("335.00000000")
    # Unchanged rows keep their first knowledge time: no new vintage.
    assert by_day[10].knowledge_time == first
    assert repository.save_observations(batch.observations) == 1
    day = datetime(2026, 11, 11, tzinfo=UTC)
    history = repository.load_observation_revisions(
        "EA-EUR", second, instrument_ids=[AAA_10Y], event_time=day, event_time_from=day)
    history.sort(key=lambda o: o.knowledge_time)
    assert [o.value for o in history] == [Decimal("334.00829714"), Decimal("335")]


def test_backfill_rows_are_provisional_until_seen_as_vintages(tmp_path, monkeypatch):
    now = datetime(2026, 11, 13, 19, 12, 44, tzinfo=UTC)
    adapter, _ = _adapter(tmp_path, monkeypatch, (
        document(fixture("ecb-yc-aaa-sr2y-2026-11-09-to-12.csv"), "EA.ECB.YC_AAA_SPOT_2Y"),), now=now, backfill=True)
    batch = asyncio.run(adapter.collect())
    assert {o.quality for o in batch.observations} == {QualityState.PROVISIONAL}
    assert {o.knowledge_time for o in batch.observations} == {now}


# ----------------------------------------------------------------------------- EST distribution and compounded

EST = {
    "EA.ECB.ESTR_P25": "ecb-est-eu000a2x2a25-r25-2026-11-09-to-13.csv",
    "EA.ECB.ESTR_P75": "ecb-est-eu000a2x2a25-r75-2026-11-09-to-13.csv",
    "EA.ECB.ESTR_TRANSACTIONS": "ecb-est-eu000a2x2a25-nt-2026-11-09-to-13.csv",
    "EA.ECB.ESTR_ACTIVE_BANKS": "ecb-est-eu000a2x2a25-nb-2026-11-09-to-13.csv",
    "EA.ECB.ESTR_TOP5_SHARE": "ecb-est-eu000a2x2a25-vl-2026-11-09-to-13.csv",
    "EA.ECB.ESTR_COMPOUNDED_1M": "ecb-est-eu000a2qqf24-cr-2026-11-09-to-13.csv",
    "EA.ECB.ESTR_INDEX": "ecb-est-eu000a2qqf08-ci-2026-11-09-to-13.csv",
}


def est(label: str) -> FetchedDocument:
    return document(fixture(EST[label]), label)


def test_est_distribution_and_compounded_fixtures_parse_as_published():
    latest = {label: rates_sources.parse_ecb_est(est(label))[-1] for label in EST}
    assert latest["EA.ECB.ESTR_P25"].raw_value == Decimal("2.41")
    assert latest["EA.ECB.ESTR_P75"].raw_value == Decimal("2.46")
    assert latest["EA.ECB.ESTR_TRANSACTIONS"].raw_value == Decimal("886")
    assert latest["EA.ECB.ESTR_ACTIVE_BANKS"].raw_value == Decimal("49")
    assert latest["EA.ECB.ESTR_TOP5_SHARE"].raw_value == Decimal("40")
    # The compounded series are dated with their publication day.
    assert latest["EA.ECB.ESTR_COMPOUNDED_1M"].event_time == date(2026, 11, 13)
    assert latest["EA.ECB.ESTR_COMPOUNDED_1M"].raw_value == Decimal("2.44355")
    assert latest["EA.ECB.ESTR_INDEX"].raw_value == Decimal("110.21905548")
    assert latest["EA.ECB.ESTR_P25"].event_time == date(2026, 11, 12)


@pytest.mark.parametrize("label,old,new,message", [
    ("EA.ECB.ESTR_P25", b",PC,0", b",_Z,0", "unit changed"),
    ("EA.ECB.ESTR_TRANSACTIONS", b",886,", b",886.5,", "plausibility"),
    ("EA.ECB.ESTR_TOP5_SHARE", b",40,", b",140,", "plausibility"),
    ("EA.ECB.ESTR_INDEX", b",F,", b",C,", "free for publication"),
    ("EA.ECB.ESTR_P75", b"EU000A2X2A25.R75,B", b"EU000A2X2A25.R25,B", "series key"),
])
def test_est_identity_unit_permission_and_bounds_fail_closed(label, old, new, message):
    payload = fixture(EST[label]).replace(old, new, 1)
    with pytest.raises(ValueError, match=message):
        rates_sources.parse_ecb_est(document(payload, label))


def test_est_declarations_units_clocks_and_legacy_parser_untouched():
    from seiche.sources import official
    pack = default_registry().get("EA-EUR")
    share = pack.instrument_map["EA.ECB.ESTR_TOP5_SHARE"]
    assert share.normalize("40") == Decimal("0.40") and share.canonical_unit.value == "ratio"
    assert pack.instrument_map["EA.ECB.ESTR_TRANSACTIONS"].canonical_unit.value == "count"
    assert pack.instrument_map["EA.ECB.ESTR_P25"].normalize("2.41") == Decimal("241")
    assert pack.instrument_map["EA.ECB.ESTR_P25"].source_adapter_id == "ecb_benchmark"
    compounded = pack.adapter_map["ecb_compounded"]
    assert compounded.publication_clock.local_time is None and compounded.publication_clock.business_day_lag == 0
    assert ("EA-EUR", "ecb_compounded") in PRODUCTION_ADAPTER_KEYS
    # The rate and volume keep the generic parser so stored evidence never changes.
    rate = FetchedDocument("u", "text/csv", b"KEY,TIME_PERIOD,OBS_VALUE,OBS_STATUS\nk,2026-11-12,2.443,A\n", "EA.ECB.ESTR")
    assert official.parse_ecb_benchmark(rate) == official.parse_ecb_csv(rate)
    assert official.parse_ecb_benchmark(est("EA.ECB.ESTR_P25"))[0].row_evidence.startswith(b'{"conf":"F"')


def test_compounded_rows_count_as_published_only_by_the_end_of_their_date(tmp_path, monkeypatch):
    from seiche import store
    from seiche.repository import SQLiteMarketRepository
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "est.sqlite")

    def run(now):
        async def fetch(_client):
            return (est("EA.ECB.ESTR_COMPOUNDED_1M"),)
        adapter = FunctionalCanonicalAdapter(
            pack=default_registry().get("EA-EUR"), adapter_id="ecb_compounded", source="ecb_compounded",
            fetcher=fetch, parser=rates_sources.parse_ecb_est, repository=SQLiteMarketRepository(),
            clock=lambda: now)
        return asyncio.run(adapter.collect()).observations

    midday = run(datetime(2026, 11, 13, 12, 0, tzinfo=UTC))
    assert midday[-1].event_time.day == 12  # the 13th is not knowable before the end of its date
    late = run(datetime(2026, 11, 13, 23, 30, tzinfo=UTC))
    assert late[-1].event_time.day == 13
    assert late[-1].source_publication_time == datetime(2026, 11, 13, 22, 59, 59, tzinfo=UTC)
    assert late[-1].value == Decimal("244.35500")
    assert late[-1].quality is QualityState.ESTIMATED
