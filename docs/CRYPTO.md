# Seiche and the crypto basin

*Research synthesis, July 2026. This is the roadmap for Seiche's crypto
surface: what the crypto market is missing, why Seiche is the right tool to
supply it, and what gets built in what order.*

## The one-sentence wedge

Every crypto risk tool watches the chain; none watches the funding market
underneath it. Stablecoin reserves, tokenized Treasury funds, and DeFi
rates all now sit directly on the ≤93-day bill and overnight repo market
that Seiche reads natively, point-in-time, with an honest backtest. The
"TradFi funding stress is about to hit crypto" early warning does not exist
anywhere. Seiche can be it.

## Why the wire is live now

- **Law**: the GENIUS Act (signed July 2025, rules finalizing mid-2026)
  requires compliant stablecoin reserves to sit in ≤93-day Treasuries and
  overnight repo — exactly Seiche's instruments. Issuers held ~$153B of
  T-bills by end-2025; Tether alone is a top-20 sovereign-scale holder.
- **Evidence of transmission**: BIS WP 1270 quantifies it both ways (a
  $3.5B stablecoin flow moves 3-month bill yields; effects 2-3x larger
  under bill scarcity; a $30B redemption wave moves bills ~6.4bp), and
  issuers cannot touch the Fed's backstops, so redemptions mean outright
  sales into whatever market exists that day.
- **Structure**: tokenized Treasury funds (~$13-15B, >60%/yr growth:
  USYC, BUIDL, BENJI, USDY, USTB) became DeFi and derivatives collateral
  in 2026. BIS, ECB, IMF, and the NY Fed have all published in the last
  year warning that their 24/7-redemption-versus-T+1-underlying mismatch
  is untested. No commercial tool watches the funding market for them.
- **Rates**: DeFi stablecoin lending rates converged to SOFR through
  2025-26 (Steakhouse) as tokenized Treasuries imported the risk-free rate
  on-chain — but the risk premium is latent, so the value is in catching
  the regime transition when the spread re-widens, which is a
  Station-Keeping problem, not a charting problem.
- **Precedent**: March 2023 (SVB → USDC to $0.87 within hours, contagion
  to DAI) is the proven TradFi-funding-to-crypto transmission event, per
  the Fed's own December 2025 post-mortem. September/November 2025 repo
  stress (record SRF usage) shows the underlying market still dislocates.

## The gaps (what nobody built)

1. **Funding-stress → stablecoin-reserve bridge.** Peg monitors (Bluechip,
   S&P, Webacy, DepegWatch, StableLens, Hexagate) alert after the peg
   moves. Nobody maps repo spreads, reserve scarcity, and SRF usage onto
   the reserve books the GENIUS Act just standardized. Macro-first beats
   on-chain-first on lead time.
2. **Regime gauge for RWA risk curators.** The teams parameterizing
   tokenized-Treasury collateral daily (Gauntlet, Chaos Labs, Block
   Analitica, Steakhouse) consume protocol and oracle data but have no
   funding-calendar or funding-regime input for LTVs and circuit breakers.
3. **Transmission early warning for rates desks.** Descriptive terminals
   (Velo, Kaiko/Amberdata, Glassnode, Artemis) chart funding after the
   fact. No forward, validated, plumbing-driven signal with a
   point-in-time record exists.
4. **Carry-inversion tripwire.** The late-2025 Ethena unwind (sUSDe yield
   below the Aave borrow rate → 50% TVL collapse) is a recurring,
   monitorable basis inversion nobody packages.
5. **Crypto-side PROOF.** No crypto signal product publishes an honest
   backtest over labelled episodes. Seiche's methodology is itself a
   differentiator.

## The build order

**Phase 1 — validate (no new surface).** Run the existing PROOF machinery
against labelled crypto stress episodes (Mar 2020, May 2022, Nov 2022,
Mar 2023 SVB/USDC, Oct 2025 liquidation cascade, the Ethena unwind):
what did the board read before each, point-in-time? Publish hits and
misses. This is the credibility anchor every later phase cites.

**Phase 2 — the Offshore board.** Grow the Moorings engine into a
first-class crypto surface: per-issuer reserve-stress lens (the
GENIUS-eligible book against live front-end conditions), the
carry-inversion tripwire (DeFi lending rates vs SOFR, sUSDe vs Aave
borrow), and a transmission tracker that logs each crypto stress event
against the board's contemporaneous read. New collectors stay keyless and
free where possible (DeFiLlama yields, public fund disclosures).

**Phase 3 — the regime-gauge feed.** A thin, versioned endpoint (regime,
index, turn/quarter-end flag, reserve-scarcity and SRF context) shaped for
machine consumption by risk-curator pipelines and agent frameworks, on the
existing metered MCP/API plumbing. Design partners before general release.

**Phase 4 — distribution.** The desk-agent kit (docs/HERMES.md) already
gives any AI agent the board; agent-payment rails (x402-style per-call
stablecoin payments) and crypto-native subscriptions let the same surface
earn in the currency its users hold. See docs/MONETIZATION.md.

## What Seiche will not do

No trading bots, no yield products, no token. The readings stay
PROOF-backed with misses shown, and nothing here is investment advice.
The crypto basin is context and customer, not a casino.
