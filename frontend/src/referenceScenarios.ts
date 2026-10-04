/** Local scenario arithmetic. No quote, trading authority or account balance. */
const SCALE = 10n ** 12n;
function decimal(value: string, label: string, signed = false): bigint {
  if (typeof value !== "string" || !(signed ? /^-?\d{1,12}(?:\.\d{1,12})?$/ : /^\d{1,12}(?:\.\d{1,12})?$/).test(value.trim())) {
    throw new Error(`Enter ${label} as a decimal with up to 12 places, without commas.`);
  }
  const text = value.trim(), negative = text.startsWith("-");
  const [whole, fraction = ""] = (negative ? text.slice(1) : text).split(".");
  return (BigInt(whole) * SCALE + BigInt(fraction.padEnd(12, "0"))) * (negative ? -1n : 1n);
}
function amount(value: bigint): string {
  const negative = value < 0n, absolute = negative ? -value : value;
  const rounded = (absolute + 500_000n) / 1_000_000n;
  const fraction = (rounded % 1_000_000n).toString().padStart(6, "0").replace(/0+$/, "");
  return `${negative && rounded ? "-" : ""}${rounded / 1_000_000n}${fraction ? "." + fraction : ""}`;
}
function currency(value: string) {
  if (!/^[A-Z]{3}$/.test(value)) throw new Error("Choose an ISO currency code.");
}
export interface ReferenceQuote {
  base: string; quote: string; rate: number | null; as_of: string | null;
  status: string; provider: string;
}
export interface ConversionInputs { amount: string; fee_bps: string; fixed_fee_quote: string }
export function referenceConversion(input: ConversionInputs, reference: ReferenceQuote) {
  currency(reference.base); currency(reference.quote);
  if (!reference.as_of || !/^\d{4}-\d{2}-\d{2}$/.test(reference.as_of)
      || !Number.isFinite(Date.parse(reference.as_of)) || new Date(reference.as_of).toISOString().slice(0, 10) !== reference.as_of
      || !["fresh", "stale", "historical"].includes(reference.status.toLowerCase()) || !reference.provider
      || reference.rate === null || !Number.isFinite(reference.rate) || reference.rate <= 0 || reference.rate >= 1e12) {
    throw new Error("A dated, publicly available reference is required. Restricted or missing rates cannot be converted.");
  }
  const principal = decimal(input.amount, "the amount"), fee = decimal(input.fee_bps, "the fee in basis points");
  const fixed = decimal(input.fixed_fee_quote, "the fixed receiving-currency fee");
  if (principal <= 0n || fee > 10_000n * SCALE) throw new Error("The amount must be positive and the fee must be between 0 and 10,000 basis points.");
  const rateText = reference.rate.toFixed(12), rate = decimal(rateText, "the reference rate");
  if (rate <= 0n) throw new Error("The reference is below this calculator's 12-decimal rate precision.");
  const gross = principal * rate / SCALE, variableFee = gross * fee / (SCALE * 10_000n);
  return { schema: "seiche.reference-conversion.v1", status: "reference_scenario", inputs: { ...input }, reference: { ...reference, calculation_rate: rateText },
    outputs: { gross_quote: amount(gross), variable_fee_quote: amount(variableFee), fixed_fee_quote: amount(fixed), net_quote: amount(gross - variableFee - fixed) },
    assumptions: ["Dated reference valuation, not an executable FX quote or a forecast.", "The supplied percentage fee is applied to gross receiving-currency value; the fixed fee is also in the receiving currency.", "Amounts are rounded to six decimals; reference rates are rounded to twelve decimals. Currency settlement rounding may differ.", "No account, transfer, tax or execution cost has been independently verified."] };
}
export interface FundingInputs {
  currency: string; principal: string; rate_a_pct: string; rate_b_pct: string;
  fees_a: string; fees_b: string; days: string; day_count: string;
}
export function fundingComparison(input: FundingInputs) {
  currency(input.currency);
  const principal = decimal(input.principal, "principal"), a = decimal(input.rate_a_pct, "rate A", true), b = decimal(input.rate_b_pct, "rate B", true);
  const fa = decimal(input.fees_a, "fees A"), fb = decimal(input.fees_b, "fees B");
  if (!/^\d{1,4}$/.test(input.days) || !["360", "365"].includes(input.day_count)) throw new Error("Use 1–3,660 actual days and a 360- or 365-day basis.");
  const days = BigInt(input.days), basis = BigInt(input.day_count);
  if (principal <= 0n || days < 1n || days > 3660n || [a, b].some(v => v < -100n * SCALE || v > 1000n * SCALE)) {
    throw new Error("Use a positive principal, 1–3,660 days and annual rates between −100% and 1,000%.");
  }
  const ia = principal * a * days / (SCALE * 100n * basis), ib = principal * b * days / (SCALE * 100n * basis);
  return { schema: "seiche.funding-comparison.v1", status: "caller_scenario", inputs: { ...input },
    outputs: { interest_a: amount(ia), interest_b: amount(ib), total_cost_a: amount(ia + fa), total_cost_b: amount(ib + fb), cost_b_minus_a: amount(ib + fb - ia - fa) },
    assumptions: ["Two financing scenarios in the same currency, using caller-supplied rates and fees.", "Simple interest = principal × annual rate / 100 × actual days / selected basis; rates are assumed unchanged for the whole term.", "This does not reproduce compounded overnight benchmarks or apply business-day, holiday, reset or contract conventions.", "Costs exclude principal repayment and any omitted taxes, collateral costs, FX hedges or rollover risk. No borrowing offer or eligibility is verified.", "Results are rounded to six decimal places."] };
}
