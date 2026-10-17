# Money Market Desk v1

Public data definitions and interpretation limits. Current coverage is reported
by the live API and source ledger; missing or unavailable observations retain
their explicit status.

## Metric contract

Every metric is a typed observation, not a naked scalar:

```json
{
  "id": "sofr_iorb_spread",
  "label": "SOFR minus IORB",
  "value": -4.0,
  "unit": "bp",
  "asof": "2026-08-20",
  "published_at": null,
  "expected_next_update": "next New York business day, about 08:00 ET",
  "cadence": "daily, T+1",
  "source": "Federal Reserve Bank of New York + Federal Reserve Board",
  "source_url": "https://www.newyorkfed.org/markets/reference-rates/sofr",
  "source_tier": "official",
  "revision_status": "latest known vintage",
  "formula": "100 * (SOFR_pct - IORB_pct)",
  "formula_version": "mm.usd.spread.v1",
  "alignment": "same observation date; no cross-clock fill",
  "change_1d": 1.0,
  "change_5d": -2.0,
  "change_20d": 0.0,
  "robust_z_1y": 0.35,
  "percentile_3y": 54.2,
  "confidence": "high",
  "status": "normal",
  "explanation": "Positive values mean secured overnight cash cleared above the Fed's reserve rate."
}
```

The canonical v2 observation store remains the long-run source of truth. The
v1 desk initially derives from the already collected, cached source series so
it can ship without weakening the sealed market-pack path. Migration is
complete only when the desk can be reconstructed from bitemporal canonical
observations with these clocks:

- `event_time`: the market or accounting period measured;
- `source_publication_time`: when the publisher released the value;
- `knowledge_time`: when Seiche could first have known it;
- `revision_id`: the exact source revision or content identity;
- `sealed_at`: when Seiche bound the product to its evidence record.

## Quant definitions

### Policy-relative rates

For rate `r` quoted in percent and policy anchor `a` quoted in percent:

```text
spread_bp(r, a) = 100 * (r - a)
```

Core spreads are EFFR-IORB, SOFR-IORB, SOFR-EFFR, SOFR-TGCR, BGCR-TGCR,
DVP-repo minus tri-party repo, and financial/nonfinancial CP minus a matched
3-month Treasury rate. Two observations are combined only on an exact common
event date unless a metric explicitly declares an as-of join.

### SOFR distribution

```text
IQR_bp       = 100 * (P75 - P25)
upper_tail_bp = 100 * (P99 - median)
full_tail_bp  = 100 * (P99 - P1)
tail_skew     = (P99 - median) / max(median - P1, epsilon)
```

SOFR is a broad secured overnight benchmark, not a pure general-collateral
rate. Tail measures are distribution diagnostics, not direct probabilities of
default or a claim about a single venue.

### Empirical context

For a current value `x_t` and a trailing sample known at `t`:

```text
percentile_3y = 100 * empirical_CDF_last_3_native_years(x_t)
robust_z_1y   = (x_t - median_last_native_year)
                / (1.4826 * MAD_last_native_year)
```

If the median absolute deviation is zero and the latest value equals the
median, robust z is zero; otherwise it is unavailable. Expanding
or trailing calculations must be recomputed on truncated samples in tests to
prove that a past row does not change when future data arrives. A native year
is 252 observations for a daily business-day series and 52 for a weekly
series; no slow series is padded to reach a daily row count.

### Repo venue decomposition

```text
cleared_premium_bp = 100 * (DVP_overnight_rate - tri_party_overnight_rate)
venue_share        = venue_volume / sum(available_venue_volumes)
volume_change_pct  = 100 * (volume_t / volume_t-k - 1)
```

Preliminary OFR repo observations retain their preliminary label. Aggregate
changes and ranks use only dates with the same reported component set as the
latest eligible date. A missing venue therefore reduces coverage and changes
the comparison mask; it is never silently treated as zero or allowed to create
a mechanical move in the total.

### Liquidity balances

Balance-sheet stocks are not added into an undocumented “net liquidity”
number. The desk shows each stock and its weekly change. A later accounting
bridge may publish:

```text
delta_reserves ~= delta_Fed_assets
                 - delta_currency
                 - delta_TGA
                 - delta_reverse_repo_liabilities
                 - delta_other_liabilities_and_capital
```

The residual must be displayed, the sign convention must be tested, and the
bridge must be labeled an accounting reconciliation rather than a forecast.

### Descriptive regime

The desk regime is context-only and must never feed the production composite.
Each eligible stress-oriented metric first maps to a raw trailing empirical
percentile. With `m` non-stale eligible channels, Seiche then applies the
dependence-robust Bonferroni upper-tail adjustment:

```text
adjusted_tail_probability = min(1, m * (1 - raw_percentile / 100))
adjusted_percentile       = 100 * (1 - adjusted_tail_probability)
```

The headline thresholds apply to the adjusted percentile:

- `NORMAL`: below the 75th percentile;
- `WATCH`: 75th to below 90th;
- `STRAIN`: 90th to below 97.5th;
- `STRESS`: 97.5th or above.

The page reports the selected component, its raw and adjusted ranks, family
size, sample length, and the counter-case. Reserve balances and ON-RRP remain
visible plumbing context but do not enter the headline selector because slow
policy-driven stock depletion is not itself a calibrated funding dislocation.
Freshness is measured against an
explicit live-response or historical-replay evaluation date, never against the
latest row inside the same frozen snapshot. Stale observations stay visible as
historical context but cannot set the regime; if every rankable component is
stale, the state is `CANNOT_ASSESS`. Metrics whose economic stress direction is
negative invert their percentile explicitly. The adjustment controls a family
of upper-tail flags under arbitrary dependence; it does not turn an empirical
rank into an event probability. Thresholds are configuration, not statistical
truth, and must be versioned before they affect alerts.

## Reliability and data quality

Quality gates run before publication:

- schema and identifier validation;
- unit and sign-convention validation;
- monotonic event/publication/knowledge clocks;
- duplicate and revision checks;
- expected-cadence and holiday-aware freshness;
- rate bounds, non-negative volumes, and balance scale checks;
- exact-date alignment tests for spreads;
- preliminary/final status preservation;
- distribution ordering `P1 <= P25 <= median <= P75 <= P99`;
- deterministic JSON serialization with no NaN or infinity;
- truncation equality for every historical derived row;
- public redistribution-policy enforcement.

Operational targets after the canonical v2 product ships:

- 99.9% monthly availability for cached public reads;
- p95 cached API latency below 250 ms;
- official-source update visible within 15 minutes of successful collection;
- stale state shown within one missed expected publication window;
- no silent use of a last-known-good value after its stale threshold;
- raw capture, normalized observation, and published product hashes retained
  for every release.

## Security and legal controls

- source credentials are server-side secrets and never enter static payloads;
- public routes are allowlisted in the curated OpenAPI document;
- query limits, payload bounds, timeouts, and cursor validation apply to
  series and search endpoints;
- source terms and redistribution status live with each adapter: `derived_only`
  can expose approved non-reversible statistics while withholding raw values,
  whereas `prohibited` omits the adapter, source metadata, values, and faults
  from public payloads entirely;
- New York Fed attribution and non-affiliation notices remain visible;
- CME, DTCC, ICI, and other licensed data remain disabled until the exact
  display, derived-data, and API rights are documented;
- the terminal states “Research data, not investment advice.”
