"""Closed dispatcher for Railway stateful shadow and cutover requests."""

from __future__ import annotations

from pathlib import Path
import sys

from seiche import stateful_cutover
from seiche import stateful_migration


REQUEST_PATH = Path("/migration/request.json")


def run(request_path: Path = REQUEST_PATH) -> int:
    try:
        body = stateful_migration._stable_read(request_path, maximum_bytes=32 * 1024)
        request = stateful_migration._decode_canonical_json(
            body,
            label="stateful entrypoint request",
        )
    except stateful_migration.MigrationContractError as exc:
        raise stateful_cutover.CutoverContractError(str(exc)) from exc
    schema = request.get("schema")
    if schema == stateful_migration.REQUEST_SCHEMA:
        return stateful_migration.run_shadow()
    if schema == stateful_cutover.REQUEST_SCHEMA:
        return stateful_cutover.run()
    from seiche import stateful_application

    if schema == stateful_application.REQUEST_SCHEMA:
        from seiche import stateful_application_runtime

        return stateful_application_runtime.run()
    raise stateful_cutover.CutoverContractError(
        "stateful entrypoint request schema is unsupported"
    )


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except (
        stateful_cutover.CutoverContractError,
        stateful_migration.MigrationContractError,
    ) as error:
        print(f"seiche Railway stateful entrypoint: {error}", file=sys.stderr)
        raise SystemExit(1) from None
