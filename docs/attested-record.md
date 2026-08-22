# The attested record

Seiche publishes a stress reading and a regime call every day. A call like
that is only worth something if nobody, including the operator, can quietly
rewrite it after the fact. A backtest can be polished forever; an
as-published record that provably never changed cannot. This document
explains the layer that makes the record provable, how to verify it yourself,
and what it honestly does and does not prove.

## What already existed

The notary (`seiche/notary.py`, `GET /api/notary`) hash chains every
published reading: each reading is canonicalised, hashed with SHA-256, and
linked to the reading before it. Change any past reading and every later link
breaks. That makes the record internally consistent, but it leaves two holes:

1. **Authorship.** Anyone with file access can rewrite the whole chain from
   genesis and recompute every hash. A chain proves order, not who wrote it.
2. **Time.** Nothing stops an operator from regenerating a flattering history
   yesterday and claiming it is a year old.

## What the attest layer adds

`seiche/attest.py` closes both holes. It never changes what the record says;
it only makes the record provable.

**A daily ledger.** One append-only JSONL chain per stream under
`backend/data/_pit_ledger/`. Two streams exist today:

* `stress_readings`: one record per day with the regime call, the composite
  value and subscores, a summary of the forward event odds, and vintage
  hashes (the SHA-256 of the full published reading, which is exactly the
  digest the notary chains, plus the hash of the weight vector that produced
  the value). One committed record per day; a day can never be reissued.
* `proof_scoreboard`: the PROOF backtest scoreboard (the same artifact the
  `proof_backtest` tool serves), every section hashed and a combined root
  committed, so the published track record itself is frozen in time.

**Signatures.** Each committed record's hash is signed with an Ed25519
operator key. The signed message is domain separated as
`seiche-pit-v1:{stream}:{day}:{record_hash}`, so a signature can never be
replayed onto another stream or day. Rewriting history now requires the
private key, and a rewrite made with a leaked key is attributable.

**Bitcoin anchoring.** Each day's record hash (the raw 32 bytes, no
intermediate encoding) is submitted to the public OpenTimestamps calendar
servers. The calendars aggregate digests into a Merkle tree that is committed
to the Bitcoin blockchain. Once anchored, nobody can backdate the record: the
two stored fragments link the record hash to the calendar commitment and that
commitment to a Bitcoin attestation. A proof is called **Bitcoin attested**
when those fragments parse and link exactly. It is called **Bitcoin confirmed**
only after the final commitment matches the Merkle root in the canonical block
header returned by a configured Bitcoin Core node. Until the calendar's
aggregation lands in a block (usually a few hours), the proof is honestly
marked `pending`; the `upgrade` command stores the continuation later.
Seiche deliberately accepts the calendar-proof subset it emits and can replay:
forks, append/prepend, SHA-1, RIPEMD-160 and SHA-256 operations, plus pending
and Bitcoin attestations whose continuation commitments are 32 bytes. The
parser applies the reference operation/result, payload, URI and recursion
bounds; other official OTS operations fail closed instead of being guessed at.
Live calendar responses are streamed and capped at 10,000 bytes before they
can enter attestation storage.

**Run receipts.** Each attestation run also signs a small manifest (day,
regime, value, ledger commit result) stored under
`backend/data/_attest/runs/`, so the act of publication is itself signed.

## Operating it

Everything is off by default. Enabling it changes nothing about the readings.

* `SEICHE_ATTEST=1` turns on the snapshot hook: when the daily reading is
  recorded, it is also committed to the `stress_readings` ledger and signed.
  A failure inside attestation is caught and logged; it never breaks a
  reading.
* `SEICHE_ATTEST_OTS=1` additionally submits unanchored days to the
  OpenTimestamps calendars during the hook (needs network). The
  cron friendly alternative is the CLI below.
* `SEICHE_PIT_LEDGER_DIR` and `SEICHE_ATTEST_DIR` override the storage
  locations (defaults: `backend/data/_pit_ledger/` and
  `backend/data/_attest/`).

The CLI is idempotent and safe to run from cron:

```
python -m seiche.attest status                # what is committed / signed / anchored
python -m seiche.attest sign                  # catch up signing of committed records
python -m seiche.attest anchor                # submit unanchored days to the calendars
python -m seiche.attest upgrade               # complete pending proofs to Bitcoin
python -m seiche.attest verify                # chain/signatures/linked OTS evidence
python -m seiche.attest verify \
  --bitcoin-node http://USER:PASS@127.0.0.1:8332  # also confirm canonical headers
python -m seiche.attest prove-scoreboard      # commit, sign and anchor the PROOF scoreboard
python -m seiche.attest pubkey                # operator public key (hex)
```

A sensible schedule: `anchor` once a day after the reading, `upgrade` a few
hours later.

The operator keypair is generated on first use in the attest directory. The
private key is written with permissions 0600 and must never leave the server.
The mutable `operator_key.pub` file and the key embedded in a signature are
identifiers, not trust roots. Production keys are pinned in the signed Seiche
release; a custom installation bootstraps a separate `trusted_operator_keys`
file once. A rotation is deliberately fail closed: approve the new public key
in that authenticated policy first (for production, through a reviewed signed
release), retain old approved keys for historical verification, then rotate
the private key. An unapproved signer makes the whole verdict invalid.

## Verifying it yourself

The commitment-verification surface is public and read only:

* `GET /api/attest/pubkey`: the current Ed25519 key, approved key set, signing
  domain, and message format. The signed release remains the trust root; do not
  learn a signing key from the same server whose history you are checking.
* `GET /api/attest/stream/stress_readings`: per day commitments (day, record
  hash and signature), the complete pending and continuation fragments as
  canonical base64, their parsed attestations, and a server-side verdict.
* `GET /api/attest/verify/stress_readings`: the server-side structural verdict
  alone. It intentionally reports zero Bitcoin confirmations unless a verifier
  explicitly supplies a Bitcoin Core trust boundary through the CLI.

To verify independently:

1. **The chain** needs only a JSON parser and SHA-256 when you have the ledger
   records: each record's hash is SHA-256 over the canonical JSON of
   `{day, payload, prev_hash}` (sorted keys, no whitespace), and each
   `prev_hash` must equal the previous record's hash, back to 64 zeroes. The
   generic public endpoint intentionally omits `payload`; it lets you verify
   links between signed commitments, while payload-to-hash verification needs
   the corresponding published aggregate or ledger export.
2. **The signatures** verify with any Ed25519 implementation: the message is
   `seiche-pit-v1:{stream}:{day}:{record_hash}`. Accept the signature only if
   its key is in the authenticated allowlist in the signed release (or the
   installation's separately authenticated trust file).
3. **The OTS path** begins at the record hash. Parse the pending fragment and
   require its calendar and parsed attestations to match the stored fields;
   then begin the continuation at that exact pending commitment and require
   its Bitcoin height and parsed attestations to match. The API publishes both
   fragments because neither one alone proves this path.
4. **The Bitcoin confirmation** asks a trusted Bitcoin Core node for
   `getblockhash(height)` and the raw 80-byte `getblockheader`. Recompute the
   header hash and require its Merkle-root field to equal the OTS final
   commitment. A Bitcoin attestation tag or plausible height alone is not a
   confirmation.

Or run `python -m seiche.attest verify` against the ledger and sidecars. It
checks the chain, exact record/signature identity sets, the authenticated key
policy, and both OTS fragments. Add `--bitcoin-node URL` (or set
`SEICHE_BITCOIN_RPC_URL`) to perform step 4. Without a node the report says
`bitcoin_confirmation_check: not_requested`, counts structurally valid proofs
under `n_anchors_bitcoin_attested`, and reports zero under
`n_anchors_bitcoin_confirmed`.

## Honest limits

* **Anchoring proves existence by a date, not correctness.** A Bitcoin
  anchored record proves the reading existed in exactly this form when it was
  anchored. It says nothing about whether the reading was right. The PROOF
  scoreboard is the honesty layer for correctness; this layer only guarantees
  the scoreboard and the readings you see today are the ones that were
  published then.
* **It cannot prove age retroactively.** Anchoring the PROOF scoreboard today
  proves it existed today, not that it existed last year. The honest claim is
  "unchanged since first anchored", and that claim compounds in value every
  month it stands.
* **A stolen key can sign a lie.** Signatures make rewrites attributable, not
  impossible. The Bitcoin anchor is the backstop: even the key holder cannot
  backdate an anchored record.
* **The generic endpoint is commitment-only.** It never returns stream
  payloads. Independent signature and timestamp checks therefore prove the
  identity and publication time of a record hash; proving that a particular
  payload produced that hash additionally requires the corresponding
  published aggregate or ledger record.
* **One record per day.** The ledger commits the first published reading of
  each data day. Intraday revisions remain visible in the notary chain, which
  appends a link for every distinct state the record held.

Seiche is a free public good. This layer exists so that anyone who relies on
its readings can check the record instead of trusting the operator, and that
verification is free too.
