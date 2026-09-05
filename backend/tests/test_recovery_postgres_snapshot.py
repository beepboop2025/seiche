"""Exercise recovery MVCC binding with real concurrent PostgreSQL sessions."""

import os
from uuid import uuid4

import pytest

from seiche import stateful_recovery as recovery
from seiche import stateful_migration as migration

pytestmark = pytest.mark.skipif(
    not os.getenv("SEICHE_TEST_POSTGRES_URL"),
    reason="SEICHE_TEST_POSTGRES_URL is not configured",
)


@pytest.mark.parametrize("dump_fails", [False, True])
def test_dump_and_counts_share_snapshot_despite_concurrent_commits(
    tmp_path, monkeypatch, dump_fails
):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    base = os.environ["SEICHE_TEST_POSTGRES_URL"]
    schema = "recovery_mvcc_" + uuid4().hex
    tables = (
        "canonical_observations",
        "collector_runs",
        "forward_validation_records",
        "market_snapshots",
    )
    dsn = make_conninfo(base, options=f"-c search_path={schema}")
    snapshots = []
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            with psycopg.connect(dsn) as writer:
                for table in tables:
                    writer.execute(
                        sql.SQL("CREATE TABLE {} (id integer PRIMARY KEY)").format(
                            sql.Identifier(table)
                        )
                    )
                    writer.execute(
                        sql.SQL("INSERT INTO {} VALUES (1)").format(
                            sql.Identifier(table)
                        )
                    )

            def dump(_destination, observed_dsn, *, snapshot_id):
                assert observed_dsn == dsn
                snapshots.append(snapshot_id)
                # This commit occurs after count capture and snapshot export.
                with psycopg.connect(dsn) as writer:
                    for table in tables:
                        writer.execute(
                            sql.SQL("INSERT INTO {} VALUES (2)").format(
                                sql.Identifier(table)
                            )
                        )
                assert migration.inspect_postgres_counts(dsn) == (2, 2, 2, 2)
                with psycopg.connect(dsn) as reader:
                    reader.execute(
                        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                    )
                    reader.execute(
                        sql.SQL("SET TRANSACTION SNAPSHOT {}").format(
                            sql.Literal(snapshot_id)
                        )
                    )
                    assert (
                        reader.execute(migration._COUNTS_SQL).fetchone()[0] == "1|1|1|1"
                    )
                if dump_fails:
                    raise recovery.RecoveryContractError("injected dump failure")

            monkeypatch.setattr(recovery, "_dump_postgres", dump)
            if dump_fails:
                with pytest.raises(recovery.RecoveryContractError, match="injected"):
                    recovery._snapshot_postgres(tmp_path / "seiche.dump", dsn)
            else:
                assert recovery._snapshot_postgres(tmp_path / "seiche.dump", dsn) == (
                    1,
                    1,
                    1,
                    1,
                )
            assert len(snapshots) == 1
            # Success and failure must both close the exporting transaction.
            with pytest.raises(psycopg.Error, match="invalid snapshot"):
                with psycopg.connect(dsn) as reader:
                    reader.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    reader.execute(
                        sql.SQL("SET TRANSACTION SNAPSHOT {}").format(
                            sql.Literal(snapshots[0])
                        )
                    )
        finally:
            admin.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )
