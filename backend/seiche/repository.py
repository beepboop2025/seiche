"""Repository boundary for canonical observations and sealed market products.

SQLite remains the zero-configuration compatibility backend. Setting
``SEICHE_DATABASE_URL`` selects PostgreSQL for v2 metadata and snapshots; the
legacy mnemonic cache remains in ``seiche.store`` during the v1 migration.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Protocol

from seiche import store
from seiche.domain.observation import Observation, SemanticRole


class MarketRepository(Protocol):
    def save_observations(self, observations: Iterable[Observation]) -> int: ...

    def load_observations_as_of(
        self,
        market_id: str,
        knowledge_time: str | datetime,
        *,
        event_time: str | datetime | None = None,
        roles: Iterable[SemanticRole] | None = None,
    ) -> list[Observation]: ...

    def canonical_coverage(self, market_id: str) -> list[dict]: ...

    def seal_market_snapshot(
        self,
        *,
        market_id: str,
        product: str,
        event_cutoff: str | datetime,
        knowledge_cutoff: str | datetime,
        calibration_id: str,
        evidence_eligible: bool,
        payload: object,
    ) -> str: ...

    def load_latest_market_snapshot(self, market_id: str, product: str) -> dict | None: ...

    def load_market_snapshot_as_of(
        self,
        market_id: str,
        product: str,
        knowledge_time: str | datetime,
    ) -> dict | None: ...

    def save_collector_run(self, run: dict) -> str: ...

    def latest_collector_runs(self, market_id: str | None = None) -> list[dict]: ...

    def append_forward_record(
        self,
        *,
        snapshot_id: str,
        market_id: str,
        product: str,
        event_cutoff: str | datetime,
        knowledge_cutoff: str | datetime,
        calibration_id: str,
        payload: object,
    ) -> str: ...

    def forward_record_count(self, market_id: str | None = None) -> int: ...


class SQLiteMarketRepository:
    """Delegate to the additive SQLite migration in ``seiche.store``."""

    save_observations = staticmethod(store.save_observations)
    load_observations_as_of = staticmethod(store.load_observations_as_of)
    canonical_coverage = staticmethod(store.canonical_coverage)
    seal_market_snapshot = staticmethod(store.seal_market_snapshot)
    load_latest_market_snapshot = staticmethod(store.load_latest_market_snapshot)
    load_market_snapshot_as_of = staticmethod(store.load_market_snapshot_as_of)
    save_collector_run = staticmethod(store.save_collector_run)
    latest_collector_runs = staticmethod(store.latest_collector_runs)
    append_forward_record = staticmethod(store.append_forward_record)
    forward_record_count = staticmethod(store.forward_record_count)


_OBSERVATION_COLUMNS = (
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

_OBSERVATION_INSERT_COLUMNS = (*_OBSERVATION_COLUMNS, "record_hash")
_OBSERVATION_INSERT_PLACEHOLDERS = tuple(
    "%s::jsonb" if column == "jurisdiction_codes" else "%s"
    for column in _OBSERVATION_INSERT_COLUMNS
)

_POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS canonical_observations (
  market_id TEXT NOT NULL,
  monetary_area_id TEXT NOT NULL,
  jurisdiction_codes JSONB NOT NULL,
  currency TEXT NOT NULL,
  instrument_id TEXT NOT NULL,
  semantic_role TEXT NOT NULL,
  value NUMERIC,
  canonical_unit TEXT NOT NULL,
  rate_compounding TEXT,
  day_count TEXT,
  event_time TIMESTAMPTZ NOT NULL,
  knowledge_time TIMESTAMPTZ NOT NULL,
  source_publication_time TIMESTAMPTZ NOT NULL,
  revision_id TEXT NOT NULL,
  source TEXT NOT NULL,
  evidence_hash TEXT NOT NULL,
  connector_classification TEXT NOT NULL,
  redistribution_status TEXT NOT NULL,
  quality TEXT NOT NULL,
  staleness TEXT NOT NULL,
  record_hash TEXT NOT NULL,
  PRIMARY KEY (market_id, instrument_id, event_time, knowledge_time, source, revision_id)
);
CREATE INDEX IF NOT EXISTS canonical_observations_asof
  ON canonical_observations (market_id, semantic_role, event_time, knowledge_time);
CREATE TABLE IF NOT EXISTS market_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  market_id TEXT NOT NULL,
  product TEXT NOT NULL,
  event_cutoff TIMESTAMPTZ NOT NULL,
  knowledge_cutoff TIMESTAMPTZ NOT NULL,
  sealed_at TIMESTAMPTZ NOT NULL,
  calibration_id TEXT NOT NULL,
  evidence_eligible BOOLEAN NOT NULL,
  payload_hash TEXT NOT NULL,
  payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS market_snapshots_latest
  ON market_snapshots (market_id, product, knowledge_cutoff DESC, sealed_at DESC);
CREATE TABLE IF NOT EXISTS collector_runs (
  run_id TEXT PRIMARY KEY,
  market_id TEXT NOT NULL,
  adapter_id TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ NOT NULL,
  observations_written INTEGER NOT NULL,
  attempts INTEGER NOT NULL,
  next_due TIMESTAMPTZ NOT NULL,
  fault TEXT
);
CREATE INDEX IF NOT EXISTS collector_runs_latest
  ON collector_runs (market_id, adapter_id, finished_at DESC);
CREATE TABLE IF NOT EXISTS forward_validation_records (
  record_id TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL UNIQUE,
  market_id TEXT NOT NULL,
  product TEXT NOT NULL,
  event_cutoff TIMESTAMPTZ NOT NULL,
  knowledge_cutoff TIMESTAMPTZ NOT NULL,
  calibration_id TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  payload_hash TEXT NOT NULL,
  previous_record_hash TEXT NOT NULL,
  record_hash TEXT NOT NULL UNIQUE,
  payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS forward_records_chain
  ON forward_validation_records (market_id, product, created_at, record_id);
"""


def _utc(value: str | datetime) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC).replace(microsecond=0)


def _observation_values(observation: Observation) -> tuple[Any, ...]:
    record = observation.to_record()
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    record_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return (
        observation.market_id,
        observation.monetary_area_id,
        json.dumps(list(observation.jurisdiction_codes)),
        observation.currency,
        observation.instrument_id,
        observation.semantic_role.value,
        observation.value,
        observation.canonical_unit.value,
        observation.rate_compounding.value if observation.rate_compounding else None,
        observation.day_count.value if observation.day_count else None,
        observation.event_time,
        observation.knowledge_time,
        observation.source_publication_time,
        observation.revision_id,
        observation.source,
        observation.evidence_hash,
        observation.connector_classification.value,
        observation.redistribution_status.value,
        observation.quality.value,
        observation.staleness.value,
        record_hash,
    )


class PostgresMarketRepository:
    """PostgreSQL implementation for canonical metadata and sealed snapshots."""

    def __init__(self, dsn: str) -> None:
        if not dsn.strip():
            raise ValueError("PostgreSQL DSN is required")
        self.dsn = dsn
        self._initialized = False
        self._init_lock = threading.Lock()

    def _connect(self):
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError(
                "PostgreSQL selected; install seiche[postgres]"
            ) from exc
        return psycopg.connect(self.dsn)

    def _ensure_schema(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            with self._connect() as connection:
                # Executing individual statements works with both psycopg's
                # simple and extended query protocols.
                for statement in _POSTGRES_SCHEMA.split(";"):
                    if statement.strip():
                        connection.execute(statement)
            self._initialized = True

    def save_observations(self, observations: Iterable[Observation]) -> int:
        batch = tuple(observations)
        if not batch:
            return 0
        self._ensure_schema()
        columns = ",".join(_OBSERVATION_INSERT_COLUMNS)
        placeholders = ",".join(_OBSERVATION_INSERT_PLACEHOLDERS)
        inserted = 0
        with self._connect() as connection:
            with connection.cursor() as cursor:
                for observation in batch:
                    values = _observation_values(observation)
                    cursor.execute(
                        f"""INSERT INTO canonical_observations ({columns})
                            VALUES ({placeholders})
                            ON CONFLICT DO NOTHING
                            RETURNING record_hash""",
                        values,
                    )
                    if cursor.fetchone() is not None:
                        inserted += 1
                        continue
                    cursor.execute(
                        """SELECT record_hash FROM canonical_observations
                            WHERE market_id=%s AND instrument_id=%s AND event_time=%s
                              AND knowledge_time=%s AND source=%s AND revision_id=%s""",
                        (
                            observation.market_id,
                            observation.instrument_id,
                            observation.event_time,
                            observation.knowledge_time,
                            observation.source,
                            observation.revision_id,
                        ),
                    )
                    existing = cursor.fetchone()
                    if existing is None or existing[0] != values[-1]:
                        raise ValueError(
                            "canonical observation identity collision with different content"
                        )
        return inserted

    def load_observations_as_of(
        self,
        market_id: str,
        knowledge_time: str | datetime,
        *,
        event_time: str | datetime | None = None,
        roles: Iterable[SemanticRole] | None = None,
    ) -> list[Observation]:
        self._ensure_schema()
        predicates = ["market_id=%s", "knowledge_time<=%s"]
        params: list[Any] = [market_id.upper(), _utc(knowledge_time)]
        if event_time is not None:
            predicates.append("event_time<=%s")
            params.append(_utc(event_time))
        role_values = tuple(role.value for role in roles) if roles is not None else ()
        if role_values:
            predicates.append(f"semantic_role IN ({','.join(['%s'] * len(role_values))})")
            params.extend(role_values)
        selected = ",".join(_OBSERVATION_COLUMNS)
        query = f"""
            WITH ranked AS (
              SELECT {selected},
                     ROW_NUMBER() OVER (
                       PARTITION BY market_id, instrument_id, event_time
                       ORDER BY knowledge_time DESC, source_publication_time DESC,
                                revision_id DESC
                     ) AS vintage_rank
                FROM canonical_observations
               WHERE {' AND '.join(predicates)}
            )
            SELECT {selected} FROM ranked
             WHERE vintage_rank=1
             ORDER BY event_time, instrument_id
        """
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        observations = []
        for row in rows:
            record = dict(zip(_OBSERVATION_COLUMNS, row, strict=True))
            if isinstance(record["jurisdiction_codes"], str):
                record["jurisdiction_codes"] = json.loads(record["jurisdiction_codes"])
            observations.append(Observation.from_record(record))
        return observations

    def canonical_coverage(self, market_id: str) -> list[dict]:
        self._ensure_schema()
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT semantic_role, COUNT(*), MIN(event_time), MAX(event_time),
                          MAX(knowledge_time),
                          SUM(CASE WHEN quality='unavailable' THEN 1 ELSE 0 END)
                     FROM canonical_observations
                    WHERE market_id=%s
                    GROUP BY semantic_role
                    ORDER BY semantic_role""",
                (market_id.upper(),),
            ).fetchall()
        return [
            {
                "semantic_role": row[0],
                "observations": row[1],
                "event_start": row[2].isoformat() if row[2] else None,
                "event_end": row[3].isoformat() if row[3] else None,
                "latest_knowledge_time": row[4].isoformat() if row[4] else None,
                "unavailable_observations": row[5],
            }
            for row in rows
        ]

    def seal_market_snapshot(
        self,
        *,
        market_id: str,
        product: str,
        event_cutoff: str | datetime,
        knowledge_cutoff: str | datetime,
        calibration_id: str,
        evidence_eligible: bool,
        payload: object,
    ) -> str:
        self._ensure_schema()
        market = market_id.upper()
        event = _utc(event_cutoff)
        knowledge = _utc(knowledge_cutoff)
        if event > knowledge:
            raise ValueError("event_cutoff cannot follow knowledge_cutoff")
        payload_json = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        identity = "|".join(
            (market, product, event.isoformat(), knowledge.isoformat(), calibration_id, payload_hash)
        )
        snapshot_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO market_snapshots
                     (snapshot_id, market_id, product, event_cutoff,
                      knowledge_cutoff, sealed_at, calibration_id,
                      evidence_eligible, payload_hash, payload)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                   ON CONFLICT DO NOTHING""",
                (
                    snapshot_id,
                    market,
                    product,
                    event,
                    knowledge,
                    datetime.now(UTC),
                    calibration_id,
                    evidence_eligible,
                    payload_hash,
                    payload_json,
                ),
            )
        return snapshot_id

    @staticmethod
    def _snapshot(row: tuple | None) -> dict | None:
        if row is None:
            return None
        payload = json.loads(row[9]) if isinstance(row[9], str) else row[9]
        return {
            "snapshot_id": row[0],
            "market_id": row[1],
            "product": row[2],
            "event_cutoff": row[3].isoformat(),
            "knowledge_cutoff": row[4].isoformat(),
            "sealed_at": row[5].isoformat(),
            "calibration_id": row[6],
            "evidence_eligible": bool(row[7]),
            "payload_hash": row[8],
            "payload": payload,
        }

    def load_latest_market_snapshot(self, market_id: str, product: str) -> dict | None:
        self._ensure_schema()
        with self._connect() as connection:
            row = connection.execute(
                """SELECT snapshot_id, market_id, product, event_cutoff,
                          knowledge_cutoff, sealed_at, calibration_id,
                          evidence_eligible, payload_hash, payload
                     FROM market_snapshots
                    WHERE market_id=%s AND product=%s
                    ORDER BY knowledge_cutoff DESC, sealed_at DESC
                    LIMIT 1""",
                (market_id.upper(), product),
            ).fetchone()
        return self._snapshot(row)

    def load_market_snapshot_as_of(
        self,
        market_id: str,
        product: str,
        knowledge_time: str | datetime,
    ) -> dict | None:
        self._ensure_schema()
        with self._connect() as connection:
            row = connection.execute(
                """SELECT snapshot_id, market_id, product, event_cutoff,
                          knowledge_cutoff, sealed_at, calibration_id,
                          evidence_eligible, payload_hash, payload
                     FROM market_snapshots
                    WHERE market_id=%s AND product=%s AND knowledge_cutoff<=%s
                    ORDER BY knowledge_cutoff DESC, sealed_at DESC
                    LIMIT 1""",
                (market_id.upper(), product, _utc(knowledge_time)),
            ).fetchone()
        return self._snapshot(row)

    def save_collector_run(self, run: dict) -> str:
        self._ensure_schema()
        canonical = json.dumps(
            run,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        run_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO collector_runs
                     (run_id, market_id, adapter_id, status, started_at,
                      finished_at, observations_written, attempts, next_due, fault)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT DO NOTHING""",
                (
                    run_id,
                    str(run["market_id"]).upper(),
                    run["adapter_id"],
                    run["status"],
                    _utc(run["started_at"]),
                    _utc(run["finished_at"]),
                    int(run["observations_written"]),
                    int(run["attempts"]),
                    _utc(run["next_due"]),
                    run.get("fault"),
                ),
            )
        return run_id

    def latest_collector_runs(self, market_id: str | None = None) -> list[dict]:
        self._ensure_schema()
        predicate = "WHERE market_id=%s" if market_id is not None else ""
        params = (market_id.upper(),) if market_id is not None else ()
        with self._connect() as connection:
            rows = connection.execute(
                f"""WITH ranked AS (
                      SELECT run_id, market_id, adapter_id, status, started_at,
                             finished_at, observations_written, attempts,
                             next_due, fault,
                             ROW_NUMBER() OVER (
                               PARTITION BY market_id, adapter_id
                               ORDER BY finished_at DESC, run_id DESC
                             ) AS run_rank
                        FROM collector_runs {predicate}
                    )
                    SELECT run_id, market_id, adapter_id, status, started_at,
                           finished_at, observations_written, attempts,
                           next_due, fault
                      FROM ranked WHERE run_rank=1
                      ORDER BY market_id, adapter_id""",
                params,
            ).fetchall()
        keys = (
            "run_id", "market_id", "adapter_id", "status", "started_at",
            "finished_at", "observations_written", "attempts", "next_due", "fault",
        )
        output = []
        for row in rows:
            record = dict(zip(keys, row, strict=True))
            for key in ("started_at", "finished_at", "next_due"):
                record[key] = record[key].isoformat()
            output.append(record)
        return output

    def append_forward_record(
        self,
        *,
        snapshot_id: str,
        market_id: str,
        product: str,
        event_cutoff: str | datetime,
        knowledge_cutoff: str | datetime,
        calibration_id: str,
        payload: object,
    ) -> str:
        """Append an idempotent link to the per-market/product paper trail.

        The transaction-scoped advisory lock prevents two workers from
        branching the same hash chain when source schedules finish together.
        """

        self._ensure_schema()
        market = market_id.upper()
        event = _utc(event_cutoff)
        knowledge = _utc(knowledge_cutoff)
        payload_json = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        chain_key = f"{market}|{product}"
        with self._connect() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (chain_key,))
            existing = connection.execute(
                "SELECT record_id FROM forward_validation_records WHERE snapshot_id=%s",
                (snapshot_id,),
            ).fetchone()
            if existing is not None:
                return existing[0]
            previous = connection.execute(
                """SELECT record_hash FROM forward_validation_records
                    WHERE market_id=%s AND product=%s
                    ORDER BY created_at DESC, record_id DESC LIMIT 1""",
                (market, product),
            ).fetchone()
            previous_hash = previous[0] if previous else "0" * 64
            identity = "|".join(
                (
                    snapshot_id,
                    market,
                    product,
                    event.isoformat(),
                    knowledge.isoformat(),
                    calibration_id,
                    payload_hash,
                    previous_hash,
                )
            )
            record_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            connection.execute(
                """INSERT INTO forward_validation_records
                     (record_id, snapshot_id, market_id, product, event_cutoff,
                      knowledge_cutoff, calibration_id, created_at, payload_hash,
                      previous_record_hash, record_hash, payload)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                (
                    record_hash,
                    snapshot_id,
                    market,
                    product,
                    event,
                    knowledge,
                    calibration_id,
                    datetime.now(UTC).replace(microsecond=0),
                    payload_hash,
                    previous_hash,
                    record_hash,
                    payload_json,
                ),
            )
        return record_hash

    def forward_record_count(self, market_id: str | None = None) -> int:
        self._ensure_schema()
        predicate = " WHERE market_id=%s" if market_id is not None else ""
        params = (market_id.upper(),) if market_id is not None else ()
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) FROM forward_validation_records{predicate}",
                params,
            ).fetchone()
        return int(row[0]) if row else 0


@lru_cache(maxsize=1)
def get_repository() -> MarketRepository:
    dsn = os.getenv("SEICHE_DATABASE_URL", "").strip()
    return PostgresMarketRepository(dsn) if dsn else SQLiteMarketRepository()


def reset_repository_cache() -> None:
    """Tests and process bootstrap may reset selection after changing env."""

    get_repository.cache_clear()
