/** Public corridor evidence and caller-owned gold funding scenarios. */
export type RecordValue = Record<string, unknown>;
export interface FundingRow {
  currency: string; label: string; instrument: string; value: string | null;
  unit: string; as_of: string | null; status: string; source: string;
  source_url: string | null; missed_publication_opportunities: number | null; reason: string;
}
export interface FxRow {
  pair: string; value: string | null; as_of: string | null; status: string;
  provider: string; quote_convention: string; source_url: string | null;
}
export interface GiftCityData {
  schema: string; generated_at: string; status: string; funding: FundingRow[];
  forex: FxRow[]; uae_attribution: string; positioning: RecordValue; sources: { title: string; url: string }[];
  methodology: string[]; raw: RecordValue;
}
export interface GoldInputs {
  quantity_kg: string; fineness: string; price_usd_per_oz: string;
  annual_rate_pct: string; days: number; fx_inr_per_usd: string; fees_usd: string; day_count: number;
}
export type GoldForm = { [K in keyof GoldInputs]: string };
export interface GoldResult { schema: string; status: string; inputs: RecordValue; outputs: Record<string, string>; assumptions: string[]; raw: RecordValue }

const DECIMAL = /^-?(?:\d+(?:\.\d*)?|\.\d+)$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const TEXT = (value: unknown, fallback = ""): string => typeof value === "string" ? value : fallback;
const object = (value: unknown, name: string): RecordValue => {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`Invalid ${name} response.`);
  return value as RecordValue;
};
const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
function decimal(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if ((typeof value !== "string" && typeof value !== "number") || !DECIMAL.test(String(value)) || !Number.isFinite(Number(value))) throw new Error("Invalid numeric evidence.");
  return String(value);
}
function date(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string" || !DATE.test(value) || !Number.isFinite(Date.parse(value)) || new Date(value).toISOString().slice(0, 10) !== value) throw new Error("Invalid observation date.");
  return value;
}
export function safeSourceUrl(value: unknown): string | null {
  try {
    if (typeof value !== "string") return null;
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}
export function normalizeGiftCity(value: unknown): GiftCityData {
  const raw = object(value, "GIFT City");
  if (raw.schema !== "seiche.gift-city.v1" || !Array.isArray(raw.funding) || raw.funding.length > 20) throw new Error("The GIFT City evidence contract is unavailable.");
  const forex = object(raw.forex, "FX");
  if (!Array.isArray(forex.rows) || forex.rows.length > 100) throw new Error("The FX reference contract is unavailable.");
  return {
    schema: raw.schema, generated_at: TEXT(raw.generated_at), status: TEXT(raw.status, "unavailable"), raw,
    uae_attribution: forex.uae_capture && typeof forex.uae_capture === "object"
      ? TEXT((forex.uae_capture as RecordValue).attribution) : "",
    funding: raw.funding.map((value) => {
      const r = object(value, "funding row");
      if (!/^[A-Z]{3}$/.test(TEXT(r.currency))) throw new Error("Invalid funding currency.");
      const missed = r.missed_publication_opportunities;
      return { currency: TEXT(r.currency), label: TEXT(r.label, TEXT(r.currency)), instrument: TEXT(r.instrument), value: decimal(r.value),
        unit: TEXT(r.unit, "%"), as_of: date(r.as_of), status: TEXT(r.status, "unavailable"), source: TEXT(r.source), source_url: safeSourceUrl(r.source_url),
        missed_publication_opportunities: typeof missed === "number" && Number.isInteger(missed) && missed >= 0 ? missed : null, reason: TEXT(r.reason) };
    }),
    forex: forex.rows.map((value) => {
      const r = object(value, "FX row");
      if (!/^[A-Z]{3}\/[A-Z]{3}$/.test(TEXT(r.pair))) throw new Error("Invalid FX pair.");
      const sources = Array.isArray(r.sources) ? r.sources : [];
      const source = sources[0] && typeof sources[0] === "object" ? sources[0] as RecordValue : {};
      return { pair: TEXT(r.pair), value: decimal(r.value), as_of: date(r.as_of), status: TEXT(r.status, "unavailable"),
        provider: TEXT(r.provider, TEXT(r.source, TEXT(source.source_id, "Reference source"))),
        quote_convention: TEXT(r.quote_convention, TEXT(r.unit)), source_url: safeSourceUrl(r.source_url ?? source.source_url) };
    }),
    positioning: raw.gold && typeof raw.gold === "object" && !Array.isArray(raw.gold)
      && (raw.gold as RecordValue).positioning && typeof (raw.gold as RecordValue).positioning === "object"
      ? object((raw.gold as RecordValue).positioning, "gold positioning") : {},
    sources: (Array.isArray(raw.sources) ? raw.sources : []).flatMap((value) => {
      if (!value || typeof value !== "object") return [];
      const s = value as RecordValue, url = safeSourceUrl(s.url ?? s.source_url);
      return url ? [{ title: TEXT(s.title, TEXT(s.label, "Official source")), url }] : [];
    }), methodology: strings(raw.methodology),
  };
}

export function goldInputs(form: GoldForm): GoldInputs {
  const numeric = (key: keyof GoldForm, low: number, high: number, positive = false): string => {
    const value = form[key].trim();
    if (value.length > 25 || !/^[0-9]{1,12}(\.[0-9]{1,12})?$/.test(value) || !Number.isFinite(Number(value)) || Number(value) < low || Number(value) > high || (positive && Number(value) <= 0))
      throw new Error(`Enter a valid ${key.replaceAll("_", " ")} (${positive ? "greater than " : "at least "}${low}, up to ${high}).`);
    return value;
  };
  if (!/^\d+$/.test(form.days) || Number(form.days) < 0 || Number(form.days) > 3660) throw new Error("Funding days must be a whole number from 0 to 3660.");
  if (!["360", "365"].includes(form.day_count)) throw new Error("Choose ACT/360 or ACT/365.");
  return { quantity_kg: numeric("quantity_kg", 0, 1e12, true), fineness: numeric("fineness", 0, 1, true),
    price_usd_per_oz: numeric("price_usd_per_oz", 0, 1e12, true), annual_rate_pct: numeric("annual_rate_pct", 0, 1000),
    days: Number(form.days), fx_inr_per_usd: numeric("fx_inr_per_usd", 0, 1e12, true), fees_usd: numeric("fees_usd", 0, 1e12), day_count: Number(form.day_count) };
}
export function normalizeGoldResult(value: unknown, expected?: GoldInputs): GoldResult {
  const raw = object(value, "gold scenario");
  if (raw.schema !== "seiche.gold-carry.v1" || raw.status !== "scenario") throw new Error("The gold calculation did not return a scenario contract.");
  const outputs = object(raw.outputs, "gold outputs");
  const inputs = object(raw.inputs, "gold inputs");
  if (expected && Object.entries(expected).some(([key, value]) => inputs[key] !== value)) throw new Error("The calculation response does not match your assumptions.");
  for (const key of ["fine_troy_oz", "fine_grams", "metal_value_usd", "funding_cost_usd", "total_cost_usd", "all_in_inr_per_gram"]) {
    if (typeof outputs[key] !== "string" || decimal(outputs[key]) === null) throw new Error("The gold calculation is missing a required result.");
  }
  return { schema: raw.schema, status: raw.status, inputs, outputs: outputs as Record<string, string>, assumptions: strings(raw.assumptions), raw };
}
export function stateTone(status: string): "good" | "warn" | "bad" {
  const s = status.toLowerCase();
  if (["fresh", "available", "current"].includes(s)) return "good";
  if (["stale", "unavailable", "future", "future_observation", "restricted", "blocked"].includes(s)) return "bad";
  return "warn";
}
export function formatValue(value: unknown, digits = 4): string {
  if ((typeof value !== "number" && typeof value !== "string") || value === "" || !Number.isFinite(Number(value))) return "—";
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits: digits }).format(Number(value));
}
