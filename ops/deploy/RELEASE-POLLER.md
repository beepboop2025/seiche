# Direct Hetzner release controller

`seiche-release-poll.timer` replaces the hosted `deploy-hetzner` runner for
trusted `main` commits. It does not make the production checkout its test
workspace. The controller creates a detached candidate, installs an isolated
virtual environment, runs the same full suite as `publish.yml`, re-checks that
`origin/main` did not move, and then hands the exact tested SHA to the existing
root deploy wrapper. That wrapper remains the sole owner of service quiescence,
snapshot activation, Caddy deployment, health gates, and rollback.

## Safety boundary

- The box's `beepboop2025/seiche` deploy key stays **read-only**.
- Protect `main` before enabling this timer. A source write credential on the
  same unprotected box would turn a box compromise into a push-to-root loop.
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

## Install without activating

From the canonical production checkout, as root:

```bash
bash /home/seiche/app/ops/deploy/install-release-poller.sh
SEICHE_CONTROL_GATE_ONLY=1 /usr/local/sbin/seiche-release-poll
```

The first command requires the installed root wrapper to contain the
expected-target-SHA pin. It installs the script and units but leaves the timer
disabled and inactive. `SEICHE_CONTROL_GATE_ONLY=1` deliberately bypasses the
already-deployed fast path, runs the isolated full gate, records only its gate
receipt, and exits before invoking the deploy wrapper. Confirm that receipt and
the unchanged deployed SHA before handoff.

After disabling the GitHub Actions `deploy-hetzner` trigger, activate polling:

```bash
SEICHE_ENABLE_RELEASE_POLLER=1 \
  bash /home/seiche/app/ops/deploy/install-release-poller.sh
systemctl status seiche-release-poll.timer --no-pager
```

Do not enable both controllers. Two triggers cannot corrupt the checkout—the
deploy wrapper has its own lock—but duplicate release attempts obscure which
control plane owns an incident.
