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
