export type RvCoverageRecord = {
  status?: string;
  usable_rows?: number;
  total_rows?: number;
  coverage_unit?: string;
};

export function rvQualityLabel(record: RvCoverageRecord | null | undefined): string | null {
  if (!record || record.status === "complete") return null;
  const status = record.status === "partial" ? "PARTIAL" : "UNAVAILABLE";
  const usable = Number.isFinite(record.usable_rows) ? Number(record.usable_rows) : null;
  const total = Number.isFinite(record.total_rows) ? Number(record.total_rows) : null;
  const unit = record.coverage_unit === "expected_contracts" ? "contracts" : "rows";
  return usable != null && total != null
    ? `${status} · ${usable}/${total} ${unit}`
    : status;
}

export function rvMetricQualityLabel(
  engine: Record<string, any> | null | undefined,
  metric: string,
): string | null {
  return rvQualityLabel(engine?.metric_coverage?.[metric]);
}
