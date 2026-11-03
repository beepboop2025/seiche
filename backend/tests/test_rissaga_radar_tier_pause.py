"""Rissaga reads a paused LiquiLens Failure Radar as paused.

LiquiLens can pause Failure Radar tier publication (its
backend/tier_publication.py, LiquiLens PR #201). The paused board carries a
tier_publication block and no tier, colour or tier count, and its PDs are
research only. Rissaga's LiquiLens desk line, which reaches the owner DM,
the Hermes handoff and the desk outbox, must then say the tiers are paused.
It must never read zero counts ("0 red, 0 orange of 8") off the board, a
calm the lab never published, name a watch institution, or treat entering
or leaving the pause as a board escalation. A board that publishes tiers
reads exactly as before.

Fixtures (field subsets of the LiquiLens bot fixtures, values verbatim;
market fields are left out of this public repository):
- liquilens_failure_radar_live_2026-11-03.json: GET
  https://api.liquilens.in/api/failure-radar/board on 2026-11-03, first
  eight rows, tier counts recounted for them.
- liquilens_failure_radar_paused_pr201.json: the same board through
  public_radar_board() from LiquiLens backend/tier_publication.py at PR #201
  head 4e868c6.
"""

import copy
import json
import os
import re
import sys
from datetime import datetime, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "bot"))

import rissaga as rz  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
NOW = 1_800_000_000.0
NOW_DT = datetime.fromtimestamp(NOW, timezone.utc)
TIER_WORD = re.compile(r"\b(?:red|orange|yellow|green)\b", re.IGNORECASE)
CALM_OR_ALARM = re.compile(r"\b0 (?:red|orange)|\bof \d+\b|\bwatch\b|none|"
                           r"escalation|quiet", re.IGNORECASE)


def _fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


LIVE = _fixture("liquilens_failure_radar_live_2026-11-03.json")
PAUSED = _fixture("liquilens_failure_radar_paused_pr201.json")
BARE = {k: v for k, v in PAUSED.items() if k != "tier_publication"}
PAUSED_LINE = ("LiquiLens review tiers paused since 2026-11-03, failure PDs "
               "research only (8 institutions on the board)")


def mixed(board):
    """The live board with four rows on watch tiers (published)."""
    out = copy.deepcopy(board)
    for row, tier in zip(out["rows"], ("red", "orange", "yellow", "yellow")):
        row["tier"] = tier
    counts = {"red": 0, "orange": 0, "yellow": 0, "green": 0}
    for row in out["rows"]:
        counts[row["tier"]] += 1
    out["tiers"] = counts
    return out


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(rz, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(rz, "CHANNEL_MODE", "hermes")
    monkeypatch.setattr(rz.time, "sleep", lambda s: None)

    def _no_net(*a, **k):
        raise RuntimeError("network blocked in tests")

    monkeypatch.setattr(rz, "_http_get", _no_net)


def _boards(monkeypatch, board):
    def fake_get(url, timeout=20):
        return board if url.endswith("/failure-radar/board") else None

    monkeypatch.setattr(rz, "get_json", fake_get)
    return rz.read_boards()


def test_fixtures_are_the_published_and_paused_shapes():
    assert "tier_publication" not in LIVE
    assert sum(LIVE["tiers"].values()) == len(LIVE["rows"]) == 8
    assert all(row["tier"] for row in LIVE["rows"])
    assert PAUSED["tier_publication"]["state"] == "paused"
    assert "tiers" not in PAUSED
    assert all("tier" not in row and "grade" not in row for row in PAUSED["rows"])
    assert all(row["hazard"]["research_only"] for row in PAUSED["rows"])


@pytest.mark.parametrize("board", (PAUSED, BARE), ids=("pause block", "no tier fields"))
def test_paused_board_reads_as_paused_with_no_counts(monkeypatch, board):
    boards = _boards(monkeypatch, board)
    ll = boards["ll"]
    assert ll["paused"] is True and ll["total"] == 8
    assert not {"red", "orange", "yellow", "top"} & set(ll)
    for beat in ("bank_stress", "india_watch"):
        line = rz.board_line(beat, boards)
        assert line.startswith("LiquiLens review tiers paused")
        assert "failure PDs research only (8 institutions on the board)" in line
        assert not TIER_WORD.search(line), line
        assert not CALM_OR_ALARM.search(line), line
        assert rz._BEAT_STRESSED[beat](boards) is False
    if board is PAUSED:
        assert rz.board_line("bank_stress", boards) == PAUSED_LINE


def test_paused_desk_line_reaches_the_dm_handoff_and_outbox(monkeypatch):
    boards = _boards(monkeypatch, PAUSED)
    item = {"key": "fdic_press", "tier": 1.0,
            "title": "FDIC declares Alpha bank failure and begins receivership",
            "link": "https://example.com/alpha", "snippet": "",
            "source_name": "FDIC", "ts": NOW - 3600}
    marked = rz.rank([item], boards, NOW)
    assert marked and marked[0]["rep"]["beat"] == "bank_stress"
    text = rz.compose(marked, boards, {"fdic_press": "ok"}, NOW_DT)
    assert f"LiquiLens desk: {PAUSED_LINE}" in text
    payload = rz.latest_payload(marked, boards, NOW_DT)
    item_out = payload["items"][0]
    assert item_out["desk_line"] == PAUSED_LINE
    lines = [route["desk_line"] for route in item_out["routes"]
             if route["desk"] == "LIQUILENS"]
    assert lines and set(lines) == {PAUSED_LINE}


def test_entering_or_leaving_the_pause_is_not_an_escalation(monkeypatch):
    published = _boards(monkeypatch, mixed(LIVE))["ll"]
    paused = _boards(monkeypatch, PAUSED)["ll"]
    more = copy.deepcopy(published)
    more["red"] += 1
    for prev, cur, expected in ((published, paused, []),
                                (paused, published, []),
                                (paused, paused, []),
                                (published, more, ["bank_stress"])):
        rz.save_state("last_boards.json", {"ll": prev})
        events = rz.board_events({"ll": cur}, NOW)
        assert [e["beat"] for e in events] == expected, (prev, cur)
    # The paused read is what the next run compares against.
    rz.save_state("last_boards.json", {"ll": published})
    rz.board_events({"ll": paused}, NOW)
    assert rz.load_state("last_boards.json", {})["ll"] == paused


def test_published_board_reads_exactly_as_before(monkeypatch):
    boards = _boards(monkeypatch, LIVE)
    assert boards["ll"] == {"red": 0, "orange": 0, "yellow": 0, "total": 8,
                            "top": None}
    assert rz.board_line("bank_stress", boards) == "LiquiLens board 0 red, 0 orange of 8"
    boards = _boards(monkeypatch, mixed(LIVE))
    assert rz.board_line("bank_stress", boards) == (
        "LiquiLens board 1 red, 1 orange of 8, watch AU Small Finance Bank")
    assert rz._BEAT_STRESSED["bank_stress"](boards) is True
    published = {**mixed(LIVE), "tier_publication": {"state": "published"}}
    assert _boards(monkeypatch, published)["ll"]["red"] == 1
