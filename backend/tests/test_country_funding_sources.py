"""Official-file adapters reject shifted identities and retain native periods."""
import asyncio
import csv
from datetime import UTC, date, datetime
from decimal import Decimal
import io
from pathlib import Path

import pytest

from seiche.markets.funding_reference import JP_TENORS
from seiche.markets.registry import default_registry
from seiche.sources.canonical import FetchedDocument, FunctionalCanonicalAdapter, PublicationTimePolicy
from seiche.sources import country_funding as sources


def document(text, label, *, payload=None):
    return FetchedDocument("https://official.example.test/reference", "text/plain",
                           payload if payload is not None else text.encode(), label)


def fred_table(**changes):
    metadata = {"Series ID": "IRLTLT01KRM156N", "Source": "Organization for Economic Co-operation and Development",
                "Frequency": "Monthly", "Units": "Percent", "Seasonal Adjustment": "Not Seasonally Adjusted",
                "Notes": "REF_AREA: KOR MEASURE: IRLT UNIT_MEASURE: PA ACTIVITY: _Z ADJUSTMENT: _Z TRANSFORMATION: _Z FREQ: M"}
    metadata.update(changes)
    return ("<table>" + "".join(f"<tr><th>{key}</th><td>{value}</td></tr>" for key, value in metadata.items())
            + '</table><table id="data-table-observations"><tr><th>DATE</th><th>VALUE</th></tr>'
            '<tr><th>2024-01-01</th><td>3.500</td></tr><tr><th>2024-02-01</th><td>3.125</td></tr></table>')


def test_fred_month_labels_do_not_become_first_day_market_quotes():
    points = sources.parse_fred_oecd(document(fred_table(), "fred_oecd:IRLTLT01KRM156N"), start=date(2024, 2, 1))
    assert len(points) == 1 and points[0].event_time == date(2024, 2, 29)
    assert points[0].raw_value == Decimal("3.125")
    assert points[0].source_publication_time is None
    assert points[0].publication_time_policy is PublicationTimePolicy.UNKNOWN
    assert b'2024-02-01' in points[0].row_evidence


@pytest.mark.parametrize("changes", [
    {"Series ID": "IRLTLT01AUM156N"}, {"Source": "Unreviewed publisher"},
    {"Frequency": "Daily"}, {"Units": "Basis Points"}, {"Seasonal Adjustment": "Seasonally Adjusted"},
    {"Notes": "REF_AREA: CHN MEASURE: IRLT UNIT_MEASURE: PA FREQ: M"},
    {"Notes": "REF_AREA: KOR MEASURE: IR3TIB UNIT_MEASURE: PA FREQ: M"},
])
def test_fred_wrong_country_tenor_source_units_or_frequency_fails(changes):
    with pytest.raises(ValueError):
        sources.parse_fred_oecd(document(fred_table(**changes), "fred_oecd:IRLTLT01KRM156N"))


@pytest.mark.parametrize("old,new", [("3.125", "NaN"), ("2024-02-01", "2024-02-02"),
                                     ("2024-02-01", "2024-01-01"), ("<th>VALUE</th>", "<th>PRICE</th>")])
def test_fred_nonfinite_ambiguous_or_changed_rows_fail(old, new):
    with pytest.raises(ValueError):
        sources.parse_fred_oecd(document(fred_table().replace(old, new), "fred_oecd:IRLTLT01KRM156N"))


def test_script_tags_cannot_supply_fred_metadata():
    with pytest.raises(ValueError):
        sources.parse_fred_oecd(document("<script>" + fred_table() + "</script>", "fred_oecd:IRLTLT01KRM156N"))


def mof_csv():
    return "Interest Rate\nDate," + ",".join(f"{t}Y" for t in JP_TENORS) + "\n2026/10/02," + ",".join("1.25" for _ in JP_TENORS) + "\n"


def test_mof_fixed_tenor_identity_and_japanese_footer():
    points = sources.parse_mof_curve(document("", "jp_mof_current", payload=(mof_csv() + "注記\n").encode("cp932")))
    assert len(points) == 15 and len({p.instrument_id for p in points}) == 15
    assert points[-1].instrument_id == "JP.MOF.JGB_40Y" and points[-1].raw_value == Decimal("1.25")
    assert all(p.publication_time_policy is PublicationTimePolicy.UNKNOWN for p in points)
    assert sources.parse_mof_curve(document(mof_csv(), "jp_mof_history"), history_before=date(2026, 10, 1)) == ()


@pytest.mark.parametrize("old,new", [("5Y", "6Y"), ("1.25", "Infinity")])
def test_mof_maturity_changes_and_invalid_rates_fail(old, new):
    with pytest.raises(ValueError):
        sources.parse_mof_curve(document(mof_csv().replace(old, new, 1), "jp_mof_current"))


def test_boe_keeps_spot_yield_series_and_never_infers_missing_tenors():
    points = sources.parse_boe_curve(document("DATE,IUDSNZC,IUDMNZC,IUDLNZC\n02 Oct 2026,3.25,,4.01\n", "boe_sovereign_curve"))
    assert {p.instrument_id for p in points} == {"GB.BOE.ZERO_COUPON_5Y", "GB.BOE.ZERO_COUPON_20Y"}
    with pytest.raises(ValueError):
        sources.parse_boe_curve(document("DATE,IUDSIFR,IUDMNZC,IUDLNZC\n02 Oct 2026,3,4,5\n", "boe_sovereign_curve"))


def csv_document(row, label):
    output = io.StringIO()
    writer = csv.DictWriter(output, list(row))
    writer.writeheader()
    writer.writerow(row)
    return document(output.getvalue(), label)


def ecb_row(**changes):
    return {"KEY": "IRS.M.FR.L.L40.CI.0000.EUR.N.Z", "FREQ": "M", "REF_AREA": "FR", "IR_TYPE": "L",
            "TR_TYPE": "L40", "MATURITY_CAT": "CI", "BS_COUNT_SECTOR": "0000", "CURRENCY_TRANS": "EUR",
            "IR_BUS_COV": "N", "IR_FV_TYPE": "Z", "UNIT": "PC", "UNIT_MULT": "0",
            "OBS_CONF": "F", "TIME_PERIOD": "2024-02", "OBS_VALUE": "3.12", **changes}


def test_ecb_monthly_reference_retains_country_and_leap_month():
    point, = sources.parse_ecb_national_yield(csv_document(ecb_row(), "ecb_sovereign:FR"))
    assert point.instrument_id == "FR.ECB.LONG_TERM_10Y_MONTHLY" and point.event_time == date(2024, 2, 29)


@pytest.mark.parametrize("changes", [{"REF_AREA": "DE"}, {"FREQ": "D"}, {"UNIT_MULT": "2"},
                                      {"CURRENCY_TRANS": "USD"}, {"OBS_CONF": "C"}, {"MATURITY_CAT": "1Y"}])
def test_ecb_rejects_wrong_identity_or_nonpublic_observation(changes):
    with pytest.raises(ValueError):
        sources.parse_ecb_national_yield(csv_document(ecb_row(**changes), "ecb_sovereign:FR"))


def test_bis_policy_is_country_bound_and_requires_attribution():
    row = {"REF_AREA": "CN", "FREQ": "D", "UNIT_MEASURE": "368", "UNIT_MULT": "0", "OBS_CONF": "F",
           "SOURCE_REF": "People's Bank of China", "TIME_PERIOD": "2026-09-29", "OBS_VALUE": "1.40"}
    point, = sources.parse_bis_policy(csv_document(row, "bis_policy:CN"))
    assert point.instrument_id == "CN.BIS.POLICY_REFERENCE" and b"People's Bank of China" in point.row_evidence
    for changes in ({"REF_AREA": "KR"}, {"SOURCE_REF": ""}, {"OBS_CONF": "C"}, {"UNIT_MEASURE": "USD"}):
        with pytest.raises(ValueError):
            sources.parse_bis_policy(csv_document({**row, **changes}, "bis_policy:CN"))


def test_cbc_discount_decision_keeps_its_own_old_date():
    html = ('<div class="tabItem"><h3>Discount Rate</h3><span>2024-03-22</span><b>2.0%</b></div>'
            '<div class="tabItem"><h3>Interbank Overnight Call-loan Rate</h3><span>2026-10-05</span><b>0.812%</b></div>')
    points = {p.instrument_id: p for p in sources.parse_cbc_rates(document(html, "cbc_public_rates"))}
    assert points["TW.CBC.DISCOUNT_DECISION"].event_time == date(2024, 3, 22)
    assert points["TW.CBC.OVERNIGHT_CALL"].event_time == date(2026, 10, 5)
    with pytest.raises(ValueError):
        sources.parse_cbc_rates(document(html.replace("<span>2024-03-22</span>", ""), "cbc_public_rates"))


def test_original_cbc_biff_workbook_parses_all_eight_independent_columns():
    fixture = Path(__file__).parent / "fixtures/country_funding/cbc-selected-rates.xls"
    points = sources.parse_cbc_monthly(document("", "cbc_monthly_rates", payload=fixture.read_bytes()), start=date(2026, 8, 1))
    assert len(points) == 8 and {p.event_time for p in points} == {date(2026, 8, 31)}
    rates = {p.instrument_id: p.raw_value for p in points}
    assert rates["TW.CBC.OVERNIGHT_CALL_MONTHLY"] == Decimal("0.822")
    assert rates["TW.CBC.GOVERNMENT_10Y_MONTHLY"] == Decimal("1.96")
    assert rates["TW.CBC.CP_31_90D_MONTHLY"] == Decimal("1.78")


def test_canonical_adapter_does_not_backdate_a_monthly_capture():
    source = document(fred_table(), "fred_oecd:IRLTLT01KRM156N")
    async def fetch(_client):
        return (source,)
    class Repository:
        def load_observations_as_of(self, *args, **kwargs):
            assert kwargs["instrument_ids"] == ("KR.OECD.LONG_TERM_10Y_MONTHLY",)
            return []
    now = datetime(2026, 10, 6, tzinfo=UTC)
    adapter = FunctionalCanonicalAdapter(pack=default_registry().get("KR-KRW"), adapter_id="oecd_fred_reference",
        source="oecd_fred_reference", fetcher=fetch, parser=sources.parse_fred_oecd, repository=Repository(), clock=lambda: now)
    batch = asyncio.run(adapter.collect())
    assert {r.knowledge_time for r in batch.observations} == {now}
    assert all(r.source_publication_time is None for r in batch.observations)
    assert batch.observations[-1].value == Decimal("312.5")


def test_bis_flagged_missing_values_are_gaps_and_unflagged_nan_still_fails():
    valid = {"REF_AREA": "SE", "FREQ": "D", "UNIT_MEASURE": "368", "UNIT_MULT": "0", "OBS_CONF": "F",
             "SOURCE_REF": "Sveriges Riksbank", "TIME_PERIOD": "2026-09-29", "OBS_VALUE": "1.75", "OBS_STATUS": "A"}
    missing = {**valid, "TIME_PERIOD": "2026-09-27", "OBS_VALUE": "NaN", "OBS_STATUS": "M"}
    output = io.StringIO()
    writer = csv.DictWriter(output, list(valid));writer.writeheader();writer.writerows([missing, valid])
    points = sources.parse_bis_policy(document(output.getvalue(), "bis_policy:SE"))
    assert len(points) == 1 and points[0].event_time == date(2026, 9, 29)
    for changed in ({**missing, "OBS_STATUS": "A"}, {**missing, "OBS_VALUE": "1.75"}):
        with pytest.raises(ValueError):
            sources.parse_bis_policy(csv_document(changed, "bis_policy:SE"))


def test_duplicate_daily_dates_are_rejected_instead_of_arbitrarily_selected():
    with pytest.raises(ValueError, match="duplicated"):
        sources.parse_mof_curve(document(mof_csv() + mof_csv().splitlines()[-1] + "\n", "jp_mof_current"))
    with pytest.raises(ValueError, match="duplicated"):
        sources.parse_boe_curve(document("DATE,IUDSNZC,IUDMNZC,IUDLNZC\n02 Oct 2026,3,4,5\n02 Oct 2026,3,4,6\n", "boe_sovereign_curve"))
