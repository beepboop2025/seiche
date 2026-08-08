"""SQLite cache: observations + fetch log.

Keeps cold starts fast and upstreams unhammered. A cached series is reused
until its cadence-aware TTL lapses; on refresh failure the stale copy is
served with its true staleness class (fail-loud, but degrade gracefully).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta, timezone

import pandas as pd

from seiche.config import DATA_DIR, DB_PATH
from seiche.sources.base import Series

_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS observations (
             mnemonic TEXT NOT NULL, obs_date TEXT NOT NULL, value REAL,
             PRIMARY KEY (mnemonic, obs_date))"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS fetches (
             mnemonic TEXT PRIMARY KEY, source TEXT, remote_id TEXT,
             label TEXT, unit TEXT, freq TEXT, fetched_at TEXT)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS observation_vintages (
             mnemonic TEXT NOT NULL, obs_date TEXT NOT NULL,
             knowledge_time TEXT NOT NULL, value REAL,
             PRIMARY KEY (mnemonic, obs_date, knowledge_time))"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS blobs (
             key TEXT PRIMARY KEY, fetched_at TEXT, payload TEXT)"""
    )
    return conn


def save_series(s: Series) -> None:
    with _lock, _conn() as conn:
        knowledge_time = _canonical_utc(s.fetched_at)
        rows = [
            (s.mnemonic, idx.date().isoformat(), None if pd.isna(v) else float(v))
            for idx, v in s.points.items()
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO observations VALUES (?,?,?)",
            rows,
        )
        conn.executemany(
            "INSERT OR IGNORE INTO observation_vintages VALUES (?,?,?,?)",
            [
                (mnemonic, obs_date, knowledge_time, value)
                for mnemonic, obs_date, value in rows
            ],
        )
        conn.execute(
            "INSERT OR REPLACE INTO fetches VALUES (?,?,?,?,?,?,?)",
            (s.mnemonic, s.source, s.remote_id, s.label, s.unit, s.freq, s.fetched_at),
        )


def _canonical_utc(value: str | datetime) -> str:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("knowledge_time must be timezone-aware")
    return parsed.astimezone(UTC).isoformat(timespec="seconds")


def load_series_as_of(
    mnemonic: str,
    knowledge_time: str | datetime,
) -> Series | None:
    """Reconstruct the latest captured revision knowable by ``knowledge_time``.

    This is deliberately unable to answer dates before the first local capture;
    current values are never backfilled into an earlier vintage.
    """

    cut = _canonical_utc(knowledge_time)
    with _lock, _conn() as conn:
        meta = conn.execute(
            "SELECT source, remote_id, label, unit, freq FROM fetches WHERE mnemonic=?",
            (mnemonic,),
        ).fetchone()
        rows = conn.execute(
            """SELECT vintage.obs_date, vintage.value, vintage.knowledge_time
                 FROM observation_vintages AS vintage
                 JOIN (
                   SELECT obs_date, MAX(knowledge_time) AS latest_knowledge
                     FROM observation_vintages
                    WHERE mnemonic=? AND knowledge_time<=?
                    GROUP BY obs_date
                 ) AS latest
                   ON vintage.obs_date=latest.obs_date
                  AND vintage.knowledge_time=latest.latest_knowledge
                WHERE vintage.mnemonic=?
                ORDER BY vintage.obs_date""",
            (mnemonic, cut, mnemonic),
        ).fetchall()
    if not meta or not rows:
        return None
    idx = pd.DatetimeIndex([row[0] for row in rows])
    points = pd.Series([row[1] for row in rows], index=idx, dtype=float)
    fetched_at = max(row[2] for row in rows)
    return Series(
        mnemonic,
        meta[0],
        meta[1],
        meta[2],
        meta[3],
        meta[4],
        fetched_at,
        points,
    )
def load_series(mnemonic: str) -> Series | None:
    with _lock, _conn() as conn:
        meta = conn.execute(
            "SELECT source, remote_id, label, unit, freq, fetched_at FROM fetches WHERE mnemonic=?",
            (mnemonic,),
        ).fetchone()
        if not meta:
            return None
        rows = conn.execute(
            "SELECT obs_date, value FROM observations WHERE mnemonic=? ORDER BY obs_date",
            (mnemonic,),
        ).fetchall()
    idx = pd.DatetimeIndex([r[0] for r in rows])
    pts = pd.Series([r[1] for r in rows], index=idx, dtype=float)
    return Series(mnemonic, meta[0], meta[1], meta[2], meta[3], meta[4], meta[5], pts)


def is_fresh(mnemonic: str, ttl_minutes: int) -> bool:
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT fetched_at FROM fetches WHERE mnemonic=?", (mnemonic,)
        ).fetchone()
    if not row:
        return False
    fetched = datetime.fromisoformat(row[0])
    return datetime.now(timezone.utc) - fetched < timedelta(minutes=ttl_minutes)


def save_blob(key: str, payload: object) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO blobs VALUES (?,?,?)",
            (key, datetime.now(timezone.utc).isoformat(timespec="seconds"), json.dumps(payload)),
        )


def load_pit_records(limit: int = 2000) -> list[dict]:
    """As-published point-in-time records (pit:YYYY-MM-DD blobs), oldest first."""
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT payload FROM blobs WHERE key LIKE 'pit:%' ORDER BY key DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [json.loads(r[0]) for r in reversed(rows)]


def load_blob(key: str, ttl_minutes: int | None = None) -> object | None:
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT fetched_at, payload FROM blobs WHERE key=?", (key,)
        ).fetchone()
    if not row:
        return None
    if ttl_minutes is not None:
        fetched = datetime.fromisoformat(row[0])
        if datetime.now(timezone.utc) - fetched > timedelta(minutes=ttl_minutes):
            return None
    return json.loads(row[1])
