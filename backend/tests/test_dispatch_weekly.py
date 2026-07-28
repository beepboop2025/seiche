"""The Monday flagship: fixed section order, stable call IDs, honest grading.

The whole value of a pre-registered weekly is that it cannot quietly rewrite
last week's expectations, so the tests here are mostly about memory: the calls
carry forward, they grade against a later snapshot, misses print first, and a
same-day rebuild reproduces the issue byte for byte. Synthetic data only.
"""

import json

import pytest

from seiche.dispatch_weekly import (
    TAG,
    build_weekly,
    issue_number,
    write_weekly,
)

SECTIONS = [
    "## 1 · The week in one paragraph",
    "## 2 · The calendar",
    "## 3 · Supply",
    "## 4 · Reserves",
    "## 5 · Pre-registered calls",
    "## 6 · Last week's calls, graded",
    "## 7 · What would change the desk's mind this week",
]


# ---------------------------------------------------------------------------
# a board with every engine the weekly reads, so the fixed skeleton is
# exercised live rather than in its dark-engine fallbacks
# ---------------------------------------------------------------------------
def _full_snap(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["calendar"] = {
        "fomc_next_90d": [{"date": "2026-07-15", "days_until": 2}],
        "corporate_tax_next_90d": [{"date": "2026-09-15", "days_until": 64}],
        "upcoming_settlements": [{"date": "2026-07-16", "amount_b": 95.0}],
        "crunch_windows": [{"date": "2026-07-17", "reason": "month-end pressure",
                            "worst_case_b": 3013.7, "settlement_b": 74.0}],
    }
    snap["engines"]["kink"] = {
        "ok": True, "kink_reserves_b": 3634.4, "current_reserves_b": 3062.1,
        "distance_b": -572.3, "drain_per_bday_b": 2.39, "days_to_kink": None,
        "r2": 0.617, "consistency": 0.87,
        "observed_spread_now_bp": -3.7, "predicted_spread_now_bp": -2.1,
    }
    snap["engines"]["runway"] = {
        "ok": True,
        "scenarios": {
            "base": {"path": [["2026-07-08", 3062.1], ["2026-07-15", 3050.0]],
                     "crossing_date": None, "end_reserves_b": 2990.0,
                     "verdict": "no crossing inside 13 weeks"},
            "fast_drain": {"path": [["2026-07-08", 3062.1], ["2026-07-15", 3030.0]],
                           "crossing_date": "2026-09-30", "end_reserves_b": 2900.0,
                           "verdict": "crosses week 11"},
            "slow": {"path": [["2026-07-08", 3062.1], ["2026-07-15", 3058.0]],
                     "crossing_date": None, "end_reserves_b": 3020.0,
                     "verdict": "no crossing inside 13 weeks"},
        },
        "assumptions": {"trailing_drift_b_per_week": -8.0, "drift_window_weeks": 13,
                        "qt_pace_b_per_month": 25.0, "tga_now_b": 820.0,
                        "tga_median_b": 760.0, "tga_p75_b": 850.0, "rrp_now_b": 0.7,
                        "settlements_gross_b": 169.0, "settlement_passthrough": 0.25,
                        "start_reserves_b": 3062.1, "start_date": "2026-07-08",
                        "kink_reserves_b": 3634.4},
        "caveats": ["arithmetic on stated assumptions, not a forecast of policy"],
    }
    snap["engines"]["supplydesk"] = {
        "ok": True, "asof": "2026-07-13", "horizon_end": "2026-08-10",
        "announced_through": "2026-07-23",
        "rows": [
            {"date": "2026-07-16", "bills_gross_b": 190.0, "coupons_gross_b": 0.0,
             "maturing_b": 95.0, "net_new_cash_b": 95.0,
             "projected": False, "amount_estimated": False},
            {"date": "2026-07-23", "bills_gross_b": 180.0, "coupons_gross_b": 42.0,
             "maturing_b": 200.0, "net_new_cash_b": 22.0,
             "projected": False, "amount_estimated": True},
            {"date": "2026-07-30", "bills_gross_b": 185.0, "coupons_gross_b": 0.0,
             "maturing_b": 105.0, "net_new_cash_b": 80.0,
             "projected": True, "amount_estimated": False},
        ],
        "totals": {"gross_b": 597.0, "maturing_b": 400.0, "net_new_cash_b": 197.0},
        "heaviest_day": {"date": "2026-07-16", "net_new_cash_b": 95.0},
        "caveats": ["maturing includes Fed SOMA holdings, which roll over at auction"],
    }
    snap["engines"]["stigma"] = {
        "ok": True, "stigma_score": 12.0,
        "takeup": {"latest_b": 0.0, "max20_b": 0.12, "classification": "de_minimis"},
        "letter_line": "Repo held under the SRF ceiling for all of the last 20 sessions.",
    }
    snap["engines"]["rdenowcast"] = {
        "ok": True, "ours_bp_per_1pct": -0.37, "nyfed_bp_per_1pct": -0.29,
        "nyfed_band_68": [-0.51, -0.08], "within_68_band": True, "direction_agree": True,
        "nowcast_lead_days": 22, "nyfed_asof": "2026-06-30",
        "scorecard_summary": {"n": 18, "within_band": 12, "direction_agree": 16,
                              "mean_abs_diff_bp": 0.14},
    }
    snap["engines"]["officialbid"] = {"ok": True, "classification": "rotation",
                                      "letter_line": "Foreign officials cut custody by $9.9B."}
    snap["deep"]["turn"] = {
        "ok": True,
        "next_turn": {"date": "2026-07-16", "mode": "month_end", "forecast_bp": 4.8,
                      "published": "naive", "band_bp": [0.9, 8.8], "severity": 2},
        "recent_turns": [{"date": "2026-06-30", "mode": "quarter_end", "slosh_bp": 6.0}],
    }
    snap["deep"]["montecarlo"]["fan"] = [
        {"h": 5, "p10": 38.4, "median": 41.0, "p90": 43.6},
        {"h": 21, "p10": 33.8, "median": 38.8, "p90": 45.4},
    ]
    snap["deep"]["modelcourt"] = {
        "ok": True, "ensemble": {"p": 0.082, "rule": "skill_weighted"},
        "dispersion": {"spread": 0.088},
        "adjudication": "Model Court, 5bd event odds: 4 models span 5.2 to 14.0 pct.",
        "ledger_status": "no ledger yet",
    }
    return snap


@pytest.fixture()
def week_snap(fake_snap):
    return _full_snap(fake_snap)


# ---------------------------------------------------------------------------
# shape and skeleton
# ---------------------------------------------------------------------------
def test_issue_shape_and_slug(week_snap):
    d = build_weekly(week_snap, date="2026-07-13", issue_no=1)
    assert d["slug"] == "2026-07-13-week-ahead"
    assert d["tag"] == TAG == "WEEK AHEAD"
    assert set(d) == {"slug", "title", "date", "tag", "summary", "free_md", "desk_md", "state"}
    assert "Issue 1" in d["free_md"]


def test_section_order_is_invariant(week_snap):
    """Numbered, fixed, and in this order every week: a regular extracts the
    delta by scrolling to a section number, not by reading the whole issue."""
    d = build_weekly(week_snap, date="2026-07-13", issue_no=1)
    positions = [d["free_md"].find(h) for h in SECTIONS]
    assert all(p >= 0 for p in positions), list(zip(SECTIONS, positions))
    assert positions == sorted(positions)


def test_section_order_holds_on_a_dark_board(fake_snap):
    """Every section stays in the skeleton and says the engine is dark rather
    than vanishing and shifting the numbering under the reader."""
    d = build_weekly(fake_snap, date="2026-07-13", issue_no=1)
    positions = [d["free_md"].find(h) for h in SECTIONS]
    assert all(p >= 0 for p in positions)
    assert positions == sorted(positions)
    assert "supply desk is dark" in d["free_md"]
    assert "runway projection is dark" in d["free_md"]
    assert "kink fit is dark" in d["free_md"]
    assert "RDE nowcast is dark" in d["free_md"]


def test_no_dashes_anywhere(week_snap):
    d = build_weekly(week_snap, date="2026-07-13", issue_no=4)
    for field in ("title", "summary", "free_md", "desk_md"):
        assert "—" not in d[field] and "–" not in d[field], field


def test_engine_dashes_are_sanitized_not_fatal(week_snap):
    snap = json.loads(json.dumps(week_snap))
    snap["calendar"]["crunch_windows"][0]["reason"] = "month-end — with an em dash"
    d = build_weekly(snap, date="2026-07-13", issue_no=1)
    assert "—" not in d["free_md"]
    assert "month-end" in d["free_md"]


def test_lint_gate_blocks_a_bad_issue(week_snap):
    """The publish-blocking lint is imported from the daily letter, not
    reimplemented: a malformed ordinal reaching the copy refuses the issue."""
    snap = json.loads(json.dumps(week_snap))
    snap["deep"]["tell"]["plumbing_pctl"] = None
    snap["deep"]["tell"]["market_pctl"] = None
    snap["engines"]["supplydesk"]["announced_through"] = "the 53th session"
    with pytest.raises(SystemExit):
        build_weekly(snap, date="2026-07-13", issue_no=1)


def test_no_board_no_issue():
    with pytest.raises(SystemExit):
        build_weekly({"engines": {"composite": {}}}, date="2026-07-13")


# ---------------------------------------------------------------------------
# 2 the calendar
# ---------------------------------------------------------------------------
def test_calendar_carries_every_dated_event_with_impact(week_snap):
    d = build_weekly(week_snap, date="2026-07-13", issue_no=1)
    md = d["free_md"]
    assert "| date | event | expected funding impact | what the desk watches |" in md
    assert "2026-07-16 (Thu)" in md            # settlement, with its weekday
    assert "+95B net new cash" in md           # from the supply desk, not gross
    assert "FOMC decision" in md and "2026-07-15" in md
    assert "month end turn" in md
    assert "Flagged crunch window" in md and "2026-07-17" in md
    # a tax date 64 days out is outside the ten day window and stays out
    assert "Corporate tax date" not in md


def test_calendar_falls_back_to_gross_settlements_when_supply_is_dark(week_snap):
    snap = json.loads(json.dumps(week_snap))
    snap["engines"]["supplydesk"] = {"ok": False, "reason": "no auction history"}
    d = build_weekly(snap, date="2026-07-13", issue_no=1)
    assert "Auction settlement" in d["free_md"]
    assert "gross settlement and not net new cash" in d["free_md"]


# ---------------------------------------------------------------------------
# 3 supply, 4 reserves
# ---------------------------------------------------------------------------
def test_supply_table_marks_announced_against_projected(week_snap):
    d = build_weekly(week_snap, date="2026-07-13", issue_no=1)
    md = d["free_md"]
    assert "| settles | bills $B | coupons $B | maturing $B | net new cash $B | status |" in md
    assert "| announced |" in md
    assert "| projected |" in md
    assert "| announced, size estimated |" in md
    assert "Announcements run through 2026-07-23" in md


def test_reserves_table_carries_the_three_legs_and_the_external_check(week_snap):
    d = build_weekly(week_snap, date="2026-07-13", issue_no=1)
    md = d["free_md"]
    assert "| leg | 13w end level $B | kink crossing |" in md
    assert "| base | 2,990 | none inside thirteen weeks |" in md
    assert "| fast drain | 2,900 | 2026-09-30 |" in md
    assert "3,634" in md and "3,062" in md and "572" in md      # the kink distance
    assert "Reserve Demand Elasticity" in md
    assert "-0.37" in md and "-0.29" in md                      # ours against theirs


# ---------------------------------------------------------------------------
# 5 pre-registered calls
# ---------------------------------------------------------------------------
def test_calls_are_registered_with_stable_ids_and_numbers(week_snap):
    d = build_weekly(week_snap, date="2026-07-13", issue_no=7)
    calls = d["state"]["calls"]
    assert 3 <= len(calls) <= 5
    assert [c["id"] for c in calls] == [f"W7-{i}" for i in range(1, len(calls) + 1)]
    for c in calls:
        assert c["expected"] and c["rule"] and c["resolve_by"]
        assert c["issued"] == "2026-07-13"
        assert f"**{c['id']}**" in d["free_md"]
        assert c["resolve_by"] in d["free_md"]


def test_calls_come_from_live_board_state(week_snap):
    d = build_weekly(week_snap, date="2026-07-13", issue_no=7)
    kinds = [c["kind"] for c in d["state"]["calls"]]
    # dated events lead the candidate order, levels fill behind them
    assert kinds[:4] == ["turn", "supply", "srf", "reserves"]
    turn = d["state"]["calls"][0]
    assert turn["lo"] == 0.9 and turn["hi"] == 8.8 and turn["turn_date"] == "2026-07-16"
    srf = d["state"]["calls"][2]
    assert srf["threshold"] == 1.0 and srf["baseline"] == 0.12
    supply = d["state"]["calls"][1]
    # only a date that survives into next week's forward table is registered
    assert supply["settle_date"] == "2026-07-30"
    assert supply["tol"] == 8.0                       # 10 pct of 80, above the $5B floor


def test_calls_are_all_dated_inside_the_week(week_snap):
    d = build_weekly(week_snap, date="2026-07-13", issue_no=7)
    for c in d["state"]["calls"]:
        assert c["resolve_by"] <= "2026-07-20", c["id"]


def test_composite_band_uses_a_one_week_fan_not_a_monthly_one(week_snap):
    """A 21 session fan is the wrong width for a seven day claim, so the call
    takes the 5 session band and says where the band came from."""
    d = build_weekly(week_snap, date="2026-07-13", issue_no=7)
    comp = [c for c in d["state"]["calls"] if c["kind"] == "composite"][0]
    assert (comp["lo"], comp["hi"]) == (38.4, 43.6)
    assert "at 5 sessions" in comp["expected"]

    snap = json.loads(json.dumps(week_snap))
    snap["deep"]["montecarlo"]["fan"] = [{"h": 21, "p10": 33.8, "median": 38.8, "p90": 45.4}]
    d2 = build_weekly(snap, date="2026-07-13", issue_no=7)
    comp2 = [c for c in d2["state"]["calls"] if c["kind"] == "composite"][0]
    assert (comp2["lo"], comp2["hi"]) == (36.0, 46.0)
    assert "no fan at a one week horizon" in comp2["expected"]


def test_thin_board_still_registers_falsifiable_calls(fake_snap):
    d = build_weekly(fake_snap, date="2026-07-13", issue_no=1)
    calls = d["state"]["calls"]
    assert calls and all(c["rule"] for c in calls)
    assert {"composite", "regime"} <= {c["kind"] for c in calls}


# ---------------------------------------------------------------------------
# 6 grading: hit, miss and open all covered against a synthetic prior week
# ---------------------------------------------------------------------------
def _prior_state(date="2026-07-06"):
    """Last week's issue, hand built so this week's grading has a hit, a miss
    and an open in it."""
    return {
        "date": date,
        "issue": 6,
        "calls_prev": [],
        "calls": [
            {"id": "W6-1", "kind": "composite", "issue": 6, "issued": date, "carried": 0,
             "claim": "The composite reads between 38.0 and 44.0 on next week's board.",
             "expected": "38.0 to 44.0", "rule": "hit if inside the band",
             "lo": 38.0, "hi": 44.0, "at_issue": 41.0, "resolve_by": "2026-07-13"},
            {"id": "W6-2", "kind": "srf", "issue": 6, "issued": date, "carried": 0,
             "claim": "SRF take-up stays under $1B on every session of the week.",
             "expected": "under $1B", "rule": "hit if the twenty session maximum stays under it",
             "threshold": 1.0, "baseline": 0.05, "resolve_by": "2026-07-13"},
            {"id": "W6-3", "kind": "reserves", "issue": 6, "issued": date, "carried": 0,
             "claim": "Reserves print near $3,200B on next week's H.4.1.",
             "expected": "$3,200B, tolerance $25B", "rule": "hit if within tolerance",
             "target": 3200.0, "tol": 25.0, "resolve_by": "2026-07-13"},
            {"id": "W6-4", "kind": "turn", "issue": 6, "issued": date, "carried": 0,
             "claim": "The month end turn on 2026-07-31 prints inside +0.9 to +8.8bp.",
             "expected": "+0.9 to +8.8bp", "rule": "hit if the realized slosh is inside",
             "turn_date": "2026-07-31", "lo": 0.9, "hi": 8.8, "resolve_by": "2026-08-03"},
        ],
        "record_prev": {"graded": 0, "hit": 0, "miss": 0},
        "record": {"graded": 8, "hit": 5, "miss": 3},
    }


def test_prior_week_is_graded_hit_miss_and_open(week_snap):
    d = build_weekly(week_snap, date="2026-07-13", state=_prior_state(), issue_no=7)
    md = d["free_md"]
    verdicts = {}
    for line in md.splitlines():
        if line.startswith("| W6-"):
            cells = [c.strip() for c in line.split("|")]
            verdicts[cells[1]] = cells[2]
    assert verdicts["W6-1"] == "**HIT**"     # composite 41.0 inside 38 to 44
    assert verdicts["W6-2"] == "**HIT**"     # take-up max20 0.12 under $1B
    assert verdicts["W6-3"] == "**MISS**"    # reserves 3,062.1 against a 3,200 target
    assert verdicts["W6-4"] == "**OPEN**"    # the turn is not in the board's record yet
    assert "Last week: 1 miss, 2 hit, 1 still open." in md


def test_misses_print_first(week_snap):
    d = build_weekly(week_snap, date="2026-07-13", state=_prior_state(), issue_no=7)
    rows = [line for line in d["free_md"].splitlines() if line.startswith("| W6-")]
    assert "**MISS**" in rows[0]
    assert "**OPEN**" in rows[-1]


def test_graded_rows_print_the_actual_beside_the_expected(week_snap):
    d = build_weekly(week_snap, date="2026-07-13", state=_prior_state(), issue_no=7)
    md = d["free_md"]
    assert "$3,200B, tolerance $25B" in md      # what was expected
    assert "$3,062B" in md                      # what actually printed
    assert "$0.12B twenty session maximum" in md


def test_lifetime_record_accrues_and_opens_do_not_count(week_snap):
    d = build_weekly(week_snap, date="2026-07-13", state=_prior_state(), issue_no=7)
    assert d["state"]["record"] == {"graded": 11, "hit": 7, "miss": 4}
    assert "Lifetime the desk has resolved 11 calls and hit 7 of them, 64%." in d["free_md"]


def test_open_calls_carry_one_week_then_drop(week_snap):
    d = build_weekly(week_snap, date="2026-07-13", state=_prior_state(), issue_no=7)
    carried = [c for c in d["state"]["calls"] if c["id"] == "W6-4"]
    assert carried and carried[0]["carried"] == 1
    assert "Open calls carry one more week" in d["free_md"]
    assert "W6-4" not in d["free_md"].split("## 5 · Pre-registered calls")[1].split(
        "## 6 ·")[0]   # a carried call is not re-advertised as a fresh call

    # a second week without the data drops it, and the letter says it dropped
    d2 = build_weekly(week_snap, date="2026-07-20", state=d["state"], issue_no=8)
    assert "Dropped unresolved" in d2["free_md"] and "W6-4" in d2["free_md"]
    assert not [c for c in d2["state"]["calls"] if c["id"] == "W6-4"]


def test_turn_call_grades_once_the_slosh_is_on_the_record(week_snap):
    snap = json.loads(json.dumps(week_snap))
    snap["deep"]["turn"]["recent_turns"].append(
        {"date": "2026-07-31", "mode": "month_end", "slosh_bp": 14.0})
    d = build_weekly(snap, date="2026-07-13", state=_prior_state(), issue_no=7)
    assert "| W6-4 | **MISS**" in d["free_md"]     # 14.0bp is through the top of the band
    assert "+14.0bp realized" in d["free_md"]


def test_supply_call_stays_open_while_the_row_is_projected(week_snap):
    state = {"date": "2026-07-06", "issue": 6, "calls_prev": [],
             "record": {"graded": 0, "hit": 0, "miss": 0},
             "calls": [{"id": "W6-1", "kind": "supply", "issue": 6, "issued": "2026-07-06",
                        "carried": 0, "claim": "The 2026-07-30 settlement lands near +80B.",
                        "expected": "+80B net new cash, tolerance $8.0B",
                        "rule": "hit if announced and within tolerance",
                        "settle_date": "2026-07-30", "target": 80.0, "tol": 8.0,
                        "resolve_by": "2026-07-13"}]}
    d = build_weekly(week_snap, date="2026-07-13", state=state, issue_no=7)
    assert "| W6-1 | **OPEN**" in d["free_md"]
    assert "still projected at +80B, not yet announced" in d["free_md"]

    # once Treasury announces it, the same call grades against the printed figure
    snap = json.loads(json.dumps(week_snap))
    snap["engines"]["supplydesk"]["rows"][2].update({"projected": False, "net_new_cash_b": 83.0})
    d2 = build_weekly(snap, date="2026-07-13", state=state, issue_no=7)
    assert "| W6-1 | **HIT**" in d2["free_md"] and "+83B announced" in d2["free_md"]


def test_dark_engine_makes_a_call_open_never_a_hit(fake_snap):
    d = build_weekly(fake_snap, date="2026-07-13", state=_prior_state(), issue_no=7)
    md = d["free_md"]
    assert "| W6-2 | **OPEN**" in md and "the stigma gauge is dark" in md
    assert "| W6-3 | **OPEN**" in md and "the kink fit is dark" in md
    assert "never scored as a hit" in md


def test_first_issue_says_there_is_nothing_to_grade(week_snap):
    d = build_weekly(week_snap, date="2026-07-13", issue_no=1)
    assert "no prior issue to grade" in d["free_md"]
    assert "first Week Ahead" in d["free_md"]
    assert d["state"]["record"] == {"graded": 0, "hit": 0, "miss": 0}


# ---------------------------------------------------------------------------
# 7 falsifiers, and the continuation
# ---------------------------------------------------------------------------
def test_falsifier_ledger_is_reused_from_the_daily_letter(week_snap):
    d = build_weekly(week_snap, date="2026-07-13", issue_no=1)
    tail = d["free_md"].split("## 7 · What would change the desk's mind this week")[1]
    assert "- **E1**" in tail          # EROSION ledger from dispatch_daily._falsifiers
    assert "today it reads +12" in tail


def test_continuation_carries_the_ledger_and_the_assumptions(week_snap):
    d = build_weekly(week_snap, date="2026-07-13", state=_prior_state(), issue_no=7)
    desk = d["desk_md"]
    assert "### The call ledger" in desk
    assert "resolved 11 calls" in desk
    assert "| id | kind | resolves | grading rule |" in desk
    assert "Reserve path assumptions" in desk
    assert "What the supply table does not know" in desk
    assert "### Still open" in desk and "W6-4" in desk


# ---------------------------------------------------------------------------
# idempotence and the state file
# ---------------------------------------------------------------------------
def test_same_day_rebuild_reproduces_the_issue(week_snap):
    """CI may rebuild the issue for the announce step after writing it; finding
    its own state must not regrade an empty set or renumber the calls."""
    d = build_weekly(week_snap, date="2026-07-13", state=_prior_state(), issue_no=7)
    again = build_weekly(week_snap, date="2026-07-13", state=d["state"], issue_no=7)
    assert again == d


def test_build_is_deterministic(week_snap):
    a = build_weekly(week_snap, date="2026-07-13", issue_no=3)
    b = build_weekly(week_snap, date="2026-07-13", issue_no=3)
    assert a == b


def test_next_week_grades_this_week_not_last(week_snap):
    d1 = build_weekly(week_snap, date="2026-07-13", state=_prior_state(), issue_no=7)
    d2 = build_weekly(week_snap, date="2026-07-20", state=d1["state"], issue_no=8)
    graded_ids = {line.split("|")[1].strip() for line in d2["free_md"].splitlines()
                  if line.startswith("| W")}
    assert any(i.startswith("W7-") for i in graded_ids)
    assert "W6-1" not in graded_ids     # last week's set is not regraded


# ---------------------------------------------------------------------------
# files, index and issue numbering
# ---------------------------------------------------------------------------
def test_write_creates_files_and_prepends_index(week_snap, tmp_path):
    from seiche.dispatch_weekly import MARKER

    (tmp_path / "frontend" / "public" / "dispatches").mkdir(parents=True)
    (tmp_path / "frontend" / "public" / "dispatches" / "index.json").write_text(json.dumps([
        {"slug": "2026-07-10-daily", "title": "old", "date": "2026-07-10",
         "tag": "EROSION", "summary": "old"}
    ]))
    d = build_weekly(week_snap, date="2026-07-13", issue_no=1)
    write_weekly(d, repo_root=tmp_path)

    free = (tmp_path / "frontend" / "public" / "dispatches" / f"{d['slug']}.md").read_text()
    assert MARKER in free
    paid = (tmp_path / "backend" / "seiche" / "dispatches" / f"{d['slug']}.paid.md").read_text()
    assert "continuation" in paid

    idx = json.loads((tmp_path / "frontend" / "public" / "dispatches" / "index.json").read_text())
    assert [e["slug"] for e in idx] == [d["slug"], "2026-07-10-daily"]
    assert set(idx[0]) == {"slug", "title", "date", "tag", "summary"}
    assert idx[0]["tag"] == "WEEK AHEAD"
    assert idx[0]["slug"].endswith("-week-ahead")
    assert idx[0]["summary"] and idx[0]["title"]


def test_rewrite_same_day_does_not_duplicate_index(week_snap, tmp_path):
    d = build_weekly(week_snap, date="2026-07-13", issue_no=1)
    write_weekly(d, repo_root=tmp_path)
    write_weekly(d, repo_root=tmp_path)
    idx = json.loads((tmp_path / "frontend" / "public" / "dispatches" / "index.json").read_text())
    assert len([e for e in idx if e["slug"] == d["slug"]]) == 1


def test_state_goes_to_its_own_file_not_the_daily_letters(week_snap, tmp_path):
    """A Monday runs both letters; two generators writing state.json would eat
    each other's memory."""
    d = build_weekly(week_snap, date="2026-07-13", issue_no=1)
    write_weekly(d, repo_root=tmp_path)
    paid = tmp_path / "backend" / "seiche" / "dispatches"
    assert not (paid / "state.json").exists()
    state = json.loads((paid / "weekly_state.json").read_text())
    assert state["date"] == "2026-07-13"
    assert [c["id"] for c in state["calls"]] == [c["id"] for c in d["state"]["calls"]]


def test_issue_number_counts_weekly_issues_only(tmp_path):
    index = tmp_path / "index.json"
    index.write_text(json.dumps([
        {"slug": "2026-07-13-daily"}, {"slug": "2026-07-12-daily"},
        {"slug": "2026-07-06-week-ahead"}, {"slug": "2026-06-29-week-ahead"},
    ]))
    assert issue_number(index, "2026-07-13-week-ahead") == 3
    # a same-day rewrite of the issue keeps its own number instead of skipping one
    index.write_text(json.dumps([
        {"slug": "2026-07-13-week-ahead"}, {"slug": "2026-07-06-week-ahead"},
    ]))
    assert issue_number(index, "2026-07-13-week-ahead") == 2
    assert issue_number(tmp_path / "missing.json", "2026-07-13-week-ahead") == 1


def test_telegram_digest_carries_the_calls_and_the_link(week_snap):
    from seiche.dispatch_weekly import build_telegram_digest

    d = build_weekly(week_snap, date="2026-07-13", issue_no=2)
    msg = build_telegram_digest(d)
    assert "The Week Ahead" in msg
    assert "W2-1" in msg
    assert f"https://seiche.info/#dispatches/{d['slug']}" in msg
    assert len(msg) < 4096
    assert "—" not in msg and "–" not in msg
