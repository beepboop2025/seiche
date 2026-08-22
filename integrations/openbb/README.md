# Seiche for OpenBB

`openbb-seiche` brings Seiche's public, source-clocked funding-liquidity and
world-markets evidence into OpenBB. It installs both a provider and a router, so
the fetchers work independently and the commands appear under `obb.seiche`.

## Install

```bash
pip install openbb openbb-seiche
openbb-build
```

During development from the Seiche repository:

```bash
pip install -e integrations/openbb
openbb-build
```

## Use

```python
from openbb import obb

stress = obb.seiche.funding_stress(provider="seiche")
markets = obb.seiche.world_markets(selector="summary", provider="seiche")
china = obb.seiche.world_markets(selector="china_macro", provider="seiche")
health = obb.seiche.data_health(staleness="aging", provider="seiche")

print(stress.to_dataframe())
print(markets.to_dataframe())
print(health.to_dataframe())
```

The integration calls Seiche's anonymous, read-only hosted API. No API key is
required. Results retain generation/evidence clocks, status labels, source
links, and the research-not-advice boundary instead of presenting a derived
reading as a raw market quote.

For self-hosted development, set `SEICHE_OPENBB_BASE_URL` to an HTTPS origin (or
to exact loopback HTTP on `localhost`, `127.0.0.1`, or `::1`). The override must
be a bare origin without credentials, path, query, or fragment. Production
defaults to `https://api.seiche.info`; redirects, non-JSON responses, and
responses over 2 MB fail closed.

## Commands

| Command | Public Seiche contract | Output |
|---|---|---|
| `obb.seiche.funding_stress()` | `/api/gauge` | One current regime row with the public ensemble and turn context |
| `obb.seiche.world_markets()` | `/api/v2/world-markets` | Domain rows, a metadata-only China row, source rows, or one methodology row |
| `obb.seiche.data_health()` | `/api/health` | One row per source, filterable by source and staleness |

Seiche is a research and evidence tool, not a real-time quote service, execution
venue, or investment adviser. The source code is AGPL-3.0-or-later; upstream
data remains governed by its source terms.

`world_markets(selector="money_markets" | "forex" | "capital_markets")`
returns only that domain and keeps the bounded selected projection in
`details`. `selector="sources"` returns one row per official source-registry
entry; `selector="methodology"` returns the projection limits and evidence
classes. Summary and all-domain rows retain snapshot, evaluation, and evidence
clocks separately. A recent response never advances a source observation clock.

`selector="china_macro"` returns one `china_macro` row containing release-pinned
NBS series identities and, when available, owner-attested provenance. It is not
an economic time series: values, raw exports, history, gauge eligibility, and
scoring eligibility remain excluded. OpenBB exposes `knowledge_time` in its own
column and keeps both `as_of` and `selected_evidence_as_of` null for that row.
The same rule holds for the China row appended by `selector="all"`; it never
borrows the latest clock from the three market domains. Owner attestation proves
which export Seiche received, not that NBS digitally signed it or granted public
redistribution rights.
