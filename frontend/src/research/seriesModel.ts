import {dateOnly, record} from "./core";

export interface Observation {date: string; value: number | null}
export interface EconomicSeries {
  key: string;
  label: string;
  unit: string;
  points: Observation[];
  asOf: string;
  state: string;
  cadence: string;
  lag: string;
  source: string;
  remote: string;
  sourceUrl?: string;
  /** A citation the source asks for wherever its values are shown, with its https terms. */
  attribution?: string;
  terms?: string;
}

/** Validate the catalog and history together before displaying any number. */
export function seriesModel(payload: unknown, entry: unknown): EconomicSeries {
  const p = record(payload) && record(payload.provenance) ? payload.provenance : null;
  const asOf = p && dateOnly(p.asof);
  if (!record(payload) || !record(entry) || entry.available !== true || entry.csv_restricted ||
      typeof entry.json !== "string" || !p || p.mnemonic !== entry.mnemonic ||
      typeof p.mnemonic !== "string" || typeof p.unit !== "string" || p.unit !== entry.unit ||
      !asOf || !Array.isArray(payload.points) || payload.points.length > 2000) {
    throw new Error("The series metadata could not be verified.");
  }
  let previous = "";
  const points = payload.points.map((point: unknown): Observation => {
    const date = Array.isArray(point) && dateOnly(point[0]);
    if (!Array.isArray(point) || point.length !== 2 || !date || date <= previous || (date > asOf && point[1] !== null) ||
        (point[1] !== null && (typeof point[1] !== "number" || !Number.isFinite(point[1])))) {
      throw new Error("The observation history could not be verified.");
    }
    previous = date;
    return {date, value: point[1]};
  });
  if (!points.some(point => point.date === asOf && point.value !== null)) {
    throw new Error("The latest observation is absent from the history.");
  }
  const text = (value: unknown, fallback = "Not supplied") => typeof value === "string" ? value : fallback;
  return {key: p.mnemonic, label: text(entry.label, p.mnemonic), unit: p.unit, points: points.filter(point => point.date <= asOf), asOf,
    state: text(p.staleness), cadence: text(entry.cadence), lag: text(entry.native_lag),
    source: text(p.source, ""), remote: text(p.remote_id, "")};
}
