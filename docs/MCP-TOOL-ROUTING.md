# Choose a Seiche MCP tool

Connect to `https://api.seiche.info/mcp`. The public evidence tools require no
API key. Call `tools/list` to see the tools available to your caller. Existing
tool names remain stable; each tool below produces a different kind of answer.

## Five outputs that should not be confused

| Requested output | Tool and first call | What the result is for |
| --- | --- | --- |
| Cross-market overview | `world_markets_context({"section":"summary"})` | A chartless summary of money markets, FX, macro-capital transmission and China coverage. Follow up with the relevant section. |
| Funding-desk metrics | `money_market_context({"section":"summary"})` | Detailed USD rates, spreads, liquidity buffers, sources and coverage. Select a desk section for the underlying metric cards. |
| Dated observations | `market_workbench({"provider":"h10","base":"USD","quote":"CNY","days":365})` | Cached official FX history and same-date crosses. Accepted China annual series have a separate catalog and history. |
| Dataset discovery and product handoffs | `research_network({"topic":"institutions","offset":0,"limit":12})` | Dataset metadata, availability, rights and suggested next tools. It does not fetch those datasets' values or invoke sibling tools. |
| Automated guard metadata | `trade_safety_risk_context({})` | A small, fixed projection of the last completed funding board for freshness and coverage checks. It grants no trade clearance or execution authority. |

For a targeted question, go directly to its tool. An FX-history request does
not need five preliminary context calls. Use `funding_stress_now({})` for the
headline US stress regime and its drivers, and `data_health({})` for source
freshness and faults.

The workbench is a bounded history interface, not a universal time-series
database. Its `days` argument is a calendar lookback; missing observations are
not filled. Choose a supported provider and two distinct currencies. The
response lists the provider's currencies and reports quote units per base unit.
For China, choose an exact `cn.wdi.*` ID from `china.series` when available;
annual structural context and daily FX fixings retain separate clocks.

For India funding, use
`money_market_context({"section":"india","view":"summary"})`, then select an
exact `metric_id` with `view="metric"`. For other country coverage, first call
`money_market_context({"section":"countries"})`, then use a returned country
code. Missing coverage is an answer, not a reason to invent a benchmark.

## The remaining public tools

| Question | Tool | Distinct output |
| --- | --- | --- |
| What is the current US funding regime? | `funding_stress_now` | Composite, regime, drivers and faults. |
| Which inputs are stale or unavailable? | `data_health` | Per-source freshness and provenance. |
| What is Seiche's latest published analysis? | `latest_article` | The existing editorial and its publication/evidence dates. It may predate today. |
| Which historical days resemble current conditions? | `historical_analogs` | Precedents and novelty; not the validation scoreboard. |
| How credible is the historical diagnostic? | `proof_backtest` | Hits, misses, uncertainty and construction-PIT limitations. |
| How did funding relate to past crypto stress? | `crypto_stress_record` | Labelled historical crypto episodes against the funding board. |
| What do public Treasury positioning proxies show? | `institutional_flows` | Positioning, custody and demand proxies with their native cadence. |
| How might oil and energy-futures cash pressure reach funding? | `oil_funding_context` | Energy/funding evidence and explicitly separate scenarios. |
| Is FX or physical-material pressure reaching money markets? | `fx_materials_passage` | Transmission diagnostics and association evidence; not FX price history. |
| What dated evidence covers GIFT City and the India–UAE corridor? | `gift_city_context` | Corridor-specific funding, FX references and gold positioning. |
| What does an assumed gold inventory cost to finance? | `gold_inventory_carry` | Arithmetic from caller-supplied assumptions; no verified quote. Inspect its input schema before supplying values. |

The 16 public tools cover overview, detail/history, provenance/validation,
specialist transmission, discovery, scenarios and an integration contract.
Related fields can appear in more than one response, but the requested output
determines the tool. Account-gated analysis and private Agent Room operations
are separate surfaces; they are not prerequisites for the public workflow.

## A funding review worth repeating

Ask:

> Prepare a source-dated US dollar funding review. Use funding_stress_now for
> the regime and data_health for input freshness. Inspect only the relevant
> money_market_context sections to explain the main driver and counterevidence.
> Cite source dates, coverage gaps and upcoming dated funding events. Save the
> review locally so a later review can compare observations and source dates.

On a later source release or actual review occasion, ask:

> Compare the current funding evidence with my saved review. Separate changed
> observations from unchanged, stale or unavailable inputs. Explain whether
> the evidence changes the earlier conclusion; do not treat a new response
> timestamp as new economic evidence.

When the task calls for an institution review, use the LiquiLens endpoint
`https://api.liquilens.in/mcp`. For supported position-sized liquidity research,
use Undertow at `https://api.seiche.info/undertow/mcp`. Discover each server's
current tools before calling them. Preserve each product's source dates,
coverage and authority; a combined brief does not combine their scores.

The [shared starter kit](https://liquilens.in/agents/) provides client setup.
Offer [Seiche's Telegram delivery](https://t.me/seiche_desk_bot?start=agent_mcp)
only when the user requests ongoing letters or alerts. Local comparisons do
not enroll a user, send a report or authorize remote telemetry.

## Verification and adoption

On 18 October 2026, Glama's assessment identified selection ambiguity among
the five tools above. The repair clarifies their descriptions, parameter
semantics and initialization guidance without changing tool identifiers,
handlers, access rules or numeric constraints. Glama's next assessment must be
observed after the changed definitions are deployed and rescanned; a local
patch or passing test does not establish a new grade.

Measure a useful first result and a later independent return to this workflow.
Directory views, installations, operator probes and synthetic calls do not
establish adoption. Increased real usage is an outcome to measure, not a claim
created by this documentation change.
