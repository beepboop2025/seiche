# Railway Telegram authority transfer (phase 7)

Phase 7 moves the Seiche Telegram bot's flat state, long-poll authority, and
three delivery schedules from Hetzner to a dedicated Railway service. It does
not share the Phase 5 API service or volume, and it never runs two
`getUpdates` consumers.

The transition has four explicit states:

1. Hetzner owns polling and delivery; Railway is a credentialed but
   non-authoritative candidate.
2. Hetzner is persistently masked and its final state is restored on Railway;
   neither side polls or sends.
3. A protected grant lets one Railway worker make its first successful poll,
   run due schedules, and publish a fresh heartbeat.
4. Railway seals the activation receipt; Hetzner records it and remains
   masked.

Railway documents that a service can have only one attached volume and that
replicas cannot be used with volumes. That constraint is useful here: one
service, one volume, and one sequential worker form the Telegram ownership
boundary. The bot is a continuously running process, not a Railway cron job;
Railway cron jobs are intended to finish and do not support long-running
processes. See Railway's official [volume reference](https://docs.railway.com/volumes/reference),
[volume backups](https://docs.railway.com/volumes/backups), and
[cron-job guide](https://docs.railway.com/cron-jobs).

Merging these files prepares the control plane. It does not create Railway
resources, freeze Hetzner, expose a token, grant authority, enable schedules,
or prove a live canary.

## Implemented contract

- `.github/workflows/railway-telegram.yml` has separately protected
  preparation, candidate, activation, rollback, and monitoring jobs.
- `ops/railway/Dockerfile.telegram` verifies a canonical request, exact Git
  bundle commit/tree, canonical archive bytes, and both SHA-256 digests before
  making the worktree read-only.
- `seiche.telegram_migration` accepts only a bounded flat JSON/JSONL tree,
  rejects links and unsafe archives, binds the final offset and subscriber
  identity, and permits exactly one immutable grant.
- `seiche.telegram_runtime` is the root supervisor. It restores candidates but
  starts no bot until the grant exists. The worker runs as uid/gid 10001.
- `seiche.telegram_worker` owns polling and the alert, letter, and tandem
  schedules sequentially. It polls successfully before outbound schedules,
  persists an in-flight marker before every delivery, and refuses an uncertain
  retry rather than risking a duplicate.
- `seiche-telegram-migration-controller.sh` is the root-only forced SSH
  command. It freezes seven host units, waits longer than one long poll,
  snapshots `/var/lib/seiche-bot`, supports rollback only before grant, and
  records the final Railway activation idempotently.

The candidate receipt is byte-exact. After grant, the live validator permits
normal subscriber and schedule evolution but forbids the Telegram offset from
moving backwards. An activation receipt is not written until the current
worker has produced both a valid first-poll proof and a fresh heartbeat after
running due schedules.

## Dedicated Railway service

Create a service named `seiche-telegram` in the same controlled project or a
separate project with the same governance. It must have:

- exactly one Railway volume named `seiche-telegram`, mounted at
  `/var/lib/seiche-telegram`;
- exactly one replica in the reviewed region;
- exactly one Railway-provided HTTPS domain, used only for `/healthz`;
- no connected GitHub source or automatic deployment;
- no cron schedule; and
- no database reference or credential from the Phase 5 stateful core.

The workflow uploads the exact reviewed source with `railway up`. The service
receives only these explicit variables:

- `SEICHE_RELEASE_SHA`
- `SEICHE_RAILWAY_TELEGRAM_VOLUME_ID`
- `SEICHE_BOT_TOKEN`
- `LAB_CHANNEL_ID`

Railway supplies its deployment, project, environment, service, volume, mount,
and region identity. The channel ID is captured from the source poller's
environment during the freeze and must equal the protected
`SEICHE_LAB_CHANNEL_ID`; this preserves the letter/alert channel destination.
Do not add Hetzner, GitHub, off-site-storage, database, edge, signing, or
release-controller credentials to the service.

## Protected GitHub environments

Require reviewers and prevent self-review for the admin, cutover, activation,
and rollback environments. Restrict the monitor environment to `main` and give
it only read-only control-plane credentials, but do not require per-run human
approval: its six-hour schedule must execute unattended.

`railway-telegram-admin` receives:

- `RAILWAY_TOKEN`
- `RAILWAY_PROJECT_ID`
- `RAILWAY_ENVIRONMENT_ID`
- `RAILWAY_TELEGRAM_SERVICE_ID`
- `RAILWAY_TELEGRAM_VOLUME_ID`
- `RAILWAY_TELEGRAM_VOLUME_NAME`
- `RAILWAY_TELEGRAM_REGION`
- `RAILWAY_TELEGRAM_ORIGIN`
- `SEICHE_BOT_TOKEN`
- `SEICHE_LAB_CHANNEL_ID`

`railway-telegram-cutover` receives the same Railway values, bot token, and Lab
channel ID, plus:

- `HETZNER_HOST`
- `HETZNER_TELEGRAM_MIGRATION_USER`
- `HETZNER_TELEGRAM_MIGRATION_SSH_KEY`
- `SEICHE_OFFSITE_S3_ENDPOINT`
- `SEICHE_OFFSITE_S3_BUCKET`
- `SEICHE_OFFSITE_S3_PREFIX`
- `SEICHE_OFFSITE_S3_ACCESS_KEY_ID`
- `SEICHE_OFFSITE_S3_SECRET_ACCESS_KEY`
- `SEICHE_OFFSITE_S3_REGION`

The off-site bucket must be outside Railway, versioned, and created with Object
Lock enabled. The principal needs put and HEAD access with COMPLIANCE
retention; it needs no delete or retention-shortening permission.

`railway-telegram-activation` receives the Railway values, origin, bot token,
and the three Hetzner connection values. `railway-telegram-rollback` receives
the Railway IDs and volume ID plus the Hetzner connection values.
`railway-telegram-monitor` receives the Railway IDs, volume ID and origin plus
`HETZNER_HOST`, `HETZNER_TELEGRAM_MONITOR_USER`, and the separate
`HETZNER_TELEGRAM_MONITOR_SSH_KEY`. It does not receive the bot token,
off-site write credentials, or the mutation-capable migration key.

Keep `RAILWAY_TELEGRAM_PHASE7_ENABLED` absent or unequal to `true` until the
post-activation monitor is green.

## Install the bounded Hetzner controller

Install the reviewed file as root without changing its bytes:

```bash
install -o root -g root -m 0500 \
  ops/deploy/seiche-telegram-migration-controller.sh \
  /etc/seiche/libexec/seiche-telegram-migration-controller.sh
install -o root -g root -m 0500 \
  ops/deploy/seiche-telegram-status-controller.sh \
  /etc/seiche/libexec/seiche-telegram-status-controller.sh
```

Use a dedicated SSH key that is not a deploy, backup, or operator key. Add only
that public key to root's `authorized_keys` with a forced command:

```text
restrict,command="/etc/seiche/libexec/seiche-telegram-migration-controller.sh" ssh-ed25519 REVIEWED_PUBLIC_KEY seiche-telegram-migration
```

Set `HETZNER_TELEGRAM_MIGRATION_USER=root`. Independently verify that the
hard-coded Ed25519 host key in the workflow is the current host key before
merging; a host-key change requires a separately reviewed workflow update.

Create a second key for monitoring. Its authorized-key entry must force the
status-only wrapper:

```text
restrict,command="/etc/seiche/libexec/seiche-telegram-status-controller.sh" ssh-ed25519 REVIEWED_MONITOR_PUBLIC_KEY seiche-telegram-monitor
```

Set `HETZNER_TELEGRAM_MONITOR_USER=root`. The wrapper accepts only
`status REQUEST_ID`, validates the request ID, clears the original command,
and delegates that one operation to the serialized controller.

Confirm the source is healthy before any candidate run:

```bash
systemctl is-active seiche-bot.service
systemctl is-active seiche-bot-alert.timer
systemctl is-active seiche-bot-letter.timer
systemctl is-active seiche-bot-tandem.timer
test -f /var/lib/seiche-bot/offset.json
test -f /var/lib/seiche-bot/subscribers.json
```

The controller itself rechecks the active poller, captures and validates its
Lab channel identity, rejects an already masked source, enforces the same
file-count/per-file/total-byte bounds as Railway, serializes requests with
`flock`, and accepts only `freeze`, `status`, `fetch`, pre-grant `rollback`,
and `acknowledge`.

## 1. Prepare the non-authoritative service

On the exact reviewed `main` SHA, dispatch:

```bash
gh workflow run railway-telegram.yml --ref main \
  -f operation=prepare-service \
  -f confirmation=PREPARE_NON_AUTHORITATIVE_TELEGRAM_SERVICE
```

Review the private artifact and image-request attestation. The run is accepted
only when the exact deployment reports candidate mode with no request or
faults, the volume and domain are unique, the service secret boundary is
closed, daily/weekly/monthly volume-backup schedules exist, and the bootstrap
backup is fresh and locked.

Preparation may be repeated only while the service still reports a clean
candidate with no request. It does not contact Telegram or Hetzner and grants
no authority. The workflow refuses to prepare an already granted or production
service in place; Phase 7 intentionally pins the bot deployment at its
activation SHA.

## 2. Freeze Hetzner and restore the candidate

Start only when an activation reviewer and rollback operator are available:

```bash
gh workflow run railway-telegram.yml --ref main \
  -f operation=candidate \
  -f confirmation=FREEZE_HETZNER_TELEGRAM_FOR_CANDIDATE
```

The controller disables, stops, and masks the poller and all three timer/service
pairs, waits 65 seconds, proves no process remains, and creates a four-hour
fence. GitHub validates and restores the exact archive without enabling
Telegram calls. It then seals metadata, fence, state identity, transfer,
candidate receipt, and subscriber-bearing archive outside Railway under
30-day COMPLIANCE Object Lock. The archive is removed from the Actions
workspace before artifact upload.

After the exact restore, the candidate job also creates and locks a Railway
native backup named for the snapshot and request. This proves the first native
recovery point contains the restored state rather than the earlier empty
bootstrap volume.

Record these four values from the green summary:

- request ID;
- snapshot ID;
- exact Railway deployment UUID; and
- candidate receipt SHA-256.

Hetzner remains frozen. Activate or roll back promptly; the four-hour lifetime
is a ceiling, not a target outage.

## Pre-grant rollback

Rollback is legal only while no Railway grant exists:

```bash
gh workflow run railway-telegram.yml --ref main \
  -f operation=rollback \
  -f request_id=ACCEPTED_REQUEST_ID \
  -f confirmation=RESTORE_HETZNER_TELEGRAM_BEFORE_GRANT
```

The workflow first proves the grant file is absent. The host restores the
captured unit enablement/activity and restarts the old poller. If a grant exists,
rollback fails closed. After a canceled candidate job, reconcile and roll back
that exact request before starting another freeze.

## 3. Activate the one Railway poller

Dispatch with the exact candidate outputs:

```bash
gh workflow run railway-telegram.yml --ref main \
  -f operation=activate \
  -f request_id=ACCEPTED_REQUEST_ID \
  -f snapshot_id=ACCEPTED_SNAPSHOT_ID \
  -f deployment_id=ACCEPTED_DEPLOYMENT_UUID \
  -f candidate_receipt_sha256=ACCEPTED_CANDIDATE_SHA256 \
  -f confirmation=RAILWAY_BECOMES_SOLE_TELEGRAM_CONSUMER
```

The protected job revalidates the frozen host, request, candidate, deployment,
token digest, and fence before publishing one immutable grant. The Railway
worker then proves `getMe`, a successful `getUpdates` with no 409 conflict,
non-decreasing offset, scheduler baseline, and a fresh heartbeat. Only then is
the activation sealed and acknowledged on the still-masked host.

After the grant, never use the rollback operation or manually unmask Hetzner.
Any failure is a forward Railway recovery incident.

## 4. Verify and enable monitoring

Manually dispatch `operation=monitor`. It must prove:

- production health at the exact receipted activation SHA, independently of
  the newer `main` SHA running the monitor;
- the full transfer/candidate/grant/proof/activation chain;
- a heartbeat no older than two minutes;
- exact volume/project/environment/service/mount identity;
- at least 20 percent volume headroom;
- exact daily, weekly, and monthly backup schedules;
- a native backup no older than 26 hours;
- the locked Phase 7 bootstrap canary;
- the exact locked post-restore candidate backup; and
- the exact Hetzner request still persistently fenced, with its stored
  activation acknowledgement digest matching Railway's activation receipt.

Only after that run is green, set repository variable
`RAILWAY_TELEGRAM_PHASE7_ENABLED=true`. Monitoring then runs at minute 23 every
six hours. The service itself continues polling and scheduling; the GitHub
schedule is evidence monitoring, not bot execution.

## Recovery boundary

Railway's recurring native backups cover current bot state. The locked external
candidate archive is a portable, immutable proof that the source state can be
restored independently of the Railway volume. It is a cutover canary, not a
daily portable export of future subscriber/offset changes.

If the Railway service fails but its volume is intact, restart only the same
single-replica service. It revalidates the historic authority chain, removes
only recognized interrupted temp writes, forbids offset rollback, and requires
a new current heartbeat before reporting production.

If the volume must be restored, select a reviewed Railway backup in an isolated
service first. A production restore or reverse transfer needs a new authority
fence and receipt; never start the stale Hetzner generation beside the Railway
poller. If disaster-independent, current portable recovery is required, add a
separately protected recurring export phase that pauses this one worker,
archives the live flat state, restores it in isolation, and seals it outside
Railway before claiming that stronger property.

Phase 7 does not define an in-place bot-code upgrade after activation. Keep the
dedicated service pinned to the receipted SHA. A future upgrade requires a
separately reviewed, authority-preserving deployment receipt that binds the old
activation chain to the new image and proves one current poller before the new
deployment reports ready. Do not rerun `prepare-service` against production.

## Scope

Phase 7 covers `/var/lib/seiche-bot`, `seiche-bot.service`, and its alert,
letter, and tandem timer/service pairs. It does not migrate Rissaga/Hermes,
`/var/lib/rissaga`, or their publisher/fallback units. Those are an adjacent
authority domain and require their own state, delivery-idempotency, and channel
ownership transfer before claiming every adjacent publishing surface has moved.
