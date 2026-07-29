"""Supply Desk tests: synthetic calendars, no network.

The invariants that matter: stale 2024 vintages in the upcoming feed never
reach the table, TBA amounts are filled and flagged instead of dropped,
the house projection is labeled projected=true and stops claiming to be an
announcement, pre-window coupons arrive only through the MSPD overlay, and
net new cash is plain gross minus maturing on every row.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.limit_memory("256 MB")

from seiche.engines import supplydesk

TODAY = pd.Timestamp("2026-07-28")  # a Tuesday


# ---------------------------------------------------------------------------
# Synthetic frames
# ---------------------------------------------------------------------------

def _auction_row(sec_type, term, issue, maturity, accepted, offering=None):
    return {
        "cusip": "TEST00000",
        "security_type": sec_type,
        "security_term": term,
        "auction_date": (pd.Timestamp(issue) - pd.Timedelta(days=5)).date().isoformat(),
        "issue_date": pd.Timestamp(issue).date().isoformat(),
        "maturity_date": pd.Timestamp(maturity).date().isoformat(),
        "offering_amt": str(offering if offering is not None else accepted),
        "total_accepted": str(accepted),
        "bid_to_cover_ratio": "2.8",
        "primary_dealer_accepted": str(accepted * 0.1),
        "direct_bidder_accepted": str(accepted * 0.1),
        "indirect_bidder_accepted": str(accepted * 0.6),
        "high_yield": "4.0",
        "high_discnt_rate": "4.0",
    }


def _history() -> pd.DataFrame:
    rows = []
    # 4-Week bills, 95B, issued every Tuesday for 26 weeks; 8-Week bills 100B.
    for k in range(26):
        issue = TODAY - pd.Timedelta(weeks=26 - k)  # Tuesdays, last = 2026-07-21
        rows.append(_auction_row("Bill", "4-Week", issue, issue + pd.Timedelta(days=28), 95e9))
        rows.append(_auction_row("Bill", "8-Week", issue, issue + pd.Timedelta(days=56), 100e9))
    # 2-Year notes, 30B, month-end cadence; all mature outside the window.
    for issue in ("2026-02-27", "2026-03-31", "2026-04-30", "2026-05-29", "2026-06-30"):
        rows.append(_auction_row("Note", "2-Year", issue, pd.Timestamp(issue) + pd.DateOffset(years=2), 30e9))
    # An old 2-Year issued 2024 maturing inside the window: coupon cash back.
    rows.append(_auction_row("Note", "2-Year", "2024-08-12", "2026-08-12", 42e9))
    return pd.DataFrame(rows)


def _upcoming(include_stale=True) -> pd.DataFrame:
    rows = [
        # Announced with amounts.
        {"record_date": "2026-07-24", "security_type": "Bill", "security_term": "4-Week",
         "auction_date": "2026-07-30", "issue_date": "2026-08-04", "offering_amt": "110000000000"},
        {"record_date": "2026-07-24", "security_type": "Note", "security_term": "2-Year",
         "auction_date": "2026-07-29", "issue_date": "2026-07-31", "offering_amt": "30000000000"},
        # Announced, amount TBA.
        {"record_date": "2026-07-24", "security_type": "Bill", "security_term": "8-Week",
         "auction_date": "2026-07-30", "issue_date": "2026-08-04", "offering_amt": "null"},
    ]
    if include_stale:
        # The live feed really ships these (record_date 2024): must be ignored.
        rows.append({"record_date": "2024-03-08", "security_type": "Bill", "security_term": "4-Week",
                     "auction_date": "2024-03-14", "issue_date": "2024-03-19", "offering_amt": "95000000000"})
    return pd.DataFrame(rows)


def _mspd() -> pd.DataFrame:
    return pd.DataFrame([
        # Pre-window 10y note maturing mid-window: only MSPD can see it.
        {"record_date": "2026-06-30", "security_class1_desc": "Notes", "security_class2_desc": "OLD10Y001",
         "issue_date": "2016-08-12", "maturity_date": "2026-08-12", "issued_amt": "60000", "outstanding_amt": "60000"},
        # Continuation tranche row: outstanding null, must not double count.
        {"record_date": "2026-06-30", "security_class1_desc": "Notes", "security_class2_desc": "OLD10Y001",
         "issue_date": "2016-09-12", "maturity_date": "2026-08-12", "issued_amt": "20000", "outstanding_amt": "null"},
        # Post-window issue: already counted from auction results, must be excluded.
        {"record_date": "2026-06-30", "security_class1_desc": "Notes", "security_class2_desc": "NEW2Y0001",
         "issue_date": "2024-08-12", "maturity_date": "2026-08-12", "issued_amt": "42000", "outstanding_amt": "42000"},
    ])


def _run(**kw):
    return supplydesk.forward_table(
        kw.pop("upcoming", _upcoming()), kw.pop("auctions", _history()),
        kw.pop("mspd", _mspd()), today=TODAY, **kw,
    )


def _row(res, date):
    return next((r for r in res["rows"] if r["date"] == date), None)


# ---------------------------------------------------------------------------
# Table arithmetic
# ---------------------------------------------------------------------------

def test_net_new_cash_is_gross_minus_maturing_on_every_row():
    res = _run()
    assert res["ok"]
    for r in res["rows"]:
        assert r["net_new_cash_b"] == pytest.approx(r["gross_b"] - r["maturing_b"], abs=0.11)
        assert r["gross_b"] == pytest.approx(r["bills_gross_b"] + r["coupons_gross_b"], abs=0.11)
        assert r["maturing_b"] == pytest.approx(r["bills_maturing_b"] + r["coupons_maturing_b"], abs=0.11)


def test_announced_settlement_row():
    res = _run()
    r = _row(res, "2026-08-04")
    # 110 announced 4wk + 100 TBA-filled 8wk settle; 95 + 100 mature.
    assert r is not None
    assert r["bills_gross_b"] == pytest.approx(210.0)
    assert r["bills_maturing_b"] == pytest.approx(195.0)
    assert r["net_new_cash_b"] == pytest.approx(15.0)
    assert r["amount_estimated"] is True   # the 8-Week TBA fill
    assert r["projected"] is False


def test_maturity_only_dates_show_cash_back():
    res = _run()
    r = _row(res, "2026-07-28")
    assert r is not None
    assert r["gross_b"] == pytest.approx(0.0)
    assert r["net_new_cash_b"] == pytest.approx(-195.0)


def test_stale_2024_vintage_rows_never_reach_the_table():
    res = _run()
    assert all(r["date"] >= "2026" for r in res["rows"])
    # And their amounts do not leak into any bucket either: identical table
    # with and without the stale row.
    clean = _run(upcoming=_upcoming(include_stale=False))
    assert res["rows"] == clean["rows"]


def test_horizon_always_covered_by_projection():
    res = _run()
    assert res["announced_through"] == "2026-08-04"
    assert res["horizon_end"] == "2026-08-25"
    for date, expect_bills in (("2026-08-11", 210.0), ("2026-08-18", 210.0), ("2026-08-25", 210.0)):
        r = _row(res, date)
        assert r is not None, f"projection missing for {date}"
        assert r["projected"] is True
        # 4wk carries the announced 110 forward; 8wk carries its last 100.
        assert r["bills_gross_b"] == pytest.approx(expect_bills)


def test_projection_does_not_double_count_announced_dates():
    res = _run()
    r = _row(res, "2026-08-04")
    assert r["projected"] is False
    assert r["bills_gross_b"] == pytest.approx(210.0)  # not 210 + a projected repeat


def test_quarterly_and_irregular_tenors_are_not_projected():
    res = _run()
    # 2-Year cadence ~31d from 07-31 lands past the horizon: no coupon
    # issuance after the announced 07-31 row.
    for r in res["rows"]:
        if r["date"] > "2026-07-31":
            assert r["coupons_gross_b"] == pytest.approx(0.0)


def test_auctioned_but_unsettled_results_fill_the_gross_side():
    # Treasury drops rows from the upcoming feed at auction time; the result
    # row (issue date still ahead) must carry the settlement instead.
    au = pd.concat([_history(), pd.DataFrame([
        _auction_row("Bill", "6-Week", "2026-07-30", "2026-09-10", 65e9),
    ])], ignore_index=True)
    res = _run(auctions=au)
    r = _row(res, "2026-07-30")
    assert r is not None
    assert r["bills_gross_b"] == pytest.approx(65.0)
    assert r["projected"] is False


def test_result_supersedes_its_own_announcement():
    # 2-Year announced at 30B for 07-31 AND already auctioned at 30.5B:
    # the accepted amount wins, no double count.
    au = pd.concat([_history(), pd.DataFrame([
        _auction_row("Note", "2-Year", "2026-07-31", "2028-07-31", 30.5e9),
    ])], ignore_index=True)
    res = _run(auctions=au)
    r = _row(res, "2026-07-31")
    assert r["coupons_gross_b"] == pytest.approx(30.5)


# ---------------------------------------------------------------------------
# Maturing side: auction history + MSPD overlay
# ---------------------------------------------------------------------------

def test_mspd_overlay_adds_pre_window_coupons_only():
    res = _run()
    r = _row(res, "2026-08-12")
    # 42 from the 2024 auction (history) + 60 from the 2016 note (MSPD).
    # The MSPD 42 duplicate (post-window issue) and the null continuation
    # row must both be excluded.
    assert r is not None
    assert r["coupons_maturing_b"] == pytest.approx(102.0)
    assert r["net_new_cash_b"] == pytest.approx(-102.0)


def test_without_mspd_old_coupons_are_missing_and_caveated():
    res = _run(mspd=None)
    r = _row(res, "2026-08-12")
    assert r["coupons_maturing_b"] == pytest.approx(42.0)
    assert any("MSPD" in c for c in res["caveats"])


def test_renamed_mspd_column_degrades_to_no_overlay_and_names_it():
    """A renamed upstream column used to raise KeyError and take the whole
    table down; missing inputs degrade everywhere else in this engine."""
    m = _mspd().rename(columns={"outstanding_amt": "outstanding_amount"})
    res = _run(mspd=m)
    assert res["ok"]
    r = _row(res, "2026-08-12")
    assert r["coupons_maturing_b"] == pytest.approx(42.0)   # auction history only
    caveat = next(c for c in res["caveats"] if "MSPD" in c)
    assert "missing outstanding_amt" in caveat              # the missing column, named


def test_every_required_mspd_column_is_checked():
    for col in ("maturity_date", "issue_date", "outstanding_amt"):
        res = _run(mspd=_mspd().drop(columns=[col]))
        assert res["ok"], f"dropping {col} took the table down"
        assert any("MSPD" in c and col in c for c in res["caveats"])
    # all three gone: one caveat naming all three, still no crash
    res = _run(mspd=_mspd().drop(columns=["maturity_date", "issue_date", "outstanding_amt"]))
    assert res["ok"]
    assert any("missing maturity_date, issue_date, outstanding_amt" in c for c in res["caveats"])


def test_totals_and_heaviest_day():
    res = _run()
    tot = res["totals"]
    assert tot["net_new_cash_b"] == pytest.approx(tot["gross_b"] - tot["maturing_b"], abs=0.5)
    assert tot["net_new_cash_b"] == pytest.approx(sum(r["net_new_cash_b"] for r in res["rows"]), abs=0.5)
    heavy = res["heaviest_day"]
    assert heavy["net_new_cash_b"] == max(r["net_new_cash_b"] for r in res["rows"])


def test_contract_keys():
    res = _run()
    for key in ("ok", "method", "caveats", "asof", "rows", "totals", "heaviest_day",
                "announced_through", "horizon_end"):
        assert key in res
    assert res["asof"] == "2026-07-28"


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------

def test_no_auction_history_fails_closed():
    res = supplydesk.forward_table(_upcoming(), pd.DataFrame(), None, today=TODAY)
    assert res["ok"] is False and "reason" in res


def test_empty_upcoming_still_projects_from_history():
    res = _run(upcoming=pd.DataFrame())
    assert res["ok"]
    assert all(r["projected"] or r["gross_b"] == 0.0 for r in res["rows"])


# ---------------------------------------------------------------------------
# Source-level stale-vintage guard (the bill_desk 2024 bug): cache-hit path
# only, no network: the client is never touched when the blob is fresh.
# ---------------------------------------------------------------------------

def test_fetch_upcoming_auctions_drops_stale_vintages(monkeypatch):
    from seiche.sources import fiscaldata

    now = pd.Timestamp.now().normalize()
    rows = [
        {"record_date": "2024-03-08", "security_type": "Bill", "security_term": "4-Week",
         "auction_date": "2024-03-14", "issue_date": "2024-03-19", "offering_amt": "95000000000"},
        {"record_date": now.date().isoformat(), "security_type": "Bill", "security_term": "4-Week",
         "auction_date": (now + pd.Timedelta(days=2)).date().isoformat(),
         "issue_date": (now + pd.Timedelta(days=7)).date().isoformat(), "offering_amt": "110000000000"},
        # Auctioned yesterday, settles in three days: still pending, must survive.
        {"record_date": now.date().isoformat(), "security_type": "Bill", "security_term": "8-Week",
         "auction_date": (now - pd.Timedelta(days=1)).date().isoformat(),
         "issue_date": (now + pd.Timedelta(days=3)).date().isoformat(), "offering_amt": "100000000000"},
    ]
    monkeypatch.setattr(fiscaldata.store, "load_blob",
                        lambda key, ttl=None: {"fetched_at": "now", "rows": rows})
    out = asyncio.run(fiscaldata.fetch_upcoming_auctions(client=None))
    up = out["upcoming"]
    assert len(up) == 2
    assert "2024-03-14" not in set(up["auction_date"])


def test_reopenings_rejoin_their_original_tenor():
    """FiscalData states security_term as the REMAINING term on a reopening,
    so one economic tenor arrived as several keys and the long coupons were
    never projected while their maturities counted in full."""
    from seiche.engines.supplydesk import _tenor_key
    st = pd.Series(["Note", "Note", "Note", "Bond", "Bond", "Bill"])
    tm = pd.Series(["10-Year", "9-Year 11-Month", "9-Year 10-Month",
                    "30-Year", "29-Year 10-Month", "4-Week"])
    keys = list(_tenor_key(st, tm))
    assert keys[:3] == ["Note 10-Year"] * 3
    assert keys[3:5] == ["Bond 30-Year"] * 2
    assert keys[5] == "Bill 4-Week"        # bills keep their stated term


# ---------------------------------------------------------------------------
# business-day rolling and the projection collision window
# ---------------------------------------------------------------------------

def test_roll_to_bday_covers_weekends_and_federal_holidays():
    # Saturday rolls to Monday (the pre-existing behavior)
    assert supplydesk._roll_to_bday(pd.Timestamp("2026-08-15")) == pd.Timestamp("2026-08-17")
    # Labor Day (Monday 2026-09-07) rolls to Tuesday: weekends-only rolling
    # used to book this cash a day early, on a date no cash moves
    assert supplydesk._roll_to_bday(pd.Timestamp("2026-09-07")) == pd.Timestamp("2026-09-08")
    # Independence Day 2026 falls on a Saturday, observed Friday 07-03: the
    # roll walks through the observed holiday AND the weekend to Monday
    assert supplydesk._roll_to_bday(pd.Timestamp("2026-07-03")) == pd.Timestamp("2026-07-06")


def test_monday_holiday_maturity_books_next_business_day():
    today = pd.Timestamp("2026-08-25")  # the Tuesday before Labor Day week
    au = pd.DataFrame([
        _auction_row("Bill", "4-Week", "2026-08-11", "2026-09-07", 95e9),  # matures Labor Day
        _auction_row("Bill", "4-Week", "2026-08-18", "2026-09-15", 95e9),
    ])
    up = pd.DataFrame([
        {"record_date": "2026-08-21", "security_type": "Bill", "security_term": "4-Week",
         "auction_date": "2026-08-27", "issue_date": "2026-09-01",
         "offering_amt": "95000000000"},
    ])
    res = supplydesk.forward_table(up, au, today=today)
    assert res["ok"]
    dates = {r["date"] for r in res["rows"]}
    assert "2026-09-07" not in dates          # no cash moves on Labor Day
    r = next(r for r in res["rows"] if r["date"] == "2026-09-08")
    assert r["bills_maturing_b"] == pytest.approx(95.0)


def test_projection_never_lands_beside_an_announced_settlement():
    """The collision guard is a one-business-day window, not an exact date:
    a cadence-rounded projection a day off an announced settlement is the
    same issue re-guessed, and printing both would double-book the tenor."""
    res = _run()
    announced = [pd.Timestamp(r["date"]) for r in res["rows"]
                 if not r["projected"] and r["gross_b"] > 0]
    projected = [pd.Timestamp(r["date"]) for r in res["rows"] if r["projected"]]
    for p in projected:
        for a in announced:
            assert abs(int(np.busday_count(a.date(), p.date()))) > 1, (a, p)
