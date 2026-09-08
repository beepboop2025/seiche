#!/usr/local/bin/python
"""Translate only the two reviewed PostgreSQL restore commands to local PG18."""

import os
from pathlib import Path
import sys


IMAGE = "postgres:18.6-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af"


def command(arguments, evidence):
    root = Path(evidence)
    if not root.is_absolute() or root.is_symlink() or root.parent.name != "recovery-verification":
        raise ValueError("restore root is outside the isolated verifier")
    prefix = ["run", "--rm", "--network", "host", "--env", "PGPASSWORD"]
    create = ["psql", "--host", "127.0.0.1", "--username", "postgres", "--dbname", "postgres",
              "--set", "ON_ERROR_STOP=1", "--command",
              "CREATE DATABASE seiche_phase6_restore TEMPLATE template0 ENCODING 'UTF8';"]
    restore = ["pg_restore", "--exit-on-error", "--no-owner", "--no-privileges", "--host",
               "127.0.0.1", "--username", "postgres", "--dbname=seiche_phase6_restore", "/recovery/seiche.dump"]
    if arguments == [*prefix, IMAGE, *create]:
        return ["/usr/lib/postgresql/18/bin/psql", *create[1:]]
    if arguments == [*prefix, "--volume", str(root / "bundle") + ":/recovery:ro", IMAGE, *restore]:
        return ["/usr/lib/postgresql/18/bin/pg_restore", *restore[1:-1], str(root / "bundle/seiche.dump")]
    raise ValueError("command is outside the two reviewed native restore operations")


if __name__ == "__main__":
    selected = command(sys.argv[1:], os.environ["EVIDENCE_ROOT"])
    os.execv(selected[0], selected)
