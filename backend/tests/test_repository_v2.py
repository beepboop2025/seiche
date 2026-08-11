from __future__ import annotations

import pytest

from seiche.repository import (
    PostgresMarketRepository,
    SQLiteMarketRepository,
    _MIN_POSTGRES_SERVER_VERSION,
    _OBSERVATION_INSERT_COLUMNS,
    _OBSERVATION_INSERT_PLACEHOLDERS,
    _POSTGRES_SCHEMA,
    get_repository,
    reset_repository_cache,
)


class _FakeConnection:
    def __init__(self, server_version: int) -> None:
        self.info = type("Info", (), {"server_version": server_version})()
        self.statements: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback

    def execute(self, statement: str) -> None:
        self.statements.append(statement)


def test_repository_defaults_to_sqlite(monkeypatch) -> None:
    monkeypatch.delenv("SEICHE_DATABASE_URL", raising=False)
    reset_repository_cache()
    assert isinstance(get_repository(), SQLiteMarketRepository)


def test_database_url_selects_postgres_without_connecting_at_import(monkeypatch) -> None:
    monkeypatch.setenv("SEICHE_DATABASE_URL", "postgresql://example.invalid/seiche")
    reset_repository_cache()
    assert isinstance(get_repository(), PostgresMarketRepository)
    monkeypatch.delenv("SEICHE_DATABASE_URL", raising=False)
    reset_repository_cache()


def test_postgres_schema_preserves_bitemporal_and_snapshot_indexes() -> None:
    assert "event_time TIMESTAMPTZ NOT NULL" in _POSTGRES_SCHEMA
    assert "knowledge_time TIMESTAMPTZ NOT NULL" in _POSTGRES_SCHEMA
    assert "source_publication_time TIMESTAMPTZ NOT NULL" in _POSTGRES_SCHEMA
    assert "market_snapshots_latest" in _POSTGRES_SCHEMA
    assert "CREATE TABLE IF NOT EXISTS market_snapshot_staging" in _POSTGRES_SCHEMA
    assert "CREATE TABLE IF NOT EXISTS release_snapshot_handoffs" in _POSTGRES_SCHEMA
    handoff_schema = _POSTGRES_SCHEMA.split(
        "CREATE TABLE IF NOT EXISTS release_snapshot_handoffs", 1
    )[1].split(");", 1)[0]
    assert "envelope TEXT NOT NULL" in handoff_schema
    assert "envelope JSONB" not in handoff_schema
    assert (
        "CREATE TABLE IF NOT EXISTS active_release_snapshot_handoff" in _POSTGRES_SCHEMA
    )


def test_postgres_schema_rejects_servers_older_than_version_11(monkeypatch) -> None:
    repository = PostgresMarketRepository("postgresql://example.invalid/seiche")
    connection = _FakeConnection(_MIN_POSTGRES_SERVER_VERSION - 1)
    monkeypatch.setattr(repository, "_connect", lambda: connection)

    with pytest.raises(RuntimeError, match="PostgreSQL 11 or newer"):
        repository._ensure_schema()

    assert connection.statements == []
    assert repository._initialized is False


def test_postgres_observation_insert_casts_only_json_fields() -> None:
    placeholders = dict(
        zip(
            _OBSERVATION_INSERT_COLUMNS,
            _OBSERVATION_INSERT_PLACEHOLDERS,
            strict=True,
        )
    )
    assert placeholders["jurisdiction_codes"] == "%s::jsonb"
    assert all(
        placeholder == "%s"
        for column, placeholder in placeholders.items()
        if column != "jurisdiction_codes"
    )
