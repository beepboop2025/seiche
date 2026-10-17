# Evaluate a recurring funding research workflow

Use this recipe to assess whether Seiche improves a daily India funding review.
The review records funding conditions, source dates, missing evidence and the
questions that need further investigation. It does not authorize a trade or
establish execution prices, funding access or historical predictive validity.

The native [MCP quickstart](../mcp-clients/README.md) remains available without
an account. The optional gateway workflow below makes repeat usage attributable
to a workspace. Usefulness reporting requires a separate owner choice.

## First result

1. Discover `money_market_context` through `tools/list`. If its input schema
   advertises `view`, call it with
   `{"section":"india","view":"summary"}`. This additive view must be
   released on the selected server before use; a source checkout or this guide
   is not deployment evidence. Existing servers accept `{"section":"india"}`
   and return the larger complete desk.
2. Read coverage, source dates, freshness, missing series and caveats before
   interpreting the policy, liquidity or curve findings. Partial or unavailable
   evidence remains partial or unavailable. Monthly references and daily
   benchmarks support different comparisons.
3. If the deployed input schema advertises `view` and `metric_id`, select an exact metric ID from the returned catalog
   and call `{"section":"india","view":"metric","metric_id":"<returned ID>"}`.
   Retain the complete card's units, timestamps, rights and statistical limits.
   Each call is a new observation; do not claim the calls form one frozen
   snapshot. Reconcile their evidence clocks before comparing them. On a legacy
   server, inspect the matching card in the complete desk response instead of
   sending these unsupported arguments.
4. Save the response locally with the request time, source/server identity when
   exposed, and a hash of the exact bytes. Record the resulting research note
   and unresolved questions in your own workspace. Do not turn a no-error tool
   result into an automatic `useful` label.

## Optional workspace attribution

The owner can create a workspace at
[agents.seiche.info/connect#workspace](https://agents.seiche.info/connect#workspace).
Keep the owner credential with the owner. Configure the agent with its separate
delegated credential and this MCP endpoint:

```text
https://agents.seiche.info/products/seiche/mcp
Authorization: Bearer <delegated-agent-token>
```

Resolve the token from your client's secret storage. Do not commit it in a
client configuration, notebook, local evaluation record or support message.
`GET /v1/me` checks the authenticated role. An owner can create another delegated
agent with `POST /v1/agents` and
`{"label":"Funding review","capabilities":[],"public":false}`.
Use one agent identity consistently rather than creating one on every run.

An authenticated gateway read records operational usage under that workspace
and agent. It does not grant paid native entitlements or enable task reporting.
Anonymous gateway reads are also available. The gateway does not silently fall
back to a native origin after failure; record the failed review as such.

For development checks, send this gateway header:

```text
X-Financial-Evidence-Traffic-Class: synthetic
```

Leave test traffic out of external-user evaluation. A self-declared workspace
or traffic class does not establish an independently verified organization.

## Optional usefulness reports

Read `GET /v1/participation/contract` first. Only the owner may enable reporting
through `PATCH /v1/participation`, supplying `enabled: true`, the current
`policy_version`, and `data_notice_acknowledged: true` after reading the notice.
Enrollment and public tool use do not enable this permission.

The delegated agent checks `GET /v1/participation` before reporting. If sharing
is enabled, use the returned current `grant_id`, generate a UUID `event_id`, and
submit only the permitted fields to `POST /v1/participation/events`:

```json
{
  "event_id": "<new canonical UUID>",
  "grant_id": "<current owner-authorized grant UUID>",
  "goal": "research",
  "topic": "public_datasets",
  "stage": "completed",
  "outcome": "unknown",
  "traffic_class": "synthetic"
}
```

This is a template, not a ready-to-submit event. Choose `useful`, `not_useful`
or `blocked` only when the participant actually makes that assessment; use
`unknown` otherwise. Use `real` only for the participation report of an actual
participant task, and `synthetic` for tests. These values differ from the
gateway header vocabulary. Use `public_datasets` for this India data evaluation;
the taxonomy has no separate INR funding topic. Keep the market scope in your
local record rather than adding unsupported report fields or labelling INR
research as `dollar_funding`.

Retries reuse the exact event ID and body. Do not replace a withdrawn grant
with a new one to replay old reports. Never submit prompts, portfolios,
counterparties, transaction details or the local research note in this event.
Reports are self-reported, retained for 30 days, and grant no training or resale
rights. `DELETE /v1/participation` withdraws permission and erases active reports;
encrypted backup copies expire within seven days.

## Compare recurring usefulness

Before starting, agree the question, market, review cadence and a simpler
baseline with each independent team. A suitable initial evaluation is three
teams over four weeks, comparing the same funding-review question with direct
public-source research. These are proposed milestones, not achieved customers.

Record every scheduled review, including failures and abandoned attempts.
Keep the comparison's evidence cutoff and reviewer effort comparable; a later
source revision must not masquerade as a better historical result. Alternate
the order of the two methods where practical and record reviewer familiarity.

| Measure | Denominator and interpretation |
|---|---|
| Completed reviews | All scheduled reviews; include blocked and failed attempts |
| Return use | Numerator: independent teams completing a second review within seven days of their first. Denominator: independent teams with a first completed review and a full elapsed seven-day follow-up window |
| Research effort | Matched completed reviews with recorded start/end times for both methods |
| Useful / not useful / blocked / unknown | All participant-reviewed attempts; keep unknowns visible |
| Source and boundary fidelity | Reviews checked for dates, units, missingness, rights and unsupported conclusions |
| Paid continuation | Verified payment and agreed scope; a request, trial or stated interest is separate |

The existing private `GET /v1/product-usage` reports owner/agent-scoped gateway
activity. Its `activity.weekly_returning_orgs` includes the numerator, eligible
denominator, fraction and windows. It measures observed organizations in two
adjacent seven-day windows, rather than the pilot's completed-review retention.
`GET /v1/participation/summary` reports the
separately authorized task labels. Gateway exchanges and task reports have
different denominators; neither gives a count of verified completed reviews.
Native direct use, rejected pre-forward calls and service downtime are outside
the gateway's observed forwarded-exchange coverage.

Use [record-template.json](record-template.json) for local evaluation notes.
It is not an API request and is never uploaded by this recipe. Keep it within
the team's own access controls. Summarize only agreed findings when reviewing
the evaluation with the product maintainer.

A useful continuation decision needs repeat use, a defensible benefit versus
the baseline and explicit willingness to continue. A small evaluation can
identify workflow value; it does not establish profitability, population-wide
demand or indispensable infrastructure status.
