"""The daily dispatch generator: deterministic prose, honest degradation,
correct files. The letter must never invent a number and never publish
without a live composite."""

import json

import pytest

from seiche.dispatch_daily import MARKER, build_dispatch, write_dispatch


def test_build_carries_the_board_numbers(fake_snap):
    d = build_dispatch(fake_snap, prev_value=38.0)
    assert d["slug"] == "2026-07-10-daily"
    assert d["tag"] == "EROSION"
    # the composite value, the regime and the day delta all appear verbatim
    assert "41" in d["free_md"] and "EROSION" in d["free_md"]
    assert "+3.0" in d["free_md"]  # 41 - 38 vs the last published reading
    assert "41" in d["summary"] and "EROSION" in d["summary"]
    # the crunch window from the snapshot reaches the letter
    assert "2026-07-31" in d["free_md"]


def test_build_is_deterministic(fake_snap):
    a = build_dispatch(fake_snap, prev_value=38.0)
    b = build_dispatch(fake_snap, prev_value=38.0)
    assert a == b


def test_no_composite_no_letter():
    with pytest.raises(SystemExit):
        build_dispatch({"engines": {"composite": {}}})


def test_faults_are_reported_not_hidden(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["faults"] = [{"source": "CFTC", "detail": "stale"}]
    d = build_dispatch(snap)
    assert "CFTC" in d["free_md"]


def test_quiet_tape_is_stated(fake_snap):
    # fake_snap has no flagged sonar movers -> the letter says so explicitly
    d = build_dispatch(fake_snap)
    assert "±2.5 robust z" in d["free_md"]


def test_write_creates_files_and_prepends_index(fake_snap, tmp_path):
    (tmp_path / "frontend" / "public" / "dispatches").mkdir(parents=True)
    (tmp_path / "frontend" / "public" / "dispatches" / "index.json").write_text(json.dumps([
        {"slug": "2026-07-09-fat-tail", "title": "old", "date": "2026-07-09",
         "tag": "STRAIN", "summary": "old"}
    ]))
    d = build_dispatch(fake_snap)
    write_dispatch(d, repo_root=tmp_path)

    free = (tmp_path / "frontend" / "public" / "dispatches" / f"{d['slug']}.md").read_text()
    assert MARKER in free
    paid = (tmp_path / "backend" / "seiche" / "dispatches" / f"{d['slug']}.paid.md").read_text()
    assert "forward read" in paid

    idx = json.loads((tmp_path / "frontend" / "public" / "dispatches" / "index.json").read_text())
    assert [e["slug"] for e in idx] == [d["slug"], "2026-07-09-fat-tail"]  # newest first


def test_rewrite_same_day_does_not_duplicate_index(fake_snap, tmp_path):
    d = build_dispatch(fake_snap)
    write_dispatch(d, repo_root=tmp_path)
    write_dispatch(d, repo_root=tmp_path)
    idx = json.loads((tmp_path / "frontend" / "public" / "dispatches" / "index.json").read_text())
    assert len([e for e in idx if e["slug"] == d["slug"]]) == 1


def test_press_para_surfaces_scuttlebutt_flags_display_only():
    from seiche import dispatch_daily
    assert dispatch_daily._press_para({"engines": {}}) == []
    out = dispatch_daily._press_para({"engines": {"scuttlebutt": {
        "flags": ["repo chatter surging (z 2.1 vs own baseline)"]}}})
    assert out and "display only" in out[0] and "feeding no score" in out[0]
    assert "—" not in out[0] and "–" not in out[0]   # house copy rule holds


def test_no_dashes_in_the_letter(fake_snap):
    """House copy rule: the published letter carries no em or en dashes."""
    d = build_dispatch(fake_snap, prev_value=38.0)
    for field in ("title", "summary", "free_md"):
        assert "—" not in d[field] and "–" not in d[field], field


def test_telegram_digest_carries_numbers_and_link(fake_snap):
    from seiche.dispatch_daily import build_telegram_digest

    d = build_dispatch(fake_snap, prev_value=38.0)
    msg = build_telegram_digest(d, fake_snap)
    assert "41" in msg and "EROSION" in msg
    assert f"https://seiche.info/#dispatches/{d['slug']}" in msg
    assert "2026-07-31" in msg  # the crunch window reaches the digest
    assert len(msg) < 4096  # telegram message cap
    assert "—" not in msg and "–" not in msg


def test_announce_fails_loud_without_credentials(fake_snap, monkeypatch):
    from seiche.dispatch_daily import announce_telegram

    monkeypatch.delenv("SEICHE_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SEICHE_TELEGRAM_CHAT_ID", raising=False)
    d = build_dispatch(fake_snap)
    with pytest.raises(SystemExit):
        announce_telegram(d, fake_snap)


# ---------------------------------------------------------------------------
# novelty state: a print is news once, then a standing flag
# ---------------------------------------------------------------------------
def _mover(label, asof, age_d, z):
    return {"label": label, "last": 378.0, "unit": "$M", "level_z": z, "change_z": z,
            "max_abs_z": abs(z), "flag": True, "stale": False, "age_d": age_d, "asof": asof}


def _snap_with_movers(fake_snap, movers):
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["sonar"] = {"ok": True, "movers": movers}
    return snap


def test_new_print_headlines_and_enters_state(fake_snap):
    snap = _snap_with_movers(fake_snap, [_mover("Swap lines (H.4.1)", "2026-07-09", 1, 16.5)])
    d = build_dispatch(snap, date="2026-07-10")
    assert "Swap lines (H.4.1)" in d["title"]
    assert "printed" in d["free_md"]
    assert d["state"]["reported"] == {"Swap lines (H.4.1)": "2026-07-09"}


def test_already_reported_print_does_not_reheadline(fake_snap):
    """The Jul 24-28 failure: the same weekly print headlined five days
    running. With the state carried forward it headlines once, then moves to
    the standing-flags line with a title that says nothing new printed."""
    snap = _snap_with_movers(fake_snap, [_mover("Swap lines (H.4.1)", "2026-07-09", 1, 16.5)])
    d1 = build_dispatch(snap, date="2026-07-10")

    snap2 = _snap_with_movers(fake_snap, [_mover("Swap lines (H.4.1)", "2026-07-09", 2, 16.5)])
    d2 = build_dispatch(snap2, prev_value=41.0, date="2026-07-11", state=d1["state"])
    assert "Swap lines (H.4.1)" not in d2["title"]
    assert d2["title"] != d1["title"]
    assert "Still flagged" in d2["free_md"]
    assert "printed" not in d2["free_md"]  # no movers line dressed as news
    # the standing flag still carries its number and its print date
    assert "Swap lines (H.4.1)" in d2["free_md"] and "2026-07-09" in d2["free_md"]


def test_freshest_novel_print_takes_the_headline(fake_snap):
    """The buried-SRF failure: a loud week-old print must not outrank a
    quieter print from last night when both are news."""
    snap = _snap_with_movers(fake_snap, [
        _mover("Swap lines (H.4.1)", "2026-07-04", 6, 16.5),
        _mover("SRF accepted", "2026-07-09", 1, 12.8),
    ])
    d = build_dispatch(snap, date="2026-07-10")
    assert "SRF accepted" in d["title"] and "overnight" in d["title"]


def test_newer_print_of_held_series_is_news_again(fake_snap):
    snap = _snap_with_movers(fake_snap, [_mover("Swap lines (H.4.1)", "2026-07-09", 1, 16.5)])
    d1 = build_dispatch(snap, date="2026-07-10")
    snap2 = _snap_with_movers(fake_snap, [_mover("Swap lines (H.4.1)", "2026-07-16", 1, 9.0)])
    d2 = build_dispatch(snap2, date="2026-07-17", state=d1["state"])
    assert "Swap lines (H.4.1)" in d2["title"]


def test_same_day_rebuild_reproduces_the_letter(fake_snap):
    """CI rebuilds the letter for the Telegram announce the better part of an
    hour after writing it; finding its own morning's state must not change
    the novelty read."""
    snap = _snap_with_movers(fake_snap, [_mover("Swap lines (H.4.1)", "2026-07-09", 1, 16.5)])
    d = build_dispatch(snap, date="2026-07-10")
    again = build_dispatch(snap, date="2026-07-10", state=d["state"])
    assert again == d


def test_unflagged_series_falls_out_of_state(fake_snap):
    snap = _snap_with_movers(fake_snap, [_mover("Swap lines (H.4.1)", "2026-07-09", 1, 16.5)])
    d1 = build_dispatch(snap, date="2026-07-10")
    d2 = build_dispatch(fake_snap, date="2026-07-11", state=d1["state"])  # no sonar at all
    assert d2["state"]["reported"] == {}


def test_write_persists_state_sidecar(fake_snap, tmp_path):
    snap = _snap_with_movers(fake_snap, [_mover("Swap lines (H.4.1)", "2026-07-09", 1, 16.5)])
    d = build_dispatch(snap, date="2026-07-10")
    write_dispatch(d, repo_root=tmp_path)
    state = json.loads((tmp_path / "backend" / "seiche" / "dispatches" / "state.json").read_text())
    assert state["date"] == "2026-07-10"
    assert state["reported"] == {"Swap lines (H.4.1)": "2026-07-09"}


def test_quiet_day_pulls_the_forward_read(fake_snap):
    """A day with no new print borrows its spine from the forward odds, which
    recompute daily even when the tape does not."""
    d = build_dispatch(fake_snap, date="2026-07-10")  # no sonar movers at all
    assert "Bathymetry puts the odds" in d["free_md"]
    assert "15%" in d["free_md"]   # bathymetry h5 from the fixture
    assert "17%" in d["free_md"]   # the learned model's read


def test_held_only_day_pulls_the_forward_read(fake_snap):
    snap = _snap_with_movers(fake_snap, [_mover("Swap lines (H.4.1)", "2026-07-05", 5, 16.5)])
    state = {"date": "2026-07-09", "reported": {"Swap lines (H.4.1)": "2026-07-05"}}
    d = build_dispatch(snap, date="2026-07-10", state=state)
    assert "Still flagged" in d["free_md"]
    assert "Bathymetry puts the odds" in d["free_md"]


def test_news_day_leaves_forward_read_to_the_desk_section(fake_snap):
    snap = _snap_with_movers(fake_snap, [_mover("SRF accepted", "2026-07-09", 1, 12.8)])
    d = build_dispatch(snap, date="2026-07-10")
    assert "Bathymetry puts the odds" not in d["free_md"]
    assert "Bathymetry puts the odds" in d["desk_md"]  # the desk read still carries it


def test_held_and_quiet_variants_carry_no_dashes(fake_snap):
    """House copy rule holds across the new date-seeded variants."""
    held_state = {"date": "2026-07-09", "reported": {"Swap lines (H.4.1)": "2026-07-05"}}
    for day in ("2026-07-10", "2026-07-11", "2026-07-12"):
        snap = _snap_with_movers(fake_snap, [_mover("Swap lines (H.4.1)", "2026-07-05", 5, 16.5)])
        d = build_dispatch(snap, date=day, state=held_state)
        for field in ("title", "summary", "free_md"):
            assert "—" not in d[field] and "–" not in d[field], (day, field)


# ---------------------------------------------------------------------------
# skeleton v2: lint, live falsifiers, attribution, kink, de minimis, memory
# ---------------------------------------------------------------------------
def test_lint_blocks_bad_copy():
    from seiche.dispatch_daily import lint_letter
    assert lint_letter("the 53th percentile") == ["malformed ordinal ('53th')"]
    assert "miscased SRF ('Srf')" in lint_letter("Srf or discount window")
    assert any("em dash" in i for i in lint_letter("a — b"))
    assert any("None" in i for i in lint_letter("reads None today"))
    assert lint_letter("None of those printed") == []           # legit English
    assert lint_letter("the 53rd percentile, the 21st, the 112th") == []


def test_ordinal_helper():
    from seiche.dispatch_daily import _ordinal
    assert _ordinal(53) == "53rd"
    assert _ordinal(11) == "11th"
    assert _ordinal(21) == "21st"
    assert _ordinal(None) == "?"


def test_letter_never_publishes_lint_violations(fake_snap):
    """The gate is wired into build_dispatch: a malformed ordinal survives
    the engine-text sanitizer, reaches the letter, and the build refuses."""
    snap = json.loads(json.dumps(fake_snap))
    snap["deep"]["ml"]["verdict"] = "elevated, the 53th percentile of history"
    with pytest.raises(SystemExit):
        build_dispatch(snap)


def test_engine_dashes_are_sanitized_not_fatal(fake_snap):
    """An engine's em dash is the engine's problem, not the reader's: the
    letter cleans it at the interpolation point instead of refusing to
    publish over someone else's copy."""
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["scuttlebutt"] = {"flags": ["press piece — with an em dash"]}
    d = build_dispatch(snap)
    assert "press piece" in d["free_md"]
    assert "—" not in d["free_md"]


def test_fixed_skeleton_sections_present(fake_snap):
    d = build_dispatch(fake_snap, issue_no=19)
    for header in ("## 1 · The reading", "## 2 · What moved", "## 3 · The Tell",
                   "## 4 · Reserve scarcity", "## 5 · The official sector",
                   "## 6 · The dates that matter", "## 7 · What the board is honest about"):
        assert header in d["free_md"], header
    assert "Issue 19" in d["free_md"]


def test_dark_sections_say_so(fake_snap):
    """No kink, no officialbid engine in the fixture: the sections stay in
    the skeleton and say the engine is dark instead of vanishing."""
    d = build_dispatch(fake_snap)
    assert "kink engine is dark" in d["free_md"]
    assert "official sector engine is dark" in d["free_md"]


def test_kink_section_carries_the_fit(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["kink"] = {
        "ok": True, "kink_reserves_b": 3634.4, "current_reserves_b": 3062.1,
        "distance_b": -572.3, "drain_per_bday_b": 2.39, "days_to_kink": None,
        "r2": 0.617, "consistency": 0.87,
        "observed_spread_now_bp": -3.7, "predicted_spread_now_bp": -2.1,
    }
    d = build_dispatch(snap)
    assert "3,634" in d["free_md"] and "3,062" in d["free_md"]
    assert "below the estimate" in d["free_md"]
    assert "Reserve Demand Elasticity" in d["free_md"]
    assert "R² 0.62" in d["free_md"]
    assert "watches the slope, not the distance" in d["free_md"]


def test_deminimis_print_gets_scale_context(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["sonar"] = {"ok": True, "movers": [{
        "label": "Central bank liquidity swaps", "last": 378.0, "unit": "$M",
        "level_z": 1.5, "change_z": 16.5, "max_abs_z": 16.5, "flag": True,
        "stale": False, "age_d": 1, "asof": "2026-07-09",
        "hist_peak_abs": 449000.0, "share_of_peak": 0.0008, "woke_from_zero": True,
    }]}
    d = build_dispatch(snap, date="2026-07-10")
    assert "For scale" in d["free_md"]
    assert "449,000" in d["free_md"]
    assert "de minimis" in d["free_md"]
    assert "first nonzero print" in d["free_md"]


def test_falsifiers_carry_live_numbers(fake_snap):
    d = build_dispatch(fake_snap)   # EROSION, tell +12
    assert "- **E1**" in d["desk_md"]
    assert "today it reads +12" in d["desk_md"]


def test_falsifier_resolution_on_regime_change(fake_snap):
    state = {"date": "2026-07-09",
             "letter": {"regime": "STRAIN", "value": 47.0, "decomp": {}, "crunch": {}}}
    d = build_dispatch(fake_snap, date="2026-07-10", state=state)
    assert "written for STRAIN" in d["desk_md"]
    assert "moved to EROSION" in d["desk_md"]


def test_day_change_attribution_from_letter_memory(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["composite"]["decomposition"] = [
        {"component": "kink", "score": 70.0, "contribution": 9.0, "status": "OK"},
        {"component": "auctions", "score": 40.0, "contribution": 4.0, "status": "OK"},
    ]
    state = {"date": "2026-07-09",
             "letter": {"regime": "EROSION", "value": 38.0,
                        "decomp": {"kink": 8.0, "auctions": 4.5}, "crunch": {}}}
    d = build_dispatch(snap, date="2026-07-10", state=state)
    assert "Change gets attributed" in d["free_md"]
    assert "reserve scarcity (kink) +1.0" in d["free_md"]
    assert "auction digestion (auctions) -0.5" in d["free_md"]


def test_calendar_revision_is_acknowledged(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["weather"]["crunch_windows"] = [
        {"date": "2026-07-31", "reason": "auction settlement", "settlement_b": 95.0,
         "worst_case_b": 3014.0}]
    state = {"date": "2026-07-09",
             "letter": {"regime": "EROSION", "value": 38.0, "decomp": {},
                        "crunch": {"date": "2026-07-31", "settlement_b": 266.0,
                                   "worst_case_b": 3014.0}}}
    d = build_dispatch(snap, date="2026-07-10", state=state)
    assert "Revisions get said, not slipped" in d["free_md"]
    assert "266" in d["free_md"] and "95" in d["free_md"]


def test_worst_case_names_its_referent(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["weather"]["crunch_windows"] = [
        {"date": "2026-07-31", "reason": "auction settlement", "worst_case_b": 3014.0}]
    d = build_dispatch(snap)
    assert "worst case reserves near $3,014B after the drain" in d["free_md"]


def test_fomc_eve_gets_a_stanza(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["calendar"] = {"fomc_next_90d": [{"date": "2026-07-11", "days_until": 1}]}
    d = build_dispatch(snap, date="2026-07-10")
    assert "FOMC decides tomorrow" in d["free_md"]
    assert "IORB and ON RRP settings" in d["free_md"]
    assert "grades this stanza against the statement" in d["free_md"]


def test_court_adjudicates_by_brier(fake_snap):
    d = build_dispatch(fake_snap)
    assert "Adjudication, not averaging" in d["desk_md"]
    assert "the dated term structure (Swell)" in d["desk_md"]   # brier 0.04 beats 0.05
    assert "0.04" in d["desk_md"]
    # the stack's pooled read is printed with its dispersion
    assert "19%" in d["desk_md"] and "0.03" in d["desk_md"]


def test_proof_numbers_reach_the_honesty_coda(fake_snap):
    d = build_dispatch(fake_snap)
    assert "event recall 79%" in d["free_md"]
    assert "base rate of 6%" in d["free_md"]
    assert "run precision 61%" in d["free_md"]


def test_odds_ledger_appends_once(fake_snap, tmp_path):
    d = build_dispatch(fake_snap, date="2026-07-10")
    assert {r["model"] for r in d["odds"]} >= {"bathymetry", "ml", "swell", "stacker"}
    write_dispatch(d, repo_root=tmp_path)
    write_dispatch(d, repo_root=tmp_path)   # same-day rebuild must not double-append
    ledger = (tmp_path / "backend" / "seiche" / "dispatches" / "odds_ledger.jsonl").read_text()
    rows = [json.loads(l) for l in ledger.splitlines()]
    assert len([r for r in rows if r["date"] == "2026-07-10"]) == len(d["odds"])


def test_state_letter_memory_roundtrip(fake_snap):
    d1 = build_dispatch(fake_snap, date="2026-07-10")
    assert d1["state"]["letter"]["regime"] == "EROSION"
    # same-day rebuild with the new state reproduces the letter exactly
    again = build_dispatch(fake_snap, date="2026-07-10", state=d1["state"])
    assert again == d1


def test_runway_prints_crossing_when_one_exists(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["kink"] = {"ok": True, "kink_reserves_b": 3634.4,
                               "current_reserves_b": 3700.0, "distance_b": 65.6,
                               "drain_per_bday_b": 2.39, "days_to_kink": 52,
                               "r2": 0.62, "consistency": 0.87,
                               "observed_spread_now_bp": -3.7, "predicted_spread_now_bp": -2.1}
    snap["engines"]["runway"] = {"ok": True, "scenarios": {
        "base": {"crossing_date": "2026-10-14", "end_reserves_b": 3600.0},
        "fast_drain": {"crossing_date": "2026-09-30", "end_reserves_b": 3560.0},
        "slow": {"crossing_date": "2026-11-04", "end_reserves_b": 3640.0}}}
    d = build_dispatch(snap)
    assert "through the kink on **2026-10-14**" in d["free_md"]
    assert "bracket it between 2026-09-30 and 2026-11-04" in d["free_md"]


def test_runway_says_so_when_no_crossing(fake_snap):
    """A projection that reaches no threshold is still a reading."""
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["kink"] = {"ok": True, "kink_reserves_b": 3634.4,
                               "current_reserves_b": 3700.0, "distance_b": 65.6,
                               "drain_per_bday_b": 0.4, "days_to_kink": None,
                               "r2": 0.62, "consistency": 0.87,
                               "observed_spread_now_bp": -3.7, "predicted_spread_now_bp": -2.1}
    snap["engines"]["runway"] = {"ok": True, "scenarios": {
        "base": {"crossing_date": None, "end_reserves_b": 3668.8},
        "fast_drain": {"crossing_date": None, "end_reserves_b": 3660.6},
        "slow": {"crossing_date": None, "end_reserves_b": 3684.4}}}
    d = build_dispatch(snap)
    assert "no kink crossing inside thirteen weeks" in d["free_md"]
    assert "3,669" in d["free_md"]


def test_official_bid_line_reaches_the_letter(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["officialbid"] = {
        "ok": True, "classification": "rotation",
        "letter_line": "Foreign officials cut their Fed custody Treasuries by $9.9B over 13 weeks."}
    d = build_dispatch(snap)
    assert "$9.9B" in d["free_md"]
    assert "official sector engine is dark" not in d["free_md"]


def test_stigma_line_reaches_the_letter(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["stigma"] = {
        "ok": True, "stigma_score": 62,
        "letter_line": "SOFR's 99th percentile cleared the SRF ceiling on 6 of the last 20 sessions."}
    d = build_dispatch(snap)
    assert "cleared the SRF ceiling on 6 of the last 20 sessions" in d["free_md"]


def test_supply_desk_table_reaches_the_desk_read(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["supplydesk"] = {"ok": True, "rows": [
        {"date": "2026-07-30", "bills_gross_b": 190.0, "coupons_gross_b": 0.0,
         "maturing_b": 95.0, "net_new_cash_b": 95.0, "projected": False, "amount_estimated": False},
        {"date": "2026-08-06", "bills_gross_b": 180.0, "coupons_gross_b": 0.0,
         "maturing_b": 200.0, "net_new_cash_b": -20.0, "projected": True, "amount_estimated": False}],
        "heaviest_day": {"date": "2026-07-30", "net_new_cash_b": 95.0}}
    d = build_dispatch(snap)
    assert "### The supply desk" in d["desk_md"]
    assert "net new cash" in d["desk_md"]
    assert "projected" in d["desk_md"] and "announced" in d["desk_md"]
    assert "heaviest settlement ahead is 2026-07-30" in d["desk_md"]


def test_model_court_replaces_the_hand_rolled_adjudication(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["deep"]["modelcourt"] = {
        "ok": True,
        "adjudication": "Model Court, 5bd event odds: 4 models span 5.2 to 14.0 pct, pooled 8.2 pct.",
        "ledger_status": "no ledger yet; the court reads published backtests only"}
    d = build_dispatch(snap)
    assert "Model Court, 5bd event odds" in d["desk_md"]
    assert "Ledger status:" in d["desk_md"]
    assert "Adjudication, not averaging" not in d["desk_md"]   # the fallback stands down


def test_net_and_gross_positioning_print_together(fake_snap):
    """Gross alone is the number that gets quoted and misleads. Net says
    whether the book is two-sided; neither is a directional view, because
    the offsetting cash leg is not in COT, and the letter says so."""
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["rvxray"] = {"ok": True, "gross_short_b": 1033.8, "net_b": -866.6}
    d = build_dispatch(snap)
    assert "$1,034B gross short" in d["desk_md"]
    assert "$867B net short" in d["desk_md"]
    assert "signature of the cash-futures basis trade" in d["desk_md"]
    assert "not price a bet it cannot see" in d["desk_md"]


def test_two_sided_book_is_read_as_offsetting(fake_snap):
    """The other side of the same test: when net is a small share of gross,
    the headline short overstates the exposure and the letter says that."""
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["rvxray"] = {"ok": True, "gross_short_b": 1000.0, "net_b": -200.0}
    d = build_dispatch(snap)
    assert "Only 20% of the gross stands one way" in d["desk_md"]
    assert "overstates the exposure" in d["desk_md"]


def test_repeated_fault_source_is_named_once(fake_snap):
    """One source failing twice is one broken gauge, not two."""
    snap = json.loads(json.dumps(fake_snap))
    snap["faults"] = [{"source": "gdelt", "detail": "timeout"},
                      {"source": "gdelt", "detail": "timeout"},
                      {"source": "CFTC", "detail": "stale"}]
    d = build_dispatch(snap)
    assert "Faults on the board today: gdelt, CFTC." in d["free_md"]


def test_rde_external_check_reads_cleanly(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["kink"] = {"ok": True, "kink_reserves_b": 3634.4,
                               "current_reserves_b": 3062.1, "distance_b": -572.3,
                               "r2": 0.62, "consistency": 0.87,
                               "observed_spread_now_bp": -3.7, "predicted_spread_now_bp": -2.1}
    snap["engines"]["rdenowcast"] = {
        "ok": True, "nyfed_asof": "2026-07-06", "nyfed_bp_per_1pct": -0.268,
        "ours_bp_per_1pct": -0.315, "within_68_band": True, "direction_agree": True,
        "nowcast_lead_days": 16}
    d = build_dispatch(snap)
    md = d["free_md"]
    assert "inside their 68% band, direction agrees." in md
    assert "runs 16 days ahead of their release cycle" in md
    assert ", and the direction" not in md          # the doubled conjunction is gone
    assert "one of us is wrong" in md


# ---------------------------------------------------------------------------
# Regressions from the 2026-07-28 expert review. Each of these shipped wrong
# once and would have been caught by the reader we are writing for.
# ---------------------------------------------------------------------------
def test_court_ranks_on_skill_not_raw_brier(fake_snap):
    """The live bug: ML had the lowest raw Brier (0.0383) and was named best,
    while its own climatology was 0.0384, i.e. no skill at all. Raw Brier is
    not comparable across members scored on different samples."""
    snap = json.loads(json.dumps(fake_snap))
    snap["deep"].pop("modelcourt", None)
    snap["deep"]["ml"]["validation"] = {"brier": 0.0383, "brier_climatology": 0.0384}
    snap["deep"]["swell"]["validation"] = {"brier": 0.0401, "brier_climatology": 0.0457}
    snap["deep"]["tidetables"]["skill"] = {"brier": 0.0511, "brier_climatology": 0.0507}
    d = build_dispatch(snap)
    assert "the dated term structure (Swell) leads" in d["desk_md"]
    assert "the learned model leads" not in d["desk_md"]
    assert "+12" in d["desk_md"]              # swell skill ~12.3%


def test_court_says_so_when_nobody_beats_climatology(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["deep"].pop("modelcourt", None)
    snap["deep"]["ml"]["validation"] = {"brier": 0.0383, "brier_climatology": 0.0384}
    snap["deep"]["swell"]["validation"] = {"brier": 0.0460, "brier_climatology": 0.0457}
    snap["deep"]["tidetables"]["skill"] = {"brier": 0.0511, "brier_climatology": 0.0507}
    d = build_dispatch(snap)
    assert "no member clears its own climatology" in d["desk_md"]
    assert "a view, not an edge" in d["desk_md"]


def test_member_without_climatology_is_unranked_not_winner(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["deep"].pop("modelcourt", None)
    snap["deep"]["ml"]["validation"] = {"brier": 0.001}      # unbeatable, no climatology
    snap["deep"]["swell"]["validation"] = {"brier": 0.0401, "brier_climatology": 0.0457}
    snap["deep"]["tidetables"]["skill"] = {}
    d = build_dispatch(snap)
    assert "the learned model published no climatology" in d["desk_md"]
    assert "the dated term structure (Swell) leads" in d["desk_md"]


def test_falsifier_reads_the_discount_window_it_names(fake_snap):
    """S1 named 'SRF or discount window' but only read SRF, and published the
    item as untouched while the window sat at $4.9B against a $1B line."""
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["composite"]["regime"] = "STRAIN"
    snap["headline"] = {"dw_b": {"value": 4.9, "asof": "2026-07-22"}}
    snap["engines"]["sonar"] = {"ok": True, "movers": [
        {"label": "SRF accepted", "last": 0.02, "unit": "$B", "level_z": 12.8,
         "change_z": 3.2, "max_abs_z": 12.8, "flag": False, "stale": False,
         "age_d": 1, "asof": "2026-07-27"}]}
    d = build_dispatch(snap)
    assert "discount window $4.9B" in d["desk_md"]
    assert "reads BREACHED" in d["desk_md"]


def test_falsifier_not_breached_stays_quiet(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["composite"]["regime"] = "STRAIN"
    snap["headline"] = {"dw_b": {"value": 0.1, "asof": "2026-07-22"}}
    d = build_dispatch(snap)
    assert "reads BREACHED" not in d["desk_md"]


def test_days_until_counts_from_the_letter_not_the_snapshot(fake_snap):
    """The snapshot computes days_until against its own generated_at. A letter
    published the next morning said 'FOMC decides tomorrow' on the day of."""
    snap = json.loads(json.dumps(fake_snap))
    snap["calendar"] = {"fomc_next_90d": [{"date": "2026-07-29", "days_until": 1}]}
    d = build_dispatch(snap, date="2026-07-29")
    assert "FOMC decides today" in d["free_md"]
    assert "decides tomorrow" not in d["free_md"]


def test_missing_calendar_field_cannot_kill_the_letter(fake_snap):
    """A None days_until once rendered 'None days out', which the lint treats
    as a format leak, which raises SystemExit, which means no letter."""
    snap = json.loads(json.dumps(fake_snap))
    snap["calendar"] = {"corporate_tax_next_90d": [{"date": None, "days_until": None}]}
    snap["deep"]["turn"] = {"next_turn": {"date": None, "mode": "month_end",
                                          "forecast_bp": 4.8, "band_bp": [0.9, 8.8],
                                          "severity": None}}
    d = build_dispatch(snap, date="2026-07-10")
    assert "None" not in d["free_md"]
    assert "The turn model puts" not in d["free_md"]   # undated turn is skipped, not printed
    assert "corporate tax date" not in d["free_md"]    # undated tax entry likewise


def test_unforeseen_leak_is_repaired_and_confessed_not_fatal(fake_snap):
    """A missing letter is worse than a letter with a question mark in it, so
    a format leak self-heals and says so instead of stopping the presses."""
    snap = json.loads(json.dumps(fake_snap))
    snap["deep"]["tell"]["reading"] = "None"      # reaches the letter verbatim
    snap["deep"]["tell"]["tell"] = 42.0           # force the wide-gap branch that quotes it
    d = build_dispatch(snap)
    assert "None" not in d["free_md"]
    assert "did not arrive from the board today" in d["free_md"]


def test_legitimate_none_word_survives_sanitizing():
    from seiche.dispatch_daily import sanitize_leaks
    out, n = sanitize_leaks("None of those printed.")
    assert out == "None of those printed." and n == 1


def test_scarcity_claim_is_reconciled_against_the_tape(fake_snap):
    """Asserting reserves are deep into scarcity while SOFR prints below IORB,
    with no acknowledgement, is the contradiction an expert catches first."""
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["kink"] = {"ok": True, "kink_reserves_b": 3634.4,
                               "current_reserves_b": 3062.1, "distance_b": -572.3,
                               "r2": 0.62, "consistency": 0.87,
                               "observed_spread_now_bp": -3.7, "predicted_spread_now_bp": -2.1}
    d = build_dispatch(snap)
    assert "the tape disagrees" in d["free_md"]
    assert "abundance signature" in d["free_md"]
    assert "hypothesis the tape has not yet confirmed" in d["free_md"]


def test_rde_does_not_claim_the_nyfed_uses_public_series(fake_snap):
    """The NY Fed's RDE is estimated on confidential bank-level fed funds
    transactions. Claiming 'the same public series' is false and this is
    exactly the audience that knows it."""
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["kink"] = {"ok": True, "kink_reserves_b": 3634.4,
                               "current_reserves_b": 3062.1, "distance_b": -572.3,
                               "r2": 0.62, "consistency": 0.87,
                               "observed_spread_now_bp": -3.7, "predicted_spread_now_bp": -2.1}
    d = build_dispatch(snap)
    assert "same public series the NY Fed uses" not in d["free_md"]
    assert "confidential bank-level fed funds transactions" in d["free_md"]
    assert "public-data approximation" in d["free_md"]


def test_crowded_seat_is_the_high_percentile_not_the_loudest_z(fake_snap):
    """Ranking by |z| named a 3rd-percentile contract 'the most crowded seat'
    while a 90th-percentile contract sat two rows below it."""
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["crowding"] = {"ok": True, "asof": "2026-07-21", "rows": [
        {"contract": "SOFR-3M", "lev_net_share_oi": -0.206, "z": -2.26, "pctl": 3.0},
        {"contract": "UST 2Y NOTE", "lev_net_share_oi": -0.368, "z": 1.39, "pctl": 90.0}]}
    d = build_dispatch(snap)
    assert "most crowded seat is **UST 2Y NOTE** at the 90th percentile" in d["desk_md"]
    assert "emptiest is SOFR-3M at the 3rd" in d["desk_md"]


def test_positioning_provenance_is_correct_and_dated(fake_snap):
    """The dealer warehouse is the NY Fed primary dealer survey, not COT."""
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["warehouse"] = {"ok": True, "total_net_b": 432.0, "total_pctl": 96.0,
                                    "long_end_share_pct": 37.0, "asof": "2026-07-15"}
    d = build_dispatch(snap)
    assert "NY Fed primary dealer survey" in d["desk_md"]
    assert "as of 2026-07-15" in d["desk_md"]
    assert "Positioning data is COT" not in d["desk_md"]


def test_censored_lead_time_is_labelled_not_reported_as_a_median(fake_snap):
    """7 of 8 lead times pinned at the evaluation horizon is a censoring
    boundary, not a median."""
    snap = json.loads(json.dumps(fake_snap))
    snap["deep"]["backtest"]["event_capture"] = {
        "recall": 0.62, "precision_runs": 0.17, "base_rate": 0.05,
        "median_lead_d": 60, "n_alert_runs": 23, "recall_ci95": [0.355, 0.823],
        "lead_times_d": [8, 60, 60, 60, 60, 60, 60, 60]}
    d = build_dispatch(snap)
    assert "is censored" in d["free_md"]
    assert "7 of 8 episodes" in d["free_md"]
    assert "a floor rather than a central estimate" in d["free_md"]
    assert "95% interval 36% to 82%" in d["free_md"]


def test_dark_sections_are_counted_in_the_honesty_coda(fake_snap):
    """Section 5 said an engine was dark while section 7 said all engines
    report live, because a missing engine never raises a fault."""
    d = build_dispatch(fake_snap)     # fixture has no kink/officialbid/stigma/runway
    assert "of the letter's named sections are dark today" in d["free_md"]
    assert "All sources and engines report live." not in d["free_md"]


def test_stacker_verdict_keeps_the_self_critical_half(fake_snap):
    """The verdict was split on the first ' (' which discarded 'does NOT beat
    the best single member' — the only part that costs the board anything."""
    snap = json.loads(json.dumps(fake_snap))
    snap["deep"]["stacker"] = {
        "ok": True, "p_now": 0.06, "dispersion_now": 0.02,
        "verdict": "published signal = mean (Brier 0.0407 vs mean 0.0407); does NOT beat the best single member"}
    d = build_dispatch(snap)
    assert "does NOT beat the best single member" in d["desk_md"]


def test_supply_desk_shows_only_forward_settlements(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["supplydesk"] = {"ok": True, "rows": [
        {"date": "2026-07-08", "bills_gross_b": 299.0, "coupons_gross_b": 0.0,
         "maturing_b": 228.0, "net_new_cash_b": 71.0, "projected": False, "amount_estimated": False},
        {"date": "2026-07-30", "bills_gross_b": 278.0, "coupons_gross_b": 0.0,
         "maturing_b": 246.0, "net_new_cash_b": 32.0, "projected": False, "amount_estimated": False}],
        "heaviest_day": {"date": "2026-07-08", "net_new_cash_b": 71.0}}
    d = build_dispatch(snap, date="2026-07-10")
    assert "2026-07-08" not in d["desk_md"]                       # already settled
    assert "heaviest settlement ahead is 2026-07-30" in d["desk_md"]


def test_ledger_resolves_closed_horizons_only():
    """The court can never convene on a ledger nothing ever grades. Rows
    resolve when their five-day window has closed, and never before."""
    from seiche.dispatch_daily import resolve_ledger
    rows = [{"date": "2026-07-01", "model": "swell", "horizon_bd": 5, "p": 0.2}]
    # a calm run: no jump anywhere near the 10bp bar
    for i, day in enumerate(["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06",
                             "2026-07-07", "2026-07-08", "2026-07-09"]):
        rows.append({"date": day, "kind": "spread", "spread_bp": -3.0 + 0.1 * i})
    out, n = resolve_ledger(rows)
    assert n == 1
    assert out[0]["realized"] is False

    # the same forecast with an open horizon stays null
    short = [{"date": "2026-07-01", "model": "swell", "horizon_bd": 5, "p": 0.2},
             {"date": "2026-07-01", "kind": "spread", "spread_bp": -3.0},
             {"date": "2026-07-02", "kind": "spread", "spread_bp": -3.0},
             {"date": "2026-07-03", "kind": "spread", "spread_bp": -3.0}]
    out2, n2 = resolve_ledger(short)
    assert n2 == 0 and out2[0].get("realized") is None


def test_ledger_marks_a_real_funding_event():
    from seiche.dispatch_daily import resolve_ledger
    rows = [{"date": "2026-07-01", "model": "swell", "horizon_bd": 5, "p": 0.2}]
    days = ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06", "2026-07-07",
            "2026-07-08", "2026-07-09"]
    for i, day in enumerate(days):
        # flat, then a 15bp pop inside the window
        rows.append({"date": day, "kind": "spread",
                     "spread_bp": -3.0 if i < 5 else 12.0})
    out, n = resolve_ledger(rows)
    assert n == 1 and out[0]["realized"] is True


def test_ledger_never_rewrites_a_published_forecast():
    from seiche.dispatch_daily import resolve_ledger
    rows = [{"date": "2026-07-01", "model": "swell", "horizon_bd": 5, "p": 0.2345}]
    for i, day in enumerate(["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06",
                             "2026-07-07", "2026-07-08", "2026-07-09"]):
        rows.append({"date": day, "kind": "spread", "spread_bp": -3.0})
    out, _ = resolve_ledger(rows)
    assert out[0]["p"] == 0.2345
    # already-resolved rows are left exactly as they are
    out[0]["realized"] = True
    again, n = resolve_ledger(out)
    assert n == 0 and again[0]["realized"] is True


def test_spread_row_recorded_with_the_odds(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["headline"] = {"sofr_pct": {"value": 3.64}, "iorb_pct": {"value": 3.65}}
    d = build_dispatch(snap, date="2026-07-10")
    spreads = [r for r in d["odds"] if r.get("kind") == "spread"]
    assert len(spreads) == 1
    assert spreads[0]["spread_bp"] == -1.0


def test_sonar_label_with_a_dash_does_not_kill_the_letter(fake_snap):
    """A series label is engine-supplied text and must be sanitized like the
    rest; an em dash in a label once cost the entire day's letter."""
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["sonar"] = {"ok": True, "movers": [
        {"label": "SOFR — IORB spread", "last": 4.0, "unit": "%", "level_z": 3.0,
         "change_z": 3.0, "max_abs_z": 3.0, "flag": True, "stale": False,
         "age_d": 1, "asof": "2026-07-09"}]}
    d = build_dispatch(snap, date="2026-07-10")
    assert "SOFR, IORB spread" in d["free_md"]
    assert "—" not in d["free_md"]


def test_missing_warehouse_share_does_not_render_a_placeholder(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["warehouse"] = {"ok": True, "total_net_b": 432.0,
                                    "total_pctl": 96.0, "long_end_share_pct": None}
    d = build_dispatch(snap)
    assert "?%" not in d["desk_md"]
    assert "long end" not in d["desk_md"]


def test_missing_coverage_does_not_render_a_placeholder(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["composite"]["coverage_pct"] = None
    d = build_dispatch(snap)
    assert "?%" not in d["free_md"]


def test_no_series_label_carries_a_dash():
    """Series labels reach the letter, the CSV headers and the methodology
    page. Two shipped with em dashes and one of them was six weeks from
    becoming a publish-blocker the day it cleared SONAR's history gate."""
    import seiche.config as cfg
    bad = []
    for name in dir(cfg):
        v = getattr(cfg, name)
        if isinstance(v, list) and v and hasattr(v[0], "label"):
            bad += [(s.mnemonic, s.label) for s in v
                    if "—" in str(s.label) or "–" in str(s.label)]
    assert bad == [], f"dashed series labels: {bad}"


def test_standing_flags_carry_the_scale_caveat_too(fake_snap):
    """The de-minimis note applied only to new movers, but a standing flag
    reprints its sigma every day until the series moves. A 16-sigma flag on
    $378M ran for five letters with no scale context."""
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["sonar"] = {"ok": True, "movers": [{
        "label": "Central bank liquidity swaps", "last": 378.0, "unit": "$M",
        "level_z": 1.5, "change_z": 16.5, "max_abs_z": 16.5, "flag": True,
        "stale": False, "age_d": 3, "asof": "2026-07-09",
        "hist_peak_abs": 449000.0, "share_of_peak": 0.0008}]}
    state = {"date": "2026-07-09", "reported": {"Central bank liquidity swaps": "2026-07-09"}}
    d = build_dispatch(snap, date="2026-07-10", state=state)
    assert "Still flagged" in d["free_md"]
    assert "[de minimis]" in d["free_md"]
    assert "rounding error against their own history" in d["free_md"]


def test_level_only_flag_is_not_called_a_move(fake_snap):
    """A flag from the level alone under a heading called 'what moved' is a
    category error; the letter qualifies it instead."""
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["sonar"] = {"ok": True, "movers": [{
        "label": "10y Treasury constant maturity yield", "last": 4.69, "unit": "%",
        "level_z": 2.6, "change_z": -0.5, "max_abs_z": 2.6, "flag": True,
        "stale": False, "age_d": 3, "asof": "2026-07-09"}]}
    d = build_dispatch(snap, date="2026-07-10")
    assert "flag is on LEVEL, not on change" in d["free_md"]
    assert "did not travel to get there today" in d["free_md"]


def test_every_saturated_component_is_named(fake_snap):
    """The letter stated a disclosure rule and then named only the largest
    pinned component while a second sat at its ceiling."""
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["composite"]["decomposition"] = [
        {"component": "weather", "score": 100.0, "contribution": 11.0, "status": "OK"},
        {"component": "buffers", "score": 99.7, "contribution": 3.0, "status": "OK"},
        {"component": "kink", "score": 55.0, "contribution": 6.0, "status": "OK"},
    ]
    d = build_dispatch(snap)
    assert "Pinned at the ceiling today" in d["free_md"]
    assert "the calendar squeeze (weather)" in d["free_md"]
    assert "buffers" in d["free_md"]
    assert "components are" in d["free_md"]


def test_saturation_uses_the_engine_flag_when_present(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["composite"]["decomposition"] = [
        {"component": "weather", "score": 88.0, "contribution": 11.0, "status": "OK",
         "saturated": True},
        {"component": "kink", "score": 99.9, "contribution": 6.0, "status": "OK",
         "saturated": False},
    ]
    d = build_dispatch(snap)
    assert "the calendar squeeze (weather)" in d["free_md"]
    assert "reserve scarcity (kink)" not in d["free_md"].split("Pinned at the ceiling today")[1][:120]


def test_tell_components_reconcile_with_the_headline(fake_snap):
    """53 minus 39 is 14, and the letter printed +15 in bold in the same
    sentence. Now the arithmetic is shown and it adds up."""
    snap = json.loads(json.dumps(fake_snap))
    snap["deep"]["tell"] = {"ok": True, "tell": 14.6, "plumbing_pctl": 53.0,
                            "market_pctl": 39.0, "reading": "plumbing leads"}
    d = build_dispatch(snap)
    assert "**+14.6**" in d["free_md"]
    assert "a gap of +14.0" in d["free_md"]


def test_announce_uses_the_published_headline(tmp_path):
    """CI announces the better part of an hour after writing, and the board
    recomputes six times a day. The digest must match the letter it links to,
    not a fresh rebuild of it."""
    from seiche.dispatch_daily import _published_entry
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps([
        {"slug": "2026-07-29-daily", "title": "as published", "date": "2026-07-29",
         "tag": "STRAIN", "summary": "the published summary"},
    ]))
    e = _published_entry(idx, "2026-07-29-daily")
    assert e and e["title"] == "as published"
    assert _published_entry(idx, "2026-07-30-daily") is None
    assert _published_entry(tmp_path / "missing.json", "x") is None


def test_stack_says_it_pools_its_own_members(fake_snap):
    """14 percent, 5 percent and a pooled 6 percent in one paragraph reads as
    arithmetic that does not work unless the letter says the pool is over a
    different member set."""
    snap = json.loads(json.dumps(fake_snap))
    snap["deep"]["stacker"]["members_now"] = {"rule": 0.076, "ml": 0.038, "swell": 0.18}
    d = build_dispatch(snap)
    assert "pooled over its own 3 members rather than the views quoted above" in d["desk_md"]
    # with no member list published, the letter makes no claim about the pool
    plain = build_dispatch(fake_snap)
    assert "pooled over its own" not in plain["desk_md"]


def test_echoes_states_its_threshold_is_editorial(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["echo"] = {"ok": True, "matches": [
        {"episode": "Mar 2020 dash-for-cash", "lead_days": 13, "similarity": 0.68}]}
    d = build_dispatch(snap)
    assert "editorial convention, not a validated threshold" in d["desk_md"]
    assert "publishes no null distribution" in d["desk_md"]


def test_a_broken_section_costs_one_section_not_the_letter(fake_snap):
    """Engine payload shapes are not the letter's to control. A type that
    drifts upstream must cost one section, never the day's letter."""
    from seiche import dispatch_daily as dd

    def boom(*_a, **_k):
        raise TypeError("upstream shape drifted")

    orig, dd._tell_para = dd._tell_para, boom
    try:
        d = build_dispatch(fake_snap)
    finally:
        dd._tell_para = orig
    assert "Tell section could not be built" in d["free_md"]
    assert "Section faults today: Tell" in d["free_md"]
    # and the rest of the letter is intact
    assert "## 1 · The reading" in d["free_md"]
    assert "## 7 · What the board is honest about" in d["free_md"]
    assert d["title"]


def test_a_clean_board_reports_no_section_faults(fake_snap):
    d = build_dispatch(fake_snap)
    assert "Section faults today" not in d["free_md"]
    assert "could not be built" not in d["free_md"]


def test_ledger_attribution_reaches_the_scarcity_section(fake_snap):
    """The kink says whether the reserve level matters; the ledger says which
    liability moved it, which is the question a desk asks next."""
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["kink"] = {"ok": True, "kink_reserves_b": 3634.4,
                               "current_reserves_b": 3062.1, "distance_b": -572.3,
                               "r2": 0.62, "consistency": 0.87,
                               "observed_spread_now_bp": -3.7, "predicted_spread_now_bp": -2.1}
    snap["engines"]["ledger"] = {"ok": True, "letter_line":
        "Reserves fell $80.6B on the week to $3,062B, and the ledger says where from: "
        "the TGA rebuilt $73.4B."}
    d = build_dispatch(snap)
    assert "the ledger says where from" in d["free_md"]
    assert "the TGA rebuilt $73.4B" in d["free_md"]


def test_auction_report_card_reaches_the_desk_read(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["reportcard"] = {"ok": True, "letter_line":
        "The last auction on the board is the 7y note of 2026-07-28, graded C, and its "
        "funding window is still open at 0 of 4 marks."}
    d = build_dispatch(snap)
    assert "graded C" in d["desk_md"]
    assert "still open at 0 of 4 marks" in d["desk_md"]


def test_rde_prints_the_running_record_not_just_a_good_day(fake_snap):
    """The whole credibility claim rests on this comparison, so a favourable
    single print must never appear without the track record beside it."""
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["kink"] = {"ok": True, "kink_reserves_b": 3634.4,
                               "current_reserves_b": 3062.1, "distance_b": -572.3,
                               "r2": 0.62, "consistency": 0.87,
                               "observed_spread_now_bp": -3.7, "predicted_spread_now_bp": -2.1}
    snap["engines"]["rdenowcast"] = {
        "ok": True, "nyfed_asof": "2026-07-06", "nyfed_bp_per_1pct": -0.268,
        "ours_bp_per_1pct": -0.315, "within_68_band": True, "direction_agree": True,
        "nowcast_lead_days": 16,
        "scorecard_summary": {"n": 18, "within_band": 8, "direction_agree": 10,
                              "mean_abs_diff_bp": 0.265}}
    d = build_dispatch(snap)
    md = d["free_md"]
    assert "The running record, not just today" in md
    assert "landed inside their 68% band 8 times" in md
    assert "short of the two-in-three" in md          # 8/18 = 44%, honest verdict
    assert "One matching print is an anecdote" in md


def test_rde_record_is_praised_only_when_it_earns_it(fake_snap):
    snap = json.loads(json.dumps(fake_snap))
    snap["engines"]["kink"] = {"ok": True, "kink_reserves_b": 3634.4,
                               "current_reserves_b": 3062.1, "distance_b": -572.3,
                               "r2": 0.62, "consistency": 0.87,
                               "observed_spread_now_bp": -3.7, "predicted_spread_now_bp": -2.1}
    snap["engines"]["rdenowcast"] = {
        "ok": True, "nyfed_asof": "2026-07-06", "nyfed_bp_per_1pct": -0.268,
        "ours_bp_per_1pct": -0.315, "within_68_band": True, "direction_agree": True,
        "scorecard_summary": {"n": 18, "within_band": 15, "direction_agree": 17,
                              "mean_abs_diff_bp": 0.08}}
    d = build_dispatch(snap)
    assert "better than their band alone implies" in d["free_md"]
