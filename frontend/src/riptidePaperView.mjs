/**
 * Honest reading of Riptide's public paper snapshot.
 * Keep this file byte-identical in:
 *   seiche frontend/src/riptidePaperView.mjs
 *   liquilens-site start/riptide-note.mjs
 *   undertow-site research-ui/riptide-note.js
 * It prints the published paper book. It does not invent a funding essay,
 * an institution risk weight, or an exit cost.
 */

export const ALLOCATION_URL = "https://api.seiche.info/riptide/api/v1/allocation";
export const GAUGE_URL = "https://api.seiche.info/api/gauge";
export const BOARD_URL = "https://api.seiche.info/undertow/board.json";

const REGIMES = ["abundant", "ample", "tightening", "strained", "acute"];
const DEGRADED = new Set(["DEFICIENT", "STRAINED"]);
const CLEAR = new Set(["NORMAL", "SURPLUS"]);
const OUTSIDE = ["ETF", "FX", "CN", "CRYPTO", "BSTOCK"];

const PAPER_ONLY = "This is a paper book only. Real orders are off.";
const BOUNDARY = "This is not a money-market reading, not an institution risk weight, and not an exit cost.";
const SCALE = "That label is Riptide's scale (abundant, ample, tightening, strained, acute) and is separate from Seiche's funding regime.";
const INSTITUTION = "The public snapshot has no per-institution risky-asset figure. LiquiLens keeps the institution record.";
const CAUSE = "The public snapshot does not name which Undertow rows cut this weight.";
const RULE = "Riptide's published rule can cut only for UST, credit (IG and HY counted once), and EQUITY when the tier is DEFICIENT or STRAINED.";
const PARTIAL = "Partial rows cannot clear the book.";
const NOT_CAUSE = "This is the published rule applied to this board. The public Riptide snapshot does not say these rows caused today's weight.";
const UNREADABLE = "Riptide's public paper update could not be read. Nothing is filled in.";
const NOT_PAPER = "Riptide's public paper update is not shown because real orders are not confirmed off. Nothing is filled in.";
const GAUGE_MISSING = "Seiche funding gauge could not be read. The money-market reading is not filled in from Riptide.";
const BOARD_MISSING = "Undertow's published board could not be read. The paper rule is not filled in from a missing board.";

function unavailable(line) {
  return { state: "unavailable", lines: [line] };
}

function isIsoDate(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year
    && date.getUTCMonth() === month - 1
    && date.getUTCDate() === day;
}

function isSymbol(value) {
  return typeof value === "string" && /^[A-Za-z][A-Za-z0-9]{0,11}$/.test(value);
}

function isWeight(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1;
}

export function formatWeight(value) {
  if (value === 0) return "0";
  const tenths = Math.round(value * 1000) / 10;
  return `about ${tenths.toFixed(1)}%`;
}

function formatIndex(value) {
  const rounded = Math.round(value * 10000) / 10000;
  return Object.is(rounded, -0) ? "0" : String(rounded);
}

export function riptidePaperView(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return unavailable(UNREADABLE);
  }
  if (payload.real_orders !== false || payload.execution_mode !== "paper_only") {
    return unavailable(NOT_PAPER);
  }
  const weights = payload.target_weights;
  const regime = payload.regime;
  if (payload.schema !== "riptide.public.v1"
      || payload.available !== true
      || payload.kind !== "allocation"
      || !isIsoDate(payload.as_of)
      || !isSymbol(payload.symbol)
      || typeof regime !== "string"
      || !REGIMES.includes(regime)
      || !weights
      || typeof weights !== "object"
      || Array.isArray(weights)) {
    return unavailable(UNREADABLE);
  }
  const entries = Object.entries(weights);
  if (entries.length === 0 || entries.some(([symbol, weight]) => !isSymbol(symbol) || !isWeight(weight))) {
    return unavailable(UNREADABLE);
  }
  // Published decimal weights may be rounded, but a complete book must total 100%.
  if (Math.abs(entries.reduce((total, [, weight]) => total + weight, 0) - 1) > 0.001) {
    return unavailable(UNREADABLE);
  }
  entries.sort((a, b) => {
    if (a[0] === "cash") return 1;
    if (b[0] === "cash") return -1;
    return a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0;
  });
  const journal = typeof payload.journal_status === "string"
    && /^[a-z][a-z_]{0,31}$/.test(payload.journal_status)
    ? `Daily journal status: ${payload.journal_status}.`
    : "Daily journal status was not published with this snapshot.";
  return {
    state: "ready",
    lines: [
      PAPER_ONLY,
      BOUNDARY,
      `As of ${payload.as_of} the paper book for ${payload.symbol} shows Riptide regime ${regime}.`,
      SCALE,
      INSTITUTION,
      CAUSE,
      RULE,
      PARTIAL,
      journal,
      `Paper weights: ${entries.map(([symbol, weight]) => `${symbol} ${formatWeight(weight)}`).join(", ")}.`,
    ],
  };
}

export function seicheGaugeLine(gauge) {
  if (!gauge || typeof gauge !== "object" || Array.isArray(gauge)) return GAUGE_MISSING;
  const { index, regime, generated_at: generatedAt } = gauge;
  const clock = typeof generatedAt === "string"
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(generatedAt);
  if (typeof index !== "number"
      || !Number.isFinite(index)
      || typeof regime !== "string"
      || !/^[A-Z][A-Z0-9_]{0,31}$/.test(regime)
      || !clock) {
    return GAUGE_MISSING;
  }
  return `Seiche funding gauge reads ${formatIndex(index)}, regime ${regime}, generated at ${generatedAt}. This is Seiche's own scale and is separate from Riptide's paper regime.`;
}

function tierOf(segments, name) {
  const cell = segments[name];
  if (!cell || typeof cell !== "object" || Array.isArray(cell) || typeof cell.tier !== "string") return "";
  return cell.tier.trim().toUpperCase();
}

function tierText(tier) {
  if (DEGRADED.has(tier) || CLEAR.has(tier)) return tier;
  if (!tier) return "missing";
  return `${tier} and is not scored`;
}

function joinList(items) {
  if (items.length <= 1) return items[0] || "";
  if (items.length === 2) return `${items[0]} and ${items[1]}`;
  return `${items.slice(0, -1).join(", ")}, and ${items[items.length - 1]}`;
}

export function undertowRuleReading(board) {
  const segments = board?.segments;
  if (!board || typeof board !== "object" || Array.isArray(board)
      || !segments || typeof segments !== "object" || Array.isArray(segments)) {
    return unavailable(BOARD_MISSING);
  }
  const rates = tierOf(segments, "UST");
  const ig = tierOf(segments, "IG");
  const hy = tierOf(segments, "HY");
  const equity = tierOf(segments, "EQUITY");
  const degraded = [];
  if (DEGRADED.has(rates)) degraded.push("rates");
  if (DEGRADED.has(ig) || DEGRADED.has(hy)) degraded.push("credit");
  if (DEGRADED.has(equity)) degraded.push("equities");
  let effect;
  if (degraded.length === 0) {
    effect = "No rates, credit, or equities bucket is degraded, so the published rule would not cut paper risky exposure.";
  } else if (degraded.length === 1) {
    effect = `One bucket is degraded (${degraded[0]}), so the published rule would halve paper risky exposure.`;
  } else {
    const count = degraded.length === 2 ? "Two" : "Three";
    effect = `${count} buckets are degraded (${joinList(degraded)}), so the published rule would cut paper risky exposure to a quarter.`;
  }
  const creditClear = degraded.includes("credit")
    ? "The credit bucket is degraded."
    : (CLEAR.has(ig) && CLEAR.has(hy))
      ? "The credit bucket is not degraded."
      : "The credit bucket is not degraded, and it cannot clear the book.";
  const lines = [];
  if (isIsoDate(board.asof)) lines.push(`Undertow board as of ${board.asof}.`);
  lines.push(
    `Rates (UST) is ${tierText(rates)}.`,
    `Credit counts IG and HY once. IG is ${tierText(ig)}. HY is ${tierText(hy)}. ${creditClear}`,
    `Equities (EQUITY) is ${tierText(equity)}.`,
    effect,
    "Partial, unavailable, missing, and accruing rows cannot clear the book and do not cut.",
    `${joinList(OUTSIDE)} are outside this cut.`,
    NOT_CAUSE,
  );
  return { state: "ready", lines };
}

export function composePaperNote(payload, gauge, board, lead, options = {}) {
  const paper = riptidePaperView(payload);
  const lines = paper.lines.slice();
  if (lead === "liquilens") {
    const index = lines.indexOf(INSTITUTION);
    if (index >= 0) lines.splice(index, 1);
    lines.unshift(INSTITUTION);
  }
  if (!options.skipGauge) lines.push(seicheGaugeLine(gauge));
  lines.push(...undertowRuleReading(board).lines);
  return { state: paper.state, lines };
}

async function readJson(fetchImpl, url) {
  try {
    const response = await fetchImpl(url, { cache: "no-store", signal: AbortSignal.timeout(8000) });
    if (!response?.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

export async function loadPaperNote(fetchImpl = globalThis.fetch, options = {}) {
  const skipGauge = options.skipGauge === true;
  const [payload, board, gauge] = await Promise.all([
    readJson(fetchImpl, options.allocationUrl || ALLOCATION_URL),
    readJson(fetchImpl, options.boardUrl || BOARD_URL),
    skipGauge ? Promise.resolve(null) : readJson(fetchImpl, options.gaugeUrl || GAUGE_URL),
  ]);
  return composePaperNote(payload, gauge, board, options.lead || "seiche", { skipGauge });
}

export function fillPaperNote(root, fetchImpl = globalThis.fetch) {
  const body = root.querySelector("[data-riptide-body]") || root;
  const lineClass = root.dataset.lineClass || "";
  return loadPaperNote(fetchImpl, {
    lead: root.dataset.lead || "seiche",
    allocationUrl: root.dataset.allocation || ALLOCATION_URL,
    boardUrl: root.dataset.board || BOARD_URL,
    gaugeUrl: root.dataset.gauge || GAUGE_URL,
    skipGauge: root.dataset.gauge === "",
  }).then((note) => {
    body.replaceChildren();
    for (const line of note.lines) {
      const paragraph = document.createElement("p");
      if (lineClass) paragraph.className = lineClass;
      paragraph.textContent = line;
      body.appendChild(paragraph);
    }
    return note;
  });
}

if (typeof document !== "undefined") {
  const boot = () => {
    const root = document.getElementById("riptide-paper");
    if (!root || root.dataset.booted === "1") return;
    root.dataset.booted = "1";
    fillPaperNote(root);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
}
