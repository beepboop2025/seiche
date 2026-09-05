"""Repository boundary for canonical observations and sealed market products.

SQLite remains the zero-configuration compatibility backend. Setting
``SEICHE_DATABASE_URL`` selects PostgreSQL for v2 metadata and snapshots; the
legacy mnemonic cache remains in ``seiche.store`` during the v1 migration.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Protocol

from seiche import store
from seiche.domain.forward_record import (
    canonical_market_payload_json,
    forward_chain_generation,
    forward_record_hash,
    release_handoff_generated_at,
    validate_release_handoff_envelope,
    validate_snapshot_forward_binding,
)
from seiche.domain.observation import Observation, RedistributionStatus, SemanticRole

_MIN_POSTGRES_SERVER_VERSION = 110000
_RELEASE_HANDOFF_ACTIVATION_LOCK = 0x534549434845
COLLECTOR_WORKER_COMPONENT_ID = "official-market-collector"
LEGACY_SOURCE_WORKER_COMPONENT_ID = "legacy-source-worker"


class MarketRepository(Protocol):
    def save_observations(self, observations: Iterable[Observation]) -> int: ...

    def load_observations_as_of(
        self,
        market_id: str,
        knowledge_time: str | datetime,
        *,
        event_time: str | datetime | None = None,
        event_time_from: str | datetime | None = None,
        roles: Iterable[SemanticRole] | None = None,
        instrument_ids: Iterable[str] | None = None,
        sources: Iterable[str] | None = None,
    ) -> list[Observation]: ...

    def load_observation_revisions(
        self,
        market_id: str,
        knowledge_time: str | datetime,
        *,
        instrument_ids: Iterable[str] | None = None,
        event_time: str | datetime | None = None,
        event_time_from: str | datetime | None = None,
    ) -> list[Observation]: ...

    def load_observation_revisions_as_of(
        self,
        market_id: str,
        knowledge_time: str | datetime,
        *,
        event_time: str | datetime | None = None,
        roles: Iterable[SemanticRole] | None = None,
    ) -> list[Observation]: ...

    def load_observation_page(
        self,
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
    ) -> tuple[list[Observation], tuple[datetime, str] | None]: ...

    def latest_observation_hashes(
        self,
        market_id: str,
        knowledge_time: str | datetime,
        *,
        event_time: str | datetime | None = None,
        event_time_from: str | datetime | None = None,
        roles: Iterable[SemanticRole] | None = None,
        instrument_ids: Iterable[str] | None = None,
        sources: Iterable[str] | None = None,
    ) -> dict[tuple[str, datetime], str]: ...

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
        promoted: bool = True,
    ) -> str: ...

    def promote_market_snapshots(self, snapshot_ids: Iterable[str]) -> None: ...

    def load_staged_market_snapshot(self, snapshot_id: str) -> dict | None: ...

    def stage_release_handoff(
        self,
        handoff_id: str,
        producer_sha: str,
        envelope: dict,
    ) -> None: ...

    def load_release_handoff(self, handoff_id: str) -> dict | None: ...

    def load_active_release_handoff(self) -> dict | None: ...

    def activate_release_handoff(
        self,
        handoff_id: str,
        producer_sha: str,
        snapshot_bindings: Iterable[tuple[str, str, str, str]],
    ) -> None: ...

    def load_latest_market_snapshot(
        self, market_id: str, product: str
    ) -> dict | None: ...

    def load_market_snapshot_as_of(
        self,
        market_id: str,
        product: str,
        knowledge_time: str | datetime,
    ) -> dict | None: ...

    def save_collector_run(self, run: dict) -> str: ...

    def latest_collector_runs(self, market_id: str | None = None) -> list[dict]: ...

    def load_collector_states(self, market_id: str | None = None) -> list[dict]: ...

    def save_worker_heartbeat(
        self,
        *,
        component_id: str,
        heartbeat_at: str | datetime,
        expected_by: str | datetime,
    ) -> None: ...

    def load_worker_heartbeat(self, component_id: str) -> dict | None: ...

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

    def load_forward_records(
        self,
        market_id: str | None = None,
        product: str | None = None,
        calibration_id: str | None = None,
    ) -> list[dict]: ...

    def forward_record_count(self, market_id: str | None = None) -> int: ...


class SQLiteMarketRepository:
    """Delegate to the additive SQLite migration in ``seiche.store``."""

    save_observations = staticmethod(store.save_observations)
    load_observations_as_of = staticmethod(store.load_observations_as_of)
    load_observation_revisions = staticmethod(store.load_observation_revisions)
    load_observation_revisions_as_of = staticmethod(
        store.load_observation_revisions_as_of
    )
    load_observation_page = staticmethod(store.load_observation_page)
    latest_observation_hashes = staticmethod(store.latest_observation_hashes)
    canonical_coverage = staticmethod(store.canonical_coverage)
    seal_market_snapshot = staticmethod(store.seal_market_snapshot)
    promote_market_snapshots = staticmethod(store.promote_market_snapshots)
    load_staged_market_snapshot = staticmethod(store.load_staged_market_snapshot)
    stage_release_handoff = staticmethod(store.stage_release_handoff)
    load_release_handoff = staticmethod(store.load_release_handoff)
    load_active_release_handoff = staticmethod(store.load_active_release_handoff)
    activate_release_handoff = staticmethod(store.activate_release_handoff)
    load_latest_market_snapshot = staticmethod(store.load_latest_market_snapshot)
    load_market_snapshot_as_of = staticmethod(store.load_market_snapshot_as_of)
    save_collector_run = staticmethod(store.save_collector_run)
    latest_collector_runs = staticmethod(store.latest_collector_runs)
    load_collector_states = staticmethod(store.load_collector_states)
    save_worker_heartbeat = staticmethod(store.save_worker_heartbeat)
    load_worker_heartbeat = staticmethod(store.load_worker_heartbeat)
    append_forward_record = staticmethod(store.append_forward_record)
    load_forward_records = staticmethod(store.load_forward_records)
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


def _postgres_forward_record(row: tuple) -> dict:
    record = dict(zip(_FORWARD_RECORD_COLUMNS, row, strict=True))
    for key in ("event_cutoff", "knowledge_cutoff"):
        if isinstance(record[key], datetime):
            record[key] = record[key].astimezone(UTC).isoformat(timespec="seconds")
    if isinstance(record["created_at"], datetime):
        record["created_at"] = record["created_at"].astimezone(UTC).isoformat(
            timespec="microseconds"
        )
    if isinstance(record["payload"], str):
        record["payload"] = json.loads(record["payload"])
    return record


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
CREATE INDEX IF NOT EXISTS canonical_observations_series_page
  ON canonical_observations (
    market_id, event_time DESC, instrument_id DESC, knowledge_time DESC,
    source_publication_time DESC, revision_id DESC, source DESC
  );
CREATE INDEX IF NOT EXISTS canonical_observations_adapter_latest
  ON canonical_observations (
    market_id, source, instrument_id, event_time DESC, knowledge_time DESC,
    source_publication_time DESC, revision_id DESC
  );
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
CREATE TABLE IF NOT EXISTS market_snapshot_staging (
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
CREATE TABLE IF NOT EXISTS release_snapshot_handoffs (
  handoff_id TEXT PRIMARY KEY,
  producer_sha TEXT NOT NULL,
  envelope_hash TEXT NOT NULL,
  envelope TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS active_release_snapshot_handoff (
  singleton SMALLINT PRIMARY KEY CHECK (singleton = 1),
  handoff_id TEXT NOT NULL REFERENCES release_snapshot_handoffs (handoff_id)
);
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
CREATE TABLE IF NOT EXISTS collector_states (
  market_id TEXT NOT NULL,
  adapter_id TEXT NOT NULL,
  next_due TIMESTAMPTZ NOT NULL,
  consecutive_failures INTEGER NOT NULL,
  circuit_open_until TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL,
  CHECK (consecutive_failures >= 0),
  PRIMARY KEY (market_id, adapter_id)
);
CREATE TABLE IF NOT EXISTS worker_heartbeats (
  component_id TEXT PRIMARY KEY,
  heartbeat_at TIMESTAMPTZ NOT NULL,
  expected_by TIMESTAMPTZ NOT NULL,
  CHECK (expected_by >= heartbeat_at)
);
CREATE TABLE IF NOT EXISTS forward_validation_records (
  record_id TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL UNIQUE,
  market_id TEXT NOT NULL,
  product TEXT NOT NULL,
  event_cutoff TIMESTAMPTZ NOT NULL,
  knowledge_cutoff TIMESTAMPTZ NOT NULL,
  calibration_id TEXT NOT NULL,
  chain_generation SMALLINT NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL,
  payload_hash TEXT NOT NULL,
  previous_record_hash TEXT NOT NULL,
  record_hash TEXT NOT NULL UNIQUE,
  payload JSONB NOT NULL
);
ALTER TABLE forward_validation_records
  ADD COLUMN IF NOT EXISTS chain_generation SMALLINT NOT NULL DEFAULT 1;
CREATE INDEX IF NOT EXISTS forward_records_chain
  ON forward_validation_records (market_id, product, created_at, record_id);
CREATE INDEX IF NOT EXISTS forward_records_generation
  ON forward_validation_records (
    market_id, product, calibration_id, chain_generation
  );
CREATE UNIQUE INDEX IF NOT EXISTS forward_records_one_child
  ON forward_validation_records (
    market_id, product, calibration_id, previous_record_hash
  ) WHERE NOT (
    market_id = 'NZ-NZD'
    AND calibration_id = 'nz-nzd-local-forward-v1'
  );
"""


def _utc(value: str | datetime) -> datetime:
    parsed = (
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        if isinstance(value, str)
        else value
    )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC).replace(microsecond=0)


def _release_handoff_envelope(envelope: dict) -> tuple[str, str, dict]:
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
    normalized = json.loads(canonical)
    envelope_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return canonical, envelope_hash, normalized


def _observation_record_hash(observation: Observation) -> str:
    record = observation.to_record()
    canonical = json.dumps(
        record, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _observation_values(observation: Observation) -> tuple[Any, ...]:
    record_hash = _observation_record_hash(observation)
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
            raise RuntimeError("PostgreSQL selected; install seiche[postgres]") from exc
        return psycopg.connect(self.dsn)

    def _ensure_schema(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            with self._connect() as connection:
                server_version = connection.info.server_version
                if server_version < _MIN_POSTGRES_SERVER_VERSION:
                    raise RuntimeError(
                        "PostgreSQL 11 or newer is required for safe additive "
                        f"schema migration; server_version_num={server_version}"
                    )
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
        event_time_from: str | datetime | None = None,
        roles: Iterable[SemanticRole] | None = None,
        instrument_ids: Iterable[str] | None = None,
        sources: Iterable[str] | None = None,
    ) -> list[Observation]:
        self._ensure_schema()
        predicates = ["market_id=%s", "knowledge_time<=%s"]
        params: list[Any] = [market_id.upper(), _utc(knowledge_time)]
        if event_time is not None:
            predicates.append("event_time<=%s")
            params.append(_utc(event_time))
        if event_time_from is not None:
            predicates.append("event_time>=%s")
            params.append(_utc(event_time_from))
        role_values = tuple(role.value for role in roles) if roles is not None else ()
        if role_values:
            predicates.append(
                f"semantic_role IN ({','.join(['%s'] * len(role_values))})"
            )
            params.extend(role_values)
        instrument_values = tuple(dict.fromkeys(instrument_ids or ()))
        if instrument_ids is not None and not instrument_values:
            return []
        if instrument_values:
            predicates.append(
                f"instrument_id IN ({','.join(['%s'] * len(instrument_values))})"
            )
            params.extend(instrument_values)
        source_values = tuple(dict.fromkeys(sources or ()))
        if sources is not None and not source_values:
            return []
        if source_values:
            predicates.append(f"source IN ({','.join(['%s'] * len(source_values))})")
            params.extend(source_values)
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
               WHERE {" AND ".join(predicates)}
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

    def load_observation_revisions(
        self,
        market_id: str,
        knowledge_time: str | datetime,
        *,
        instrument_ids: Iterable[str] | None = None,
        event_time: str | datetime | None = None,
        event_time_from: str | datetime | None = None,
    ) -> list[Observation]:
        """Return every stored vintage knowable by the requested cutoff."""

        self._ensure_schema()
        predicates = ["market_id=%s", "knowledge_time<=%s"]
        params: list[Any] = [market_id.upper(), _utc(knowledge_time)]
        if event_time is not None:
            predicates.append("event_time<=%s")
            params.append(_utc(event_time))
        if event_time_from is not None:
            predicates.append("event_time>=%s")
            params.append(_utc(event_time_from))
        instrument_values = tuple(dict.fromkeys(instrument_ids or ()))
        if instrument_ids is not None and not instrument_values:
            return []
        if instrument_values:
            predicates.append(
                f"instrument_id IN ({','.join(['%s'] * len(instrument_values))})"
            )
            params.extend(instrument_values)

        selected = ",".join(_OBSERVATION_COLUMNS)
        query = f"""SELECT {selected}
                      FROM canonical_observations
                     WHERE {' AND '.join(predicates)}
                     ORDER BY event_time, instrument_id, knowledge_time,
                              source_publication_time, revision_id, source"""
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        observations: list[Observation] = []
        for row in rows:
            record = dict(zip(_OBSERVATION_COLUMNS, row, strict=True))
            if isinstance(record["jurisdiction_codes"], str):
                record["jurisdiction_codes"] = json.loads(
                    record["jurisdiction_codes"]
                )
            observations.append(Observation.from_record(record))
        return observations

    def load_observation_revisions_as_of(
        self,
        market_id: str,
        knowledge_time: str | datetime,
        *,
        event_time: str | datetime | None = None,
        roles: Iterable[SemanticRole] | None = None,
    ) -> list[Observation]:
        """Return every integrity-checked revision knowable by the cutoff."""

        self._ensure_schema()
        predicates = ["market_id=%s", "knowledge_time<=%s"]
        params: list[Any] = [market_id.upper(), _utc(knowledge_time)]
        if event_time is not None:
            predicates.append("event_time<=%s")
            params.append(_utc(event_time))
        role_values = tuple(role.value for role in roles) if roles is not None else ()
        if role_values:
            predicates.append(
                f"semantic_role IN ({','.join(['%s'] * len(role_values))})"
            )
            params.extend(role_values)
        selected = ",".join(_OBSERVATION_INSERT_COLUMNS)
        query = f"""
            SELECT {selected}
              FROM canonical_observations
             WHERE {" AND ".join(predicates)}
             ORDER BY event_time, instrument_id, knowledge_time,
                      source_publication_time, revision_id, source
        """
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        observations: list[Observation] = []
        for row in rows:
            record = dict(
                zip(_OBSERVATION_COLUMNS, row[: len(_OBSERVATION_COLUMNS)], strict=True)
            )
            if isinstance(record["jurisdiction_codes"], str):
                record["jurisdiction_codes"] = json.loads(
                    record["jurisdiction_codes"]
                )
            observation = Observation.from_record(record)
            stored_hash = row[len(_OBSERVATION_COLUMNS)]
            expected_hash = _observation_record_hash(observation)
            if not isinstance(stored_hash, str) or not hmac.compare_digest(
                stored_hash, expected_hash
            ):
                raise ValueError("canonical observation record_hash mismatch")
            observations.append(observation)
        return observations

    def load_observation_page(
        self,
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
        """Return one newest-first page, bounded by ``LIMIT`` in PostgreSQL."""

        if not 1 <= limit <= 5000:
            raise ValueError("limit must be between 1 and 5000")
        self._ensure_schema()
        knowledge_cutoff = _utc(knowledge_time)
        event_cutoff = _utc(event_time) if event_time is not None else None
        event_floor = _utc(event_time_from) if event_time_from is not None else None
        role_values = tuple(role.value for role in roles) if roles is not None else ()
        instrument_values = tuple(dict.fromkeys(instrument_ids or ()))
        if instrument_ids is not None and not instrument_values:
            return [], None
        source_values = tuple(dict.fromkeys(sources or ()))
        if sources is not None and not source_values:
            return [], None
        before_event: datetime | None = None
        before_instrument: str | None = None
        if before is not None:
            before_event = _utc(before[0])
            before_instrument = before[1].strip()
            if not before_instrument:
                raise ValueError("cursor instrument_id is required")

        def bounded_predicates(alias: str) -> tuple[list[str], list[Any]]:
            predicates = [
                f"{alias}.market_id=%s",
                f"{alias}.knowledge_time<=%s",
            ]
            params: list[Any] = [market_id.upper(), knowledge_cutoff]
            if event_cutoff is not None:
                predicates.append(f"{alias}.event_time<=%s")
                params.append(event_cutoff)
            if event_floor is not None:
                predicates.append(f"{alias}.event_time>=%s")
                params.append(event_floor)
            if role_values:
                predicates.append(
                    f"{alias}.semantic_role IN "
                    f"({','.join(['%s'] * len(role_values))})"
                )
                params.extend(role_values)
            if instrument_values:
                predicates.append(
                    f"{alias}.instrument_id IN "
                    f"({','.join(['%s'] * len(instrument_values))})"
                )
                params.extend(instrument_values)
            if source_values:
                predicates.append(
                    f"{alias}.source IN ({','.join(['%s'] * len(source_values))})"
                )
                params.extend(source_values)
            if before_event is not None and before_instrument is not None:
                predicates.append(
                    f"({alias}.event_time<%s OR "
                    f"({alias}.event_time=%s AND {alias}.instrument_id<%s))"
                )
                params.extend((before_event, before_event, before_instrument))
            return predicates, params

        key_predicates, key_params = bounded_predicates("candidate")
        latest_predicates, latest_params = bounded_predicates("observation")

        selected = ",".join(
            f"latest.{column}" for column in _OBSERVATION_COLUMNS
        )
        latest_selected = ",".join(
            f"observation.{column} AS {column}" for column in _OBSERVATION_COLUMNS
        )
        visible_predicates: list[str] = []
        redistribution_values = tuple(
            status.value for status in redistribution_statuses
        ) if redistribution_statuses is not None else ()
        if redistribution_statuses is not None:
            if not redistribution_values:
                return [], None
            visible_predicates.append(
                "latest.redistribution_status IN "
                f"({','.join(['%s'] * len(redistribution_values))})"
            )
        params: list[Any] = [
            *key_params,
            *latest_params,
            *redistribution_values,
            limit + 1,
        ]
        visible_where = (
            f"WHERE {' AND '.join(visible_predicates)}" if visible_predicates else ""
        )
        query = f"""
            WITH candidate_keys AS (
              SELECT candidate.event_time, candidate.instrument_id
                FROM canonical_observations AS candidate
               WHERE {' AND '.join(key_predicates)}
               GROUP BY candidate.event_time, candidate.instrument_id
               ORDER BY candidate.event_time DESC, candidate.instrument_id DESC
            )
            SELECT {selected}
              FROM candidate_keys AS candidate
              CROSS JOIN LATERAL (
                SELECT {latest_selected}
                  FROM canonical_observations AS observation
                 WHERE {' AND '.join(latest_predicates)}
                   AND observation.event_time=candidate.event_time
                   AND observation.instrument_id=candidate.instrument_id
                 ORDER BY observation.knowledge_time DESC,
                          observation.source_publication_time DESC,
                          observation.revision_id DESC,
                          observation.source DESC
                 LIMIT 1
              ) AS latest
              {visible_where}
             ORDER BY candidate.event_time DESC, candidate.instrument_id DESC
             LIMIT %s
        """
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        observations = []
        for row in rows:
            record = dict(
                zip(_OBSERVATION_COLUMNS, row[: len(_OBSERVATION_COLUMNS)], strict=True)
            )
            if isinstance(record["jurisdiction_codes"], str):
                record["jurisdiction_codes"] = json.loads(record["jurisdiction_codes"])
            observations.append(Observation.from_record(record))
        has_more = len(observations) > limit
        observations = observations[:limit]
        next_cursor = (
            (observations[-1].event_time, observations[-1].instrument_id)
            if has_more and observations
            else None
        )
        return observations, next_cursor

    def latest_observation_hashes(
        self,
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

        self._ensure_schema()
        predicates = ["market_id=%s", "knowledge_time<=%s"]
        params: list[Any] = [market_id.upper(), _utc(knowledge_time)]
        if event_time is not None:
            predicates.append("event_time<=%s")
            params.append(_utc(event_time))
        if event_time_from is not None:
            predicates.append("event_time>=%s")
            params.append(_utc(event_time_from))
        role_values = tuple(role.value for role in roles) if roles is not None else ()
        if role_values:
            predicates.append(f"semantic_role IN ({','.join(['%s'] * len(role_values))})")
            params.extend(role_values)
        instrument_values = tuple(dict.fromkeys(instrument_ids or ()))
        if instrument_ids is not None and not instrument_values:
            return {}
        if instrument_values:
            predicates.append(
                f"instrument_id IN ({','.join(['%s'] * len(instrument_values))})"
            )
            params.extend(instrument_values)
        source_values = tuple(dict.fromkeys(sources or ()))
        if sources is not None and not source_values:
            return {}
        if source_values:
            predicates.append(f"source IN ({','.join(['%s'] * len(source_values))})")
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
            SELECT instrument_id, event_time, evidence_hash FROM ranked
             WHERE vintage_rank=1
             ORDER BY event_time, instrument_id
        """
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return {
            (row[0], _utc(row[1])): row[2]
            for row in rows
        }

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
        promoted: bool = True,
    ) -> str:
        self._ensure_schema()
        market = market_id.upper()
        event = _utc(event_cutoff)
        knowledge = _utc(knowledge_cutoff)
        if event > knowledge:
            raise ValueError("event_cutoff cannot follow knowledge_cutoff")
        payload_json = canonical_market_payload_json(payload)
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        identity = "|".join(
            (
                market,
                product,
                event.isoformat(),
                knowledge.isoformat(),
                calibration_id,
                payload_hash,
            )
        )
        snapshot_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        table = "market_snapshots" if promoted else "market_snapshot_staging"
        with self._connect() as connection:
            connection.execute(
                f"""INSERT INTO {table}
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

    def promote_market_snapshots(self, snapshot_ids: Iterable[str]) -> None:
        """Atomically make an exact staged snapshot bundle visible to readers."""
        ids = tuple(dict.fromkeys(snapshot_ids))
        if not ids or any(not isinstance(item, str) or not item for item in ids):
            raise ValueError("snapshot_ids must contain non-empty strings")
        self._ensure_schema()
        placeholders = ",".join(["%s"] * len(ids))
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT snapshot_id FROM market_snapshot_staging
                     WHERE snapshot_id IN ({placeholders}) FOR UPDATE""",
                ids,
            ).fetchall()
            found = {row[0] for row in rows}
            if found != set(ids):
                raise ValueError("cannot promote a missing market snapshot")
            columns = (
                "snapshot_id,market_id,product,event_cutoff,knowledge_cutoff,"
                "sealed_at,calibration_id,evidence_eligible,payload_hash,payload"
            )
            connection.execute(
                f"""INSERT INTO market_snapshots ({columns})
                     SELECT {columns} FROM market_snapshot_staging
                      WHERE snapshot_id IN ({placeholders})
                     ON CONFLICT DO NOTHING""",
                ids,
            )

    def load_staged_market_snapshot(self, snapshot_id: str) -> dict | None:
        if not isinstance(snapshot_id, str) or not snapshot_id:
            raise ValueError("snapshot_id must be a non-empty string")
        self._ensure_schema()
        with self._connect() as connection:
            row = connection.execute(
                """SELECT snapshot_id, market_id, product, event_cutoff,
                          knowledge_cutoff, sealed_at, calibration_id,
                          evidence_eligible, payload_hash, payload
                     FROM market_snapshot_staging WHERE snapshot_id=%s""",
                (snapshot_id,),
            ).fetchone()
        return self._snapshot(row)

    def stage_release_handoff(
        self,
        handoff_id: str,
        producer_sha: str,
        envelope: dict,
    ) -> None:
        """Append an immutable release envelope, allowing exact replay only."""

        if not isinstance(handoff_id, str) or not handoff_id:
            raise ValueError("handoff_id must be a non-empty string")
        if not isinstance(producer_sha, str) or not producer_sha:
            raise ValueError("producer_sha must be a non-empty string")
        canonical, envelope_hash, normalized = _release_handoff_envelope(envelope)
        self._ensure_schema()
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO release_snapshot_handoffs
                     (handoff_id, producer_sha, envelope_hash, envelope)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT (handoff_id) DO NOTHING
                   RETURNING producer_sha, envelope_hash, envelope""",
                (handoff_id, producer_sha, envelope_hash, canonical),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    """SELECT producer_sha, envelope_hash, envelope
                         FROM release_snapshot_handoffs
                        WHERE handoff_id=%s
                        FOR UPDATE""",
                    (handoff_id,),
                ).fetchone()
            stored_envelope = json.loads(row[2]) if isinstance(row[2], str) else row[2]
            if (
                row[0] != producer_sha
                or row[1] != envelope_hash
                or stored_envelope != normalized
            ):
                raise ValueError(
                    "release handoff already exists with a different producer "
                    "or envelope"
                )

    def load_release_handoff(self, handoff_id: str) -> dict | None:
        if not isinstance(handoff_id, str) or not handoff_id:
            raise ValueError("handoff_id must be a non-empty string")
        self._ensure_schema()
        with self._connect() as connection:
            row = connection.execute(
                """SELECT envelope FROM release_snapshot_handoffs
                    WHERE handoff_id=%s""",
                (handoff_id,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row[0]) if isinstance(row[0], str) else row[0]

    def load_active_release_handoff(self) -> dict | None:
        self._ensure_schema()
        with self._connect() as connection:
            row = connection.execute(
                """SELECT handoff.envelope
                     FROM active_release_snapshot_handoff AS active
                     JOIN release_snapshot_handoffs AS handoff
                       ON handoff.handoff_id = active.handoff_id
                    WHERE active.singleton=1"""
            ).fetchone()
        if row is None:
            return None
        return json.loads(row[0]) if isinstance(row[0], str) else row[0]

    def load_active_release_handoff_read_only(self) -> dict | None:
        """Read existing candidate state without schema convergence or DML."""
        with self._connect() as connection, connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            row = connection.execute(
                """SELECT handoff.envelope
                     FROM active_release_snapshot_handoff AS active
                     JOIN release_snapshot_handoffs AS handoff
                       ON handoff.handoff_id = active.handoff_id
                    WHERE active.singleton=1"""
            ).fetchone()
        if row is None:
            return None
        return json.loads(row[0]) if isinstance(row[0], str) else row[0]

    def activate_release_handoff(
        self,
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
        self._ensure_schema()
        placeholders = ",".join(["%s"] * len(ids))
        columns = (
            "snapshot_id,market_id,product,event_cutoff,knowledge_cutoff,"
            "sealed_at,calibration_id,evidence_eligible,payload_hash,payload"
        )
        with self._connect() as connection:
            # Serialize even the first activation, when the singleton pointer
            # row does not exist yet and therefore cannot be row-locked.
            connection.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                (_RELEASE_HANDOFF_ACTIVATION_LOCK,),
            )
            handoff = connection.execute(
                """SELECT producer_sha, envelope_hash, envelope
                     FROM release_snapshot_handoffs
                    WHERE handoff_id=%s FOR UPDATE""",
                (handoff_id,),
            ).fetchone()
            if handoff is None:
                raise ValueError("release handoff does not exist")
            if handoff[0] != producer_sha:
                raise ValueError("release handoff producer mismatch")
            stored_envelope = (
                json.loads(handoff[2])
                if isinstance(handoff[2], str)
                else handoff[2]
            )
            _, envelope_hash, _ = _release_handoff_envelope(stored_envelope)
            authorized_bindings = validate_release_handoff_envelope(
                stored_envelope,
                expected_handoff_id=handoff_id,
                expected_producer_sha=producer_sha,
            )
            if (
                handoff[1] != envelope_hash
                or set(authorized_bindings) != set(bindings)
            ):
                raise ValueError(
                    "activation bindings differ from the locked handoff"
                )
            active = connection.execute(
                """SELECT active.handoff_id, handoff.producer_sha,
                          handoff.envelope_hash, handoff.envelope
                     FROM active_release_snapshot_handoff AS active
                     JOIN release_snapshot_handoffs AS handoff
                       ON handoff.handoff_id = active.handoff_id
                    WHERE active.singleton=1
                    FOR UPDATE OF active"""
            ).fetchone()
            if active is not None and active[0] != handoff_id:
                active_envelope = (
                    json.loads(active[3])
                    if isinstance(active[3], str)
                    else active[3]
                )
                _, active_envelope_hash, _ = _release_handoff_envelope(
                    active_envelope
                )
                validate_release_handoff_envelope(
                    active_envelope,
                    expected_handoff_id=active[0],
                    expected_producer_sha=active[1],
                )
                if active[2] != active_envelope_hash:
                    raise ValueError(
                        "active release handoff envelope hash is invalid"
                    )
                if active[1] == producer_sha and release_handoff_generated_at(
                    stored_envelope
                ) <= release_handoff_generated_at(active_envelope):
                    raise ValueError(
                        "cannot regress an active same-release handoff"
                    )
            staged_rows = connection.execute(
                f"""SELECT {columns} FROM market_snapshot_staging
                      WHERE snapshot_id IN ({placeholders})
                      ORDER BY snapshot_id FOR UPDATE""",
                ids,
            ).fetchall()
            staged = {
                row[0]: self._snapshot(tuple(row))
                for row in staged_rows
            }
            if set(staged) != set(ids):
                raise ValueError("cannot activate a missing market snapshot")
            forward_columns = ",".join(_FORWARD_RECORD_COLUMNS)
            forward_rows = connection.execute(
                f"""SELECT {forward_columns} FROM forward_validation_records
                      WHERE snapshot_id IN ({placeholders})
                      ORDER BY snapshot_id FOR UPDATE""",
                ids,
            ).fetchall()
            forward = {
                row[1]: _postgres_forward_record(tuple(row))
                for row in forward_rows
            }
            if set(forward) != set(ids):
                raise ValueError("cannot activate without an exact forward record")
            for snapshot_id in ids:
                product, expected_record_hash, expected_row_hash = expected[
                    snapshot_id
                ]
                if staged[snapshot_id]["product"] != product:
                    raise ValueError(
                        "release receipt product does not match staging"
                    )
                validate_snapshot_forward_binding(
                    staged[snapshot_id],
                    forward[snapshot_id],
                    expected_record_hash,
                    expected_row_hash,
                )
            connection.execute(
                f"""INSERT INTO market_snapshots ({columns})
                     SELECT {columns} FROM market_snapshot_staging
                      WHERE snapshot_id IN ({placeholders})
                      ORDER BY snapshot_id
                     ON CONFLICT DO NOTHING""",
                ids,
            )
            canonical_rows = connection.execute(
                f"""SELECT {columns} FROM market_snapshots
                      WHERE snapshot_id IN ({placeholders}) FOR UPDATE""",
                ids,
            ).fetchall()
            canonical = {
                row[0]: self._snapshot(tuple(row))
                for row in canonical_rows
            }
            if canonical != staged:
                raise RuntimeError(
                    "release handoff canonical rows differ from validated staging"
                )
            connection.execute(
                """INSERT INTO active_release_snapshot_handoff
                     (singleton, handoff_id) VALUES (1, %s)
                   ON CONFLICT (singleton) DO UPDATE
                     SET handoff_id=EXCLUDED.handoff_id""",
                (handoff_id,),
            )

    @staticmethod
    def _snapshot(row: tuple | None) -> dict | None:
        if row is None:
            return None
        payload = json.loads(row[9]) if isinstance(row[9], str) else row[9]
        return {
            "snapshot_id": row[0],
            "market_id": row[1],
            "product": row[2],
            "event_cutoff": row[3].astimezone(UTC).isoformat(timespec="seconds"),
            "knowledge_cutoff": row[4]
            .astimezone(UTC)
            .isoformat(timespec="seconds"),
            "sealed_at": row[5].astimezone(UTC).isoformat(timespec="microseconds"),
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
        run_id = store.collector_run_id(run)
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
            connection.execute(
                """INSERT INTO collector_states
                     (market_id, adapter_id, next_due, consecutive_failures,
                      circuit_open_until, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (market_id, adapter_id) DO UPDATE SET
                     next_due=EXCLUDED.next_due,
                     consecutive_failures=EXCLUDED.consecutive_failures,
                     circuit_open_until=EXCLUDED.circuit_open_until,
                     updated_at=EXCLUDED.updated_at
                   WHERE EXCLUDED.updated_at >= collector_states.updated_at""",
                (
                    str(run["market_id"]).upper(),
                    run["adapter_id"],
                    _utc(run["next_due"]),
                    int(run.get("consecutive_failures", 0)),
                    (
                        _utc(run["circuit_open_until"])
                        if run.get("circuit_open_until") is not None
                        else None
                    ),
                    _utc(run["finished_at"]),
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
        output = []
        for row in rows:
            record = dict(zip(keys, row, strict=True))
            for key in (
                "started_at",
                "finished_at",
                "next_due",
                "circuit_open_until",
            ):
                if record[key] is not None:
                    record[key] = record[key].isoformat()
            output.append(record)
        return output

    def load_collector_states(self, market_id: str | None = None) -> list[dict]:
        self._ensure_schema()
        predicate = "WHERE market_id=%s" if market_id is not None else ""
        params = (market_id.upper(),) if market_id is not None else ()
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT market_id, adapter_id, next_due,
                           consecutive_failures, circuit_open_until, updated_at
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
        output = []
        for row in rows:
            record = dict(zip(keys, row, strict=True))
            for key in ("next_due", "circuit_open_until", "updated_at"):
                if record[key] is not None:
                    record[key] = record[key].isoformat()
            output.append(record)
        return output

    def save_worker_heartbeat(
        self,
        *,
        component_id: str,
        heartbeat_at: str | datetime,
        expected_by: str | datetime,
    ) -> None:
        self._ensure_schema()
        heartbeat = _utc(heartbeat_at)
        deadline = _utc(expected_by)
        if deadline < heartbeat:
            raise ValueError("worker heartbeat deadline cannot precede its timestamp")
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO worker_heartbeats
                     (component_id, heartbeat_at, expected_by)
                   VALUES (%s,%s,%s)
                   ON CONFLICT (component_id) DO UPDATE SET
                     heartbeat_at=EXCLUDED.heartbeat_at,
                     expected_by=EXCLUDED.expected_by
                   WHERE EXCLUDED.heartbeat_at >= worker_heartbeats.heartbeat_at""",
                (component_id, heartbeat, deadline),
            )

    def load_worker_heartbeat(self, component_id: str) -> dict | None:
        self._ensure_schema()
        with self._connect() as connection:
            row = connection.execute(
                """SELECT component_id, heartbeat_at, expected_by
                     FROM worker_heartbeats WHERE component_id=%s""",
                (component_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "component_id": row[0],
            "heartbeat_at": row[1].isoformat(),
            "expected_by": row[2].isoformat(),
        }

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
        payload_json = canonical_market_payload_json(payload)
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        generation = forward_chain_generation(calibration_id)
        chain_key = f"{market}|{product}|{calibration_id}"
        with self._connect() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (chain_key,)
            )
            columns = ",".join(_FORWARD_RECORD_COLUMNS)
            existing_row = connection.execute(
                f"""SELECT {columns}
                     FROM forward_validation_records WHERE snapshot_id=%s""",
                (snapshot_id,),
            ).fetchone()
            existing = (
                _postgres_forward_record(tuple(existing_row))
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
            rows = connection.execute(
                f"""SELECT {columns} FROM forward_validation_records
                     WHERE market_id=%s AND product=%s AND calibration_id=%s""",
                (market, product, calibration_id),
            ).fetchall()
            records = [_postgres_forward_record(tuple(row)) for row in rows]
            previous_hash = "0" * 64
            previous_created_at: datetime | None = None
            if records:
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
                    _utc(existing["event_cutoff"]) != event
                    or _utc(existing["knowledge_cutoff"]) != knowledge
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
                event_cutoff=event.isoformat(),
                knowledge_cutoff=knowledge.isoformat(),
                calibration_id=calibration_id,
                payload_hash=payload_hash,
                previous_record_hash=previous_hash,
            )
            connection.execute(
                """INSERT INTO forward_validation_records
                     (record_id, snapshot_id, market_id, product, event_cutoff,
                      knowledge_cutoff, calibration_id, chain_generation,
                      created_at, payload_hash, previous_record_hash, record_hash,
                      payload)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                (
                    record_hash,
                    snapshot_id,
                    market,
                    product,
                    event,
                    knowledge,
                    calibration_id,
                    generation,
                    created_at,
                    payload_hash,
                    previous_hash,
                    record_hash,
                    payload_json,
                ),
            )
        return record_hash

    def load_forward_records(
        self,
        market_id: str | None = None,
        product: str | None = None,
        calibration_id: str | None = None,
    ) -> list[dict]:
        """Read immutable links; ordering is presentation-only, never topology."""

        self._ensure_schema()
        predicates: list[str] = []
        params: list[str] = []
        if market_id is not None:
            predicates.append("market_id=%s")
            params.append(market_id.upper())
        if product is not None:
            predicates.append("product=%s")
            params.append(product)
        if calibration_id is not None:
            predicates.append("calibration_id=%s")
            params.append(calibration_id)
        where = f" WHERE {' AND '.join(predicates)}" if predicates else ""
        columns = ",".join(_FORWARD_RECORD_COLUMNS)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT {columns}
                      FROM forward_validation_records{where}
                     ORDER BY created_at, record_id""",
                params,
            ).fetchall()
        return [_postgres_forward_record(tuple(row)) for row in rows]

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
