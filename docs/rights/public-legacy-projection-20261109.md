# Per-engine public projection of the legacy board: 2026-11-09

Policy `seiche.public-source-rights.2026-11-09` replaces the all-or-nothing
gate of `seiche.public-source-rights.2026-10-28`. It is an engineering policy
over acquired snapshots, not a blanket licence and not a claim of historical
point-in-time availability.

## The rule

A reading is public only when every input it reads is an allowed direct or
open source, and the snapshot's own provenance proves it. Otherwise the
reading is withheld as metadata: no value, no observation date, only the
names of the restricted inputs, the policy, and the terms that apply. A
withheld reading is not a calm, zero or last-known reading, and nothing is
reconstructed.

`backend/seiche/public_rights.py` holds the whole policy:

- `SOURCES`: every allowed source, keyed by its provenance identity or
  snapshot envelope, with label, publisher, terms URL, attribution and basis.
  Four entries also name the owner decision they need
  (`owner-source-decisions-20261108.md`); they are allowed only while that
  decision is recorded in `backend/seiche/source_decisions.json`. Bank of
  Japan sources are allowed on free surfaces only.
- `WITHHELD_SOURCES`: FRED copies, and sources whose reuse terms this release
  does not establish (exchange and stablecoin market data, DeFiLlama exploit
  records, GDELT text samples, the quarantined Palimpsest context, the
  Undertow current-affairs pack, CFETS fixings). An unknown source fails
  closed too.
- `ENGINE_INPUTS`, `DEEP_INPUTS`, `HEADLINE_INPUTS`, `CALENDAR_INPUTS`: the
  inputs each reading reads, mirroring `assemble._run_engines` and
  `assemble._deep_layer`. A reading without a declaration is withheld.

An input is allowed only when its provenance row is the single row for that
alias, carries the alias's registry identity or its qualified publisher
identity, is present in the completed snapshot, and (for a qualified capture)
states `redistribution_status` `allowed`. A FRED copy, a missing or duplicate
row, a row that borrows another identity, or a capture whose decision is not
recorded withholds every reading that reads it.

## What is public

With every qualified capture present, these readings read only allowed
inputs and are public:

- engines: composite, money_market (card by card), kink, rdenowcast, weather,
  supplydesk, runway, tails, stigma, echo, rvxray, crowding, officialbid,
  auctions, reportcard, ledger, resonance, undertow, phasemap, edetect,
  communique, breakwater, hydrophone, merian, roguewave, caesar, warehouse,
  thermohaline, spillover, stationkeeping;
- deep layer: history, turn, microseism, leakaudit, tidetables, swell,
  bathymetry, markov, oujump, montecarlo, funding_pop (and its riptide alias),
  gyre, seastate;
- headline: SOFR, EFFR, IORB, reserves, ON RRP, TGA, SRF and discount window;
  calendar fields whose readings are public.

The money desk is public card by card. Its regime, narratives, diagnostics
and charts are public only when every desk input is an allowed direct
acquisition; the desk names each card's legs in `source_ids`.

## What stays withheld

Exactly as before, every reading that reads a FRED copy or a blocked series
stays withheld, and so do readings over sources whose terms are not
established:

- engines: oilfunding and ballast (EIA spot prices supplied by Refinitiv),
  estuary (EIA spot prices and IMF commodities), harbors (OECD call rates
  fetched from FRED), sonar (sweeps every FRED-registry series), basins and
  moorings (exchange and stablecoin data), cpsentinel (DeFiLlama), farbasin
  (Palimpsest), scuttlebutt (GDELT), windfetch (the Undertow pack);
- deep layer: tell (VIX and the ICE BofA spreads), playbook and backtest
  (S&P 500, VIX, ICE BofA), ml (VIX, ICE BofA, TED), stacker, regatta,
  searoom and modelcourt (they read the Tell and the ML layer), book
  (S&P 500), refereegli (Nasdaq);
- headline VIX and HY OAS;
- the editorial argument, the Navigator and the alert log, which combine
  every reading;
- `/api/public`, `/api/book`, `/api/brief`, `/api/ask`, `/api/event-analysis`,
  `/api/alerts`, `/api/pit`, `/api/badge/record`, `/api/oil-funding`,
  `/api/estuary`, `/api/wrecks`, Time Machine replays and dispatches, and the
  MCP tools that serve them. Replays carry no per-input provenance, so they
  cannot prove their inputs.

## Surfaces

- `/api/overview`, `/api/deep`, `/api/gauge`, `/api/engines/{name}` and
  `/api/trade-safety/risk-context` serve the projection. A withheld engine
  route answers 403 with its restricted inputs. An engine that can never
  qualify in this release is refused before any snapshot loads.
- The anonymous MCP surface stays at its eight reviewed tools. Growing it is
  an owner decision that moves the public tool docs, `server.json` and the
  Hermes persona map together. `funding_stress_now`, `historical_analogs` and
  `trade_safety_risk_context` are projection-ready: their handlers read only
  the projection and refuse with the source-rights restriction when their
  reading is withheld, so opening one is a one-line change to `LEGACY_TOOLS`.
  Until then they are refused before any snapshot loads, as before.
  `source_rights_status` lists the projectable scope, the projection-ready
  tools, the sources with their terms, and the decisions.
- `/data/overview.json` carries exactly the projection `/api/overview`
  serves. `/data/public.json` and `/data/book_history.json` carry the
  restriction. The prerendered home page names public and withheld readings
  and prints only the composite and the public headline rows; the editorial,
  the Tell, PROOF, the Book and dispatch letters are not rendered. Link cards
  stay withheld.
- Raw series exports follow the same sources: a qualified capture exports
  while its status is allowed; FRED copies and blocked series stay refused.

Tests: `backend/tests/test_per_engine_projection.py` and
`backend/tests/test_static_rights_projection.py`.
