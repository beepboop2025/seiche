// Synthetic records for behavior tests; never part of a product data build.
export function chinaRow(overrides = {}) {
  return {
    series_id: "cn.wdi.broad_money_growth", label: "Broad money growth", value: 8.5, unit: "annual %",
    period_start: "2025-01-01", period_end: "2025-12-31", frequency: "A",
    released_at: "2026-08-01T00:00:00Z", collected_at: "2026-09-01T10:00:00Z", accepted_at: "2026-09-08T00:00:00Z",
    source_id: "world_bank_wdi", evidence_url: "https://api.worldbank.org/v2/country/CHN/indicator/FM.LBL.BMNY.ZG",
    market_channels: ["money_market"], revision: 1, observation_id: "synthetic-2025", metadata: { source_series_id: "FM.LBL.BMNY.ZG" }, ...overrides,
  };
}
export function fxRow(quote = "CNY", overrides = {}) {
  return {
    pair: `USD/${quote}`, base_currency: "USD", quote_currency: quote, value: 7.2,
    as_of: "2026-09-08", status: "fresh", evidence_status: "observed", unit: `${quote} per USD`,
    change_1obs_pct: -0.2, change_5obs_pct: 1.1, change_20obs_pct: 2.2, change_60obs_pct: null,
    realized_vol_20obs_pct: 4.1, observation_count: 3,
    sources: [{ mnemonic: quote, source_id: "DEXCHUS", source_url: "https://fred.stlouisfed.org/series/DEXCHUS", raw_unit: quote, fetched_at: "2026-09-09T01:00:00Z" }], reason: null, ...overrides,
  };
}
export function payload(selection = {}) {
  const selected = { provider: "h10", base: "USD", quote: "CNY", days: 365, china_series: "", ...selection };
  const currencies = selected.provider === "ecb" ? ["USD", "EUR", "CNY", "HUF"] : ["USD", "EUR", "CNY"];
  const rows = currencies.filter((quote) => quote !== selected.base).map((quote) => fxRow(quote, {
    pair: `${selected.base}/${quote}`, base_currency: selected.base, unit: `${quote} per ${selected.base}`,
    evidence_status: selected.base === "USD" ? "observed" : "derived",
  }));
  const series = [chinaRow(), chinaRow({ series_id: "cn.wdi.reserves_months_imports", label: "Reserves in months of imports", value: 12.8, unit: "months" })];
  const chosen = series.find((row) => row.series_id === selected.china_series) ?? series[0];
  return {
    schema: "seiche.market-workbench.v1", generated_at: "2026-09-09T12:00:00Z", selection: selected,
    forex: {
      base_currency: selected.base, quote_currency: selected.quote, quote_convention: "quote currency units per one base currency", reference_only: true,
      currencies, rows, history: [{ date: "2026-09-04", value: 7 }, { date: "2026-09-07", value: 7.1 }, { date: "2026-09-08", value: 7.2 }],
      coverage: { declared_pairs: rows.length, available_pairs: rows.length, missing_pairs: 0, returned_observations: 3 },
      methodology: ["Synthetic test data. Exact-date reference crosses; no forward filling."],
    },
    china: {
      status: "structural", reason: null,
      economic_context: { schema: "seiche.palimpsest-china-economic-context.v1", context_only: true, scoring_eligible: false, publication_status: "provisional", rights: { decision: "allowed", attribution: "Synthetic World Bank test fixture", license: "CC BY 4.0" }, clocks: { latest_observation_period_end: "2025-12-31", seiche_accepted_at: "2026-09-08T00:00:00Z" } },
      series, selected_series: chosen.series_id,
      history: [chinaRow({ ...chosen, period_start: "2024-01-01", period_end: "2024-12-31", value: 9.5 }), chosen],
      channels: [{ id: "funding", title: "Domestic money and credit", reading: "Annual money growth provides structural funding context.", series_ids: ["cn.wdi.broad_money_growth"], available_series: 1 }],
      gaps: [{ id: "cnh", label: "Offshore CNH", status: "unavailable", reason: "No matching offshore quote." }],
      fx: fxRow(),
    },
  };
}
