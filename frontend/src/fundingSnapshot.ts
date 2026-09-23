type RecordValue = Record<string, unknown>;
const record = (value: unknown): value is RecordValue => value !== null && typeof value === "object" && !Array.isArray(value);
const clock = (value: unknown): string | null => typeof value === "string" && /^\d{4}-\d{2}-\d{2}(?:T.+)?$/.test(value) && Number.isFinite(Date.parse(value)) ? value : null;

/** A small view of the published envelope; absent observations never become zero. */
export function fundingPreview(payload: unknown) {
  if (!record(payload) || !record(payload.headline)) throw new Error("The published funding snapshot is unavailable.");
  const headline = payload.headline;
  const definitions = [
    { key: "sofr_pct", label: "Overnight funding", name: "SOFR", unit: "%" },
    { key: "reserves_b", label: "Bank reserves", name: "Reserve balances", unit: "USD bn" },
    { key: "srf_accepted_b", label: "Repo facility use", name: "Standing Repo Facility", unit: "USD bn" },
  ];
  return {
    generatedAt: clock(payload.generated_at),
    faultsReported: Array.isArray(payload.faults) ? payload.faults.length : null,
    observations: definitions.map((definition) => {
      const row = headline[definition.key];
      return { ...definition, value: record(row) && typeof row.value === "number" && Number.isFinite(row.value) ? row.value : null, observedAt: record(row) ? clock(row.asof) : null };
    }),
  };
}
