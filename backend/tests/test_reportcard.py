"""Auction Report Card tests: grades restated, the event study after the
auction, honest open windows, and clean prose. Synthetic data only, no network.

The auction frame is built to a known shape and then run through the real
auctions engine, so the cards are graded by the same code the board uses
rather than by a hand-written payload.
"""

from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd
import pytest

from seiche.dispatch_daily import lint_letter
from seiche.engines import auctions as eng_auctions
from seiche.engines import reportcard

pytestmark = pytest.mark.limit_memory("256 MB")

DASHES = ("\u2014", "\u2013")

BASE = pd.Timestamp("2026-01-06")          # a Tuesday
N_AUCTIONS = 16
SPECS = (("Note", "2-Year", 0), ("Bill", "4-Week", 1), ("Note", "10-Year", 2))
LAST_10Y = "2026-08-06"                    # BASE + 14*15 + 2 days, a Thursday
SETTLE_WEEK = "2026-08-12"                 # first reserve Wednesday after settlement
SIZES = {"2-Year": 69e9, "4-Week": 85e9, "10-Year": 39e9}


# ---------------------------------------------------------------------------
# synthetic inputs
# ---------------------------------------------------------------------------
def _auction_frame(last_10y: str = "normal", fatigue_tenor: str | None = None) -> pd.DataFrame:
    """Sixteen auctions per tenor on a fortnightly grid, with a stated tweak on
    the newest 10-Year print and an optional indirect-share slide by tenor."""
    rng = np.random.default_rng(11)
    rows = []
    for stype, term, off in SPECS:
        size = SIZES[term]
        for i in range(N_AUCTIONS):
            date = BASE + pd.Timedelta(days=14 * i + off)
            btc = 2.50 + float(rng.normal(0, 0.04))
            pd_share = 0.15 + float(rng.normal(0, 0.008))
            ind_share = 0.68 + float(rng.normal(0, 0.008))
            if fatigue_tenor == f"{stype} {term}" and i >= N_AUCTIONS - 8:
                ind_share = 0.68 - 0.006 * (i - (N_AUCTIONS - 8))
            if term == "10-Year" and i == N_AUCTIONS - 1:
                if last_10y == "strong":
                    btc, pd_share, ind_share = 2.75, 0.11, 0.72
                elif last_10y == "weak":
                    btc, pd_share, ind_share = 2.05, 0.30, 0.55
            rows.append({
                "cusip": f"91282C{term[:2]}{i:02d}",
                "security_type": stype,
                "security_term": term,
                "auction_date": date.date().isoformat(),
                "issue_date": (date + pd.Timedelta(days=2)).date().isoformat(),
                "total_accepted": size,
                "offering_amt": size,
                "bid_to_cover_ratio": round(btc, 3),
                "primary_dealer_accepted": pd_share * size,
                "indirect_bidder_accepted": ind_share * size,
                "direct_bidder_accepted": (1.0 - pd_share - ind_share) * size,
                "high_yield": 4.21,
            })
    return pd.DataFrame(rows)


def _spread(end: str = "2026-08-20", jump_from: str | None = None,
            jump_bp: float = 8.0, level: float = -2.0) -> pd.Series:
    idx = pd.bdate_range("2025-12-01", end)
    s = pd.Series(level, index=idx, dtype=float)
    if jump_from:
        s.loc[s.index >= pd.Timestamp(jump_from)] += jump_bp
    return s


def _srf(end: str = "2026-08-20", spikes: dict[str, float] | None = None) -> pd.Series:
    idx = pd.bdate_range("2025-12-01", end)
    s = pd.Series(0.0, index=idx, dtype=float)
    for d, v in (spikes or {}).items():
        s.loc[pd.Timestamp(d)] = v
    return s


def _reserves(end: str = "2026-08-19", drain_week: str | None = None,
              drain_b: float = 41.0) -> pd.Series:
    idx = pd.date_range("2025-12-03", end, freq="W-WED")
    s = pd.Series(3_200_000.0 - 5_000.0 * np.arange(len(idx)), index=idx)  # $M, -5B a week
    if drain_week:
        s.loc[s.index >= pd.Timestamp(drain_week)] -= drain_b * 1000.0
    return s


def _cards(last_10y="normal", fatigue_tenor=None, spread=None, srf=None,
           reserves=None, frame=True, **kw) -> dict:
    df = _auction_frame(last_10y=last_10y, fatigue_tenor=fatigue_tenor)
    payload = eng_auctions.analyze(df)
    assert payload["ok"], payload.get("reason")
    return reportcard.report_cards(
        payload,
        _spread() if spread is None else spread,
        _srf() if srf is None else srf,
        _reserves() if reserves is None else reserves,
        auctions_frame=df if frame else None,
        **kw,
    )


def _first(r: dict, tenor: str = "Note 10-Year") -> dict:
    return next(c for c in r["cards"] if c["tenor"] == tenor)


# ---------------------------------------------------------------------------
# the clean auction
# ---------------------------------------------------------------------------
def test_clean_auction_grades_high_and_the_plumbing_agrees():
    r = _cards(last_10y="strong")
    assert r["ok"]
    assert r["asof"] == LAST_10Y                      # the 10Y is the newest print
    card = r["cards"][0]
    assert card["tenor"] == "Note 10-Year"
    assert card["slug"] == f"{LAST_10Y}-10y-note"
    assert card["permalink"] == f"/auction/{LAST_10Y}-10y-note"
    assert card["display"] == "10y note"
    assert card["cusip"] and card["size_b"] == 39.0
    assert card["issue_date"] == "2026-08-08"

    g = card["grades"]
    assert g["grade"] == "A" and g["composite_z"] < -1.0
    assert g["btc_z"] > 3 and g["pd_share_z"] < -3 and g["indirect_z"] > 3
    assert g["bid_to_cover"] == 2.75

    es = card["event_study"]
    assert es["status"] == "complete" and es["marks_in"] == 4
    assert [h["h"] for h in es["horizons"]] == ["T+0", "T+1", "T+3", "T+5"]
    assert all(h["status"] == "printed" for h in es["horizons"])
    assert es["baseline_bp"] == -2.0 and es["change_t5_bp"] == 0.0
    assert es["srf"]["status"] == "complete" and es["srf"]["max_b"] == 0.0
    assert es["reserves"]["status"] == "complete"
    assert es["reserves"]["week_ending"] == SETTLE_WEEK
    assert es["reserves"]["change_b"] == -5.0

    assert "plumbing agreed" in card["verdict"]
    assert "no SRF take-up inside the window" in card["verdict"]


def test_settlement_week_is_read_off_the_issue_date_not_the_auction_date():
    r = _cards(last_10y="strong")
    card = r["cards"][0]
    # auction 2026-08-06, settles 2026-08-08, so the reserve leg reads the
    # week ending 2026-08-12 rather than the auction week
    assert card["event_study"]["reserves"]["week_ending"] == SETTLE_WEEK


# ---------------------------------------------------------------------------
# the tailed auction, and what the plumbing did next
# ---------------------------------------------------------------------------
def test_tailed_auction_followed_by_funding_pressure():
    r = _cards(
        last_10y="weak",
        spread=_spread(jump_from=LAST_10Y, jump_bp=8.0),
        srf=_srf(spikes={"2026-08-07": 12.0, "2026-08-10": 4.0}),
        reserves=_reserves(drain_week=SETTLE_WEEK, drain_b=41.0),
    )
    card = r["cards"][0]
    g, es = card["grades"], card["event_study"]
    assert g["grade"] == "F" and g["composite_z"] > 1.0
    assert g["bid_to_cover"] == 2.05

    assert es["status"] == "complete"
    assert es["change_t5_bp"] == 8.0 and es["peak_change_bp"] == 8.0
    assert es["max_widening_bp"] == 8.0
    assert es["srf"]["max_b"] == 12.0 and es["srf"]["sum_b"] == 16.0
    assert es["srf"]["days_with_takeup"] == 2
    assert es["reserves"]["change_b"] == -46.0

    v = card["verdict"]
    assert "plumbing confirmed it" in v
    assert "widened 8.0bp by T plus 5" in v
    assert "SRF take-up peaked at $12.0B" in v
    assert "reserves fell $46B across the settlement week" in v


def test_good_grade_with_bad_plumbing_reads_as_a_disagreement():
    """The whole point of the card: the screens said fine, the pipes did not."""
    r = _cards(last_10y="strong", spread=_spread(jump_from=LAST_10Y, jump_bp=6.0))
    card = r["cards"][0]
    assert card["grades"]["grade"] == "A"
    assert "the screens called it fine and the plumbing disagreed" in card["verdict"]


def test_a_late_reversal_does_not_erase_the_widening():
    """T+5 back to flat is not the same as a quiet week, and the card keeps
    the widest mark so the pressure read survives the round trip."""
    spread = _spread()
    spread.loc["2026-08-06":"2026-08-11"] += 7.0            # T+0 through T+3 only
    r = _cards(last_10y="strong", spread=spread)
    es = r["cards"][0]["event_study"]
    assert es["change_t5_bp"] == 0.0 and es["max_widening_bp"] == 7.0
    assert "the screens called it fine and the plumbing disagreed" in r["cards"][0]["verdict"]


# ---------------------------------------------------------------------------
# an unfinished window is open, never a zero
# ---------------------------------------------------------------------------
def test_open_window_reports_status_open_and_never_fabricates_a_zero():
    r = _cards(
        last_10y="normal",
        spread=_spread(end="2026-08-07"),          # only T+0 and T+1 have printed
        srf=_srf(end="2026-08-07"),
        reserves=_reserves(end="2026-08-05"),      # settlement week not out yet
    )
    es = r["cards"][0]["event_study"]
    assert es["status"] == "open" and es["marks_in"] == 2
    by_h = {h["h"]: h for h in es["horizons"]}
    assert by_h["T+0"]["status"] == "printed" and by_h["T+1"]["status"] == "printed"
    for h in ("T+3", "T+5"):
        assert by_h[h]["status"] == "open"
        assert by_h[h]["change_bp"] is None and by_h[h]["spread_bp"] is None
        assert by_h[h]["date"] is None
    assert es["change_t5_bp"] is None
    assert es["srf"]["status"] == "open"
    assert es["reserves"]["status"] == "open" and es["reserves"]["change_b"] is None
    assert "still open at 2 of 4 marks" in r["cards"][0]["verdict"]
    assert "still open at 2 of 4 marks" in r["letter_line"]


def test_auction_ahead_of_the_funding_tape_is_open_not_scored():
    r = _cards(spread=_spread(end="2026-07-31"), srf=_srf(end="2026-07-31"))
    es = r["cards"][0]["event_study"]
    assert es["status"] == "open" and es["marks_in"] == 0
    assert es["baseline_bp"] is None
    assert all(h["change_bp"] is None for h in es["horizons"])


def test_no_funding_history_before_the_auction_is_no_data():
    """Distinct from open: the window is unmeasurable, not unfinished."""
    r = _cards(spread=_spread().loc["2026-08-06":])
    es = r["cards"][0]["event_study"]
    assert es["status"] == "no_data"
    assert all(h["change_bp"] is None for h in es["horizons"])
    assert "does not reach back far enough" in r["cards"][0]["verdict"]


# ---------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------
def test_empty_inputs_refuse():
    df = _auction_frame()
    payload = eng_auctions.analyze(df)

    r = reportcard.report_cards({}, _spread(), _srf(), _reserves())
    assert not r["ok"] and "auction digestion unavailable" in r["reason"]

    r = reportcard.report_cards({"ok": False, "reason": "no auction data"},
                                _spread(), _srf(), _reserves())
    assert not r["ok"] and "no auction data" in r["reason"]

    r = reportcard.report_cards({"ok": True, "recent_auctions": []},
                                _spread(), _srf(), _reserves())
    assert not r["ok"] and "no scored auctions" in r["reason"]

    r = reportcard.report_cards(payload, pd.Series(dtype=float), _srf(), _reserves())
    assert not r["ok"] and "SOFR-IORB" in r["reason"]

    r = reportcard.report_cards(payload, None, _srf(), _reserves())
    assert not r["ok"]

    # empty auction frame: the engine refuses at the digestion layer, and the
    # card layer repeats that refusal instead of inventing an empty board
    empty = eng_auctions.analyze(pd.DataFrame())
    assert not empty["ok"]
    assert not reportcard.report_cards(empty, _spread(), _srf(), _reserves())["ok"]


def test_missing_srf_and_reserves_degrade_without_fabricating():
    r = _cards(srf=pd.Series(dtype=float), reserves=pd.Series(dtype=float))
    es = r["cards"][0]["event_study"]
    assert es["status"] == "complete"                 # the spread leg still scores
    assert es["srf"]["status"] == "absent" and es["srf"]["max_b"] is None
    assert es["reserves"]["status"] == "absent" and es["reserves"]["change_b"] is None
    assert "SRF" not in r["cards"][0]["verdict"]


# ---------------------------------------------------------------------------
# demand fatigue
# ---------------------------------------------------------------------------
def test_demand_fatigue_flags_a_tenor_going_soft_across_auctions():
    r = _cards(fatigue_tenor="Bill 4-Week")
    rows = {row["tenor"]: row for row in r["demand_fatigue"]}
    soft = rows["Bill 4-Week"]
    assert soft["verdict"] == "softening"
    assert soft["auctions"] == 8
    assert soft["trend_pp_per_auction"] == -0.6
    assert soft["latest_indirect_pct"] == 63.8
    assert soft["display"] == "4w bill"
    assert len(soft["series"]) == 8 and all(len(p) == 2 for p in soft["series"])
    # the other tenors are flat by construction and must not be dressed up
    assert rows["Note 10-Year"]["verdict"] == "steady"
    # the card for that tenor carries its own tenor's read
    bill = _first(r, "Bill 4-Week")
    assert bill["fatigue"]["verdict"] == "softening"


# ---------------------------------------------------------------------------
# prose: the house copy rules, enforced by the letter's own lint
# ---------------------------------------------------------------------------
def _all_prose(r: dict) -> list[str]:
    out = [r["letter_line"], r["method"], *r["caveats"]]
    out += [c["verdict"] for c in r["cards"]]
    out += [row["verdict"] for row in r["demand_fatigue"]]
    return [t for t in out if t]


def test_no_dashes_anywhere_in_generated_prose():
    for r in (_cards(last_10y="strong"),
              _cards(last_10y="weak", spread=_spread(jump_from=LAST_10Y),
                     srf=_srf(spikes={"2026-08-07": 12.0})),
              _cards(spread=_spread(end="2026-08-07"), srf=_srf(end="2026-08-07"))):
        assert lint_letter(*_all_prose(r)) == []
        blob = json.dumps(r, default=str)
        for ch in DASHES:
            assert ch not in blob
        # the lint's other publish blockers must not fire either
        assert "None" not in " ".join(_all_prose(r))


def test_letter_line_is_one_clean_sentence_about_the_newest_auction():
    r = _cards(last_10y="weak", spread=_spread(jump_from=LAST_10Y),
               srf=_srf(spikes={"2026-08-07": 12.0}))
    line = r["letter_line"]
    assert isinstance(line, str) and line.endswith(".")
    assert ". " not in line                       # one sentence; decimals are fine
    assert "10y note of 2026-08-06" in line
    assert "graded F" in line
    assert "widened 8.0bp" in line
    assert "SRF take-up peaking at $12.0B" in line
    assert lint_letter(line) == []


def test_letter_line_takes_the_payload_or_the_cards_and_refuses_empty():
    r = _cards(last_10y="strong")
    assert reportcard.letter_line(r) == r["letter_line"]
    assert reportcard.letter_line(r["cards"]) == r["letter_line"]
    assert reportcard.letter_line([]) == ""
    assert reportcard.letter_line({"ok": False}) == ""


# ---------------------------------------------------------------------------
# payload contract
# ---------------------------------------------------------------------------
def test_payload_keys_slugs_and_ordering():
    r = _cards(n=5)
    for k in ("ok", "asof", "funding_asof", "n_cards", "window_bd", "cards",
              "demand_fatigue", "letter_line", "caveats", "method"):
        assert k in r
    assert r["window_bd"] == [0, 1, 3, 5]
    assert r["n_cards"] == 5 and len(r["cards"]) == 5
    dates = [c["auction_date"] for c in r["cards"]]
    assert dates == sorted(dates, reverse=True)           # newest first
    slugs = [c["slug"] for c in r["cards"]]
    assert len(set(slugs)) == len(slugs)                  # permalinks are unique
    for s in slugs:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-[0-9a-z]+-[a-z]+", s), s
    assert r["funding_asof"] == "2026-08-20"


def test_same_day_auctions_put_the_coupon_first():
    """Four auctions settle on one afternoon; the letter should lead with the
    coupon, which is the leg that puts duration on dealer balance sheets."""
    payload = {
        "ok": True,
        "recent_auctions": [
            {"date": "2026-07-27", "tenor": "Bill 13-Week", "btc": 3.06,
             "btc_z": 0.71, "pd_share": 0.248, "pd_share_z": -1.56, "score": -1.14},
            {"date": "2026-07-27", "tenor": "Note 5-Year", "btc": 2.28,
             "btc_z": -1.32, "pd_share": 0.122, "pd_share_z": -0.7, "score": 0.62},
            {"date": "2026-07-23", "tenor": "Note 10-Year", "btc": 2.4,
             "btc_z": -0.2, "pd_share": 0.15, "pd_share_z": 0.1, "score": 0.1},
        ],
    }
    r = reportcard.report_cards(payload, _spread(), _srf(), _reserves())
    assert [c["slug"] for c in r["cards"]] == [
        "2026-07-27-5y-note", "2026-07-27-13w-bill", "2026-07-23-10y-note"]
    assert "5y note of 2026-07-27" in r["letter_line"]


def test_grades_restate_the_digestion_engine_rather_than_recomputing_it():
    df = _auction_frame(last_10y="weak")
    payload = eng_auctions.analyze(df)
    r = reportcard.report_cards(payload, _spread(), _srf(), _reserves(), auctions_frame=df)
    published = {(row["date"], row["tenor"]): row for row in payload["recent_auctions"]}
    for c in r["cards"]:
        src = published[(c["auction_date"], c["tenor"])]
        g = c["grades"]
        assert g["btc_z"] == src["btc_z"]
        assert g["pd_share_z"] == src["pd_share_z"]
        assert g["composite_z"] == src["score"]
        # the indirect z is recovered from the published composite identity
        rebuilt = (reportcard.W_BTC * (-g["btc_z"]) + reportcard.W_PD * g["pd_share_z"]
                   + reportcard.W_IND * (-g["indirect_z"]))
        assert abs(rebuilt - g["composite_z"]) < 0.03


def test_grade_bands_are_the_ones_the_method_states():
    assert reportcard._grade(-2.0) == "A"
    assert reportcard._grade(-1.0) == "A"
    assert reportcard._grade(-0.4) == "B"
    assert reportcard._grade(0.0) == "C"
    assert reportcard._grade(0.35) == "C"
    assert reportcard._grade(0.9) == "D"
    assert reportcard._grade(1.4) == "F"
    assert reportcard._grade(None) is None


def test_without_the_raw_frame_the_cards_say_what_they_lost():
    r = _cards(frame=False)
    assert r["ok"]
    card = r["cards"][0]
    assert card["cusip"] is None and card["size_b"] is None
    assert card["issue_date"] is None
    assert card["grades"]["indirect_share"] is None
    assert card["grades"]["indirect_z"] is not None       # still restated from the composite
    assert card["slug"] == f"{LAST_10Y}-10y-note"         # tenor string carries the identity
    assert r["demand_fatigue"] == []
    assert any("no auction frame supplied" in c for c in r["caveats"])
    # the reserve leg falls back to the auction week when there is no issue date
    assert card["event_study"]["reserves"]["week_ending"] == "2026-08-12"


def test_srf_ops_frame_is_accepted_as_well_as_the_series():
    idx = pd.bdate_range("2025-12-01", "2026-08-20")
    frame = pd.DataFrame({"accepted": 0.0, "submitted": 0.0}, index=idx)
    frame.loc[pd.Timestamp("2026-08-07"), "accepted"] = 9.0
    r = _cards(srf=frame)
    assert r["cards"][0]["event_study"]["srf"]["max_b"] == 9.0


def test_reserves_accepted_in_millions_or_billions():
    in_b = _reserves() / 1000.0
    r_m, r_b = _cards(), _cards(reserves=in_b)
    a = r_m["cards"][0]["event_study"]["reserves"]
    b = r_b["cards"][0]["event_study"]["reserves"]
    assert a["change_b"] == b["change_b"] == -5.0
    assert a["level_b"] == b["level_b"]
