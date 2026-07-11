---
name: seiche-crypto-scout
description: Scheduled reconnaissance of the crypto x money-market frontier for Seiche. Use in a weekly cron job, or when asked about stablecoins, tokenized Treasuries, DeFi rates, crypto revenue opportunities, or "what's happening in crypto that matters to the board". Maintains a running gaps-and-opportunities ledger.
version: 1.0.0
license: AGPL-3.0
metadata:
  hermes:
    tags: [seiche, crypto, stablecoins, research, revenue]
    related_skills: [seiche-desk-brief, seiche-regime-watch]
---

# Seiche crypto scout

Stablecoins are money market funds in all but name (their issuers are
top-tier T-bill holders), tokenized Treasuries are the fastest-growing
on-chain asset class, and DeFi rates trade against SOFR. Seiche's edge is
that it reads the TradFi side of that wire natively. The scout's job is to
watch the crypto side and keep three ledgers current: stress transmission,
product gaps, and revenue opportunities.

## The weekly pass

1. **Board first.** Call `funding_stress_now` and note the regime and the
   stablecoin/Moorings context (peg deviations, circulation growth or
   drain, the weekend canary). The crypto read is framed by the funding
   read, never the other way around. For any "does TradFi stress reach
   crypto" claim, ground it in `crypto_stress_record` (the Wrecks table)
   rather than narrative.
2. **Transmission scan** (web): any stablecoin depeg or redemption wave,
   large tokenized-MMF flows, DeFi borrow-rate spikes, or basis-trade
   stress since the last pass. For each event, answer: did the board's
   regime or components move first, together, or not at all? Log the
   answer; these observations are future PROOF material for crypto-side
   validation.
3. **Gap scan** (web): new or updated tools in stablecoin risk ratings,
   tokenized-Treasury analytics, DeFi rate dashboards. For each, one line:
   what it does, what it still cannot answer that Seiche can (live
   funding-stress conditioning is the usual answer), and whether that gap
   grew or shrank.
4. **Revenue scan** (web): crypto public-goods rounds open or announced
   (Gitcoin, Optimism Retro Funding, Octant, protocol grant programs),
   agent-payment rails progress (x402 and similar machine-payable API
   standards), and any inbound-relevant news (a fund or protocol publicly
   burned by a funding event is a warm lead for the operator).
5. **Update the ledger and report.**

## The ledger

Keep a single memory entry (or a file if the deployment allows) with three
sections, each item dated:

- **TRANSMISSION**: observed crypto/TradFi stress co-movements, with the
  board's reading at the time. Prune items once superseded.
- **GAPS**: the standing list of unmet needs, each with: who feels it, what
  exists today, what Seiche uniquely adds, and a build-size guess (S/M/L).
- **REVENUE**: open opportunities with deadlines. Anything inside 14 days
  of deadline gets flagged in the weekly report's first line.

## Report format (chat-sized)

```
SEICHE CRYPTO SCOUT — week of {date}

Deadline watch: {anything closing within 14 days, or "none"}
Transmission: {1-2 lines: crypto stress events vs the board's read}
Gaps moved: {new/changed items only; "ledger unchanged" is fine}
Revenue: {new rounds, rails progress, warm leads}

{One sentence of judgment: the single most actionable item this week.}
```

## Discipline

- Every market claim needs a source link in the ledger entry. No
  recalled-from-training numbers; the crypto landscape moves too fast.
- The scout observes and recommends; it does not submit grant
  applications, contact leads, or change pricing. Those are operator
  decisions, flagged in the report.
- Keep Seiche's stance intact in anything drafted for publication:
  readings are PROOF-backed, misses shown, never investment advice.
- One weekly pass is the rhythm; an unscheduled pass is justified only by
  a live depeg or a funding regime change (the regime-watch skill decides
  that, not this one).
