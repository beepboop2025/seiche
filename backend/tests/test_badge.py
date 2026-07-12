"""The sealed-record badge: green only while the pit chain has no holes.

Pure sqlite fixtures — no network, no live DB. The badge reads the same
`pit:*` keys the pit_gap dead-man alert scans, so these tests pin the three
states a reader can encounter: no record, unbroken record, holed record.
"""

import json
import sqlite3

import pytest

from seiche import badge


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "badge.sqlite"
    monkeypatch.setattr(badge, "DB_PATH", path)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE blobs (key TEXT PRIMARY KEY, payload TEXT)")
    conn.commit()
    conn.close()
    return path


def _seed(path, dates):
    conn = sqlite3.connect(path)
    for d in dates:
        conn.execute(
            "INSERT INTO blobs (key, payload) VALUES (?, ?)",
            (f"pit:{d}", json.dumps({"date": d})),
        )
    conn.commit()
    conn.close()


def test_empty_record_is_grey_not_green(db):
    out = badge.record_badge()
    assert out["schemaVersion"] == 1
    assert out["color"] == "lightgrey"
    assert out["message"] == "no record yet"


def test_unbroken_business_days_is_green_with_span(db):
    # Mon 2026-07-06 through Fri 2026-07-10, no gaps.
    _seed(db, ["2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10"])
    out = badge.record_badge()
    assert out["color"] == "brightgreen"
    assert out["message"] == "5 days unbroken since 2026-07-06"


def test_weekend_gap_does_not_count_as_hole(db):
    # Fri 2026-07-10 then Mon 2026-07-13: the weekend is not a hole.
    _seed(db, ["2026-07-09", "2026-07-10", "2026-07-13"])
    out = badge.record_badge()
    assert out["color"] == "brightgreen"


def test_missing_business_day_turns_red_and_says_how_many(db):
    # Wed 2026-07-08 missing from an otherwise present week.
    _seed(db, ["2026-07-06", "2026-07-07", "2026-07-09", "2026-07-10"])
    out = badge.record_badge()
    assert out["color"] == "red"
    assert out["message"] == "HOLE: 1 business days missing"
