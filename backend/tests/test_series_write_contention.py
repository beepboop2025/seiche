"""Reduce current-row churn without losing capture history or atomicity."""

from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import json
import logging
import sqlite3

import pandas as pd
import pytest

from seiche import store
from seiche.sources.base import Series


FIRST = "2026-11-07T10:00:00+00:00"
SECOND = "2026-11-07T10:05:00+00:00"


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "series.sqlite")
    with closing(store._conn()) as connection:
        connection.executescript("""
            CREATE TABLE changes (kind TEXT, mnemonic TEXT, obs_date TEXT);
            CREATE TRIGGER observation_insert AFTER INSERT ON observations BEGIN
                INSERT INTO changes VALUES ('insert', NEW.mnemonic, NEW.obs_date);
            END;
            CREATE TRIGGER observation_update AFTER UPDATE ON observations BEGIN
                INSERT INTO changes VALUES ('update', NEW.mnemonic, NEW.obs_date);
            END;
            CREATE TRIGGER observation_delete AFTER DELETE ON observations BEGIN
                INSERT INTO changes VALUES ('delete', OLD.mnemonic, OLD.obs_date);
            END;
        """)
    return store.DB_PATH


def series(values=(1.0, None, 3.0), *, mnemonic="fixture", fetched_at=FIRST):
    return Series(
        mnemonic=mnemonic, source="synthetic", remote_id="not-a-provider",
        label="synthetic fixture", unit="fixture", freq="D", fetched_at=fetched_at,
        points=pd.Series(values, index=pd.date_range("2026-11-01", periods=len(values))),
    )


def read(database, statement, parameters=()):
    with closing(sqlite3.connect(database)) as connection:
        return connection.execute(statement, parameters).fetchall()


def snapshot(database):
    return {
        table: read(database, f"SELECT rowid, * FROM {table} ORDER BY rowid")
        for table in ("observations", "observation_vintages", "fetches", "blobs", "changes")
    }


@pytest.mark.parametrize("batch", [False, True])
def test_unchanged_refresh_retains_every_vintage_without_current_row_writes(database, batch):
    original = series()
    store.save_series(original)
    before = snapshot(database)
    refreshed = replace(original, fetched_at=SECOND, label="updated provenance label")
    if batch:
        store.save_series_batch((refreshed,), blobs={"capture": {"clock": SECOND}})
    else:
        store.save_series(refreshed)

    assert read(database, "SELECT rowid, * FROM observations ORDER BY rowid") == before["observations"]
    assert read(database, "SELECT rowid, * FROM changes ORDER BY rowid") == before["changes"]
    assert read(database, "SELECT knowledge_time, COUNT(*) FROM observation_vintages GROUP BY knowledge_time") == [
        (FIRST, 3), (SECOND, 3),
    ]
    assert read(database, "SELECT value FROM observation_vintages WHERE obs_date='2026-11-02'") == [(None,), (None,)]
    assert read(database, "SELECT label, fetched_at FROM fetches") == [(refreshed.label, SECOND)]


def test_revisions_and_null_transitions_update_only_changed_rows(database):
    store.save_series(series((1.0, None, 3.0, None)))
    rowids = read(database, "SELECT rowid, obs_date FROM observations ORDER BY obs_date")
    store.save_series(series((2.0, 4.0, None, None), fetched_at=SECOND))

    assert read(database, "SELECT value FROM observations ORDER BY obs_date") == [(2.0,), (4.0,), (None,), (None,)]
    assert read(database, "SELECT rowid, obs_date FROM observations ORDER BY obs_date") == rowids
    assert read(database, "SELECT kind, COUNT(*) FROM changes GROUP BY kind ORDER BY kind") == [
        ("insert", 4), ("update", 3),
    ]
    assert read(database, "SELECT value FROM observation_vintages WHERE knowledge_time=? ORDER BY obs_date", (FIRST,)) == [
        (1.0,), (None,), (3.0,), (None,),
    ]
    assert read(database, "SELECT knowledge_time, COUNT(*) FROM observation_vintages GROUP BY knowledge_time") == [
        (FIRST, 4), (SECOND, 4),
    ]


def test_replaying_a_capture_does_not_replace_its_existing_vintage(database):
    store.save_series(series((1.0,)))
    store.save_series(series((2.0,)))
    assert read(database, "SELECT value FROM observations") == [(2.0,)]
    assert read(database, "SELECT knowledge_time, value FROM observation_vintages") == [(FIRST, 1.0)]


def test_all_batch_conversion_precedes_sqlite_writer_reservation(database):
    converted = []

    class ProbePoints:
        def __init__(self, name):
            self.name = name

        def items(self):
            # A separate connection can reserve the writer while values are
            # converted. This fails if BEGIN IMMEDIATE already owns the lock.
            with closing(sqlite3.connect(database, timeout=0)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.rollback()
            converted.append(self.name)
            return iter(((pd.Timestamp("2026-11-01"), 1.0),))

    inputs = tuple(replace(series(mnemonic=name), points=ProbePoints(name)) for name in ("first", "second"))
    store.save_series_batch(inputs, blobs={"capture": {"clock": FIRST}})
    assert converted == ["first", "second"]
    assert read(database, "SELECT mnemonic FROM observations ORDER BY mnemonic") == [("first",), ("second",)]


def test_second_series_sql_failure_rolls_back_complete_provider_batch(database, caplog):
    inputs = (series(mnemonic="first"), series(mnemonic="second"))
    store.save_series_batch(inputs, blobs={"capture": {"clock": FIRST}})
    before = snapshot(database)
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript("""
            CREATE TRIGGER fail_second BEFORE UPDATE ON observations
            WHEN NEW.mnemonic = 'second' BEGIN
                SELECT RAISE(ABORT, 'injected second series failure');
            END;
        """)
    updated = tuple(series((10.0, 20.0, 30.0), mnemonic=item.mnemonic, fetched_at=SECOND) for item in inputs)
    with caplog.at_level(logging.INFO, logger="seiche.store"):
        with pytest.raises(sqlite3.IntegrityError, match="second series failure"):
            store.save_series_batch(
                updated, blobs={"capture": {"clock": SECOND}},
                expected_blobs={"capture": {"clock": FIRST}},
            )
    assert snapshot(database) == before
    record = json.loads(caplog.records[-1].getMessage().removeprefix("series_write "))
    assert record["status"] == "failed"
    assert record["commit_completion_seconds"] is None


@pytest.mark.parametrize("batch", [False, True])
def test_success_timing_is_emitted_after_commit_and_releasing_the_writer(database, batch):
    records = []

    class CommitProbe(logging.Handler):
        def emit(self, record):
            if not record.getMessage().startswith("series_write "):
                return
            with closing(sqlite3.connect(database, timeout=0)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                assert connection.execute("SELECT COUNT(*) FROM observation_vintages").fetchone() == (3,)
                connection.rollback()
            records.append(json.loads(record.getMessage().removeprefix("series_write ")))

    handler = CommitProbe()
    logger = logging.getLogger("seiche.store")
    level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        if batch:
            store.save_series_batch((series(),), blobs={"capture": {"clock": FIRST}})
        else:
            store.save_series(series())
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)

    assert len(records) == 1
    record = records[0]
    assert record["status"] == "committed"
    assert record["series_count"] == 1 and record["row_count"] == 3
    assert record["commit_completion_seconds"] >= 0
    assert record["sql_includes_writer_wait"] is (not batch)
    assert (record["writer_wait_seconds"] is not None) is batch
    assert "fixture" not in json.dumps(record) and FIRST not in json.dumps(record)
