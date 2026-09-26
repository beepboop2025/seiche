# Product analytical charts

The homepage funding observatory replaces the generic five-series economic
strip with ten funding-specific views. It also renders on the board-error
fallback so a board failure does not prevent independent series inspection.

| View | Published input or calculation |
| --- | --- |
| Overnight corridor | SOFR, TGCR, BGCR, EFFR and IORB; percent |
| Funding spreads | SOFR–IORB, EFFR–IORB and SOFR–TGCR; exact-date differences × 100 bp |
| Reserve balances | WRESBAL; USD million / 1,000 |
| Treasury cash | TGA_LONG; USD million / 1,000 |
| Overnight reverse repo | RRPONTSYD; USD billion |
| Corporate funding premiums | AA financial/nonfinancial 3M commercial paper minus DGS3M; exact-date differences × 100 bp |
| Treasury term structure | DGS3M, DGS2, DGS10 and DGS30 at their latest common observation date |
| SOFR repricing | Consecutive available SOFR observations; difference × 100 bp |
| Money-fund repo | Total repo, repo with the Fed, and repo with FICC; USD billions using the existing OFR series-wide unit rule; overlapping totals/components, never summed |
| Fed balance sheet | WALCL; USD million / 1,000 |

Only catalog members with redistributable JSON routes are fetched. The existing
series contract validates identity, units, order and observation dates before
display. Failed refreshes remove that input from calculations. No values are
carried across missing dates. Future-dated scheduled entries are omitted from
observed history; the source record remains linked. The 3-month and 1-year
controls use a common source-observation window. The maximum loaded history is
520 observations per source, not a claim to full lifetime history.

The three OFR money-fund repo routes currently declare the configured `$B` unit
but can return whole-dollar observations. `moneyFundBillions` is restricted to
their exact OFR source/remote identities and matches `assemble._vol_b`: divide
by 1e9 when the full loaded history has an absolute value above 1e6, otherwise
retain an already normalized series. This is the existing domain-specific
unit boundary, not a generic magnitude-based currency conversion. It runs
before history-window filtering and includes zero observations. Regression
fixtures check current dollar-scale observations, already-normalized history,
latest zero and mismatched provenance.

`frontend/src/research/analyticsCharts.ts` provides the shared SVG renderer. Its
plain JavaScript transpilation and identical stylesheet are vendored into the
LiquiLens and Undertow public-site repositories. Product adapters remain
separate: institution disclosures for LiquiLens, funding histories for Seiche,
and observed liquidity/exit snapshots for Undertow. A renderer change should
be propagated to both sites using TypeScript's ES2022 transpilation and checked
in all three browsers.

The renderer includes date/log/numeric axes, reference-to-zero bars, scatter
inspection, matrices and reconciled range bridges. Keyboard inspection, native
expanded-chart dialogs, CSV/SVG exports, source links and lazy accessible tables
are included. Mobile charts use a narrower coordinate layout rather than
shrinking the desktop chart's labels. CSV labels are formula-escaped.

Validation: `npm --prefix frontend test` and `npm --prefix frontend run build`.
The funding adapter tests cover exact-date joins, unit conversion, missingness,
common-date yield curves and future-observation exclusion. Publication remains
subject to the existing frontend receipt and sealed-data workflow in
`docs/FRONTEND-PUBLICATION.md`; these changes do not activate backend code or
publish new data.
