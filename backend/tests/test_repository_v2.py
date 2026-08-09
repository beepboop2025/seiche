from __future__ import annotations

from seiche.repository import (
    PostgresMarketRepository,
    SQLiteMarketRepository,
    _OBSERVATION_INSERT_COLUMNS,
    _OBSERVATION_INSERT_PLACEHOLDERS,
    _POSTGRES_SCHEMA,
    get_repository,
    reset_repository_cache,
)


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
