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
