# Source freshness for the agent platform

The central monitor checks the clocks exposed by each native product. It does
not refresh a native source, modify a signed publication, grant data rights, or
approve a trade. Transport availability, publication age, observation age,
effective date, and permission to use an output are separate facts.

Implementation: `services/agent-network/src/seiche_agent_network/freshness.py`.
Regression checks: `services/agent-network/tests/test_freshness.py`.

## Admitted contracts

| Product / tool | Qualified clock and policy | Limits |
| --- | --- | --- |
| Seiche `data_health` | Registered series `asof` with the native D/W/M/ML/Q/QL grace: 4/10/45/100/120/300 calendar days | Applies only when an explicit source-specific publication policy is absent and the returned grace matches the admitted native contract. |
| Seiche H.10 | Exact FRED remote IDs; observation week versus Monday 16:15 New York release, delayed for US federal holidays | Recomputed at inspection time. A release schedule is not a publication receipt or proof that every daily print is present. |
| Seiche H.4.1 | Exact FRED remote IDs; Wednesday observation versus Thursday 16:30 New York release, delayed for US federal holidays | A missing due observation is stale even when a generic weekly age would pass. |
| Seiche DONIA | Exact `cbuae_donia / UAE_INTEREST_RATES/DONIA / D` identity; latest chart date versus native 09:30 Dubai aim plus 90 minutes collector allowance | Weekends preserved; unverified public holidays are not excused. Publication aim is not an observed publication timestamp. |
| Seiche CBUAE reference FX | Exact native VAT/AED remote IDs and native Dubai calendar-date policy | Same-day table date is fresh; an older date is stale. No invented holiday or release calendar. |
| Undertow `exit_cost` / `crypto_exit_check` | Six native BTC venue observation timestamps; the native two-hour research observation limit | Publication timestamp does not substitute for a venue observation. This is research metadata, not an executable quote. Caller-supplied `max_age_seconds` cannot relax this monitor. |
| Undertow `trade_safety_exit_context` | Exact schema, native venue clocks, two-hour window, separate rights approval and validity deadline | Observe/paper only. The scheduled probe uses a fixed synthetic request, never a portfolio, real order or execution mode. |
| Palimpsest `query_economic_observations` | Native China-economic rights status | Current CFETS/ChinaMoney lineage restriction and affected derivatives remain restricted; this does not classify every China dataset as restricted. |
| Explicit normalized observations | `financial-evidence.source-freshness.v1` plus an operator-admitted `SourcePolicy` | A payload cannot nominate its own policy, deadline, or generated timestamp as proof of freshness. |

The H.10 and H.4.1 calendars match the source-specific contracts in
`backend/seiche/sources/publication.py`. Their public release rules are described
by the [Federal Reserve H.10 release page](https://www.federalreserve.gov/releases/h10/)
and [H.4.1 release page](https://www.federalreserve.gov/releases/h41/).
The portable calendar is bounded to evaluation years 1986–2100 and includes
observed federal holidays, including Juneteenth from 2021. It remains a schedule
expectation; unexpected source publication changes need a reviewed policy update.

## IORB: the future date is an effective date

The read-only probe on 2026-10-11 observed the exact native identity
`fred / IORB / IORB / D` with `asof=2026-10-13`,
`fetched_at=2026-10-11T17:55:50Z`, and snapshot generation at
`2026-10-11T18:23:45Z`. The [Federal Reserve's IORB publication
description](https://www.federalreserve.gov/monetarypolicy/reserve-balances.htm)
says that the next business day's rate is generally published at 16:30 Eastern
on business days, excluding federal holidays. A forward effective date is
therefore possible without a corrupt source clock.

The monitor follows the exact exception already implemented in
`backend/seiche/trade_safety.py`: date-only IORB effective date, an aware prior
collection timestamp no later than snapshot generation, a snapshot no later
than inspection, and a forward interval of 1–14 New York calendar days. It
exposes `effective_at` and `available_at` separately. The latter is the product's
collection clock, not a claim about the original publisher's first release.

This exception **does not turn the future effective rate into a current
observation**. Its row remains `unknown` for current-observation freshness with
reason `announced_effective_date_not_current_observation`. The funding subset
requires a current-effective IORB observation while retaining the announcement.
SOFR and EFFR are checked independently.

The concrete native repair is to expose both the latest already-effective IORB
point at the decision cutoff and the separately announced next-effective point,
with their collection/release clocks. Do not shift the future date backward or
delete the announced point. Make that change through the native reviewed
publication/receipt workflow; the central monitor does not alter the publisher.

## Historical clocks and remaining gaps

Only the native exact IOER and TED identities are excluded from current-input
age aggregation, and only when their observations respect the known retirement
boundaries. They remain disclosed in `historical_reference_clocks`. An arbitrary
old series or an IOER row beyond its registered final observation is not hidden.

Native GDP metadata explicitly identifies its date as `quarter_start` and
publication freshness as unknown. The monitor preserves that reference period
and its age separately, without representing it as the latest release clock or
a collector outage. The correct native enhancement is an official release or
vintage clock, not a more permissive generic age threshold.

Fifteen Seiche health entries observed in this investigation describe
heterogeneous tables and provide only retrieval clocks. Their unknown status
means per-observation clocks are not exposed through this health envelope. The
native source owner can add a bounded observation-clock summary with source
identity, cadence/release contract, availability and rights. A new collection
timestamp alone cannot fill this gap.

LiquiLens's `crypto_regime_board` exposes a board date and native stale flag. A
board date alone does not establish each underlying observation or its rights;
it remains unknown until an admitted source-observation contract is exposed.
NarcoScope's market catalog contains historical reference periods, source rights
and dataset metadata; it is not a live trading-price feed. NoiseFloor performs
calculations on caller-supplied series and has no hosted source feed to probe.
Neither is marked fresh merely because its server responds.

Palimpsest catalog artifact clocks, `mcp_checked_at`, and rights evaluation times
are not underlying economic observation clocks. WDI or other context-only
outputs retain their scoring restriction. New permitted China sources need
their own source-specific rights and point-in-time observation contract; a
healthy catalog cannot override the denied lineage.

## Running and interpreting the monitor

```sh
python -m seiche_agent_network.freshness --output /private/freshness.json --timeout 30
```

Only fixed native product endpoints are allowed. Calls use the official MCP
client, bounded response streams, explicit timeouts, no redirects, and synthetic
traffic classification. The JSON is written atomically with mode 0600. It
retains status, source identifiers and clocks, never market values, request
payloads, private portfolio data, model prompts, credentials or raw responses.

`coverage` and `source_reason_counts` describe inspected source rows, not whole
product readiness. `critical_subset` separates the US funding inputs from the
full catalog. For Undertow, `clock_freshness` describes venue clocks while
`admission_check` retains the native rights result; a rights restriction wins
the top-level status and remains visible in `reasons`.

Exit code 0 requires all probed scopes to be fresh, 1 reports stale/restricted or
unavailable scopes, and 2 reports otherwise unknown coverage. Unknown is useful
evidence of a missing contract, not permission to assume freshness. Synthetic
probe activity must stay separate from adoption and customer usage statistics.

The expanded live probe at 2026-10-11T18:44:28Z reached all six MCP services.
It qualified 120 Seiche active rows as fresh, 25 as stale and 17 as unknown,
with two explicitly retired reference rows kept separate. Undertow's six venue
clocks were fresh while the stronger rights admission was
`pending_owner_review`; Palimpsest's China-economic query was restricted.
These are dated scoped findings. Current evidence is the scheduled report's
`evaluated_at` and rows, not these historical counts. One earlier Seiche MCP
probe returned HTTP 502; the later successful probes do not erase that transient
failure.

## Undertow central access and the admission-check budget

Central public calls may use an operator-owned native MCP identity so all
visitors do not compete for the server IP's 200-call anonymous daily allowance.
This changes identity attribution at the native ingress, not source rights,
subscriber access, billing, or the native per-minute and proof limits.
`upstream_auth.py` has an explicit reviewed public-tool allowlist independent
of discovery. Discovery and `agent_access_status` remain anonymous; unknown
and subscriber tools are rejected. Visitor bearer credentials are never sent
upstream. Cookies are removed for every product. Native Undertow is stateless
and rebuilds identity per request; an unexpected MCP session response fails
closed rather than allowing an elevated session to be reused.

Provision the derived credential using `services/agent-network/ops/renew_undertow_token.py`
and its root-only oneshot/daily timer. Configure both the central service and
the freshness service with:

```text
SEICHE_UNDERTOW_MCP_TOKEN_FILE=/var/lib/seiche-agent-network/undertow-mcp-token.json
```

The credential file must be a regular, single-link, non-symlink file with mode
0600, owned by the service account, no larger than 16 KiB. Its exact JSON
fields are `token`, `expires_at` (aware ISO8601 matching the native token's
signed expiry), `product: "undertow"`, and `purpose: "public_mcp_gateway"`.
The native signing secret stays in the native environment; only the derived
token enters this file. Atomic replacement is read on the next request without
a restart. The renewal job checks the existing signature and owner identity,
then renews a 30-day token when seven days or less remain. The central reader
checks local format and expiry; the native service remains the authority for
signature and current entitlement. Configured invalid, unreadable, or expired
credentials fail closed instead of silently reverting to anonymous quota.
The monitor exposes only credential status and expiry, never the token, owner,
file path, or signing material. `ready` describes local credential readiness,
not independently verified native entitlement.

The ten-minute monitor still refreshes Undertow's ordinary observation clocks
each run. Native `trade_safety_exit_context` has a separate 60-call daily limit,
including the owner. Its monitor attempt is therefore reserved durably at most
once per hour in private `undertow-admission-cache.json` beside the report,
with a private advisory lock to exclude overlapping monitor processes. Failed
or cancelled requests consume the reservation. An ordinary day has at most
24 monitor proof attempts; other clients share the native identity's remaining
proof and per-minute capacity. Credential rotation starts a new cache identity.
This integration does not raise those limits or guarantee unlimited throughput.

Only an explicit native `pending_owner_review`, `denied`, or `restricted`
rights result can be reused, for less than one hour. A cached check preserves
its original `checked_at`, exposes `age_seconds`, `cached`,
`cache_ttl_seconds`, and `next_attempt_at`, and contains no cached quote clocks.
A positive admission is never replayed as fresh. Expired evidence, an invalid
cache, a failed refresh, or a pending hourly retry produces unknown admission;
it cannot turn a fresh observation clock into trading permission. A restriction
continues to win the top-level status while its bounded cached evidence is valid.
