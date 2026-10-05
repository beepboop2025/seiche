"""India evidence must preserve dates, signs, conventions and source boundaries."""
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import hashlib
import json

import httpx
import pytest

from seiche import india_funding as desk
from seiche.domain.observation import Observation, QualityState, RedistributionStatus, StalenessState, evidence_sha256
from seiche.markets.india_inr.pack import PACK
from seiche.sources.canonical import FetchedDocument, FunctionalCanonicalAdapter, PublicationTimePolicy
from seiche.sources import rbi

NOW = datetime(2026, 10, 1, 17, tzinfo=UTC)


def observation(key, day, value, *, revision=None):
    spec = PACK.instrument_map[key]
    adapter = PACK.adapter_map[spec.source_adapter_id]
    event = datetime.fromisoformat(day).replace(tzinfo=UTC)
    return Observation(
        market_id="IN-INR", monetary_area_id="IN", jurisdiction_codes=("IN",), currency="INR",
        instrument_id=key, semantic_role=spec.semantic_role, value=Decimal(str(value)),
        canonical_unit=spec.canonical_unit, rate_compounding=spec.rate_compounding, day_count=spec.day_count,
        event_time=event, source_publication_time=event + timedelta(hours=19), knowledge_time=NOW,
        revision_id=revision or "bond:6.20-GS-2031", source=adapter.adapter_id,
        evidence_hash=evidence_sha256(f"{key}-{day}-{value}"), connector_classification=adapter.classification,
        redistribution_status=adapter.redistribution_status, quality=QualityState.ESTIMATED, staleness=StalenessState.FRESH,
    )


def curve_rows():
    return [observation(f"IN.RBI.GSEC_BENCHMARK_{tenor}Y", day, value, revision=f"bond:6.20-GS-{2026+tenor}")
            for tenor, values in ((5, (640, 650)), (10, (690, 694)), (30, (720, 722)))
            for day, value in zip(("2026-09-29", "2026-09-30"), values)]


def doc(text, label="rbi_mmo"):
    return FetchedDocument("https://www.rbi.org.in/test", "text/html", text.encode(), label)


@pytest.mark.parametrize("closing", ["</script >", "</SCRIPT\t>", "</script\n>"])
def test_report_extraction_ignores_code_comments_and_style(closing):
    page = (f'<script>"<tr><td>fake yield</td></tr>"{closing}'
            '<!-- <tr><td>comment yield</td></tr> -->'
            '<style>.value { content: "wrong date"; }</style >'
            '<table><tr id="report"><td>6.5</td><td>Sep&nbsp;30</td></tr></table>')
    rows = list(rbi._rows(page))
    assert len(rows) == 1 and rows[0][:2] == (["report"], ["6.5", "Sep 30"])
    assert rbi._text(page) == "6.5 Sep 30"
    assert rbi._text("<script>unclosed code") == ""


@pytest.mark.parametrize("short,long,expected", [
    (10, 4, "bear_flattening"), (4, 10, "bear_steepening"),
    (-10, -4, "bull_steepening"), (-4, -10, "bull_flattening"),
    (10, 10, "parallel_selloff"), (-10, -10, "parallel_rally"),
    (0, 0, "unchanged"), ("0.05", "-0.05", "unchanged"),
    (0, 5, "one_leg_steepening"), (5, 0, "one_leg_flattening"),
    (-5, 5, "twist_steepening"), (5, -5, "twist_flattening"),
])
def test_curve_classification(short, long, expected):
    assert desk.classify_move(Decimal(str(short)), Decimal(str(long))) == expected


def test_dates_units_and_possible_mechanism_are_matched():
    rows = curve_rows() + [observation("IN.RBI.SYSTEM_LIQUIDITY", d, v) for d, v in
                          (("2026-09-29", -1000), ("2026-09-30", -2000))]
    result = desk.build(rows, now=NOW)
    spread = next(s for s in result["spreads"] if s["id"] == "5s10s")
    assert (spread["value_bp"], spread["change_bp"], spread["short_change_bp"], spread["long_change_bp"]) == (44, -6, 10, 4)
    assert spread["movement"] == "bear_flattening"
    assert result["liquidity_summary"]["net_injection_crore"] == -200
    assert result["liquidity_summary"]["change_in_absorption_crore"] == 100
    assert result["mechanism"]["status"] == "consistent_with"
    assert result["mechanism"]["causal_claim"] is False
    metric = next(m for s in result["sections"] for m in s["metrics"] if m["id"] == "IN.RBI.SYSTEM_LIQUIDITY")
    assert metric["value"] == -200 and metric["canonical_value"] == -2000
    assert metric["change_1_observation"] == -100
    assert metric["history"][-1][1] == -200


@pytest.mark.parametrize("mode,status", [("stale", "stale"), ("roll", "benchmark_roll"), ("dates", "unaligned")])
def test_no_false_daily_movement(mode, status):
    rows = curve_rows()
    if mode == "roll":
        rows[1] = replace(rows[1], revision_id="bond:7.00-GS-2032")
    if mode == "dates":
        rows = [r for r in rows if not (r.instrument_id.endswith("10Y") and r.event_time.day == 30)]
    result = desk.build(rows, now=NOW + timedelta(days=20) if mode == "stale" else NOW)
    spread = next(s for s in result["spreads"] if s["id"] == "5s10s")
    assert spread["status"] == status
    assert spread["movement"] == "unavailable" and spread["change_bp"] is None
    assert result["mechanism"]["status"] == "insufficient_evidence"


def test_no_mechanism_from_mismatched_liquidity_dates():
    rows = curve_rows() + [observation("IN.RBI.SYSTEM_LIQUIDITY", d, v) for d, v in
                          (("2026-09-28", -1000), ("2026-09-30", -2000))]
    assert desk.build(rows, now=NOW)["mechanism"]["status"] == "insufficient_evidence"


def test_mechanism_uses_curve_window_even_when_liquidity_has_a_newer_day():
    rows = curve_rows() + [observation("IN.RBI.SYSTEM_LIQUIDITY", d, v) for d, v in
                          (("2026-09-29", -1000), ("2026-09-30", -2000))]
    newer = replace(rows[-1], event_time=datetime(2026, 10, 1, tzinfo=UTC),
                    source_publication_time=None, value=Decimal('5000'))
    result = desk.build([*rows, newer], now=NOW)
    assert result['mechanism']['status'] == 'consistent_with'
    assert result['mechanism']['matched_liquidity_window']['through'] == '2026-09-30'


def test_no_mixed_frequency_spread_and_no_40_year_extrapolation():
    rows = [observation(f"IN.RBI.SGL_MONTHLY_{t}Y", d, v, revision=f"rbi-sgl-monthly:{t}Y")
            for t, pair in ((2, (600, 615)), (5, (650, 660)), (10, (680, 684)), (30, (700, 702)))
            for d, v in zip(("2026-07-31", "2026-08-31"), pair)]
    rows = [replace(r, source_publication_time=None) for r in rows]
    result = desk.build(rows, now=NOW, include_history=False)
    assert all(s["value_bp"] is None for s in result["spreads"])
    assert all(s["status"] == "historical_reference" for s in result["monthly_curve"]["spreads"])
    assert all(s["asof"] == "2026-08-31" for s in result["monthly_curve"]["spreads"])
    assert next(n for n in result["monthly_curve"]["nodes"] if n["tenor_years"] == 40)["value"] is None
    assert '"history":' not in json.dumps(result)


@pytest.mark.parametrize("change", [
    {"redistribution_status": RedistributionStatus.DERIVED_ONLY},
    {"source": "unknown-source"}, {"knowledge_time": NOW + timedelta(seconds=1)},
    {"source_publication_time": NOW + timedelta(seconds=1), "knowledge_time": NOW + timedelta(seconds=1)},
    {"quality": QualityState.REJECTED},
])
def test_disallowed_rows_cannot_enter_desk(change):
    row = replace(curve_rows()[0], **change)
    assert desk.eligible_rows([row], NOW) == []


def test_mmo_multiple_maturities_signs_and_independent_dates():
    points = rbi.parse_mmo(doc('''Date: 05 Oct 2026 Money Market Operations as on October 04, 2026
      <table><tr><td id="OORevRepo">Reverse Repo</td><td>Oct 1</td><td>4</td><td>Oct 5</td><td>100</td><td>5.24</td></tr>
      <tr><td>Sep 30</td><td>5</td><td>Oct 5</td><td>25</td><td>5.25</td></tr>
      <tr id="MSF3"><td>MSF</td><td>Oct 4</td><td>1</td><td>Oct 5</td><td>10</td><td>5.50</td></tr>
      <tr id="SDF2"><td>SDF</td><td>Oct 4</td><td>1</td><td>Oct 5</td><td>200</td><td>5.00</td></tr>
      <tr id="Netliquidityinjectedoutstandingtoday"><td>Net</td><td></td><td></td><td>-315</td><td></td></tr>
      <tr id="GovernmentIndiaSurplusCashBalance"><td>Govt</td><td>October 01, 2026</td><td>0.00</td></tr>
      <tr id="Netdurableliquidity"><td>Durable</td><td>September 15, 2026</td><td>999</td></tr>
      <tr id="MORepo"><td>Repo</td><td></td><td></td><td></td><td>-</td><td>-</td></tr></table>'''))
    rows = {p.instrument_id: p for p in points}
    assert rows["IN.RBI.VRRR_OUTSTANDING"].raw_value == 125
    assert rows["IN.RBI.FACILITY_TAKEUP"].raw_value == 210
    assert rows["IN.RBI.SYSTEM_LIQUIDITY"].raw_value == -315
    assert rows["IN.GOVERNMENT.CASH_BALANCE"].event_time == date(2026, 10, 1)
    assert rows["IN.RBI.DURABLE_LIQUIDITY"].event_time == date(2026, 9, 15)
    assert "IN.RBI.VRR_TODAY" not in rows
    assert all(p.publication_time_policy is PublicationTimePolicy.UNKNOWN for p in points)


def test_homepage_never_borrows_fx_date_or_invents_bond_conventions():
    bonds = ''.join(f'<tr><td>6.20% GS {year}</td><td>: 6.5% #</td></tr>' for year in (2029, 2031, 2036, 2041, 2056))
    text = f'FX As at 1pm October 05, 2026<h2>Government Securities Market</h2><table>{bonds}</table># as on October 01, 2026<h2>Others</h2>'
    rows = rbi.parse_sovereign(doc(text))
    assert {p.event_time for p in rows} == {date(2026, 10, 1)}
    assert [p.instrument_id.rsplit('_', 1)[-1] for p in rows] == ['3Y', '5Y', '10Y', '15Y', '30Y']
    assert all(p.revision_id.startswith('bond:') for p in rows)
    assert PACK.instrument_map[rows[0].instrument_id].day_count.value == "source_native"
    with pytest.raises(ValueError, match="observation date"):
        rbi.parse_sovereign(doc(text.replace('# as on October 01, 2026', '')))


def monthly_csv():
    return ('"Month-end Yield of SGL Transactions in Government Dated Securities"\n'
            '"Per cent per annum"\n' + ';'.join(['""'] + [f'"{t} Year' + ('s' if t > 1 else '') + '"' for t in range(1, 31)]) + '\n'
            + '"Feb-2024";' + ';'.join(f'"{5 + t/100}"' for t in range(1, 31)) + '\n"Source : Reserve Bank of India."\n')


def test_monthly_original_report_retains_tenor_identity_and_leap_month():
    points = rbi.parse_monthly_curve(doc(monthly_csv(), "rbi_monthly_curve"))
    assert len(points) == 8
    assert {p.event_time for p in points} == {date(2024, 2, 29)}
    assert next(p for p in points if p.instrument_id.endswith("14Y")).raw_value == Decimal("5.14")
    assert all(p.publication_time_policy is PublicationTimePolicy.UNKNOWN for p in points)
    repeated_attribution = monthly_csv().replace('\n"Source : Reserve Bank of India."', ';"Source : Reserve Bank of India."')
    assert len(rbi.parse_monthly_curve(doc(repeated_attribution, "rbi_monthly_curve"))) == 8
    with pytest.raises(ValueError, match="do not align"):
        rbi.parse_monthly_curve(doc(monthly_csv().replace('"14 Years"', '"40 Years"'), "rbi_monthly_curve"))


@pytest.mark.asyncio
async def test_monthly_archive_checks_hash_before_admission():
    content = monthly_csv().encode()
    def handler(request):
        if request.url.path.endswith("latest.json"):
            return httpx.Response(200, json={"version": "2026-09-29"})
        if request.url.path.endswith("MANIFEST.json"):
            return httpx.Response(200, json={"entries": [{"path": rbi.MONTHLY_REPORT, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}]})
        return httpx.Response(200, content=content.replace(b'5.14', b'9.99'))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="manifest"):
            await rbi.fetch_monthly_curve(client)


def test_credit_ranges_are_not_three_month_quotes():
    points = rbi.parse_wss(doc('''Certificates of Deposit Rate of Interest<table>
        <tr><td>30-Sep-26</td><td>574064</td><td>74607</td><td>5.77-7.33</td></tr></table>''', "rbi_cd"))
    assert {p.instrument_id for p in points} == {"IN.RBI.CD_OUTSTANDING", "IN.RBI.CD_ISSUED", "IN.RBI.CD_RATE_LOW", "IN.RBI.CD_RATE_HIGH"}
    assert {p.event_time for p in points} == {date(2026, 9, 30)}


def test_rbi_rss_retains_weekend_call_date_and_native_publication_clock():
    body = '<table><tr id="OSCallMoney"><td>Call Money</td><td>1832.9</td><td>4.94</td></tr></table>'
    feed = ('<rss><channel><item><title>Money Market Operations as on October 03, 2026</title>'
            '<link>https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=63724</link>'
            '<pubDate>Mon, 05 Oct 2026 12:05:00</pubDate><description><![CDATA[' + body + ']]></description>'
            '</item></channel></rss>')
    point, = rbi.parse_money_market_document(doc(feed, 'rbi_mmo_rss'))
    assert point.event_time == date(2026, 10, 3)
    assert point.source_publication_time == datetime(2026, 10, 5, 6, 35, tzinfo=UTC)
    assert point.quality is QualityState.ESTIMATED
    assert point.raw_value == Decimal('4.94')
    with pytest.raises(ValueError, match='no dated'):
        rbi.parse_money_market_document(doc(feed.replace('www.rbi.org.in', 'untrusted.example'), 'rbi_mmo_rss'))


def test_auction_yield_is_separate_and_never_uses_price_or_weighted_average():
    text = '''Date : Sep 04, 2026 Government Stock - Full Auction Results<table>
      <tr><td>Auction Results</td><td>New GS 2031</td><td>7.71% GS 2066</td></tr>
      <tr><td>III.</td><td>Cut-off price / Yield</td><td>100.00</td><td>100.58</td></tr>
      <tr><td>(YTM: 6.5300%)</td><td>(YTM: 7.6618%)</td></tr>
      <tr><td>(WAY: 6.5163%)</td><td>(WAY: 7.6570%)</td></tr></table>'''
    points = rbi.parse_auctions(doc(text, 'rbi_auction_reference'))
    assert [(p.instrument_id, p.raw_value) for p in points] == [
        ('IN.RBI.GSEC_AUCTION_5Y', Decimal('6.5300')), ('IN.RBI.GSEC_AUCTION_40Y', Decimal('7.6618'))]
    assert all(p.event_time == date(2026, 9, 4) for p in points)
    rows = [observation(p.instrument_id, '2026-09-04', p.raw_value * 100, revision=p.revision_id) for p in points]
    result = desk.build(rows, now=NOW)
    assert all(s['value_bp'] is None for s in result['spreads'])
    assert all(n['value'] is None for n in result['monthly_curve']['nodes'])


def test_rest_and_mcp_share_india_payload_and_reject_queries(monkeypatch):
    from fastapi import Request, Response, HTTPException
    from seiche import api, mcp_server
    result = desk.build(curve_rows(), now=NOW, include_history=False)
    monkeypatch.setattr(desk, 'read', lambda **kwargs: result)
    request = Request({'type': 'http', 'query_string': b'', 'headers': [], 'client': ('127.0.0.1', 12345)})
    response = Response()
    assert api.india_funding_v2(request, response) == mcp_server.tool_money_market({'section': 'india'}, True)['india']
    assert response.headers['Cache-Control'] == 'public, max-age=60'
    with pytest.raises(HTTPException) as exc:
        api.india_funding_v2(Request({**request.scope, 'query_string': b'days=99999'}), Response())
    assert exc.value.status_code == 422


def test_calendar_month_cadence_preserves_month_ends():
    from seiche.markets.atlas import _next_event_day, _periods_per_year
    assert _periods_per_year("P1M") == 12
    feb = _next_event_day(date(2024, 1, 31), "P1M", PACK.settlement_calendar)
    assert feb == date(2024, 2, 29)
    assert _next_event_day(feb, "P1M", PACK.settlement_calendar) == date(2024, 3, 31)


def test_india_keeps_call_money_as_operating_benchmark_with_public_treps():
    from seiche.markets.atlas import build_global_money_market_atlas
    rows = [observation('IN.MARKET.CALL_WAR', '2026-09-30', 494),
            observation('IN.RBI.TREPS_WAR', '2026-09-30', 487)]
    market = build_global_money_market_atlas((PACK,), {'IN-INR': rows}, as_of=NOW)['markets'][0]
    assert market['benchmark']['id'] == 'IN.MARKET.CALL_WAR'
    assert market['funding_curve']['schema'] == desk.SCHEMA


def test_canonical_reads_are_bounded_and_mcp_is_chartless(monkeypatch):
    from seiche import mcp_server
    calls = []
    class Repository:
        def load_observations_as_of(self, *args, **kwargs):
            calls.append((args, kwargs))
            return curve_rows()
    monkeypatch.setattr(desk, "get_repository", lambda: Repository())
    result = mcp_server.tool_money_market({"section": "india"}, True)
    assert result["india"]["market_id"] == "IN-INR"
    assert result["chart_history_included"] is False
    assert '"history":' not in json.dumps(result)
    assert len(calls) == 1 and calls[0][0][0] == "IN-INR"
    assert calls[0][1]["event_time_from"] is not None
    assert all(not key.startswith("IN.CCIL") for key in calls[0][1]["instrument_ids"])


def test_reader_fault_is_sanitized(monkeypatch):
    class Repository:
        def load_observations_as_of(self, *args, **kwargs):
            raise RuntimeError("postgresql://secret@private-db")
    monkeypatch.setattr(desk, "get_repository", lambda: Repository())
    result = desk.read(now=NOW)
    assert result["status"] == "unavailable" and "secret" not in json.dumps(result)
