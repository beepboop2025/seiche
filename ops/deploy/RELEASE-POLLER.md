# Direct Hetzner release controller

`seiche-release-poll.timer` replaces the hosted `deploy-hetzner` runner for
SSH-signed `main` commits. Before trusting any gate, the controller requires the
exact tip's author email and SSH signature to match the host-pinned release
identity. Its default path then fetches the public OCI gate artifact, verifies
GitHub's OIDC attestation against the exact repository, workflow, ref, commit,
tree, source-archive bytes, and artifact digest, and stores a root-owned local
receipt. Railway supplies CPU only; GitHub packages and attests the evidence;
Hetzner remains the only production authority. The existing root deploy wrapper
remains the sole owner of service quiescence, snapshot activation, Caddy
deployment, health gates, and rollback. See `RAILWAY-GATE.md` for bootstrap and
the complete Phase 1/Phase 2 contract.

## Safety boundary

- The box's `beepboop2025/seiche` deploy key stays **read-only**.
- `/etc/seiche-release.allowed-signers` is a root-owned, mode `0444`, single-key
  trust anchor. The public key must be readable by the unprivileged Git process,
  but only root can change it. The installer creates it atomically and refuses
  to replace, relink, broaden, or silently rotate an existing pin.
- Every release tip must be authored as
  `beepboop2025@users.noreply.github.com` and carry a valid SSH signature from
  that pinned key. Verification occurs before worktree creation, dependency
  installation, tests, receipts, or the deploy wrapper. Branch protection is
  still recommended for review policy, but an unsigned push to an unprotected
  `main` is inert on the production host.
- Scheduled desk commits are a deliberately narrower non-release class. The
  poller exits successfully without candidate execution only when the author is
  exactly `desk@seiche.info`, the subject starts with `dispatch: ` or
  `week ahead: `, the commit has one parent, and every changed path is under
  `frontend/public/dispatches/`, `frontend/public/articles/`, or
  `backend/seiche/dispatches/`. They trigger the static publisher only. Any
  mixed path or identity falls back to the strict release-signature failure.
- Never put a source write credential on this box. A read-only deploy key keeps
  a host compromise from becoming a push-to-root loop even though only signed
  commits are eligible for release.
- The normal path executes no candidate tests on Hetzner. The local detached
  worktree and isolated `dev,collectors` venv exist only when an operator runs
  the controller with `SEICHE_CONTROL_LOCAL_GATE_BREAK_GLASS=1`. A 404 for an
  exact-SHA artifact defers inside a bounded one-hour publication window;
  unsafe, unavailable, malformed, red, late, or unattested evidence fails. No
  remote outcome ever selects the local path automatically.
- `/run/seiche-control/release.lock` coalesces polls. The existing independent
  `/run/seiche-deploy/deploy.lock` still serializes checkout/service mutation.
- Immutable `*.gate.json`, `*.snapshot.json`, and `*.release.json` receipts live
  under `/var/lib/seiche-control/receipts`. The normal v3 release receipt hashes
  both attested inputs. A wrapper failure never writes a release receipt; its
  established rollback path remains authoritative. Root-owned gate and snapshot
  pending markers retain the first missing-artifact observation so a broken
  producer becomes an alert after the bounded publication SLO instead of
  silently deferring forever.
- The installer shares the poller's lock. It atomically replaces the poller,
  deploy wrapper, both remote verifiers, service, and timer, and restores all six
  files plus the previous timer state if verification, `daemon-reload`,
  activation, or the installer itself fails.
- If `main` advances during remote verification or a local break-glass gate,
  the tested candidate is discarded.
  The wrapper also checks `SEICHE_EXPECTED_TARGET_SHA` before stopping a unit,
  closing the smaller race between the gate and wrapper hand-off.
- Before stopping any service, the wrapper requires three one- and five-minute
  load samples, ten seconds apart, at or below 75 percent of online CPU count.
  The longer average prevents a brief dip from admitting immediately after a
  sustained sibling workload. A poller first invokes the same check in
  admission-only mode before an explicit local gate, and the wrapper repeats it
  before quiescence. Remote verification can proceed while the host is busy,
  then waits up to 15 minutes for deployment admission without rerunning tests.
  A local break-glass run may use the same bounded wait for its test load to
  cool. The poller re-fetches `origin/main` after verification and again before
  handoff so a superseded candidate remains inert. A still-busy host records no
  release receipt and defers without paging; admission probe errors remain
  failures.

## Install without activating

Before installing the controller, prepare `/var/lib/seiche-nbs` as the third
authenticated bind mount described in `HETZNER-VOLUME.md`, with consumers
stopped and all four cutover locks held. Do not root-run or install a preflight
helper, unit, config template, wrapper, or controller from the mutable checkout.
The first v2 cutover order is fixed: provision the release signer independently,
fetch the target, materialize its signed privileged assets, install the v2
preflight set from that retained root, prove all three mounts, then install the
controller disabled and run its gate.

On a fresh host, `/etc/seiche-release.allowed-signers` is an out-of-band trust
anchor, not a release artifact. Transfer the owner public key from the owner
workstation or an independently controlled key record into the root-only
`OWNER_PUBKEY` path below. Confirm the principal and fingerprint through that
independent channel. Never read or copy the key or expected fingerprint from the
checkout being authenticated. The no-clobber sequence below either validates an
existing exact pin or publishes one same-directory staged inode; it never
replaces a pin.

The NBS root must already be an exact Volume-backed mount with `root:seiche`
ownership and mode `0750`. Neither controller installer creates, mounts, chmods,
or chowns it. A missing or unsafe root means the storage cutover is incomplete.
Replace the target placeholder once; it must equal the fetched canonical
`origin/main` tip:

```bash
set -euo pipefail
PATH=/usr/bin:/bin
export PATH
APP=/home/seiche/app
TARGET=0000000000000000000000000000000000000000
PRINCIPAL=beepboop2025@users.noreply.github.com
SIGNERS=/etc/seiche-release.allowed-signers
OWNER_PUBKEY=/root/seiche-owner-release-key.pub
EXPECTED_FINGERPRINT=SHA256:yhoa/PIDMM6M/ZennILp8jtRJy5pArncJRARbQssTMI
VOLUME_MOUNT=/mnt/HC_Volume_106588294
VOLUME_SOURCE=/dev/disk/by-id/scsi-0HC_Volume_106588294

cleanup_bootstrap_stages() {
  [ -z "${SIGNER_STAGE:-}" ] || rm -f -- "$SIGNER_STAGE"
  [ -z "${STAGE:-}" ] || rm -f -- "$STAGE"
  [ -z "${PREFLIGHT_STAGE:-}" ] || rm -f -- "$PREFLIGHT_STAGE"
  [ -z "${CONFIG_STAGE:-}" ] || rm -f -- "$CONFIG_STAGE"
  if [ -n "${UNIT_STAGE_DIR:-}" ]; then
    rm -f -- "$UNIT_STAGE_DIR/seiche-storage-preflight.service"
    rmdir -- "$UNIT_STAGE_DIR" 2>/dev/null || true
  fi
}
trap cleanup_bootstrap_stages EXIT

[[ "$TARGET" =~ ^[0-9a-f]{40}$ ]]
[ "$(stat -c '%U:%G:%a:%h' "$OWNER_PUBKEY")" = root:root:400:1 ]
FINGERPRINT=$(/usr/bin/ssh-keygen -E sha256 -lf "$OWNER_PUBKEY")
case "$FINGERPRINT" in
  "256 $EXPECTED_FINGERPRINT "*"(ED25519)") ;;
  *) exit 1 ;;
esac
IFS=' ' read -r KEY_TYPE KEY_MATERIAL KEY_COMMENT <"$OWNER_PUBKEY"
[ "$KEY_TYPE" = ssh-ed25519 ]
[ -n "$KEY_MATERIAL" ]
EXPECTED_SIGNER_LINE="$PRINCIPAL $KEY_TYPE $KEY_MATERIAL"

SIGNER_STAGE=$(mktemp /etc/.seiche-release.allowed-signers.XXXXXX)
printf '%s\n' "$EXPECTED_SIGNER_LINE" >"$SIGNER_STAGE"
chown root:root "$SIGNER_STAGE"
chmod 0444 "$SIGNER_STAGE"
/usr/bin/sync -f "$SIGNER_STAGE"
if [ -e "$SIGNERS" ] || [ -L "$SIGNERS" ]; then
  [ -f "$SIGNERS" ] && [ ! -L "$SIGNERS" ]
  [ "$(stat -c '%U:%G:%a:%h' "$SIGNERS")" = root:root:444:1 ]
  /usr/bin/cmp -s "$SIGNER_STAGE" "$SIGNERS"
  rm -f -- "$SIGNER_STAGE"
else
  ln "$SIGNER_STAGE" "$SIGNERS"
  rm -f -- "$SIGNER_STAGE"
  /usr/bin/sync /etc
fi
SIGNER_STAGE=
[ "$(stat -c '%U:%G:%a:%h' "$SIGNERS")" = root:root:444:1 ]

/usr/sbin/runuser -u seiche -- /usr/bin/env -i \
  HOME=/home/seiche LANG=C LC_ALL=C PATH=/usr/bin:/bin \
  GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
  GIT_NO_LAZY_FETCH=1 GIT_NO_REPLACE_OBJECTS=1 GIT_OPTIONAL_LOCKS=0 \
  /usr/bin/git -C "$APP" fetch --no-tags origin main

MAIN=$(/usr/bin/env -i \
  HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin \
  GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
  GIT_NO_LAZY_FETCH=1 GIT_NO_REPLACE_OBJECTS=1 GIT_OPTIONAL_LOCKS=0 \
  /usr/bin/git -c "safe.directory=$APP" -C "$APP" \
    rev-parse --verify 'refs/remotes/origin/main^{commit}')
[ "$TARGET" = "$MAIN" ]

/usr/bin/env -i \
  HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin \
  GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
  GIT_NO_LAZY_FETCH=1 GIT_NO_REPLACE_OBJECTS=1 GIT_OPTIONAL_LOCKS=0 \
  /usr/bin/git -c "safe.directory=$APP" -C "$APP" \
    fsck --strict --no-reflogs --no-dangling "$TARGET"

AUTHOR=$(/usr/bin/env -i \
  HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin \
  GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
  GIT_NO_LAZY_FETCH=1 GIT_NO_REPLACE_OBJECTS=1 GIT_OPTIONAL_LOCKS=0 \
  /usr/bin/git -c "safe.directory=$APP" -C "$APP" \
    show -s --format=%ae "$TARGET")
[ "$AUTHOR" = "$PRINCIPAL" ]
/usr/bin/env -i \
  HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin \
  GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
  GIT_NO_LAZY_FETCH=1 GIT_NO_REPLACE_OBJECTS=1 GIT_OPTIONAL_LOCKS=0 \
  /usr/bin/git -c "safe.directory=$APP" -C "$APP" \
    -c gpg.format=ssh -c "gpg.ssh.allowedSignersFile=$SIGNERS" \
    -c gpg.ssh.program=/usr/bin/ssh-keygen verify-commit "$TARGET"

TREE_LINE=$(/usr/bin/env -i \
  HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin \
  GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
  GIT_NO_LAZY_FETCH=1 GIT_NO_REPLACE_OBJECTS=1 GIT_OPTIONAL_LOCKS=0 \
  /usr/bin/git -c "safe.directory=$APP" -C "$APP" \
    ls-tree "$TARGET" -- ops/deploy/seiche-deploy-wrapper.sh)
IFS=$' \t' read -r MODE TYPE OID TREE_PATH <<<"$TREE_LINE"
[ "$MODE" = 100644 ]
[ "$TYPE" = blob ]
[ "$TREE_PATH" = ops/deploy/seiche-deploy-wrapper.sh ]

install -d -o root -g root -m 0700 /run/seiche-deploy
BOOTSTRAP=/run/seiche-deploy/bootstrap-wrapper-$TARGET
STAGE=$(mktemp /run/seiche-deploy/.bootstrap-wrapper.XXXXXX)
/usr/bin/env -i \
  HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin \
  GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
  GIT_NO_LAZY_FETCH=1 GIT_NO_REPLACE_OBJECTS=1 GIT_OPTIONAL_LOCKS=0 \
  /usr/bin/git -c "safe.directory=$APP" -C "$APP" cat-file blob "$OID" \
  >"$STAGE"
CALCULATED=$(/usr/bin/env -i \
  HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin \
  GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
  GIT_NO_LAZY_FETCH=1 GIT_NO_REPLACE_OBJECTS=1 GIT_OPTIONAL_LOCKS=0 \
  /usr/bin/git hash-object --stdin <"$STAGE")
[ "$CALCULATED" = "$OID" ]
chown root:root "$STAGE"
chmod 0500 "$STAGE"
/usr/bin/sync -f "$STAGE"
[ ! -e "$BOOTSTRAP" ]
ln "$STAGE" "$BOOTSTRAP"
rm -f -- "$STAGE"
/usr/bin/sync /run/seiche-deploy
[ "$(stat -c '%U:%G:%a:%h' "$BOOTSTRAP")" = root:root:500:1 ]
/usr/bin/bash -n "$BOOTSTRAP"

BOOTSTRAP_OUTPUT=$(/usr/bin/env -i \
  HOME=/root LANG=C.UTF-8 PATH=/usr/bin:/bin \
  SEICHE_DEPLOY_BOOTSTRAP_ASSETS_ONLY=1 \
  SEICHE_EXPECTED_TARGET_SHA="$TARGET" \
  /usr/bin/bash -p "$BOOTSTRAP")
ASSET_ROOT=${BOOTSTRAP_OUTPUT##* }
case "$ASSET_ROOT" in
  /run/seiche-deploy/release-assets-"$TARGET"-[0-9]*) ;;
  *) exit 1 ;;
esac
[ "$(stat -c '%U:%G:%a' "$ASSET_ROOT")" = root:root:700 ]

# The materializer requires these three exact signed sources. Capture the old
# compatible v1 set before replacing any member; never print the config.
for source in \
  ops/deploy/seiche-storage-preflight.py \
  ops/deploy/seiche-storage-preflight.service \
  ops/deploy/storage-volume.env.example; do
  [ "$(stat -c '%U:%G:%a:%h' "$ASSET_ROOT/$source")" = root:root:644:1 ]
done
ROLLBACK_ROOT=/root/seiche-storage-v1-before-$TARGET
[ ! -e "$ROLLBACK_ROOT" ]
install -d -o root -g root -m 0700 "$ROLLBACK_ROOT"
for source in \
  /etc/seiche/libexec/seiche-storage-preflight.py \
  /etc/systemd/system/seiche-storage-preflight.service \
  /etc/seiche/storage-volume.env; do
  label=${source#/}
  label=${label//\//__}
  if [ -e "$source" ] || [ -L "$source" ]; then
    [ -f "$source" ] && [ ! -L "$source" ]
    cp --archive --no-dereference -- "$source" "$ROLLBACK_ROOT/$label"
    printf 'present %s\n' "$source" >>"$ROLLBACK_ROOT/manifest"
  else
    printf 'absent %s\n' "$source" >>"$ROLLBACK_ROOT/manifest"
  fi
done
chmod 0400 "$ROLLBACK_ROOT/manifest"
/usr/bin/sync "$ROLLBACK_ROOT"

VOLUME_UUID=$(/usr/sbin/blkid -s UUID -o value "$VOLUME_SOURCE")
[[ "$VOLUME_UUID" =~ ^[0-9a-fA-F-]{36}$ ]]
install -d -o root -g root -m 0755 /etc/seiche/libexec
PREFLIGHT_STAGE=$(mktemp /etc/seiche/libexec/.seiche-storage-preflight.XXXXXX)
install -o root -g root -m 0755 \
  "$ASSET_ROOT/ops/deploy/seiche-storage-preflight.py" "$PREFLIGHT_STAGE"
/usr/bin/python3 -I -B "$PREFLIGHT_STAGE" --help >/dev/null
/usr/bin/sync -f "$PREFLIGHT_STAGE"

UNIT_STAGE_DIR=$(mktemp -d /etc/systemd/system/.seiche-storage-preflight.XXXXXX)
install -o root -g root -m 0644 \
  "$ASSET_ROOT/ops/deploy/seiche-storage-preflight.service" \
  "$UNIT_STAGE_DIR/seiche-storage-preflight.service"
systemd-analyze verify "$UNIT_STAGE_DIR/seiche-storage-preflight.service"
/usr/bin/sync -f "$UNIT_STAGE_DIR/seiche-storage-preflight.service"

CONFIG_STAGE=$(mktemp /etc/seiche/.storage-volume.env.XXXXXX)
/usr/bin/python3 -I -B - \
  "$ASSET_ROOT/ops/deploy/storage-volume.env.example" "$CONFIG_STAGE" \
  "$VOLUME_MOUNT" "$VOLUME_SOURCE" "$VOLUME_UUID" <<'PY'
import os
from pathlib import Path
import sys

template_path, stage_path, mount_path, source_path, uuid = sys.argv[1:]
replacements = {
    "SEICHE_STORAGE_MOUNT_PATH": mount_path,
    "SEICHE_STORAGE_EXPECTED_SOURCE": source_path,
    "SEICHE_STORAGE_EXPECTED_UUID": uuid,
}
seen = set()
output = []
for line in Path(template_path).read_text(encoding="ascii").splitlines():
    key = line.partition("=")[0]
    if key in replacements:
        if key in seen:
            raise SystemExit(f"duplicate signed storage key: {key}")
        seen.add(key)
        line = f"{key}={replacements[key]}"
    output.append(line)
if seen != replacements.keys():
    raise SystemExit("signed storage template is incomplete")
body = ("\n".join(output) + "\n").encode("ascii")
descriptor = os.open(
    stage_path,
    os.O_WRONLY | os.O_TRUNC | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
)
try:
    os.write(descriptor, body)
    os.fchmod(descriptor, 0o640)
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
chown root:root "$CONFIG_STAGE"

systemctl stop seiche-storage-preflight.service 2>/dev/null || true
mv -f -- "$PREFLIGHT_STAGE" /etc/seiche/libexec/seiche-storage-preflight.py
PREFLIGHT_STAGE=
mv -f -- "$UNIT_STAGE_DIR/seiche-storage-preflight.service" \
  /etc/systemd/system/seiche-storage-preflight.service
rmdir -- "$UNIT_STAGE_DIR"
UNIT_STAGE_DIR=
mv -f -- "$CONFIG_STAGE" /etc/seiche/storage-volume.env
CONFIG_STAGE=
/usr/bin/sync /etc/seiche/libexec /etc/systemd/system /etc/seiche
systemctl daemon-reload
systemctl start seiche-storage-preflight.service
/usr/bin/python3 -I -B /etc/seiche/libexec/seiche-storage-preflight.py \
  --config /etc/seiche/storage-volume.env \
  --state-path /var/lib/seiche \
  --nbs-path /var/lib/seiche-nbs \
  --backup-path /var/backups/seiche-market

/usr/bin/env -i \
  HOME=/root LANG=C.UTF-8 PATH=/usr/bin:/bin \
  SEICHE_PRIVILEGED_ASSET_ROOT="$ASSET_ROOT" \
  SEICHE_RELEASE_TARGET_SHA="$TARGET" \
  SEICHE_NBS_RUNTIME_ROOT=/opt/seiche-nbs-intake \
  /usr/bin/bash -p "$ASSET_ROOT/ops/deploy/install-release-poller.sh"

/usr/bin/env -i \
  HOME=/root LANG=C.UTF-8 PATH=/usr/bin:/bin \
  SEICHE_CONTROL_GATE_ONLY=1 \
  /usr/local/sbin/seiche-release-poll
```

The bootstrap performs no service mutation. It accepts only root, refuses SSH,
requires the target to equal the already-fetched `origin/main`, rechecks the
full object graph and pinned signature, verifies its own exact blob, and retains
one root-owned asset tree. Any failure before the retained-path line exposes no
asset root. Keep `ASSET_ROOT` through the disabled gate and activation; do not
re-materialize privileged bytes from the checkout.

If helper, unit, config, reload, or preflight validation fails before the first
v2 candidate is accepted, keep all services and timers stopped, restore all
three members as one compatible set from `ROLLBACK_ROOT` according to its
presence manifest, reload systemd, and follow the storage rollback in
`HETZNER-VOLUME.md`. Preserve the failed signed asset root and v2 stages for
inspection. Once a v2 candidate accepts or new NBS evidence is ingested, do not
restore v1 storage or the old real NBS directory: application rollback must
retain the v2 mount, sealed runtime, and all newly committed evidence.

The signed-tree installer creates `/opt/seiche-nbs-intake` only after the v2
three-mount and isolated Ed25519 proofs, installs the updated rollback-aware
wrapper and release-poll unit, and leaves the timer disabled and inactive. On a
failed controller transaction it restores all five controller files and timer
state, and removes the anchor only when this run created it and it remains the
same empty directory. The gate-only service start reruns the v2 preflight before
candidate admission. `SEICHE_CONTROL_GATE_ONLY=1` deliberately bypasses the
already-deployed fast path, verifies the tip signature and attested Railway
artifact, records only its gate receipt, and exits before invoking the deploy
wrapper. Confirm that the receipt says `gate_provider=railway` and that the
deployed SHA stayed unchanged before handoff.

The release which first introduces shared-host admission still enters through
the previously installed wrapper. Bootstrap it only during a manually verified
quiet window. After that new wrapper is installed, the poller's preflight and
the wrapper's second check enforce the quiet-host boundary automatically.

### Migrate the forced SSH fallback

The dedicated deploy key must target the canonical controller wrapper, never a
historical copy under `/root`. Perform this migration only after the signed
controller has installed `/var/lib/seiche-deploy/bin/seiche-deploy-wrapper.sh`
and the exact target is healthy. The command retains only the requested SHA,
starts privileged Bash with an empty environment, and passes an independent
forced-entry marker so an empty SSH request cannot become local maintenance.

First prove the effective sshd boundary. `restrict` on the migrated key disables
user RC execution. The host must pin root's login shell to the root-owned
`/bin/bash`, have no active `Match` blocks (so source address cannot change the
contract), disable user environment files, accept exactly the two reviewed
locale patterns, and set no server-side environment values. The audit also pins
the active `ssh.service` to Ubuntu's reviewed unit and empty `SSHD_OPTS`, checks
the configuration syntax, reloads that service without replacing its main PID,
and only then evaluates the policy which the listener has accepted:

```bash
set -euo pipefail
CANONICAL_WRAPPER=/var/lib/seiche-deploy/bin/seiche-deploy-wrapper.sh
AUTHORIZED_KEYS=/root/.ssh/authorized_keys
AUTHORIZED_BACKUP=/root/.ssh/authorized_keys.seiche-forced-v1-before-$TARGET
SSHD_SERVICE=ssh.service
SSHD_UNIT=/usr/lib/systemd/system/ssh.service
SSHD_DEFAULTS=/etc/default/ssh
SSHD_EFFECTIVE=$(mktemp /run/seiche-deploy/.sshd-effective.XXXXXX)
trap 'rm -f -- "${SSHD_EFFECTIVE:-}"' EXIT

[ ! -L /var/lib/seiche-deploy/bin ]
[ ! -L "$CANONICAL_WRAPPER" ]
[ "$(stat -c '%U:%G:%a:%F' /var/lib/seiche-deploy/bin)" = root:root:700:directory ]
[ "$(stat -c '%U:%G:%a:%h:%F' "$CANONICAL_WRAPPER")" = \
  'root:root:700:1:regular file' ]
[ "$(head -n 1 "$CANONICAL_WRAPPER")" = '#!/bin/bash -p' ]
/usr/bin/bash -n "$CANONICAL_WRAPPER"

/usr/bin/python3 -I -B - \
  /etc/ssh/sshd_config /etc/ssh/sshd_config.d 0 0 \
  '/etc/ssh/sshd_config.d/*.conf' <<'PY'
from pathlib import Path
import stat
import sys

root_config = Path(sys.argv[1])
fragment_dir = Path(sys.argv[2])
expected_uid = int(sys.argv[3])
expected_gid = int(sys.argv[4])
expected_include = sys.argv[5]
files = [root_config, *sorted(fragment_dir.glob("*.conf"))]
for path in files:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != expected_uid
        or info.st_gid != expected_gid
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        raise SystemExit(f"unsafe sshd config source: {path}")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.partition("#")[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        key = parts[0]
        if key.casefold() == "match":
            raise SystemExit("active sshd Match blocks are forbidden")
        if key.casefold() == "include" and (
            len(parts) != 2
            or path != root_config
            or parts[1].strip() != expected_include
        ):
            raise SystemExit("unreviewed sshd Include path")
PY

ROOT_PASSWD=$(getent passwd root)
[ "${ROOT_PASSWD%%:*}" = root ]
root_shell=${ROOT_PASSWD##*:}
[ "$root_shell" = /bin/bash ]
[ ! -L /bin/bash ]
[ "$(stat -c '%U:%G:%a:%h:%F' /bin/bash)" = \
  'root:root:755:1:regular file' ]

[ ! -L "$SSHD_UNIT" ]
[ ! -L "$SSHD_DEFAULTS" ]
[ "$(stat -c '%U:%G:%a:%h:%F' "$SSHD_UNIT")" = \
  'root:root:644:1:regular file' ]
[ "$(stat -c '%U:%G:%a:%h:%F' "$SSHD_DEFAULTS")" = \
  'root:root:644:1:regular file' ]
[ "$(/usr/bin/systemctl show "$SSHD_SERVICE" --property=LoadState --value)" = loaded ]
[ "$(/usr/bin/systemctl show "$SSHD_SERVICE" --property=FragmentPath --value)" = \
  "$SSHD_UNIT" ]
[ -z "$(/usr/bin/systemctl show "$SSHD_SERVICE" --property=DropInPaths --value)" ]
[ "$(/usr/bin/systemctl show "$SSHD_SERVICE" --property=EnvironmentFiles --value)" = \
  "$SSHD_DEFAULTS (ignore_errors=yes)" ]
[ -z "$(/usr/bin/systemctl show "$SSHD_SERVICE" --property=Environment --value)" ]
[ -z "$(/usr/bin/systemctl show "$SSHD_SERVICE" --property=PassEnvironment --value)" ]
[ -z "$(/usr/bin/systemctl show "$SSHD_SERVICE" --property=UnsetEnvironment --value)" ]

SSHD_ACTIVE_DEFAULTS=$(
  /usr/bin/sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$SSHD_DEFAULTS"
)
[ "$SSHD_ACTIVE_DEFAULTS" = SSHD_OPTS= ]
SSHD_EXEC_START=$(
  /usr/bin/systemctl show "$SSHD_SERVICE" --property=ExecStart --value
)
SSHD_EXEC_RELOAD=$(
  /usr/bin/systemctl show "$SSHD_SERVICE" --property=ExecReload --value
)
/usr/bin/python3 -I -B - "$SSHD_EXEC_START" "$SSHD_EXEC_RELOAD" <<'PY'
import re
import sys

def commands(serialized: str) -> list[tuple[str, str]]:
    return [
        (path, argv.strip())
        for path, argv in re.findall(
            r"\{ path=([^ ;]+) ; argv\[\]=([^;]+) ;", serialized
        )
    ]

if commands(sys.argv[1]) != [
    ("/usr/sbin/sshd", "/usr/sbin/sshd -D $SSHD_OPTS")
]:
    raise SystemExit("ssh.service ExecStart is not the reviewed default")
if commands(sys.argv[2]) != [
    ("/usr/sbin/sshd", "/usr/sbin/sshd -t"),
    ("/bin/kill", "/bin/kill -HUP $MAINPID"),
]:
    raise SystemExit("ssh.service ExecReload is not the reviewed reload path")
PY

/usr/bin/systemctl is-active --quiet "$SSHD_SERVICE"
[ "$(/usr/bin/systemctl show "$SSHD_SERVICE" --property=SubState --value)" = running ]
SSHD_MAIN_PID_BEFORE=$(
  /usr/bin/systemctl show "$SSHD_SERVICE" --property=MainPID --value
)
case "$SSHD_MAIN_PID_BEFORE" in
  ''|0|*[!0-9]*) echo 'ssh.service has no valid active main PID' >&2; exit 1 ;;
esac
[ "$(/usr/bin/readlink -f "/proc/$SSHD_MAIN_PID_BEFORE/exe")" = /usr/sbin/sshd ]
/usr/bin/python3 -I -B - "/proc/$SSHD_MAIN_PID_BEFORE/environ" <<'PY'
from pathlib import Path
import sys

payload = Path(sys.argv[1]).read_bytes()
if len(payload) > 1024 * 1024:
    raise SystemExit("ssh.service main-process environment is unexpectedly large")
values = [
    entry.partition(b"=")[2]
    for entry in payload.split(b"\0")
    if entry.partition(b"=")[0] == b"SSHD_OPTS"
]
if len(values) > 1:
    raise SystemExit("ssh.service main process has duplicate SSHD_OPTS entries")
if values and values != [b""]:
    raise SystemExit("ssh.service main process was launched with custom SSHD_OPTS")
PY

/usr/sbin/sshd -t
/usr/bin/systemctl reload "$SSHD_SERVICE"
/usr/bin/systemctl is-active --quiet "$SSHD_SERVICE"
[ "$(/usr/bin/systemctl show "$SSHD_SERVICE" --property=SubState --value)" = running ]
SSHD_MAIN_PID_AFTER=$(
  /usr/bin/systemctl show "$SSHD_SERVICE" --property=MainPID --value
)
[ "$SSHD_MAIN_PID_AFTER" = "$SSHD_MAIN_PID_BEFORE" ]
[ "$(/usr/bin/readlink -f "/proc/$SSHD_MAIN_PID_AFTER/exe")" = /usr/sbin/sshd ]

/usr/sbin/sshd -T \
  -C user=root,host=seiche-deploy,addr=192.0.2.1 >"$SSHD_EFFECTIVE"
grep -Fxq 'permituserenvironment no' "$SSHD_EFFECTIVE"
/usr/bin/python3 -I -B - "$SSHD_EFFECTIVE" <<'PY'
from pathlib import Path
import sys

acceptenv = []
setenv = []
for line in Path(sys.argv[1]).read_text(encoding="ascii").splitlines():
    key, _, value = line.partition(" ")
    if key == "acceptenv":
        acceptenv.extend(value.split())
    if key == "setenv":
        setenv.extend(value.split())
if acceptenv != ["LANG", "LC_*"]:
    raise SystemExit(f"sshd AcceptEnv is not the exact allowlist: {acceptenv}")
if setenv:
    raise SystemExit("sshd SetEnv must remain empty")
PY
```

Then atomically transform only the one exact legacy forced-key line. The
transformer refuses ambiguous files, unsafe metadata, key changes, symlinks,
and noncanonical bytes. It creates a no-clobber rollback copy before the
same-directory fsynced replacement and never prints key material:

```bash
/usr/bin/python3 -I -B - "$AUTHORIZED_KEYS" "$AUTHORIZED_BACKUP" <<'PY'
import base64
import os
from pathlib import Path
import secrets
import stat
import sys

path = Path(sys.argv[1])
backup = Path(sys.argv[2])
old = (
    b'command="/root/seiche-deploy-wrapper.sh",no-port-forwarding,'
    b'no-agent-forwarding,no-X11-forwarding,no-pty '
)
new = (
    b'restrict,command="/usr/bin/env -i HOME=/root LANG=C LC_ALL=C '
    b'PATH=/usr/bin:/bin SSH_ORIGINAL_COMMAND=\\"$SSH_ORIGINAL_COMMAND\\" '
    b'/usr/bin/bash -p /var/lib/seiche-deploy/bin/'
    b'seiche-deploy-wrapper.sh --seiche-forced-entry-v1" '
)
maximum = 128 * 1024
parent_fd = os.open(
    path.parent,
    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
)
stage = ""

def read_regular(name: str, *, mode: int) -> bytes:
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_fd,
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_gid != 0
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != mode
            or info.st_size > maximum
        ):
            raise SystemExit(f"unsafe authorized-key file: {name}")
        chunks = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        body = b"".join(chunks)
        if len(body) > maximum:
            raise SystemExit(f"oversized authorized-key file: {name}")
        return body
    finally:
        os.close(descriptor)

def write_all(descriptor: int, body: bytes) -> None:
    offset = 0
    while offset < len(body):
        written = os.write(descriptor, body[offset:])
        if written < 1:
            raise SystemExit("authorized-key write made no progress")
        offset += written

def stage_body(body: bytes) -> str:
    name = f".authorized_keys.seiche-{secrets.token_hex(16)}"
    descriptor = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=parent_fd,
    )
    try:
        write_all(descriptor, body)
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return name

try:
    parent = os.fstat(parent_fd)
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != 0
        or parent.st_gid != 0
        or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        raise SystemExit("unsafe root SSH directory")
    body = read_regular(path.name, mode=0o600)
    if b"\r" in body or b"\0" in body or not body.endswith(b"\n"):
        raise SystemExit("authorized_keys is not canonical text")
    lines = body.splitlines(keepends=True)
    old_lines = [line for line in lines if line.startswith(old)]
    new_lines = [line for line in lines if line.startswith(new)]
    if len(new_lines) == 1 and not old_lines:
        read_regular(backup.name, mode=0o600)
        raise SystemExit(0)
    if len(old_lines) != 1 or new_lines:
        raise SystemExit("legacy forced-key line is absent or ambiguous")
    tail = old_lines[0][len(old):].removesuffix(b"\n")
    fields = tail.split(b" ", 2)
    if len(fields) < 2 or fields[0] != b"ssh-ed25519":
        raise SystemExit("forced deploy key is not the reviewed key type")
    try:
        base64.b64decode(fields[1], validate=True)
    except ValueError as exc:
        raise SystemExit("forced deploy key is malformed") from exc

    replacement = new + tail + b"\n"
    candidate = b"".join(
        replacement if line == old_lines[0] else line for line in lines
    )
    try:
        prior = read_regular(backup.name, mode=0o600)
    except FileNotFoundError:
        backup_stage = stage_body(body)
        try:
            os.link(
                backup_stage,
                backup.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
        finally:
            os.unlink(backup_stage, dir_fd=parent_fd)
        os.fsync(parent_fd)
    else:
        if prior != body:
            raise SystemExit("authorized_keys rollback copy conflicts")

    stage = stage_body(candidate)
    os.rename(stage, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    stage = ""
    os.fsync(parent_fd)
finally:
    if stage:
        try:
            os.unlink(stage, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
    os.close(parent_fd)
PY

[ "$(stat -c '%U:%G:%a:%h:%F' "$AUTHORIZED_KEYS")" = \
  'root:root:600:1:regular file' ]
/usr/bin/ssh-keygen -l -f "$AUTHORIZED_KEYS" >/dev/null
! grep -Fq 'command="/root/seiche-deploy-wrapper.sh"' "$AUTHORIZED_KEYS"
grep -Fq -- '--seiche-forced-entry-v1" ssh-ed25519 ' "$AUTHORIZED_KEYS"
rm -f -- "$SSHD_EFFECTIVE"
SSHD_EFFECTIVE=
trap - EXIT
```

From the owner workstation, use the dedicated deploy key and pinned host key to
run `ops/deploy/trigger-forced-deploy.sh` for the already-deployed exact
`TARGET`. Both passes must return zero and production must remain on that SHA.
An empty requested command must fail; it must never enter local maintenance.
Only after that proof, retire the unreachable legacy wrapper recoverably:

```bash
RETIRED=/root/seiche-deploy-wrapper.retired-$TARGET
[ ! -e "$RETIRED" ]
mv -- /root/seiche-deploy-wrapper.sh "$RETIRED"
chmod 0400 "$RETIRED"
/usr/bin/sync /root
```

If the forced smoke fails, restore `AUTHORIZED_BACKUP` through a root-owned
mode-0600 same-directory stage and move `RETIRED` back before retrying. Never
edit or paste the deploy public key during rollback.

Use this handoff order; do not skip directly to timer activation:

1. Sign the exact intended `main` tip with the pinned SSH key and push it.
2. Confirm the host still uses a read-only source deploy key.
3. Install the controller disabled, inspect the signer-pin metadata, and run a
   gate-only cycle for that exact signed SHA.
4. Confirm the immutable gate receipt and that production's deployed SHA did
   not move.
5. Enable the host timer, run one release cycle, and confirm the deployed SHA,
   strict release health, release receipt, and timer state.
6. Atomically migrate the dedicated forced key, run the two-pass same-SHA
   fallback smoke, reject an empty request, and retire the legacy `/root` copy.

After completing steps 1–5, activate polling:

```bash
/usr/bin/env -i \
  HOME=/root LANG=C.UTF-8 PATH=/usr/bin:/bin \
  SEICHE_PRIVILEGED_ASSET_ROOT="$ASSET_ROOT" \
  SEICHE_RELEASE_TARGET_SHA="$TARGET" \
  SEICHE_NBS_RUNTIME_ROOT=/opt/seiche-nbs-intake \
  SEICHE_ENABLE_RELEASE_POLLER=1 \
  /usr/bin/bash -p "$ASSET_ROOT/ops/deploy/install-release-poller.sh"
systemctl status seiche-release-poll.timer --no-pager
```

After exact-SHA health, release receipt, timer, and rollback-bundle inspection
are green, remove only the two resolved bootstrap paths shown above. Preserve
the signed asset root on any failure so root can inspect it; never substitute a
checkout path for a retry.

Do not enable both controllers. Two triggers cannot corrupt the checkout—the
deploy wrapper has its own lock—but duplicate release attempts obscure which
control plane owns an incident.

The disabled/manual `deploy-hetzner` fallback retries only wrapper exit `75`
for an independent, bounded ten-minute window on each of its two passes. Every
retry remains pinned to the same reviewed SHA and repeats the host's admission
check. Other SSH or wrapper failures stop immediately, and a host that stays
busy through either bound leaves production unchanged and the workflow red.
The workflow's outer timeout still covers both windows and all remote release
work. External route checks run only after both passes complete successfully.
