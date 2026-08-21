# Direct Hetzner release controller

`seiche-release-poll.timer` replaces the hosted `deploy-hetzner` runner for
SSH-signed `main` commits. It does not make the production checkout its test
workspace. Before executing any candidate code, the controller requires the
exact tip's author email and SSH signature to match the host-pinned release
identity. It then creates a detached candidate, installs an isolated virtual
environment, runs the same full suite as `publish.yml`, re-checks that
`origin/main` did not move, waits for the test-induced host load to cool,
re-checks `origin/main`, and hands the exact tested SHA to the existing root
deploy wrapper. That wrapper remains the sole owner of service quiescence,
snapshot activation, Caddy deployment, health gates, and rollback.

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
- Never put a source write credential on this box. A read-only deploy key keeps
  a host compromise from becoming a push-to-root loop even though only signed
  commits are eligible for release.
- Candidate code runs as `seiche`, from
  `/var/lib/seiche-control/candidates/main`, without any production
  `EnvironmentFile`. Its isolated gate installs the same `dev,collectors`
  dependency surface and runs the same full backend suite as static publish.
  The shared Unix identity is needed for the box's read-only Git key, so this is
  protection against accidental live-tree writes, not a hostile-code sandbox;
  only protected, trusted `main` may feed it.
- `/run/seiche-control/release.lock` coalesces polls. The existing independent
  `/run/seiche-deploy/deploy.lock` still serializes checkout/service mutation.
- Immutable `*.gate.json` and `*.release.json` receipts live under
  `/var/lib/seiche-control/receipts`. A wrapper failure never writes a release
  receipt; its established rollback path remains authoritative.
- The installer shares the poller's lock. It atomically replaces the binary and
  two units, and restores all three files plus the previous timer state if
  verification, `daemon-reload`, activation, or the installer itself fails.
- If `main` advances during the full gate, the tested candidate is discarded.
  The wrapper also checks `SEICHE_EXPECTED_TARGET_SHA` before stopping a unit,
  closing the smaller race between the gate and wrapper hand-off.
- Before stopping any service, the wrapper requires three one- and five-minute
  load samples, ten seconds apart, at or below 75 percent of online CPU count.
  The longer average prevents a brief dip from admitting immediately after a
  sustained sibling workload. A poller first invokes the same check in
  admission-only mode, before candidate installation or tests, and the wrapper
  repeats it before quiescence. After a successful full gate, the poller waits
  up to 15 minutes for the gate's own load window to cool, then re-fetches
  `origin/main` so a candidate superseded during that wait remains inert. A
  still-busy host records no release receipt and defers without paging; the
  timer retries the same signed tip on a later five-minute cycle. Admission
  probe errors remain failures. Do not raise the load ceiling or lengthen
  snapshot health deadlines to compensate for unrelated workload pressure.

## Install without activating

From the canonical production checkout, as root:

```bash
bash /home/seiche/app/ops/deploy/install-release-poller.sh
SEICHE_CONTROL_GATE_ONLY=1 /usr/local/sbin/seiche-release-poll
```

The first command requires the installed root wrapper to contain the
expected-target-SHA pin. It also creates or confirms the no-clobber signer pin;
the checked-out commit must therefore already be reviewed and signed by that
identity. It installs the script and units but leaves the timer disabled and
inactive. `SEICHE_CONTROL_GATE_ONLY=1` deliberately bypasses the
already-deployed fast path, verifies the tip signature, runs the isolated full
gate, records only its gate receipt, and exits before invoking the deploy
wrapper. Confirm that receipt and the unchanged deployed SHA before handoff.

The release which first introduces shared-host admission still enters through
the previously installed wrapper. Bootstrap it only during a manually verified
quiet window. After that new wrapper is installed, the poller's preflight and
the wrapper's second check enforce the quiet-host boundary automatically.

Use this handoff order; do not skip directly to timer activation:

1. Sign the exact intended `main` tip with the pinned SSH key and push it.
2. Confirm the host still uses a read-only source deploy key.
3. Install the controller disabled, inspect the signer-pin metadata, and run a
   gate-only cycle for that exact signed SHA.
4. Confirm the immutable gate receipt and that production's deployed SHA did
   not move.
5. Disable the GitHub Actions `deploy-hetzner` trigger.
6. Enable the host timer, run one release cycle, and confirm the deployed SHA,
   strict release health, release receipt, and timer state.

After completing steps 1–5, activate polling:

```bash
SEICHE_ENABLE_RELEASE_POLLER=1 \
  bash /home/seiche/app/ops/deploy/install-release-poller.sh
systemctl status seiche-release-poll.timer --no-pager
```

Do not enable both controllers. Two triggers cannot corrupt the checkout—the
deploy wrapper has its own lock—but duplicate release attempts obscure which
control plane owns an incident.

While the hosted `deploy-hetzner` workflow remains the active controller, its
forced-deploy client retries only wrapper exit `75` for a bounded ten-minute
window per pass. Each retry remains pinned to the same reviewed SHA and repeats
the host's admission check. Other SSH or wrapper failures stop immediately, and
a host that stays busy through either bound leaves production unchanged and the
workflow red. The workflow's 30-minute outer ceiling still applies to both
windows and all remote release work. External route checks run only after both
forced-deploy passes complete successfully.
