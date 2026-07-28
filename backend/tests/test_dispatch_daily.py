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
