# Seiche

[![sealed record](https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.seiche.info%2Fapi%2Fbadge%2Frecord)](https://api.seiche.info/api/notary)

Seiche is an open-source terminal for monitoring stress in US dollar funding markets. It combines public data on reserves, repo rates, Treasury settlement, dealer balance sheets, positioning, cross-market liquidity, and stablecoins into a live regime reading with source timestamps and reproducible backtests.

- Live dashboard: [seiche.info](https://seiche.info)
- API: [api.seiche.info](https://api.seiche.info)
- MCP endpoint: [`https://api.seiche.info/mcp`](https://api.seiche.info/mcp)
- License: [AGPL-3.0](LICENSE)

## What Seiche measures

Seiche is built for a specific question: is the dollar funding system becoming more fragile before that stress is obvious in broad market prices?

The system groups its measurements into five areas:

| Area | Examples |
|---|---|
| Funding conditions | SOFR relative to IORB, repo-rate tails, reserve scarcity, standing repo facility use |
| Treasury plumbing | Auction settlement, primary-dealer inventories, relative-value positioning, Treasury cash flows |
| Market transmission | Equity, credit, rates-volatility, crypto, and stablecoin signals |
| Forecasting and analogs | Historical analogs, event probabilities, calendar-based stress windows, ensemble disagreement |
| Verification | Point-in-time replay, walk-forward backtests, feed freshness, and an append-only record of published outputs |

The dashboard reports a 0–100 Seiche Index and a regime label: `CALM`, `EROSION`, `STRAIN`, or `STRESS`. Each reading includes its component values, source, observation date, and freshness state. A failed or stale feed reduces visible coverage instead of disappearing from the calculation without notice.

## Data sources

The default deployment uses free public sources and does not require market-data subscriptions:

- Federal Reserve Economic Data (FRED)
- Federal Reserve Bank of New York Markets data
- Office of Financial Research Short-term Funding Monitor
- US Treasury FiscalData
- Commodity Futures Trading Commission positioning data
- European Central Bank Data Portal
- DeFiLlama
- Coinbase Exchange public data

Individual series have different release schedules and revision policies. Seiche displays those lags with the reading. Daily public data is useful for structural and event monitoring, but it is not an intraday trading feed.

## Computation and AI boundaries

The core index, forecasts, historical analogs, backtests, and portfolio simulations are deterministic or statistical. They do not depend on a language model.

Seiche has two optional language-model features:

- The desk assistant explains a deterministic context pack assembled from the live board. Its input contains the values, sources, and as-of dates that an answer may cite. Without a configured model endpoint, the command returns that context pack directly.
- Navigator records one forward probability for each data day. It is evaluated only on observations published after the forecast. It has zero weight in the main ensemble until its forward record provides enough evidence to justify inclusion.

No model-generated value is used as source data. The public board remains usable when both optional features are disabled.

## Run locally

Seiche requires Python 3.12 or later and Node.js for the web interface.

```bash
git clone https://github.com/beepboop2025/seiche.git
cd seiche

# API and command-line tools
cd backend
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/uvicorn seiche.api:app --port 8787
```

In a second terminal:

```bash
cd seiche/frontend
npm install
npm run dev
```

The development interface runs at `http://localhost:5173` and proxies API requests to port 8787. The first data load fetches historical observations; later requests use a cadence-aware SQLite cache.

## Command-line interface

After installing the backend package, these commands expose the main workflows:

```bash
seiche pull               # refresh data and print the current index
seiche brief --save       # create and archive a desk brief
seiche alert              # evaluate alert rules once
seiche watch -i 1800      # refresh and evaluate alerts every 30 minutes
seiche replay 2019-09-12  # reconstruct the board as of a prior date
seiche backtest           # print the walk-forward validation summary
seiche ml                 # inspect the event-ranking model and calibration
seiche analogs            # find comparable historical trajectories
seiche swell              # show the six-week event-probability curve
seiche physics            # inspect damping, modes, determinism, and tail risk
seiche bathymetry         # inspect potential, relaxation, and first passage
seiche book               # show paper positions and the validation verdict
seiche ask "Why is the index elevated?"
seiche serve              # run the API and built web interface
seiche mcp                # run the MCP server over stdio
```

Alert state is deduplicated in SQLite. Set `SEICHE_WEBHOOK_URL` to send optional webhook notifications.

## Use Seiche from an MCP client

Seiche exposes a [Model Context Protocol](https://modelcontextprotocol.io) server for clients that need a structured funding-stress reading rather than raw macroeconomic series.

Local stdio setup:

```bash
claude mcp add seiche -- seiche-mcp
```

Hosted HTTP setup:

```bash
claude mcp add --transport http seiche https://api.seiche.info/mcp
```

The public endpoint lists only tools available to the caller. Its anonymous tools include:

| Tool | Returns |
|---|---|
| `funding_stress_now` | Current index, regime, components, and market divergence |
| `historical_analogs` | Similar historical states and their subsequent paths |
| `proof_backtest` | Walk-forward results, misses, and confidence intervals |
| `data_health` | Source, timestamp, and freshness status for each input |
| `crypto_stress_record` | Recorded crypto episodes aligned with funding conditions |
| `institutional_flows` | Public positioning and flow observations |

See [the developer page](https://seiche.info/developers.html) for a live tool runner and [docs/MCP.md](docs/MCP.md) for client configuration, access levels, metering, and the complete tool catalogue.

## Reproducibility and validation

Seiche separates a useful live reading from evidence that the reading has worked historically:

- Historical features use expanding or walk-forward calculations where applicable.
- Point-in-time replay reconstructs the board using observations available by the selected date.
- Backtests show base-rate comparisons, confidence intervals, missed episodes, and model self-demotion rules.
- The published forecast and paper-position record is hash-chained so later edits are detectable.
- Data-health output identifies stale and unavailable inputs before a reading is used.

Read [docs/GUIDE.md](docs/GUIDE.md) for the operator guide, [docs/RESEARCH.md](docs/RESEARCH.md) for methods and references, and [docs/attested-record.md](docs/attested-record.md) for record verification.

Run the test and build gates before submitting a change:

```bash
cd backend
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest

cd ../frontend
npm install
npm run build
```

## Scope and limitations

Seiche does not provide paid data, intraday ticks, supervisory information, or lender-level private exposures. Some public macroeconomic series are revised after publication; the interface labels final-vintage backtests and maintains a separate as-published record for forward evaluation. Outputs are research and monitoring tools, not investment advice.

## Related projects

- [LiquiLens](https://liquilens.in) monitors public signs of stress at banks, NBFCs, co-operatives, and microfinance lenders.
- [Undertow](https://liquilens-undertow.com) measures tradable market liquidity, depth, and estimated exit cost.
- [Palimpsest](https://palimpsest.info) publishes verifiable records for AI evaluations and censorship events.

Seiche is named for a standing wave in an enclosed body of water: pressure can accumulate before the movement is obvious at the edge.
