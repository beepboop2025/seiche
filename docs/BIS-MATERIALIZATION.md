# BIS release baseline and current materialization

The signed corpus receipt preserves the exact census observed at release. Its
`bisAggregateRows` and immutable tag are never rewritten by a daily BIS refresh.
The live gateway's `corpora.bis.materialization` provides the current census;
`checks.deep.bis_materialization` independently binds it to the successful deep
check. Both projections must match byte-for-byte in canonical JSON, including
the serving receipt SHA-256, generation time, exact pinned flow membership,
per-flow capture IDs, source and manifest hashes, and aggregate count. Their
projection SHA-256 must recompute exactly, and per-flow counts must sum to the
live aggregate. Engine identity, inventory, taxonomy and rights gates remain.

A materialization must be no more than 48 hours old and no more than five minutes
in the future. Its row count must not fall below the signed release baseline;
regressions require review. Missing, mismatched, incomplete, stale, or malformed
proofs fail publication. A healthy refresh above the baseline is admitted only
with that current proof, without changing the historical signed receipt.

Publication evidence labels `baselineBisRows` and `baselineReceiptTag`
separately from the live `bisRows` (`bisRowsScope=live_materialization`), and
records `liveMaterializationSha256` and `liveMaterializationGeneratedAt`. The
public gateway catalog updates these current counts automatically after each
successful serving materialization; it does not present the baseline as a live
census.
