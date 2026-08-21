# Seiche Data Commons readiness package

Status on 2026-08-21: technically built and rights-audited for review, not
submitted, not accepted, and not live in base Data Commons.

This package prepares the narrowest defensible Data Commons contribution: ten
OFR-produced U.S. short-term funding series collected directly from the
official OFR API. It does not attempt to make every public Seiche number a Data
Commons observation.

## Contents

- `input/config.json`: current Data Commons variable-per-row import config.
- `input/stat_vars.mcf`: proposed statistical-variable definitions for review.
- `input/observations/*.csv`: full direct-OFR observation histories.
- `build_observations.py`: fail-closed official-source refresh.
- `validate_package.py`: offline contract, uniqueness, numeric, and hash checks.
- `generated-manifest.json`: exact upstream clocks, row counts, and output hashes.
- `eligibility.csv`: complete 105-series and derived-output disposition.
- `RIGHTS_AND_SOURCES.md`: claim-level rights and contribution audit.
- `DATA_REQUEST.md`: ready-to-review request text without owner attestations.

## Rebuild and validate

From this directory:

```bash
python3 build_observations.py
python3 validate_package.py
```

The build uses the OFR `series/full` endpoint directly. It does not use Seiche's
public series CSV because the Seiche storage layer retains OFR dollar volumes in
raw dollars while display engines scale them, and it does not use FRED because
current FRED terms are incompatible with this AI-oriented database use.

## Exact eligibility result

- 10 OFR-produced series are candidate-eligible for a reviewed request.
- 2 New York Fed reference rates are held for license-compatibility review.
- 1 primary-dealer series is held for an upstream mnemonic/label mismatch.
- 68 FRED-fetched series are excluded under current FRED terms.
- 24 series already marked restricted by Seiche are excluded.
- 0 Seiche-derived outputs are ready because no output-data license and
  dependency-level rights audit exists.
- 0 global market packs are evidence-eligible in the dated public catalog.

Candidate-eligible does not mean accepted by Data Commons. The official next
step requires an owner-reviewed Google Issue Tracker data request and any
required CLA action. The prepared automation did not sign in, file the request,
accept a CLA, or make an owner representation.

## Why this can help discovery

If Data Commons accepts the import, world-economy questions about U.S. repo
activity and money-market-fund cash provision can resolve to structured,
place-and-date observations with OFR provenance and Seiche curation. It can
improve machine retrieval and citation opportunities. It cannot force an AI to
use or cite Seiche, and Data Commons review may reuse or rename the proposed
variables.
