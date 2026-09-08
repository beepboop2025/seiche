set -euo pipefail
cd "$EVIDENCE_ROOT"
SNAPSHOT_ID="$SNAPSHOT_ID" RELEASE_SHA="$RELEASE_SHA" \
  PYTHONPATH="$GITHUB_WORKSPACE/backend" python -B - <<'PY'
import json
import os
import shutil
from pathlib import Path

from seiche import stateful_migration as migration
from seiche import stateful_recovery as recovery

bundle = recovery._bundle_identity(
    Path("bundle"),
    snapshot_id=os.environ["SNAPSHOT_ID"],
    commit=os.environ["RELEASE_SHA"],
)
proof_root = Path("proof/filesystem-restore").resolve()
proof_root.mkdir(mode=0o700)
nbs_result, trees = migration.restore_filesystem_generation(
    bundle,
    proof_root,
    runtime_uid=os.geteuid(),
    runtime_gid=os.getegid(),
)
receipt = json.loads(Path("recovery-receipt.json").read_bytes())
if nbs_result != receipt["filesystem"]["nbs_full_store_audit_result"]:
    raise SystemExit("reverse-restore NBS audit differs from export")
if dict(trees) != receipt["filesystem"]["tree_sha256"]:
    raise SystemExit("reverse-restore filesystem trees differ from export")
state = migration.palimpsest_china_state_from_audit(
    bundle.palimpsest_china_state_audit
)
if state != receipt["palimpsest_china_state"]:
    raise SystemExit("reverse-restore Palimpsest China state differs")
shutil.rmtree(proof_root)
PY
docker run --rm --network host --env PGPASSWORD \
  postgres:18.6-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af \
  psql --host 127.0.0.1 --username postgres --dbname postgres \
    --set ON_ERROR_STOP=1 --command \
    "CREATE DATABASE seiche_phase6_restore TEMPLATE template0 ENCODING 'UTF8';"
docker run --rm --network host \
  --env PGPASSWORD \
  --volume "$EVIDENCE_ROOT/bundle:/recovery:ro" \
  postgres:18.6-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af \
  pg_restore --exit-on-error --no-owner --no-privileges \
    --host 127.0.0.1 --username postgres \
    --dbname=seiche_phase6_restore \
    /recovery/seiche.dump
RESTORE_TARGET_DSN='postgresql://postgres@127.0.0.1:5432/seiche_phase6_restore' \
  PYTHONPATH="$GITHUB_WORKSPACE/backend" python -B - <<'PY'
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from seiche import stateful_migration as migration

receipt = json.loads(Path("recovery-receipt.json").read_bytes())
counts = migration.inspect_postgres_counts(os.environ["RESTORE_TARGET_DSN"])
floor = tuple(receipt["snapshot"]["critical_table_count_floor"])
if any(actual < minimum for actual, minimum in zip(counts, floor)):
    raise SystemExit("reverse-restore PostgreSQL counts are below export floors")
proof = {
    "schema": "seiche.railway-reverse-restore-proof.v1",
    "repository": os.environ["GITHUB_REPOSITORY"],
    "workflow": (
        "beepboop2025/seiche/"
        ".github/workflows/railway-stateful-recovery.yml"
    ),
    "commit": receipt["commit"],
    "request_id": receipt["request_id"],
    "recovery_receipt_sha256": hashlib.sha256(
        Path("recovery-receipt.json").read_bytes()
    ).hexdigest(),
    "filesystem_tree_sha256": receipt["filesystem"]["tree_sha256"],
    "palimpsest_china_state": receipt["palimpsest_china_state"],
    "nbs_full_store_audit_result": receipt["filesystem"][
        "nbs_full_store_audit_result"
    ],
    "postgres_counts": list(counts),
    "postgres_count_floor": list(floor),
    "restored_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    ),
    "authority_changed": False,
    "research_only": True,
    "can_publish": False,
    "can_execute": False,
}
Path("proof/railway-reverse-restore.json").write_text(
    json.dumps(proof, sort_keys=True, separators=(",", ":")) + "\n"
)
PY
