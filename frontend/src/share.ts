/**
 * Share/export composition. Every chart and every dispatch can leave the
 * terminal as a social-grade card: 1200px wide, the wave-chip watermark and
 * SEICHE wordmark on top, the content in the middle, and a footer that
 * carries the deep link, the data-through date and the export stamp. Pure
 * canvas, no network round trip, no new server path.
 */

import { stableShareUrl } from "./shareRoutes";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const S = 2; // supersample: crisp on retina and on every platform recompress
export const CARD_W = 1200;
export const CARD_PAD = 48;
export const CHART_EXPORT_W = CARD_W - CARD_PAD * 2;

/* export-only tokens, resolved from styles.css with the same fallbacks */
const cache: Record<string, string> = {};
const tok = (name: string, fb: string): string => {
  if (!(name in cache)) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    cache[name] = v || fb;
  }
  return cache[name];
};
const mono = () => tok("--mono", "ui-monospace, Menlo, monospace");

export interface SeriesSwatch {
  label: string;
  color: string;
  pointsOnly?: boolean;
}

/** The card heading that already names this chart on screen. */
export function cardTitle(node: HTMLElement | null, fallback: string): string {
  const h = node?.closest(".card")?.querySelector("h2");
  const t = (h?.textContent ?? "").trim();
  return t || fallback;
}

export function deepLink(): string {
  return window.location.href;
}

/** Resolve the stable server-visible share path declared by a data surface.
 * Hash fragments remain useful inside the SPA, but unfurlers never receive
 * them. A card without an explicit path keeps the existing deep-link fallback. */
export function contextualLink(node: Element | null): string {
  const owner = node?.closest<HTMLElement>("[data-share-path]");
  const declared = owner?.dataset.sharePath?.trim();
  if (!declared) return deepLink();
  try {
    return stableShareUrl(declared);
  } catch {
    return deepLink();
  }
}

const slug = (s: string) =>
  s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48) || "card";

const fmtDay = (t: number) => {
  const d = new Date(t);
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
};
const fmtStamp = (t: number) => {
  const d = new Date(t);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${fmtDay(t)}, ${p(d.getUTCHours())}:${p(d.getUTCMinutes())} UTC`;
};

export function fileName(title: string): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `seiche-${slug(title)}-${d.getUTCFullYear()}${p(d.getUTCMonth() + 1)}${p(d.getUTCDate())}.png`;
}

/* ---------------------------------------------------------- drawing kit ---- */

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/** The favicon's standing wave, redrawn as the watermark chip. */
function drawWaveChip(ctx: CanvasRenderingContext2D, x: number, y: number, size: number) {
  ctx.save();
  roundRect(ctx, x, y, size, size, size * 0.22);
  ctx.fillStyle = "#0b0f16";
  ctx.fill();
  ctx.strokeStyle = tok("--panel-edge-2", "#333748");
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.translate(x, y);
  ctx.scale(size / 64, size / 64);
  ctx.beginPath();
  ctx.moveTo(6, 32);
  ctx.bezierCurveTo(12, 32, 14, 18, 21, 18);
  ctx.bezierCurveTo(28, 18, 29, 44, 36, 44);
  ctx.bezierCurveTo(43, 44, 44, 22, 50, 22);
  ctx.bezierCurveTo(56, 22, 57, 32, 60, 32);
  ctx.strokeStyle = tok("--accent", "#9c8fe8");
  ctx.lineWidth = 4.5;
  ctx.lineCap = "round";
  ctx.stroke();
  ctx.restore();
}

/** The Nocturne signature: a rule that fades at both ends. */
function fadingRule(ctx: CanvasRenderingContext2D, x0: number, x1: number, y: number, color: string) {
  const g = ctx.createLinearGradient(x0, 0, x1, 0);
  g.addColorStop(0, "rgba(0,0,0,0)");
  g.addColorStop(0.12, color);
  g.addColorStop(0.88, color);
  g.addColorStop(1, "rgba(0,0,0,0)");
  ctx.strokeStyle = g;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x0, y + 0.5);
  ctx.lineTo(x1, y + 0.5);
  ctx.stroke();
}

function wrapText(
  ctx: CanvasRenderingContext2D, text: string, x: number, y: number,
  maxW: number, lineH: number, maxLines: number,
): number {
  const words = text.split(/\s+/).filter(Boolean);
  let line = "";
  let lines = 0;
  for (let i = 0; i < words.length; i++) {
    const probe = line ? line + " " + words[i] : words[i];
    if (ctx.measureText(probe).width > maxW && line) {
      if (lines === maxLines - 1) {
        while (line && ctx.measureText(line + "…").width > maxW) line = line.replace(/\s*\S+$/, "");
        ctx.fillText(line + "…", x, y);
        return y;
      }
      ctx.fillText(line, x, y);
      y += lineH;
      lines += 1;
      line = words[i];
    } else {
      line = probe;
    }
  }
  if (line) ctx.fillText(line, x, y);
  return y;
}

interface CardShell {
  cv: HTMLCanvasElement;
  ctx: CanvasRenderingContext2D;
  contentY: number;
}

/** Ground, border, watermark row, headline block. Returns where content starts. */
function cardShell(H: number, title: string, sub?: string): CardShell {
  const cv = document.createElement("canvas");
  cv.width = CARD_W * S;
  cv.height = H * S;
  const ctx = cv.getContext("2d")!;
  ctx.scale(S, S);

  ctx.fillStyle = tok("--panel", "#0b0d15");
  ctx.fillRect(0, 0, CARD_W, H);
  ctx.strokeStyle = tok("--panel-edge", "#1c1f2b");
  ctx.lineWidth = 1;
  ctx.strokeRect(0.5, 0.5, CARD_W - 1, H - 1);

  // watermark row: chip + wordmark left, domain right
  drawWaveChip(ctx, CARD_PAD, 38, 34);
  ctx.font = `600 15px ${mono()}`;
  ctx.fillStyle = tok("--accent-soft", "#bbaffe");
  const word = "S E I C H E";
  ctx.fillText(word, CARD_PAD + 48, 60);
  ctx.font = `13px ${mono()}`;
  ctx.fillStyle = tok("--faint", "#787f95");
  const dom = "seiche.info";
  ctx.fillText(dom, CARD_W - CARD_PAD - ctx.measureText(dom).width, 60);
  fadingRule(ctx, CARD_PAD, CARD_W - CARD_PAD, 84, tok("--panel-edge-2", "#333748"));

  ctx.font = "650 26px Inter, sans-serif";
  ctx.fillStyle = tok("--text", "#edeef4");
  ctx.fillText(title, CARD_PAD, 132);
  let contentY = 150;
  if (sub) {
    ctx.font = `13px ${mono()}`;
    ctx.fillStyle = tok("--faint", "#787f95");
    ctx.fillText(sub, CARD_PAD, 158);
    contentY = 176;
  }
  return { cv, ctx, contentY };
}

function cardFooter(ctx: CanvasRenderingContext2D, y: number, link: string, clock: string): number {
  fadingRule(ctx, CARD_PAD, CARD_W - CARD_PAD, y, tok("--panel-edge-2", "#333748"));
  const by = y + 28;
  ctx.font = `13px ${mono()}`;
  ctx.fillStyle = tok("--accent-soft", "#bbaffe");
  ctx.fillText(link.replace(/^https?:\/\//, ""), CARD_PAD, by);
  ctx.fillStyle = tok("--faint", "#787f95");
  ctx.fillText(clock, CARD_W - CARD_PAD - ctx.measureText(clock).width, by);
  return by + 22;
}

/* ------------------------------------------------------------ composers ---- */

export interface ChartCardMeta {
  title: string;
  sub?: string;
  series: SeriesSwatch[];
  /** css-pixel size of the snapshot canvas */
  cssW: number;
  cssH: number;
  /** unix ms of the last data point, if known */
  dataThrough?: number | null;
  /** path-based URL visible to link unfurlers */
  link?: string;
}

export function composeChartCard(src: HTMLCanvasElement, meta: ChartCardMeta): HTMLCanvasElement {
  const drawW = CHART_EXPORT_W;
  const drawH = Math.round(meta.cssH * (drawW / meta.cssW));

  // legend row + chart + footer, measured before the shell is drawn
  const legendH = meta.series.length ? 30 : 8;
  const probe = document.createElement("canvas").getContext("2d")!;
  const contentTop = meta.sub ? 176 : 150;
  const chartY = contentTop + legendH;
  const footRule = chartY + drawH + 26;
  const H = footRule + 50;
  void probe;

  const { cv, ctx } = cardShell(H, meta.title, meta.sub);

  if (meta.series.length) {
    ctx.font = `13px ${mono()}`;
    let lx = CARD_PAD;
    const ly = contentTop + 14;
    for (const s of meta.series) {
      ctx.fillStyle = s.color;
      if (s.pointsOnly) {
        ctx.beginPath();
        ctx.arc(lx + 5, ly - 4, 4, 0, 7);
        ctx.fill();
      } else {
        ctx.fillRect(lx, ly - 7, 14, 4);
      }
      lx += 20;
      ctx.fillStyle = tok("--chart-ink", "#b7bbd0");
      ctx.fillText(s.label, lx, ly);
      lx += ctx.measureText(s.label).width + 22;
    }
  }

  ctx.drawImage(src, CARD_PAD, chartY, drawW, drawH);

  const clock =
    (meta.dataThrough ? `data through ${fmtDay(meta.dataThrough)} · ` : "") +
    `exported ${fmtStamp(Date.now())}`;
  cardFooter(ctx, footRule, meta.link ?? deepLink(), clock);
  return cv;
}

export interface TextCardMeta {
  title: string;
  /** small line above the title, e.g. "30 Jul 2026 · desk letter" */
  kicker?: string;
  body?: string;
  link?: string;
}

export function composeTextCard(meta: TextCardMeta): HTMLCanvasElement {
  // measure pass: the same wrap, on a probe context
  const probe = document.createElement("canvas").getContext("2d")!;
  probe.font = "650 30px Inter, sans-serif";
  let y = meta.kicker ? 156 : 138;
  const titleTop = y;
  y = wrapTextMeasure(probe, meta.title, CARD_W - CARD_PAD * 2, 40, 3, y) + 16;
  let bodyTop = 0;
  if (meta.body) {
    probe.font = "15px Inter, sans-serif";
    bodyTop = y + 18;
    y = wrapTextMeasure(probe, meta.body, CARD_W - CARD_PAD * 2 - 4, 25, 5, bodyTop);
  }
  const H = Math.max(y + 84, 400);
  const footRule = H - 50; // the footer sits on the card's floor, never adrift

  const cv = document.createElement("canvas");
  cv.width = CARD_W * S;
  cv.height = H * S;
  const ctx = cv.getContext("2d")!;
  ctx.scale(S, S);

  ctx.fillStyle = tok("--panel", "#0b0d15");
  ctx.fillRect(0, 0, CARD_W, H);
  ctx.strokeStyle = tok("--panel-edge", "#1c1f2b");
  ctx.lineWidth = 1;
  ctx.strokeRect(0.5, 0.5, CARD_W - 1, H - 1);

  drawWaveChip(ctx, CARD_PAD, 38, 34);
  ctx.font = `600 15px ${mono()}`;
  ctx.fillStyle = tok("--accent-soft", "#bbaffe");
  ctx.fillText("S E I C H E", CARD_PAD + 48, 60);
  ctx.font = `13px ${mono()}`;
  ctx.fillStyle = tok("--faint", "#787f95");
  const dom = "seiche.info";
  ctx.fillText(dom, CARD_W - CARD_PAD - ctx.measureText(dom).width, 60);
  fadingRule(ctx, CARD_PAD, CARD_W - CARD_PAD, 84, tok("--panel-edge-2", "#333748"));

  if (meta.kicker) {
    ctx.font = `13px ${mono()}`;
    ctx.fillStyle = tok("--faint", "#787f95");
    ctx.fillText(meta.kicker.toUpperCase(), CARD_PAD, 128);
  }
  ctx.font = "650 30px Inter, sans-serif";
  ctx.fillStyle = tok("--text", "#edeef4");
  wrapText(ctx, meta.title, CARD_PAD, titleTop, CARD_W - CARD_PAD * 2, 40, 3);
  if (meta.body) {
    ctx.font = "15px Inter, sans-serif";
    ctx.fillStyle = tok("--dim", "#9aa0b6");
    wrapText(ctx, meta.body, CARD_PAD, bodyTop, CARD_W - CARD_PAD * 2 - 4, 25, 5);
  }
  cardFooter(ctx, footRule, meta.link ?? deepLink(), `exported ${fmtStamp(Date.now())}`);
  return cv;
}

export interface StatCardMeta {
  title: string;
  body?: string;
  stats: { k: string; v: string }[];
  link?: string;
}

/** A board card as a social object: title, the honest sub-line, and the
 *  key/value readings in a two-column grid under the watermark row. */
export function composeStatCard(meta: StatCardMeta): HTMLCanvasElement {
  const probe = document.createElement("canvas").getContext("2d")!;
  probe.font = "650 30px Inter, sans-serif";
  const titleTop = 132;
  let y = wrapTextMeasure(probe, meta.title, CARD_W - CARD_PAD * 2, 40, 2, titleTop) + 14;
  let bodyTop = 0;
  if (meta.body) {
    probe.font = "15px Inter, sans-serif";
    bodyTop = y + 16;
    y = wrapTextMeasure(probe, meta.body, CARD_W - CARD_PAD * 2 - 4, 25, 3, bodyTop);
  }
  const stats = meta.stats.slice(0, 8);
  const rows = Math.ceil(stats.length / 2);
  const gridTop = y + 40;
  const cellH = 64;
  const H = Math.max(gridTop + rows * cellH + 84, 420);
  const footRule = H - 50;

  const cv = document.createElement("canvas");
  cv.width = CARD_W * S;
  cv.height = H * S;
  const ctx = cv.getContext("2d")!;
  ctx.scale(S, S);

  ctx.fillStyle = tok("--panel", "#0b0d15");
  ctx.fillRect(0, 0, CARD_W, H);
  ctx.strokeStyle = tok("--panel-edge", "#1c1f2b");
  ctx.lineWidth = 1;
  ctx.strokeRect(0.5, 0.5, CARD_W - 1, H - 1);

  drawWaveChip(ctx, CARD_PAD, 38, 34);
  ctx.font = `600 15px ${mono()}`;
  ctx.fillStyle = tok("--accent-soft", "#bbaffe");
  ctx.fillText("S E I C H E", CARD_PAD + 48, 60);
  ctx.font = `13px ${mono()}`;
  ctx.fillStyle = tok("--faint", "#787f95");
  const dom = "seiche.info";
  ctx.fillText(dom, CARD_W - CARD_PAD - ctx.measureText(dom).width, 60);
  fadingRule(ctx, CARD_PAD, CARD_W - CARD_PAD, 84, tok("--panel-edge-2", "#333748"));

  ctx.font = "650 30px Inter, sans-serif";
  ctx.fillStyle = tok("--text", "#edeef4");
  wrapText(ctx, meta.title, CARD_PAD, titleTop, CARD_W - CARD_PAD * 2, 40, 2);
  if (meta.body) {
    ctx.font = "15px Inter, sans-serif";
    ctx.fillStyle = tok("--dim", "#9aa0b6");
    wrapText(ctx, meta.body, CARD_PAD, bodyTop, CARD_W - CARD_PAD * 2 - 4, 25, 3);
  }

  const colW = (CARD_W - CARD_PAD * 2 - 32) / 2;
  const clamp = (text: string, maxW: number): string => {
    if (ctx.measureText(text).width <= maxW) return text;
    let t = text;
    while (t.length > 1 && ctx.measureText(t + "…").width > maxW) t = t.slice(0, -1);
    return t + "…";
  };
  stats.forEach((s, i) => {
    const col = i % 2;
    const row = Math.floor(i / 2);
    const x = CARD_PAD + col * (colW + 32);
    const yTop = gridTop + row * cellH;
    ctx.font = `11px ${mono()}`;
    ctx.fillStyle = tok("--faint", "#787f95");
    ctx.fillText(clamp(s.k.toUpperCase(), colW), x, yTop);
    ctx.font = `600 22px ${mono()}`;
    ctx.fillStyle = tok("--chart-ink-bright", "#d4d8ea");
    ctx.fillText(clamp(s.v, colW), x, yTop + 30);
  });

  cardFooter(ctx, footRule, meta.link ?? deepLink(), `exported ${fmtStamp(Date.now())}`);
  return cv;
}

/** wrapText without painting, for the measure pass */
function wrapTextMeasure(
  ctx: CanvasRenderingContext2D, text: string, maxW: number, lineH: number,
  maxLines: number, y: number,
): number {
  const words = text.split(/\s+/).filter(Boolean);
  let line = "";
  let lines = 0;
  for (let i = 0; i < words.length; i++) {
    const probe = line ? line + " " + words[i] : words[i];
    if (ctx.measureText(probe).width > maxW && line) {
      if (lines === maxLines - 1) return y;
      y += lineH;
      lines += 1;
      line = words[i];
    } else {
      line = probe;
    }
  }
  return y;
}

/* -------------------------------------------------------------- actions ---- */

const toBlob = (cv: HTMLCanvasElement): Promise<Blob> =>
  new Promise((res, rej) =>
    cv.toBlob((b) => (b ? res(b) : rej(new Error("toBlob failed"))), "image/png"),
  );

export async function savePng(cv: HTMLCanvasElement, name: string): Promise<void> {
  const blob = await toBlob(cv);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

/** Safari requires the ClipboardItem to be constructed synchronously inside
 *  the user gesture, with the blob supplied as a promise. */
export function copyCard(compose: () => Promise<HTMLCanvasElement>): Promise<boolean> {
  try {
    if (!navigator.clipboard || typeof ClipboardItem === "undefined") return Promise.resolve(false);
    const item = new ClipboardItem({ "image/png": compose().then(toBlob) });
    return navigator.clipboard.write([item]).then(() => true, () => false);
  } catch {
    return Promise.resolve(false);
  }
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export const canNativeShare: boolean = (() => {
  try {
    return !!navigator.canShare?.({
      files: [new File([new Uint8Array(1)], "x.png", { type: "image/png" })],
    });
  } catch {
    return false;
  }
})();

/** The OS share sheet, with the deep link riding along as text. */
export async function nativeShare(cv: HTMLCanvasElement, title: string, link: string): Promise<boolean> {
  try {
    const blob = await toBlob(cv);
    const file = new File([blob], fileName(title), { type: "image/png" });
    await navigator.share({ files: [file], title, text: link });
    return true;
  } catch (e) {
    // the reader closing the sheet is not a failure worth reporting
    return (e as DOMException)?.name === "AbortError";
  }
}
