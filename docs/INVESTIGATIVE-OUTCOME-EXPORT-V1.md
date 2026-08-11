# Investigative Outcome Export v1

Status: hardened domain contract implemented; atomic storage, dispatch, head
attestation, and cross-repository evidence joins remain deployment gates.

## Purpose

This contract turns Seiche forecasts into replayable outcome rows that
LiquiLens may evaluate or, when separately approved, train on. It does not
claim predictive skill. It preserves the evidence, split decision, target
schedule, and later outcome state needed to measure skill without rewriting
history.

The mutable legacy `odds_ledger.jsonl` is not eligible. A separately reviewed
migration may retain those rows as legacy evaluation evidence, but never as
prospective training facts.

## Four append-only facts

1. A `forecast` fixes the model, predeclared run, and target-rule IDs, integer
   probability, prediction time, exact target-window timestamps, exact strictly
   increasing market-local observation dates, calendar ID/version/content
   digest, canonical IANA calendar timezone, SHA-256 evidence IDs, and an
   evidence-cut digest. `prediction_time`, `knowledge_time`, and the trusted
   append `recorded_at` must be identical.
2. An `eligibility_decision` references an earlier forecast and records a
   reviewed split, rights-decision hash, reviewer, policy, and optional hash of
   the latest decision it supersedes. The first decision is issued with the
   forecast. Later decisions may retain the split or prohibit/revoke it;
   evaluation and training can never be promoted into one another, and a
   prohibition is terminal.
3. An `observation` fixes one market-local observation date, event and
   knowledge clocks, boolean or null measurability, immutable source-record
   hash, and SHA-256 evidence IDs.
4. A `resolution` references exactly the observations whose ordered local
   dates equal the forecast's committed dates. One forecast has at most one
   resolution. Each observation's UTC event instant is converted through the
   forecast's committed IANA timezone and must produce its claimed local date;
   this remains correct across UTC midnight and daylight-saving transitions. A
   cited `true` resolves the horizon positively even if another date is
   unmeasurable. A negative resolution requires every value to be measured and
   false. Otherwise the terminal resolution is `censored`, with a null label
   and explicit reason.

The timezone/date conversion is structural, but the calendar commitment is
evidence rather than an unsupported holiday assertion by this module. V1 does
not guess weekends, holidays, emergency closures, or working weekends. The
producer must join `calendar_digest` to the immutable calendar EvidenceDocument
that generated `observation_dates` and verify its timezone agrees with
`calendar_timezone`; LiquiLens independently repeats that join before treating
the dates as business days.

Every record commits to the previous record's SHA-256. Sequences and both
ledger clocks are monotone. Semantic duplicate forecasts, stale eligibility
supersession pointers, cross-split transitions, incompatible observations,
and second resolutions are invalid chain state.

Two identities prevent split leakage. `forecast_identity_digest` includes the
model/run and rejects a second value or evidence selection for the same
predeclared prediction. `case_group_digest` excludes model, probability, and
all replaceable evidence provenance, including calendar ID/version/digest and
timezone; it groups every model forecasting the same market/entity/target,
prediction/window, and exact local-date schedule. Calendar provenance remains
committed and exported, but changing it cannot create a new split identity or a
second post-hoc forecast for the same model/run. A case group may contain
multiple models, but all non-prohibited decisions in that group must use one
dataset purpose. Both digests are exported for independent LiquiLens purging.

## Point-in-time export

`build_investigative_outcome_export(entries, as_of, purpose,
trusted_head_hash)` requires an authenticated caller-supplied full-chain head.
It verifies the complete chain's hashes, links, sequence, bounds, and clock
ordering against that head, so a truncated or cherry-picked input fails.

Semantic replay is deliberately limited to the contiguous prefix whose
`knowledge_time` and `recorded_at` are both no later than `as_of`. A malformed
future semantic suffix therefore cannot change a valid prior cut; the same
suffix still fails a complete `verify_outcome_chain` audit.

Training and evaluation use disjoint reviewed decisions:

- `training` admits only forecasts whose latest visible decision is
  `training_eligible`;
- `evaluation` admits only forecasts whose latest visible decision is
  `evaluation_only`;
- a missing decision or latest `prohibited` decision enters neither.

Every admitted forecast is a denominator row:

- `pending`: no visible resolution while the target window is open;
  `outcome=null`, `label_eligible=false`;
- `matured_unresolved`: the window closed without a resolution record;
  `outcome=null`, `label_eligible=false`, and
  `censor_reason=missing_resolution_record`;
- `censored`: terminally unmeasurable; `outcome=null`,
  `label_eligible=false`;
- `resolved`: boolean outcome; `label_eligible=true`.

Null is never encoded or interpreted as false. Training consumers must filter
on `label_eligible`, while retaining pending, matured-unresolved, and censored
rows for coverage and selection-bias reporting. The canonical export carries
source hashes and labels, not raw observation values. Its SHA-256 commits to
cutoff, purpose, visible source head, selection policy, and ordered rows.
Standalone export construction and parsing require the exact v1 policy and row
fields, recompute both identities, enforce status/label/null/resolution
invariants, deterministically order rows, and reject non-canonical or oversized
bytes. This self-hash detects accidental content changes; it does not prove
producer identity, trusted-head provenance, or historical time.

## Resource and parser bounds

Each record is at most 64 KiB and each canonical export is at most 256 MiB.
Evidence references, observation dates, resolution references, JSON
depth/nodes, full-chain entries, and export rows are bounded. Duplicate keys,
non-canonical bytes, excessive nesting, huge JSON integers, non-finite numbers,
recursion failures, and parser failures are normalized to
`OutcomeIntegrityError`.

## Deployment gate

Do not dispatch or consume v1 in production until all of these exist:

1. An atomic append-only store with single-head compare-and-swap. Record
   creation must use the store's trusted clock, reject forks, fsync the record
   and head, and allow only byte-identical crash retries.
2. Signed operator receipts plus externally anchored head checkpoints. A hash
   chain alone proves neither authorship nor historical time and cannot detect
   deletion when the caller supplies an untrusted head.
3. Immutable EvidenceDocument lookup for every forecast evidence ID,
   `evidence_cut_digest`, `rights_decision_hash`, observation source/evidence
   hash, and calendar digest. The join must verify content identity,
   publication/knowledge clocks, redistribution rights, and calendar content.
4. Dispatch dual-writing against the legacy ledger for at least one complete
   target window, with parity and crash-idempotency receipts, before v1 becomes
   the only evaluation/training source.
5. A strict LiquiLens consumer verifier that validates canonical export bytes,
   producer signature/checkpoint, visible source head, policy, EvidenceDocument
   joins, and the `label_eligible` rule before admitting a row.

Legacy rows must not be silently converted into this prospective schema.
