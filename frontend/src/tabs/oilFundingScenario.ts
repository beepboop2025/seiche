export interface Scenario {
  oilPrice: number;
  fundingRate: number | null;
  usdInr: number;
  tenorDays: number;
  storagePerDay: number;
  insuranceRate: number;
  forwardSpread: number;
  cargoBarrelsM: number;
  dailyThroughputMbd: number;
  voyageDays: number;
  baselineVoyageDays: number;
  hedgeBarrelsM: number;
  oilPriceChange: number;
  initialMarginRateChange: number;
  indiaImportMbd: number;
  indiaOilShock: number;
  rbiUsdSalesB: number;
  liquidityReplenishment: number;
  underRecoveryCroreDay: number;
  compensationLagDays: number;
  cpFundingShare: number;
}

export interface ScenarioOutputs {
  carry: {
    storage: number;
    financing: number | null;
    insurance: number;
    required: number | null;
    headroom: number | null;
  };
  trade: {
    cargoCredit: number;
    financingCost: number | null;
    inTransit: number;
    incremental: number;
    multiple: number;
  };
  margin: { variation: number; initial: number; sameDay: number };
  india: {
    annualImportUsd: number;
    annualImportInr: number;
    rbiGross: number;
    rbiUnreplenished: number;
    omcStock: number;
    omcCp: number;
  };
}

const finite = (value: unknown, fallback: number): number => {
  if (value == null || (typeof value === "string" && value.trim() === "")) return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

export const finiteOrNull = (value: unknown): number | null => {
  if (value == null || (typeof value === "string" && value.trim() === "")) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

export function initialScenario(engine: Record<string, any>): Scenario {
  const a = engine?.scenario?.assumptions ?? {};
  const live = engine?.live ?? {};
  return {
    oilPrice: finite(a.oil_price_usd_per_bbl ?? live.wti?.price_usd_per_bbl, 80),
    fundingRate: finiteOrNull(a.funding_rate_pct),
    usdInr: finite(a.usd_inr ?? live.inr?.per_usd, 84),
    tenorDays: finite(a.tenor_days, 90),
    storagePerDay: finite(a.storage_usd_per_bbl_day, 0.03),
    insuranceRate: finite(a.insurance_rate_pct, 0.5),
    forwardSpread: finite(a.forward_spread_usd_per_bbl, 0),
    cargoBarrelsM: finite(a.barrels_per_cargo_m, 2),
    dailyThroughputMbd: finite(a.daily_throughput_mbd, 0.2),
    voyageDays: finite(a.voyage_days, 45),
    baselineVoyageDays: finite(a.baseline_voyage_days, 15),
    hedgeBarrelsM: finite(a.net_short_hedge_m_bbl, 1),
    oilPriceChange: finite(a.oil_price_change_usd_per_bbl, 8),
    initialMarginRateChange: finite(a.initial_margin_rate_change_pct, 5),
    indiaImportMbd: finite(a.india_import_mbd, 5),
    indiaOilShock: finite(a.india_oil_shock_usd_per_bbl, 10),
    rbiUsdSalesB: finite(a.rbi_usd_sales_b, 2),
    liquidityReplenishment: finite(a.liquidity_replenishment_pct, 25),
    underRecoveryCroreDay: finite(a.under_recovery_inr_crore_day, 1000),
    compensationLagDays: finite(a.compensation_lag_days, 30),
    cpFundingShare: finite(a.cp_funding_share_pct, 40),
  };
}

export function calculateScenario(s: Scenario): ScenarioOutputs {
  const yearFraction = s.tenorDays / 365;
  const storage = s.storagePerDay * s.tenorDays;
  const financing = s.fundingRate == null
    ? null
    : s.oilPrice * (s.fundingRate / 100) * yearFraction;
  const insurance = s.oilPrice * (s.insuranceRate / 100) * yearFraction;
  const required = financing == null ? null : storage + financing + insurance;
  const cargoCredit = s.oilPrice * s.cargoBarrelsM * 1_000_000;
  const inTransit = s.oilPrice * s.dailyThroughputMbd * 1_000_000 * s.voyageDays;
  const baselineInTransit = s.oilPrice * s.dailyThroughputMbd * 1_000_000 * s.baselineVoyageDays;
  const variation = Math.max(0, s.hedgeBarrelsM * 1_000_000 * s.oilPriceChange);
  const initial = Math.abs(s.hedgeBarrelsM) * 1_000_000 * s.oilPrice * s.initialMarginRateChange / 100;
  const annualImportUsd = s.indiaImportMbd * 1_000_000 * s.indiaOilShock * 365;
  const rbiGross = s.rbiUsdSalesB * 1_000_000_000 * s.usdInr;
  const rbiUnreplenished = rbiGross * (1 - s.liquidityReplenishment / 100);
  const omcStock = s.underRecoveryCroreDay * 10_000_000 * s.compensationLagDays;

  return {
    carry: {
      storage,
      financing,
      insurance,
      required,
      headroom: required == null ? null : s.forwardSpread - required,
    },
    trade: {
      cargoCredit,
      financingCost: s.fundingRate == null
        ? null
        : cargoCredit * (s.fundingRate / 100) * s.voyageDays / 365,
      inTransit,
      incremental: inTransit - baselineInTransit,
      multiple: s.baselineVoyageDays > 0 ? s.voyageDays / s.baselineVoyageDays : 0,
    },
    margin: { variation, initial, sameDay: variation + initial },
    india: {
      annualImportUsd,
      annualImportInr: annualImportUsd * s.usdInr,
      rbiGross,
      rbiUnreplenished,
      omcStock,
      omcCp: omcStock * s.cpFundingShare / 100,
    },
  };
}
