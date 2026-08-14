"""SQLite cache: observations + fetch log.

Keeps cold starts fast and upstreams unhammered. A cached series is reused
until its cadence-aware TTL lapses; on refresh failure the stale copy is
served with its true staleness class (fail-loud, but degrade gracefully).
"""

from __future__ import annotations

import json
import hashlib
import hmac
import sqlite3
import threading
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from seiche.config import DATA_DIR, DB_PATH
from seiche.domain.forward_record import (
    canonical_market_payload_json,
    forward_chain_generation,
    forward_record_hash,
    release_handoff_generated_at,
    validate_release_handoff_envelope,
    validate_snapshot_forward_binding,
)
from seiche.domain.observation import Observation, RedistributionStatus, SemanticRole
from seiche.sources.base import Series

_lock = threading.Lock()
_schema_lock = threading.Lock()
_forward_child_index_state: dict[tuple[str, int, int], str] = {}

_FORWARD_CHILD_INDEX_SQL = """CREATE UNIQUE INDEX IF NOT EXISTS
             forward_records_one_child
             ON forward_validation_records (
               market_id, product, calibration_id, previous_record_hash
             )
             WHERE NOT (
               market_id = 'NZ-NZD'
               AND calibration_id = 'nz-nzd-local-forward-v1'
             )"""

_COLLECTOR_RUN_ID_FIELDS = (
    "market_id",
    "adapter_id",
    "status",
    "started_at",
    "finished_at",
    "observations_written",
    "attempts",
    "next_due",
    "fault",
)


def _database_identity() -> tuple[str, int, int]:
    database = Path(DB_PATH).resolve()
    stat = database.stat()
    return str(database), stat.st_dev, stat.st_ino


def _converge_forward_child_index(conn: sqlite3.Connection) -> None:
    """Attempt the legacy uniqueness migration once per database/process.

    A pre-existing non-quarantined fork is evidence that needs operator
    review. It must disable forward appends without taking unrelated cache
    reads down with a generic ``sqlite3.IntegrityError`` on every connection.
    """

    identity = _database_identity()
    with _schema_lock:
        if identity in _forward_child_index_state:
            return
        try:
            conn.execute(_FORWARD_CHILD_INDEX_SQL)
        except sqlite3.IntegrityError:
            state = "blocked"
        else:
            state = "ready"
        # Persist the migration attempt before caching its state. A later
        # caller rollback must never remove a successfully installed index
        # while this process still remembers it as ready.
        conn.commit()
        _forward_child_index_state[identity] = state


def _require_forward_child_index(conn: sqlite3.Connection) -> None:
    indexes = {
        row[1]
        for row in conn.execute("PRAGMA index_list(forward_validation_records)")
    }
    if "forward_records_one_child" not in indexes:
        raise RuntimeError(
            "forward appends disabled: forward_records_one_child could not be "
            "installed because legacy duplicate children require integrity review; "
            "restart after quarantining the incident evidence"
        )


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
        """CREATE TABLE IF NOT EXISTS canonical_observations (
             market_id TEXT NOT NULL,
             monetary_area_id TEXT NOT NULL,
             jurisdiction_codes TEXT NOT NULL,
             currency TEXT NOT NULL,
             instrument_id TEXT NOT NULL,
             semantic_role TEXT NOT NULL,
             value TEXT,
             canonical_unit TEXT NOT NULL,
             rate_compounding TEXT,
             day_count TEXT,
             event_time TEXT NOT NULL,
             knowledge_time TEXT NOT NULL,
             source_publication_time TEXT NOT NULL,
             revision_id TEXT NOT NULL,
             source TEXT NOT NULL,
             evidence_hash TEXT NOT NULL,
             connector_classification TEXT NOT NULL,
             redistribution_status TEXT NOT NULL,
             quality TEXT NOT NULL,
             staleness TEXT NOT NULL,
             record_hash TEXT NOT NULL,
             PRIMARY KEY (
               market_id, instrument_id, event_time, knowledge_time,
               source, revision_id
             ))"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS canonical_observations_asof
             ON canonical_observations (
               market_id, semantic_role, event_time, knowledge_time
             )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS canonical_observations_series_page
             ON canonical_observations (
               market_id, event_time DESC, instrument_id DESC,
               knowledge_time DESC, source_publication_time DESC,
               revision_id DESC, source DESC
             )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS canonical_observations_adapter_latest
             ON canonical_observations (
               market_id, source, instrument_id, event_time DESC,
               knowledge_time DESC, source_publication_time DESC,
               revision_id DESC
             )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS market_snapshots (
             snapshot_id TEXT PRIMARY KEY,
             market_id TEXT NOT NULL,
             product TEXT NOT NULL,
             event_cutoff TEXT NOT NULL,
             knowledge_cutoff TEXT NOT NULL,
             sealed_at TEXT NOT NULL,
             calibration_id TEXT NOT NULL,
             evidence_eligible INTEGER NOT NULL,
             payload_hash TEXT NOT NULL,
             payload TEXT NOT NULL)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS market_snapshot_staging (
             snapshot_id TEXT PRIMARY KEY,
             market_id TEXT NOT NULL,
             product TEXT NOT NULL,
             event_cutoff TEXT NOT NULL,
             knowledge_cutoff TEXT NOT NULL,
             sealed_at TEXT NOT NULL,
             calibration_id TEXT NOT NULL,
             evidence_eligible INTEGER NOT NULL,
             payload_hash TEXT NOT NULL,
             payload TEXT NOT NULL)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS release_snapshot_handoffs (
             handoff_id TEXT PRIMARY KEY,
             producer_sha TEXT NOT NULL,
             envelope_hash TEXT NOT NULL,
             envelope TEXT NOT NULL)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS active_release_snapshot_handoff (
             singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
             handoff_id TEXT NOT NULL,
             FOREIGN KEY (handoff_id)
               REFERENCES release_snapshot_handoffs (handoff_id))"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS market_snapshots_latest
             ON market_snapshots (
               market_id, product, knowledge_cutoff DESC, sealed_at DESC
             )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS collector_runs (
             run_id TEXT PRIMARY KEY,
             market_id TEXT NOT NULL,
             adapter_id TEXT NOT NULL,
             status TEXT NOT NULL,
             started_at TEXT NOT NULL,
             finished_at TEXT NOT NULL,
             observations_written INTEGER NOT NULL,
             attempts INTEGER NOT NULL,
             next_due TEXT NOT NULL,
             fault TEXT)"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS collector_runs_latest
             ON collector_runs (market_id, adapter_id, finished_at DESC)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS collector_states (
             market_id TEXT NOT NULL,
             adapter_id TEXT NOT NULL,
             next_due TEXT NOT NULL,
             consecutive_failures INTEGER NOT NULL,
             circuit_open_until TEXT,
             updated_at TEXT NOT NULL,
             CHECK (consecutive_failures >= 0),
             PRIMARY KEY (market_id, adapter_id))"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS worker_heartbeats (
             component_id TEXT PRIMARY KEY,
             heartbeat_at TEXT NOT NULL,
             expected_by TEXT NOT NULL,
             CHECK (expected_by >= heartbeat_at))"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS forward_validation_records (
             record_id TEXT PRIMARY KEY,
             snapshot_id TEXT NOT NULL UNIQUE,
             market_id TEXT NOT NULL,
             product TEXT NOT NULL,
             event_cutoff TEXT NOT NULL,
             knowledge_cutoff TEXT NOT NULL,
             calibration_id TEXT NOT NULL,
             chain_generation INTEGER NOT NULL DEFAULT 1,
             created_at TEXT NOT NULL,
             payload_hash TEXT NOT NULL,
             previous_record_hash TEXT NOT NULL,
             record_hash TEXT NOT NULL UNIQUE,
             payload TEXT NOT NULL)"""
    )
    forward_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(forward_validation_records)")
    }
    if "chain_generation" not in forward_columns:
        # Existing records are the deployed v1 evidence. SQLite adds the
        # constant default without changing their hash-bound identity fields.
        conn.execute(
            """ALTER TABLE forward_validation_records
               ADD COLUMN chain_generation INTEGER NOT NULL DEFAULT 1"""
        )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS forward_records_chain
             ON forward_validation_records (
               market_id, product, created_at, record_id
             )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS forward_records_generation
             ON forward_validation_records (
               market_id, product, calibration_id, chain_generation
             )"""
    )
    _converge_forward_child_index(conn)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS blobs (
             key TEXT PRIMARY KEY, fetched_at TEXT, payload TEXT)"""
    )
    # Callers begin their data transaction from a clean schema checkpoint.
    conn.commit()
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


_CANONICAL_COLUMNS = (
    "market_id",
    "monetary_area_id",
    "jurisdiction_codes",
    "currency",
    "instrument_id",
    "semantic_role",
    "value",
    "canonical_unit",
    "rate_compounding",
    "day_count",
    "event_time",
    "knowledge_time",
    "source_publication_time",
    "revision_id",
    "source",
    "evidence_hash",
    "connector_classification",
    "redistribution_status",
    "quality",
    "staleness",
)


def _observation_record_hash(observation: Observation) -> str:
    canonical = json.dumps(
        observation.to_record(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _observation_row(observation: Observation) -> tuple[str | None, ...]:
    record = observation.to_record()
    record["jurisdiction_codes"] = ",".join(record["jurisdiction_codes"])
    record_hash = _observation_record_hash(observation)
    return tuple(record[column] for column in _CANONICAL_COLUMNS) + (record_hash,)


def save_observations(observations: Iterable[Observation]) -> int:
    """Append canonical observations without ever replacing a prior vintage.

    Replaying an identical row is idempotent. Reusing an immutable identity
    for different content raises instead of silently rewriting history.
    """

    batch = tuple(observations)
    if not batch:
        return 0
    inserted = 0
    placeholders = ",".join("?" for _ in range(len(_CANONICAL_COLUMNS) + 1))
    columns = ",".join((*_CANONICAL_COLUMNS, "record_hash"))
    with _lock, _conn() as conn:
        for observation in batch:
            row = _observation_row(observation)
            try:
                conn.execute(
                    f"INSERT INTO canonical_observations ({columns}) VALUES ({placeholders})",
                    row,
                )
                inserted += 1
            except sqlite3.IntegrityError as exc:
                existing = conn.execute(
                    """SELECT record_hash FROM canonical_observations
                        WHERE market_id=? AND instrument_id=? AND event_time=?
                          AND knowledge_time=? AND source=? AND revision_id=?""",
                    (
                        observation.market_id,
                        observation.instrument_id,
                        observation.event_time.isoformat(),
                        observation.knowledge_time.isoformat(),
                        observation.source,
                        observation.revision_id,
                    ),
                ).fetchone()
                if existing is None or existing[0] != row[-1]:
                    raise ValueError(
                        "canonical observation identity collision with different content"
                    ) from exc
    return inserted


def _row_to_observation(row: sqlite3.Row | tuple) -> Observation:
    record = dict(zip(_CANONICAL_COLUMNS, row[: len(_CANONICAL_COLUMNS)], strict=True))
    return Observation.from_record(record)


def _verified_row_to_observation(row: sqlite3.Row | tuple) -> Observation:
    observation = _row_to_observation(row)
    stored_hash = row[len(_CANONICAL_COLUMNS)]
    expected_hash = _observation_record_hash(observation)
    if not isinstance(stored_hash, str) or not hmac.compare_digest(
        stored_hash, expected_hash
    ):
        raise ValueError("canonical observation record_hash mismatch")
    return observation


def load_observations_as_of(
    market_id: str,
    knowledge_time: str | datetime,
    *,
    event_time: str | datetime | None = None,
    event_time_from: str | datetime | None = None,
    roles: Iterable[SemanticRole] | None = None,
    instrument_ids: Iterable[str] | None = None,
    sources: Iterable[str] | None = None,
) -> list[Observation]:
    """Return the latest knowable vintage for every instrument/event pair."""

    knowledge_cutoff = _canonical_utc(knowledge_time)
    predicates = ["market_id=?", "knowledge_time<=?"]
    params: list[str] = [market_id.upper(), knowledge_cutoff]
    if event_time is not None:
        predicates.append("event_time<=?")
        params.append(_canonical_utc(event_time))
    if event_time_from is not None:
        predicates.append("event_time>=?")
        params.append(_canonical_utc(event_time_from))
    role_values = tuple(role.value for role in roles) if roles is not None else ()
    if role_values:
        predicates.append(f"semantic_role IN ({','.join('?' for _ in role_values)})")
        params.extend(role_values)
    instrument_values = tuple(dict.fromkeys(instrument_ids or ()))
    if instrument_ids is not None and not instrument_values:
        return []
    if instrument_values:
        placeholders = ",".join("?" for _ in instrument_values)
        predicates.append(f"instrument_id IN ({placeholders})")
        params.extend(instrument_values)
    source_values = tuple(dict.fromkeys(sources or ()))
    if sources is not None and not source_values:
        return []
    if source_values:
        predicates.append(f"source IN ({','.join('?' for _ in source_values)})")
        params.extend(source_values)
    selected = ",".join(_CANONICAL_COLUMNS)
    where = " AND ".join(predicates)
    query = f"""
        WITH ranked AS (
          SELECT {selected},
                 ROW_NUMBER() OVER (
                   PARTITION BY market_id, instrument_id, event_time
                   ORDER BY knowledge_time DESC, source_publication_time DESC,
                            revision_id DESC
                 ) AS vintage_rank
            FROM canonical_observations
           WHERE {where}
        )
        SELECT {selected}
          FROM ranked
         WHERE vintage_rank=1
         ORDER BY event_time, instrument_id
    """
    with _lock, _conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_observation(row) for row in rows]


def load_observation_revisions(
    market_id: str,
    knowledge_time: str | datetime,
    *,
    instrument_ids: Iterable[str] | None = None,
    event_time: str | datetime | None = None,
    event_time_from: str | datetime | None = None,
) -> list[Observation]:
    """Return every stored vintage knowable by the requested cutoff."""

    predicates = ["market_id=?", "knowledge_time<=?"]
    params: list[str] = [market_id.upper(), _canonical_utc(knowledge_time)]
    if event_time is not None:
        predicates.append("event_time<=?")
        params.append(_canonical_utc(event_time))
    if event_time_from is not None:
        predicates.append("event_time>=?")
        params.append(_canonical_utc(event_time_from))
    instrument_values = tuple(dict.fromkeys(instrument_ids or ()))
    if instrument_ids is not None and not instrument_values:
        return []
    if instrument_values:
        predicates.append(f"instrument_id IN ({','.join('?' for _ in instrument_values)})")
        params.extend(instrument_values)

    selected = ",".join(_CANONICAL_COLUMNS)
    query = f"""SELECT {selected}
                  FROM canonical_observations
                 WHERE {' AND '.join(predicates)}
                 ORDER BY event_time, instrument_id, knowledge_time,
                          source_publication_time, revision_id, source"""
    with _lock, _conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_observation(row) for row in rows]


def load_observation_revisions_as_of(
    market_id: str,
    knowledge_time: str | datetime,
    *,
    event_time: str | datetime | None = None,
    roles: Iterable[SemanticRole] | None = None,
) -> list[Observation]:
    """Return every integrity-checked revision knowable by the cutoff."""

    predicates = ["market_id=?", "knowledge_time<=?"]
    params: list[str] = [market_id.upper(), _canonical_utc(knowledge_time)]
    if event_time is not None:
        predicates.append("event_time<=?")
        params.append(_canonical_utc(event_time))
    role_values = tuple(role.value for role in roles) if roles is not None else ()
    if role_values:
        predicates.append(f"semantic_role IN ({','.join('?' for _ in role_values)})")
        params.extend(role_values)
    selected = ",".join((*_CANONICAL_COLUMNS, "record_hash"))
    query = f"""
        SELECT {selected}
          FROM canonical_observations
         WHERE {" AND ".join(predicates)}
         ORDER BY event_time, instrument_id, knowledge_time,
                  source_publication_time, revision_id, source
    """
    with _lock, _conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_verified_row_to_observation(row) for row in rows]


def load_observation_page(
    market_id: str,
    knowledge_time: str | datetime,
    *,
    limit: int,
    event_time: str | datetime | None = None,
    event_time_from: str | datetime | None = None,
    roles: Iterable[SemanticRole] | None = None,
    instrument_ids: Iterable[str] | None = None,
    sources: Iterable[str] | None = None,
    redistribution_statuses: Iterable[RedistributionStatus] | None = None,
    before: tuple[str | datetime, str] | None = None,
) -> tuple[list[Observation], tuple[datetime, str] | None]:
    """Return one newest-first, SQL-bounded page of latest vintages.

    Redistribution is filtered only after the latest knowable vintage is
    selected.  A newly prohibited revision therefore cannot expose an older,
    otherwise redistributable revision of the same instrument/event pair.
    """

    if not 1 <= limit <= 5000:
        raise ValueError("limit must be between 1 and 5000")
    knowledge_cutoff = _canonical_utc(knowledge_time)
    event_cutoff = _canonical_utc(event_time) if event_time is not None else None
    event_floor = (
        _canonical_utc(event_time_from) if event_time_from is not None else None
    )
    role_values = tuple(role.value for role in roles) if roles is not None else ()
    instrument_values = tuple(dict.fromkeys(instrument_ids or ()))
    if instrument_ids is not None and not instrument_values:
        return [], None
    source_values = tuple(dict.fromkeys(sources or ()))
    if sources is not None and not source_values:
        return [], None
    before_event: str | None = None
    before_instrument: str | None = None
    if before is not None:
        before_event = _canonical_utc(before[0])
        before_instrument = before[1].strip()
        if not before_instrument:
            raise ValueError("cursor instrument_id is required")

    def bounded_predicates(alias: str) -> tuple[list[str], list[str]]:
        predicates = [f"{alias}.market_id=?", f"{alias}.knowledge_time<=?"]
        params = [market_id.upper(), knowledge_cutoff]
        if event_cutoff is not None:
            predicates.append(f"{alias}.event_time<=?")
            params.append(event_cutoff)
        if event_floor is not None:
            predicates.append(f"{alias}.event_time>=?")
            params.append(event_floor)
        if role_values:
            predicates.append(
                f"{alias}.semantic_role IN ({','.join('?' for _ in role_values)})"
            )
            params.extend(role_values)
        if instrument_values:
            predicates.append(
                f"{alias}.instrument_id IN "
                f"({','.join('?' for _ in instrument_values)})"
            )
            params.extend(instrument_values)
        if source_values:
            predicates.append(
                f"{alias}.source IN ({','.join('?' for _ in source_values)})"
            )
            params.extend(source_values)
        if before_event is not None and before_instrument is not None:
            predicates.append(
                f"({alias}.event_time<? OR "
                f"({alias}.event_time=? AND {alias}.instrument_id<?))"
            )
            params.extend((before_event, before_event, before_instrument))
        return predicates, params

    ranked_predicates, ranked_params = bounded_predicates("observation")

    selected = ",".join(_CANONICAL_COLUMNS)
    ranked_selected = ",".join(
        f"observation.{column} AS {column}" for column in _CANONICAL_COLUMNS
    )
    visible_predicates = ["vintage_rank=1"]
    redistribution_values = tuple(
        status.value for status in redistribution_statuses
    ) if redistribution_statuses is not None else ()
    if redistribution_statuses is not None:
        if not redistribution_values:
            return [], None
        visible_predicates.append(
            f"redistribution_status IN ({','.join('?' for _ in redistribution_values)})"
        )
    params: list[str | int] = [*ranked_params, *redistribution_values, limit + 1]
    query = f"""
        WITH ranked AS (
          SELECT {ranked_selected},
                 ROW_NUMBER() OVER (
                   PARTITION BY observation.market_id,
                                observation.instrument_id,
                                observation.event_time
                   ORDER BY observation.knowledge_time DESC,
                            observation.source_publication_time DESC,
                            observation.revision_id DESC,
                            observation.source DESC
                 ) AS vintage_rank
            FROM canonical_observations AS observation
           WHERE {' AND '.join(ranked_predicates)}
        )
        SELECT {selected}
          FROM ranked
         WHERE {' AND '.join(visible_predicates)}
         ORDER BY event_time DESC, instrument_id DESC
         LIMIT ?
    """
    with _lock, _conn() as conn:
        rows = conn.execute(query, params).fetchall()
    observations = [_row_to_observation(row) for row in rows]
    has_more = len(observations) > limit
    observations = observations[:limit]
    next_cursor = (
        (observations[-1].event_time, observations[-1].instrument_id)
        if has_more and observations
        else None
    )
    return observations, next_cursor


def latest_observation_hashes(
    market_id: str,
    knowledge_time: str | datetime,
    *,
    event_time: str | datetime | None = None,
    event_time_from: str | datetime | None = None,
    roles: Iterable[SemanticRole] | None = None,
    instrument_ids: Iterable[str] | None = None,
    sources: Iterable[str] | None = None,
) -> dict[tuple[str, datetime], str]:
    """Return only latest evidence hashes for adapter deduplication."""

    knowledge_cutoff = _canonical_utc(knowledge_time)
    predicates = ["market_id=?", "knowledge_time<=?"]
    params: list[str] = [market_id.upper(), knowledge_cutoff]
    if event_time is not None:
        predicates.append("event_time<=?")
        params.append(_canonical_utc(event_time))
    if event_time_from is not None:
        predicates.append("event_time>=?")
        params.append(_canonical_utc(event_time_from))
    role_values = tuple(role.value for role in roles) if roles is not None else ()
    if role_values:
        predicates.append(f"semantic_role IN ({','.join('?' for _ in role_values)})")
        params.extend(role_values)
    instrument_values = tuple(dict.fromkeys(instrument_ids or ()))
    if instrument_ids is not None and not instrument_values:
        return {}
    if instrument_values:
        predicates.append(f"instrument_id IN ({','.join('?' for _ in instrument_values)})")
        params.extend(instrument_values)
    source_values = tuple(dict.fromkeys(sources or ()))
    if sources is not None and not source_values:
        return {}
    if source_values:
        predicates.append(f"source IN ({','.join('?' for _ in source_values)})")
        params.extend(source_values)
    query = f"""
        WITH ranked AS (
          SELECT instrument_id, event_time, evidence_hash,
                 ROW_NUMBER() OVER (
                   PARTITION BY market_id, instrument_id, event_time
                   ORDER BY knowledge_time DESC, source_publication_time DESC,
                            revision_id DESC, source DESC
                 ) AS vintage_rank
            FROM canonical_observations
           WHERE {' AND '.join(predicates)}
        )
        SELECT instrument_id, event_time, evidence_hash
          FROM ranked
         WHERE vintage_rank=1
         ORDER BY event_time, instrument_id
    """
    with _lock, _conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return {
        (row[0], datetime.fromisoformat(row[1])): row[2]
        for row in rows
    }


def canonical_coverage(market_id: str) -> list[dict]:
    """Coverage by semantic role, based only on captured canonical rows."""

    with _lock, _conn() as conn:
        rows = conn.execute(
            """SELECT semantic_role, COUNT(*), MIN(event_time), MAX(event_time),
                      MAX(knowledge_time),
                      SUM(CASE WHEN quality='unavailable' THEN 1 ELSE 0 END)
                 FROM canonical_observations
                WHERE market_id=?
                GROUP BY semantic_role
                ORDER BY semantic_role""",
            (market_id.upper(),),
        ).fetchall()
    return [
        {
            "semantic_role": row[0],
            "observations": row[1],
            "event_start": row[2],
            "event_end": row[3],
            "latest_knowledge_time": row[4],
            "unavailable_observations": row[5],
        }
        for row in rows
    ]


def seal_market_snapshot(
    *,
    market_id: str,
    product: str,
    event_cutoff: str | datetime,
    knowledge_cutoff: str | datetime,
    calibration_id: str,
    evidence_eligible: bool,
    payload: object,
    promoted: bool = True,
) -> str:
    """Append an immutable API snapshot and return its content-derived ID."""

    normalized_market = market_id.upper()
    event = _canonical_utc(event_cutoff)
    knowledge = _canonical_utc(knowledge_cutoff)
    if event > knowledge:
        raise ValueError("event_cutoff cannot follow knowledge_cutoff")
    payload_json = canonical_market_payload_json(payload)
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    identity = "|".join(
        (normalized_market, product, event, knowledge, calibration_id, payload_hash)
    )
    snapshot_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    # Two independent source completions may materialize different payloads at
    # the same knowledge cutoff. Preserve subsecond insertion order so
    # ``latest`` cannot nondeterministically return the earlier seal.
    sealed_at = datetime.now(UTC).isoformat(timespec="microseconds")
    table = "market_snapshots" if promoted else "market_snapshot_staging"
    with _lock, _conn() as conn:
        conn.execute(
            f"""INSERT OR IGNORE INTO {table}
                 (snapshot_id, market_id, product, event_cutoff,
                  knowledge_cutoff, sealed_at, calibration_id,
                  evidence_eligible, payload_hash, payload)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                snapshot_id,
                normalized_market,
                product,
                event,
                knowledge,
                sealed_at,
                calibration_id,
                int(evidence_eligible),
                payload_hash,
                payload_json,
            ),
        )
    return snapshot_id


def promote_market_snapshots(snapshot_ids: Iterable[str]) -> None:
    """Atomically make an exact staged snapshot bundle visible to readers."""
    ids = tuple(dict.fromkeys(snapshot_ids))
    if not ids or any(not isinstance(item, str) or not item for item in ids):
        raise ValueError("snapshot_ids must contain non-empty strings")
    placeholders = ",".join("?" for _ in ids)
    with _lock, _conn() as conn:
        rows = conn.execute(
            f"""SELECT snapshot_id FROM market_snapshot_staging
                 WHERE snapshot_id IN ({placeholders})""",
            ids,
        ).fetchall()
        found = {row[0] for row in rows}
        if found != set(ids):
            raise ValueError("cannot promote a missing market snapshot")
        columns = (
            "snapshot_id,market_id,product,event_cutoff,knowledge_cutoff,"
            "sealed_at,calibration_id,evidence_eligible,payload_hash,payload"
        )
        conn.execute(
            f"""INSERT OR IGNORE INTO market_snapshots ({columns})
                 SELECT {columns} FROM market_snapshot_staging
                  WHERE snapshot_id IN ({placeholders})""",
            ids,
        )


def _release_handoff_envelope(envelope: dict) -> tuple[str, str]:
    if not isinstance(envelope, dict):
        raise ValueError("release handoff envelope must be a dictionary")
    try:
        canonical = json.dumps(
            envelope,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("release handoff envelope must be JSON-serializable") from exc
    return canonical, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stage_release_handoff(
    handoff_id: str,
    producer_sha: str,
    envelope: dict,
) -> None:
    """Append an immutable release envelope, allowing exact replay only."""

    if not isinstance(handoff_id, str) or not handoff_id:
        raise ValueError("handoff_id must be a non-empty string")
    if not isinstance(producer_sha, str) or not producer_sha:
        raise ValueError("producer_sha must be a non-empty string")
    canonical, envelope_hash = _release_handoff_envelope(envelope)
    with _lock, _conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO release_snapshot_handoffs
                 (handoff_id, producer_sha, envelope_hash, envelope)
               VALUES (?,?,?,?)""",
            (handoff_id, producer_sha, envelope_hash, canonical),
        )
        row = conn.execute(
            """SELECT producer_sha, envelope_hash, envelope
                 FROM release_snapshot_handoffs
                WHERE handoff_id=?""",
            (handoff_id,),
        ).fetchone()
        if row != (producer_sha, envelope_hash, canonical):
            raise ValueError(
                "release handoff already exists with a different producer or envelope"
            )


def load_release_handoff(handoff_id: str) -> dict | None:
    if not isinstance(handoff_id, str) or not handoff_id:
        raise ValueError("handoff_id must be a non-empty string")
    with _lock, _conn() as conn:
        row = conn.execute(
            """SELECT envelope FROM release_snapshot_handoffs
                WHERE handoff_id=?""",
            (handoff_id,),
        ).fetchone()
    return json.loads(row[0]) if row is not None else None


def load_active_release_handoff() -> dict | None:
    with _lock, _conn() as conn:
        row = conn.execute(
            """SELECT handoff.envelope
                 FROM active_release_snapshot_handoff AS active
                 JOIN release_snapshot_handoffs AS handoff
                   ON handoff.handoff_id = active.handoff_id
                WHERE active.singleton=1"""
        ).fetchone()
    return json.loads(row[0]) if row is not None else None


def activate_release_handoff(
    handoff_id: str,
    producer_sha: str,
    snapshot_bindings: Iterable[tuple[str, str, str, str]],
) -> None:
    """Atomically copy a staged bundle and advance the active envelope pointer."""

    if not isinstance(handoff_id, str) or not handoff_id:
        raise ValueError("handoff_id must be a non-empty string")
    if not isinstance(producer_sha, str) or not producer_sha:
        raise ValueError("producer_sha must be a non-empty string")
    bindings = tuple(tuple(binding) for binding in snapshot_bindings)
    if not bindings or any(
        len(binding) != 4
        or not all(isinstance(item, str) and item for item in binding)
        for binding in bindings
    ):
        raise ValueError(
            "snapshot_bindings must contain product, snapshot, record, and row hashes"
        )
    products = tuple(binding[0] for binding in bindings)
    ids = tuple(binding[1] for binding in bindings)
    record_hashes = tuple(binding[2] for binding in bindings)
    row_hashes = tuple(binding[3] for binding in bindings)
    if (
        len(set(products)) != len(products)
        or len(set(ids)) != len(ids)
        or len(set(record_hashes)) != len(record_hashes)
        or len(set(row_hashes)) != len(row_hashes)
    ):
        raise ValueError("snapshot bindings must be unique")
    expected = {
        snapshot_id: (product, record_hash, row_hash)
        for product, snapshot_id, record_hash, row_hash in bindings
    }
    placeholders = ",".join("?" for _ in ids)
    columns = (
        "snapshot_id,market_id,product,event_cutoff,knowledge_cutoff,"
        "sealed_at,calibration_id,evidence_eligible,payload_hash,payload"
    )
    with _lock, _conn() as conn:
        # Schema setup commits before returning. BEGIN IMMEDIATE therefore
        # covers every validation, copied snapshot, and the pointer flip while
        # also excluding another process-level writer.
        conn.execute("BEGIN IMMEDIATE")
        handoff = conn.execute(
            """SELECT producer_sha, envelope_hash, envelope
                 FROM release_snapshot_handoffs
                WHERE handoff_id=?""",
            (handoff_id,),
        ).fetchone()
        if handoff is None:
            raise ValueError("release handoff does not exist")
        if handoff[0] != producer_sha:
            raise ValueError("release handoff producer mismatch")
        try:
            envelope = json.loads(handoff[2])
            _, envelope_hash = _release_handoff_envelope(envelope)
            authorized_bindings = validate_release_handoff_envelope(
                envelope,
                expected_handoff_id=handoff_id,
                expected_producer_sha=producer_sha,
            )
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError("release handoff envelope is invalid") from exc
        if (
            handoff[1] != envelope_hash
            or set(authorized_bindings) != set(bindings)
        ):
            raise ValueError("activation bindings differ from the locked handoff")
        active = conn.execute(
            """SELECT active.handoff_id, handoff.producer_sha,
                      handoff.envelope_hash, handoff.envelope
                 FROM active_release_snapshot_handoff AS active
                 JOIN release_snapshot_handoffs AS handoff
                   ON handoff.handoff_id = active.handoff_id
                WHERE active.singleton=1"""
        ).fetchone()
        if active is not None and active[0] != handoff_id:
            try:
                active_envelope = json.loads(active[3])
                _, active_envelope_hash = _release_handoff_envelope(active_envelope)
                validate_release_handoff_envelope(
                    active_envelope,
                    expected_handoff_id=active[0],
                    expected_producer_sha=active[1],
                )
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValueError("active release handoff is invalid") from exc
            if active[2] != active_envelope_hash:
                raise ValueError("active release handoff envelope hash is invalid")
            if active[1] == producer_sha and release_handoff_generated_at(
                envelope
            ) <= release_handoff_generated_at(active_envelope):
                raise ValueError("cannot regress an active same-release handoff")
        staged_rows = conn.execute(
            f"""SELECT {columns} FROM market_snapshot_staging
                  WHERE snapshot_id IN ({placeholders})
                  ORDER BY snapshot_id""",
            ids,
        ).fetchall()
        staged = {row[0]: _snapshot_record(row) for row in staged_rows}
        if set(staged) != set(ids):
            raise ValueError("cannot activate a missing market snapshot")
        forward_columns = ",".join(_FORWARD_RECORD_COLUMNS)
        forward_rows = conn.execute(
            f"""SELECT {forward_columns} FROM forward_validation_records
                  WHERE snapshot_id IN ({placeholders})
                  ORDER BY snapshot_id""",
            ids,
        ).fetchall()
        forward = {
            row[1]: _forward_record_from_row(row)
            for row in forward_rows
        }
        if set(forward) != set(ids):
            raise ValueError("cannot activate without an exact forward record")
        for snapshot_id in ids:
            product, expected_record_hash, expected_row_hash = expected[snapshot_id]
            if staged[snapshot_id]["product"] != product:
                raise ValueError("release receipt product does not match staging")
            validate_snapshot_forward_binding(
                staged[snapshot_id],
                forward[snapshot_id],
                expected_record_hash,
                expected_row_hash,
            )
        conn.execute(
            f"""INSERT OR IGNORE INTO market_snapshots ({columns})
                 SELECT {columns} FROM market_snapshot_staging
                  WHERE snapshot_id IN ({placeholders})
                  ORDER BY snapshot_id""",
            ids,
        )
        canonical_rows = conn.execute(
            f"""SELECT {columns} FROM market_snapshots
                  WHERE snapshot_id IN ({placeholders})""",
            ids,
        ).fetchall()
        canonical = {row[0]: _snapshot_record(row) for row in canonical_rows}
        if canonical != staged:
            raise RuntimeError(
                "release handoff canonical rows differ from validated staging"
            )
        conn.execute(
            """INSERT INTO active_release_snapshot_handoff
                 (singleton, handoff_id) VALUES (1, ?)
               ON CONFLICT(singleton) DO UPDATE
                 SET handoff_id=excluded.handoff_id""",
            (handoff_id,),
        )


def _snapshot_record(row: tuple | None) -> dict | None:
    if row is None:
        return None
    return {
        "snapshot_id": row[0],
        "market_id": row[1],
        "product": row[2],
        "event_cutoff": row[3],
        "knowledge_cutoff": row[4],
        "sealed_at": row[5],
        "calibration_id": row[6],
        "evidence_eligible": bool(row[7]),
        "payload_hash": row[8],
        "payload": json.loads(row[9]),
    }


def load_staged_market_snapshot(snapshot_id: str) -> dict | None:
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ValueError("snapshot_id must be a non-empty string")
    with _lock, _conn() as conn:
        row = conn.execute(
            """SELECT snapshot_id, market_id, product, event_cutoff,
                      knowledge_cutoff, sealed_at, calibration_id,
                      evidence_eligible, payload_hash, payload
                 FROM market_snapshot_staging WHERE snapshot_id=?""",
            (snapshot_id,),
        ).fetchone()
    return _snapshot_record(row)


def load_latest_market_snapshot(market_id: str, product: str) -> dict | None:
    with _lock, _conn() as conn:
        row = conn.execute(
            """SELECT snapshot_id, market_id, product, event_cutoff,
                      knowledge_cutoff, sealed_at, calibration_id,
                      evidence_eligible, payload_hash, payload
                 FROM market_snapshots
                WHERE market_id=? AND product=?
                ORDER BY knowledge_cutoff DESC, sealed_at DESC
                LIMIT 1""",
            (market_id.upper(), product),
        ).fetchone()
    return _snapshot_record(row)


def load_market_snapshot_as_of(
    market_id: str,
    product: str,
    knowledge_time: str | datetime,
) -> dict | None:
    cutoff = _canonical_utc(knowledge_time)
    with _lock, _conn() as conn:
        row = conn.execute(
            """SELECT snapshot_id, market_id, product, event_cutoff,
                      knowledge_cutoff, sealed_at, calibration_id,
                      evidence_eligible, payload_hash, payload
                 FROM market_snapshots
                WHERE market_id=? AND product=? AND knowledge_cutoff<=?
                ORDER BY knowledge_cutoff DESC, sealed_at DESC
                LIMIT 1""",
            (market_id.upper(), product, cutoff),
        ).fetchone()
    return _snapshot_record(row)


def collector_run_id(run: dict) -> str:
    """Keep run identity stable as scheduler state fields evolve."""

    identity = {field: run.get(field) for field in _COLLECTOR_RUN_ID_FIELDS}
    canonical = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def save_collector_run(run: dict) -> str:
    """Append one independently scheduled collector outcome."""

    run_id = collector_run_id(run)
    with _lock, _conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO collector_runs
                 (run_id, market_id, adapter_id, status, started_at, finished_at,
                  observations_written, attempts, next_due, fault)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                str(run["market_id"]).upper(),
                run["adapter_id"],
                run["status"],
                _canonical_utc(run["started_at"]),
                _canonical_utc(run["finished_at"]),
                int(run["observations_written"]),
                int(run["attempts"]),
                _canonical_utc(run["next_due"]),
                run.get("fault"),
            ),
        )
        conn.execute(
            """INSERT INTO collector_states
                 (market_id, adapter_id, next_due, consecutive_failures,
                  circuit_open_until, updated_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(market_id, adapter_id) DO UPDATE SET
                 next_due=excluded.next_due,
                 consecutive_failures=excluded.consecutive_failures,
                 circuit_open_until=excluded.circuit_open_until,
                 updated_at=excluded.updated_at
               WHERE excluded.updated_at >= collector_states.updated_at""",
            (
                str(run["market_id"]).upper(),
                run["adapter_id"],
                _canonical_utc(run["next_due"]),
                int(run.get("consecutive_failures", 0)),
                (
                    _canonical_utc(run["circuit_open_until"])
                    if run.get("circuit_open_until") is not None
                    else None
                ),
                _canonical_utc(run["finished_at"]),
            ),
        )
    return run_id


def latest_collector_runs(market_id: str | None = None) -> list[dict]:
    predicate = "WHERE market_id=?" if market_id is not None else ""
    params = (market_id.upper(),) if market_id is not None else ()
    with _lock, _conn() as conn:
        rows = conn.execute(
            f"""WITH ranked AS (
                  SELECT run_id, market_id, adapter_id, status, started_at,
                         finished_at, observations_written, attempts, next_due,
                         fault,
                         ROW_NUMBER() OVER (
                           PARTITION BY market_id, adapter_id
                           ORDER BY finished_at DESC, run_id DESC
                         ) AS run_rank
                    FROM collector_runs {predicate}
                )
                SELECT ranked.run_id, ranked.market_id, ranked.adapter_id,
                       ranked.status, ranked.started_at, ranked.finished_at,
                       ranked.observations_written, ranked.attempts,
                       ranked.next_due, ranked.fault,
                       COALESCE(states.consecutive_failures, 0),
                       states.circuit_open_until
                  FROM ranked
                  LEFT JOIN collector_states AS states
                    ON states.market_id=ranked.market_id
                   AND states.adapter_id=ranked.adapter_id
                 WHERE ranked.run_rank=1
                 ORDER BY ranked.market_id, ranked.adapter_id""",
            params,
        ).fetchall()
    keys = (
        "run_id",
        "market_id",
        "adapter_id",
        "status",
        "started_at",
        "finished_at",
        "observations_written",
        "attempts",
        "next_due",
        "fault",
        "consecutive_failures",
        "circuit_open_until",
    )
    return [dict(zip(keys, row, strict=True)) for row in rows]


def load_collector_states(market_id: str | None = None) -> list[dict]:
    predicate = "WHERE market_id=?" if market_id is not None else ""
    params = (market_id.upper(),) if market_id is not None else ()
    with _lock, _conn() as conn:
        rows = conn.execute(
            f"""SELECT market_id, adapter_id, next_due, consecutive_failures,
                       circuit_open_until, updated_at
                  FROM collector_states {predicate}
                 ORDER BY market_id, adapter_id""",
            params,
        ).fetchall()
    keys = (
        "market_id",
        "adapter_id",
        "next_due",
        "consecutive_failures",
        "circuit_open_until",
        "updated_at",
    )
    return [dict(zip(keys, row, strict=True)) for row in rows]


def save_worker_heartbeat(
    *,
    component_id: str,
    heartbeat_at: str | datetime,
    expected_by: str | datetime,
) -> None:
    """Upsert a privacy-safe component heartbeat without regressing time."""

    heartbeat = _canonical_utc(heartbeat_at)
    deadline = _canonical_utc(expected_by)
    if deadline < heartbeat:
        raise ValueError("worker heartbeat deadline cannot precede its timestamp")
    with _lock, _conn() as conn:
        conn.execute(
            """INSERT INTO worker_heartbeats
                 (component_id, heartbeat_at, expected_by)
               VALUES (?,?,?)
               ON CONFLICT(component_id) DO UPDATE SET
                 heartbeat_at=excluded.heartbeat_at,
                 expected_by=excluded.expected_by
               WHERE excluded.heartbeat_at >= worker_heartbeats.heartbeat_at""",
            (component_id, heartbeat, deadline),
        )


def load_worker_heartbeat(component_id: str) -> dict | None:
    with _lock, _conn() as conn:
        row = conn.execute(
            """SELECT component_id, heartbeat_at, expected_by
                 FROM worker_heartbeats WHERE component_id=?""",
            (component_id,),
        ).fetchone()
    if row is None:
        return None
    return dict(
        zip(
            ("component_id", "heartbeat_at", "expected_by"),
            row,
            strict=True,
        )
    )


_FORWARD_RECORD_COLUMNS = (
    "record_id",
    "snapshot_id",
    "market_id",
    "product",
    "event_cutoff",
    "knowledge_cutoff",
    "calibration_id",
    "chain_generation",
    "created_at",
    "payload_hash",
    "previous_record_hash",
    "record_hash",
    "payload",
)


def _forward_record_from_row(row: tuple) -> dict:
    record = dict(zip(_FORWARD_RECORD_COLUMNS, row, strict=True))
    if isinstance(record["payload"], str):
        record["payload"] = json.loads(record["payload"])
    return record


def append_forward_record(
    *,
    snapshot_id: str,
    market_id: str,
    product: str,
    event_cutoff: str | datetime,
    knowledge_cutoff: str | datetime,
    calibration_id: str,
    payload: object,
) -> str:
    """Append one immutable, per-product hash-chain link for paper validation."""

    market = market_id.upper()
    event = _canonical_utc(event_cutoff)
    knowledge = _canonical_utc(knowledge_cutoff)
    payload_json = canonical_market_payload_json(payload)
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    generation = forward_chain_generation(calibration_id)
    with _lock, _conn() as conn:
        _require_forward_child_index(conn)
        # Serialize the read-head/write-child sequence across processes. The
        # partial unique index remains the database-level last line of defence.
        conn.execute("BEGIN IMMEDIATE")
        columns = ",".join(_FORWARD_RECORD_COLUMNS)
        existing_row = conn.execute(
            f"""SELECT {columns}
                 FROM forward_validation_records WHERE snapshot_id=?""",
            (snapshot_id,),
        ).fetchone()
        existing = (
            _forward_record_from_row(existing_row)
            if existing_row is not None
            else None
        )
        if existing is not None:
            if (
                existing["market_id"],
                existing["product"],
                existing["calibration_id"],
            ) != (market, product, calibration_id):
                raise ValueError(
                    "forward snapshot identity is already bound to another chain"
                )
        rows = conn.execute(
            f"""SELECT {columns} FROM forward_validation_records
                 WHERE market_id=? AND product=? AND calibration_id=?""",
            (market, product, calibration_id),
        ).fetchall()
        records = [_forward_record_from_row(row) for row in rows]
        previous_hash = "0" * 64
        previous_created_at: datetime | None = None
        if records:
            # Imported lazily to keep the storage and validation modules free
            # of an import-time cycle.
            from seiche.markets.validation_forward import verify_forward_chain

            integrity = verify_forward_chain(
                records, minimum_records=0, minimum_span_days=0
            )
            chains = integrity["metrics"]["chains"]
            if integrity["status"] != "PASS" or len(chains) != 1:
                raise ValueError(
                    "forward chain has no single valid head; refusing to append"
                )
            previous_hash = chains[0]["head_record_hash"]
            previous_record = next(
                row for row in records if row["record_hash"] == previous_hash
            )
            previous_created_at = datetime.fromisoformat(
                str(previous_record["created_at"]).replace("Z", "+00:00")
            ).astimezone(UTC)
        if existing is not None:
            if (
                _canonical_utc(existing["event_cutoff"]) != event
                or _canonical_utc(existing["knowledge_cutoff"]) != knowledge
                or existing["payload_hash"] != payload_hash
            ):
                raise ValueError(
                    "forward snapshot retry does not match its stored identity"
                )
            return str(existing["record_id"])
        created_at = datetime.now(UTC)
        if previous_created_at is not None:
            if created_at <= previous_created_at:
                created_at = previous_created_at + timedelta(microseconds=1)
        record_hash = forward_record_hash(
            snapshot_id=snapshot_id,
            market_id=market,
            product=product,
            event_cutoff=event,
            knowledge_cutoff=knowledge,
            calibration_id=calibration_id,
            payload_hash=payload_hash,
            previous_record_hash=previous_hash,
        )
        record_id = record_hash
        conn.execute(
            """INSERT INTO forward_validation_records
                 (record_id, snapshot_id, market_id, product, event_cutoff,
                  knowledge_cutoff, calibration_id, chain_generation, created_at,
                  payload_hash, previous_record_hash, record_hash, payload)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                record_id,
                snapshot_id,
                market,
                product,
                event,
                knowledge,
                calibration_id,
                generation,
                created_at.isoformat(timespec="microseconds"),
                payload_hash,
                previous_hash,
                record_hash,
                payload_json,
            ),
        )
    return record_id


def load_forward_records(
    market_id: str | None = None,
    product: str | None = None,
    calibration_id: str | None = None,
) -> list[dict]:
    """Read immutable links; ordering is presentation-only, never topology."""

    predicates: list[str] = []
    params: list[str] = []
    if market_id is not None:
        predicates.append("market_id=?")
        params.append(market_id.upper())
    if product is not None:
        predicates.append("product=?")
        params.append(product)
    if calibration_id is not None:
        predicates.append("calibration_id=?")
        params.append(calibration_id)
    where = f" WHERE {' AND '.join(predicates)}" if predicates else ""
    columns = ",".join(_FORWARD_RECORD_COLUMNS)
    with _lock, _conn() as conn:
        rows = conn.execute(
            f"""SELECT {columns}
                  FROM forward_validation_records{where}
                 ORDER BY created_at, record_id""",
            params,
        ).fetchall()
    return [_forward_record_from_row(row) for row in rows]


def forward_record_count(market_id: str | None = None) -> int:
    predicate = " WHERE market_id=?" if market_id is not None else ""
    params = (market_id.upper(),) if market_id is not None else ()
    with _lock, _conn() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) FROM forward_validation_records{predicate}",
            params,
        ).fetchone()
    return int(row[0]) if row else 0


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
            (
                key,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                json.dumps(payload),
            ),
        )


def save_blobs(payloads: dict[str, object]) -> None:
    """Atomically replace a related set of blob values."""
    if not payloads or any(not isinstance(key, str) or not key for key in payloads):
        raise ValueError("payloads must use non-empty string keys")
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [
        (key, fetched_at, json.dumps(payload))
        for key, payload in payloads.items()
    ]
    with _lock, _conn() as conn:
        conn.executemany("INSERT OR REPLACE INTO blobs VALUES (?,?,?)", rows)


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
