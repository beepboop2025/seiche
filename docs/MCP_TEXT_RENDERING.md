# MCP text rendering

The MCP wave of the family presentation program, 12 November 2026. Every
Seiche MCP tool result that carries structured content gains one text block,
appended after the serialized JSON block, that presents its readings the way
the evidence presentation contract presents them on every surface. This file
records the audit behind it (what each tool serves, where each reading's slots
live, which native values the contract does not map) and what the block
renders.

- Contract: liquilens-site `main` at `7a9d751`,
  `docs/design/EVIDENCE-PRESENTATION-CONTRACT.md` version 1.1 (sections 2 to 5
  and section 7) and `docs/design/PORTABLE-SURFACES.md`, section 2 and its MCP
  subsection.
- Presenter: `backend/seiche/mcp_text/vendor/present.py`, the family's shared
  presenter copied byte for byte from `beepboop2025/liquilens-evidence-carrier`
  pull request 84 at `a274864c`. `vendor.PROVENANCE` records its commit, size
  and SHA-256, and a test fails on any byte difference. The record is a Python
  constant because the release artifact verifier admits only reviewed
  non-Python package members. Never edit the copy: copy the source again and
  record the new commit and digest together.
- Code read: the 0.16.13 release head `e3dc734c`
  (`backend/seiche/mcp_server.py`, `public_rights.py`, `public_faults.py`,
  `markets/atlas.py`, `markets/world.py`, `market_workbench.py`,
  `gift_city.py`, `india_funding.py`, `india_context.py`,
  `country_funding.py`, `engines/money_market.py`, `sources/base.py`,
  `domain/observation.py`).
- Live samples: 9 `tools/call` requests to `https://api.seiche.info/mcp` at
  about 00:37 UTC on 12 November 2026, one per tool plus the India section. The
  gold calculator's inputs were synthetic. They were rendered through the
  adapters locally (not committed): every block held its cap, carried no en or
  em dash and printed only served numbers.

## Decisions

Spec owner decisions of 12 November 2026, as they land in Seiche:

1. `structuredContent` and the first text block stay byte-identical. The
   rendering is appended as one more text block.
2. The rendering is built only from the payload served, after
   `sanitize_public_fault_payload`. It states nothing that object lacks.
3. Resolution statuses are not display states: the India `metric` view's
   `selection_status: not_found` prints "No match for this name".
4. Seiche has no access gate; nothing in the block suggests one.
5. The block is capped at 4,096 bytes. See "Cap and order" below.
6. A slot the object lacks prints "Not stated". Boundary sentences
   (`boundary`, `disclaimer`, `clocks.boundary`) print verbatim, once per view.

- D1. `CALM`, `WATCH` and `ALARM` follow the Undertow-tier rule for LiquiLens.
  Seiche serves none of them; its desk regime words stay open (below).
- D2. Seiche's `ok: false` and `availability: unavailable` are Unavailable,
  with the producer's `reason` verbatim.
- D3. Replay verdicts are checks. Seiche serves none on the public surface.
- D5. A lookup miss prints the lookup notice (India `metric_id`).
- D6. No rendered value takes its unit from a key suffix: every value the block
  draws has a served `unit` field, so Seiche needs no suffix table. A value
  with neither would print "unit Not stated".
- D7. The presenter prints an en or em dash in producer text as a hyphen-minus.
- D8. Seiche words beyond its five, decided in this run: a word maps only when
  the producer's own code defines it in a way that clearly matches a contract
  state (next section). Every other word stays unmapped and fails closed.
- D9. Seiche has no MCP envelope budget and no public-face scrubber beyond
  `sanitize_public_fault_payload`, which already runs before both blocks are
  built.

## Words mapped beyond Seiche's five, with the producer's definitions

The presenter (`state_for("seiche", ...)`) gained four rows at `a274864c`:

| Word | Display state | Producer definition |
| --- | --- | --- |
| `stale` | Stale | `domain/observation.py` `StalenessState`; `sources/base.py` `Series.staleness` ("fresh, aging, stale, dead, measured against expected cadence": fresh within the grace, aging within twice it, stale within six times it); the same ladder in `market_workbench._pair` and the money-market `freshness.thresholds_days`. Stale is past the aging limit, so older than the display limit: contract rule 6, "the product's own stale rule". `_money_market_section_status` and `india_funding` (`_spread`, `_liquidity_change`) also use `stale` for a leg or section that is not current. |
| `STALE` | Stale | `markets/atlas.py` `_metric`: `status = effective_staleness.value.upper()`, the same `StalenessState` upper-cased. |
| `RESTRICTED` | Restricted | `markets/atlas.py` `_metric`: availability `RESTRICTED` when no publishable raw or context value exists and the instrument is not public (`_instrument_is_public`: an official-open source with redistribution allowed). Contract rule 4. |
| `UNAVAILABLE` | Unavailable | `markets/atlas.py` `_metric`: availability `UNAVAILABLE` when there is nothing to publish from a public source; `StalenessState.UNAVAILABLE` upper-cased; `public_faults._safe_status` upper-cases a kept `unavailable`. Contract rule 3. |

Two word sets keep a reading's own claim word and add no state, also by the
producer's definition:

- `fresh`, `aging`, `FRESH`, `AGING`: within the freshness limit. The
  money-market desk counts only fresh or aging cards as available
  (`coverage.rule`); `india_funding.CURRENT` and `country_funding` treat
  `{"FRESH", "AGING"}` as current.
- `available`, `AVAILABLE`: a value is present. Neither says whether it is
  observed or derived, so neither can draw a value state by itself.

## What the appended block renders

- Code: `backend/seiche/mcp_text/` (`adapters.py` maps a served result to
  readings, `render.py` assembles the block) and two hooks in
  `backend/seiche/mcp_server.py` `_handle_tools_call`: the success path and the
  legacy refusal. Tests: `backend/tests/test_mcp_text_rendering.py`.
- An item is drawn only when mapped words decide its state, and a value state
  also needs a served value. A gap from either the freshness word or the claim
  word wins, earliest contract rule first, with the producer's reason. A word
  with no row is printed once, verbatim, on the line "Not mapped to a display
  state: ..." (as `<slot> <word>`), under no state, and the item is not drawn
  in a value state. A mapped gap beside an unmapped word is still drawn: the
  unmapped word could only name another gap, never a calm state.
- Clocks are served clocks only. The block never prints the render time; a
  gap's `checked` slot reads "Not stated" unless the result serves a clock.
- Producer text prints as served, with the presenter's one typographic
  normalisation (D7). No escape sequence is printed.

| Tool | Rendered | Drawn readings | Left to the JSON block |
| --- | --- | --- | --- |
| `source_rights_status` | yes | each `withheld_sources` entry: Restricted, its reason verbatim (`public_rights.WITHHELD_SOURCES`, the contract's "withheld by rights or policy"); limits `rule`; boundary `boundary` | tools, decisions, source notes |
| `data_health` | yes | provenance rows that are `stale` (Stale) or `availability: unavailable` (Unavailable), with period `asof`, known `knowledge_time` (capture), attribution, `freshness_basis` as limits, checked `generated_at`; fault and staleness words with no row go on the open line | current rows (no value is served) |
| `money_market_context` | yes | `ok: false` or a gap status (Unavailable, reason verbatim); regime indicators and section cards that are stale or unavailable; `coverage.stale_metrics` and `unavailable_metrics`; diagnostics when unavailable; India: `missing_series`, spreads, policy comparison, liquidity summary, fixed-tenor curve and every atlas card through the rules above; the India `metric` lookup miss as a notice; country desks' atlas cards | levels and percentiles without a claim word; regime word (open) |
| `world_markets_context` | yes | domain gaps from `coverage.domains`; inside a selected domain, every item whose `status` or `evidence_status` is one of the five words (world.py's own `STATUS_DEFINITIONS`), with its value, unit, period and known-at; boundary `disclaimer` plus `clocks.boundary` | domain summaries without a value, context blocks |
| `market_workbench` | yes | every forex row (and `china.fx` once) from `evidence_status` and `status`, with value, unit, period, publisher and attribution; `china.status` and `china.gaps` gaps with reasons | changes, history, accepted China series |
| `gift_city_context` | yes | funding rows (status, then `evidence_class` or `availability`), forex rows as in the workbench, gold positioning when stale or unavailable | funding values whose only claim word is `AVAILABLE` (open) |
| `research_network` | yes | a failed catalog read (Unavailable, reason verbatim); boundary `boundary` | dataset metadata (`values_included: false`) |
| `gold_inventory_carry` | yes | none: `status: scenario` goes on the open line; limits `assumptions` | the scenario outputs |
| 14 legacy tool names | yes | `public_rights.legacy_unavailable`: Unavailable (D2), the reason verbatim | rights fields |
| 5 `agent_room_*` tools | skipped | signed documents for an authenticated caller, not evidence readings; the JSON block carries them exactly as signed | everything |
| tool failures (`ToolError`, `StructuredToolError`, unexpected) | skipped | a bad request or an internal failure is not a reading | everything |

The daily-allowance refusal in `api.py` (`_mcp_quota_result`) is not a tool
result and keeps its single block. It does not ask for a key, so it is not the
contract's access refusal; whether it should print Restricted is open.

## Cap and order

At most 4,096 bytes (`render.BLOCK_CAP_BYTES`, pinned by a test). The shared
presenter lays the readings out (`render_text(..., max_bytes=...)`): a count
per display state, every gap reading, then the rest in served order, and "Full
list in the JSON block." when any reading is cut. Ahead of it the block prints
the lookup notices, the unmapped-word line, the limits and the pointer line
"Not every served item is rendered here; the JSON block has the full result."
Nothing is cut before a gap reading: the limits give way first, then the
unmapped-word line, and calm readings are cut before either.

## 1. The envelope

| Outcome | `content` | `structuredContent` | `isError` |
| --- | --- | --- | --- |
| Success | the JSON block (`json.dumps(payload, indent=2, default=str)`, unchanged) then the contract block | `payload` when it is an object | absent |
| Success, string payload | the string, unchanged; no block | absent | absent |
| `StructuredToolError` | "ERROR: " plus the payload's `reason`; no block | the error payload | `true` |
| `ToolError` | "ERROR: " plus the sanitized reason; no block | `{ok: false, status: "FAILED", category, reason}` | `true` |
| Unexpected exception | "ERROR: internal tool failure"; no block | the safe failure envelope | `true` |
| Legacy tool name | the payload's `reason`, unchanged, then the contract block | `public_rights.legacy_unavailable(name)` | `true` |
| Unknown tool, bad arguments | none: JSON-RPC error | none | none |

`tools/list` is unchanged: no description, schema or annotation moved (0 bytes).
Every tool's `outputSchema` describes `structuredContent`, which is unchanged.
The block is a plain `{type: "text", text}` object, so the agent-network
freshness reader (`services/agent-network/.../freshness.py`) keeps reading
`structuredContent` first and skips text that is not JSON.

## 2. Tools and reading paths

Abbreviations: V value, U unit, P period, K known-at, S source, R rights,
L limits. "None" means no such path, so the slot prints Not stated.

| Tool | Reading paths |
| --- | --- |
| `source_rights_status` | The release boundary. Each `withheld_sources.<id>` is a withheld source with its reason; L `rule`; boundary `boundary` |
| `data_health` | Clocks and provenance per series, no value. P `asof`; K `knowledge_time` (capture); S `attribution`; R `redistribution_status`, `rights_basis`; L `freshness_basis` |
| `money_market_context` | V `regime.indicators[].value`; U `.unit`; P `.asof`; K none per indicator; L `caveats`. India: V `spreads[].value_bp` (not drawn while its status is open); P `asof`; L India `caveats` |
| `world_markets_context` | Domain states are Seiche's five words. Selected items: V `value`, U `unit`, P `as_of` or `asof`, K `knowledge_time`; boundary `disclaimer`, `clocks.boundary` |
| `market_workbench` | V the rate; U `unit`; P `as_of`; S `sources[].publisher`; attribution `sources[].attribution`; China gaps with `reason` |
| `gift_city_context` | V `funding[].value`; U `funding[].unit`; P `as_of`; K `evidence.knowledge_time` (capture); S `source`; forex rows as the workbench |
| `research_network` | Dataset metadata only (`values_included: false`). Boundary `boundary` |
| `gold_inventory_carry` | Arithmetic on caller inputs. L `assumptions` |

## 3. Words with no display state (open, for the owner)

Each prints on the unmapped-word line and draws nothing calm:

| Word | Where | Why it stays open |
| --- | --- | --- |
| `dead`, `DEAD` | `data_health` staleness, workbench and atlas status | `Series.staleness` uses it both for "no observation" (Unavailable) and "older than six times the grace" (Stale); the workbench uses it only for the second. One word, two contract states. |
| `unknown`, `UNKNOWN` | staleness, atlas status | no producer definition that matches a state |
| `AVAILABLE`, `DERIVED_CONTEXT` | atlas `availability` (GIFT funding, India cards, missing series) | presence of a raw or context value; neither says observed or derived. `markets/world.py` `_observed_or_derived` decides that only inside world markets. |
| `available` | money-market cards, India spreads and liquidity summary | presence; the India spread value is Seiche's own arithmetic, but the word does not say so |
| `partial`, `partial_or_unknown`, `partial_release` | tool, section and summary statuses | coverage of a response, not a reading (GIFT serves `partial` whenever anything is present) |
| `historical_reference`, `dated_reference`, `unaligned`, `benchmark_roll` | India spreads and policy comparison | no clear match; `historical_reference` is a monthly report, not past a display limit |
| `insufficient_evidence`, `consistent_with`, `not_confirmed` | India `mechanism` | a hypothesis assessment, closer to a check than a reading |
| `NORMAL`, `WATCH`, `STRAIN`, `STRESS`, `CANNOT_ASSESS` | money-market `regime.state` | a severity; the contract gives Seiche no severity channel |
| `scenario` | `gold_inventory_carry` | arithmetic over caller assumptions (S8, U6) |
| `FAILED` (and other sanitized fault statuses) | `data_health.faults[].status` | the producer describes faults as "stale or unreachable": two states |
| indicator rows with a value and a freshness word but no claim word | money-market `regime.indicators[]`, section cards | S6: Observed for the level is plausible but not served |

## 4. Producer fixes requested

- Strings that carry an en or em dash (D7), printed with a hyphen-minus until
  cleaned at source: `data_health.reading`; `market_workbench`
  `china.gaps[].label`; the India `headline`, `plain_language` and
  `curve.fixed_tenor_reason`.
- A claim word beside atlas values (`observed` or `derived`) would let GIFT
  funding rows and India cards draw their values; today only `AVAILABLE` is
  served.

## 5. Release lane

Any `backend/` merge stops the nightly export until a signed controller
install. This change is built on the 0.16.13 release head `e3dc734c` and waits
for the next signed Seiche release after 0.16.13. Pushing `main` deploys; the
pull request stays a draft until the release owner takes it.
