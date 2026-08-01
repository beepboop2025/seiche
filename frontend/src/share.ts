/**
 * Per-chart share/export. Every chart can leave the terminal as a
 * self-describing card: title from its own card heading, legend, the exact
 * pixels currently on screen (zoom included), and a footer that says where
 * the data ends and where the live board lives. Composition is pure canvas,
 * so nothing here needs a network round trip or a new server path.
 */
import uPlot from "uplot";
import type { ChartSeries } from "./Chart";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const S = 2; // export supersample: crisp on retina and on X's compressor

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

export interface ExportMeta {
  title: string;
  sub?: string;
  series: ChartSeries[];
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

const slug = (s: string) =>
  s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48) || "chart";

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

/** Compose the branded card around the chart's current pixels. Synchronous,
 *  so clipboard writes stay inside the user gesture. */
export function composeCard(u: uPlot, meta: ExportMeta): HTMLCanvasElement {
  const src = u.ctx.canvas;
  const dpr = window.devicePixelRatio || 1;
  const chartW = Math.round(src.width / dpr);
  const chartH = Math.round(src.height / dpr);

  const padX = 22;
  const W = Math.max(560, chartW + padX * 2);
  const innerW = W - padX * 2;
  const drawH = Math.round(chartH * (innerW / chartW));

  const titleY = 30;
  const subY = meta.sub ? titleY + 16 : titleY;
  const legendY = subY + 18;
  const chartY = legendY + 12;
  const footRuleY = chartY + drawH + 12;
  const footY = footRuleY + 16;
  const H = footY + 14;

  const cv = document.createElement("canvas");
  cv.width = W * S;
  cv.height = H * S;
  const ctx = cv.getContext("2d")!;
  ctx.scale(S, S);

  // ground + hairline edge, matching the on-screen card material
  ctx.fillStyle = tok("--panel", "#0b0d15");
  ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = tok("--panel-edge", "#1c1f2b");
  ctx.lineWidth = 1;
  ctx.strokeRect(0.5, 0.5, W - 1, H - 1);

  // title left, wordmark right
  ctx.fillStyle = tok("--text", "#edeef4");
  ctx.font = `600 14px Inter, sans-serif`;
  ctx.fillText(meta.title, padX, titleY);
  ctx.font = `9.5px ${mono()}`;
  ctx.fillStyle = tok("--accent-soft", "#bbaffe");
  const mark = "SEICHE · seiche.info";
  ctx.fillText(mark, W - padX - ctx.measureText(mark).width, titleY);

  if (meta.sub) {
    ctx.fillStyle = tok("--faint", "#787f95");
    ctx.font = `9.5px ${mono()}`;
    ctx.fillText(meta.sub, padX, subY);
  }

  // legend: swatches + labels, so the card explains itself off-site
  ctx.font = `9.5px ${mono()}`;
  let lx = padX;
  for (const s of meta.series) {
    ctx.fillStyle = s.color;
    if (s.pointsOnly) {
      ctx.beginPath();
      ctx.arc(lx + 4, legendY - 3.5, 3, 0, 7);
      ctx.fill();
    } else {
      ctx.fillRect(lx, legendY - 6, 9, 3);
    }
    lx += 14;
    ctx.fillStyle = tok("--chart-ink", "#b7bbd0");
    ctx.fillText(s.label, lx, legendY);
    lx += ctx.measureText(s.label).width + 16;
  }

  ctx.drawImage(src, padX, chartY, innerW, drawH);

  // footer: the deep link back to the live board, and the honest clock
  ctx.strokeStyle = tok("--chart-grid", "rgba(237,238,244,0.08)");
  ctx.beginPath();
  ctx.moveTo(padX, footRuleY + 0.5);
  ctx.lineTo(W - padX, footRuleY + 0.5);
  ctx.stroke();

  ctx.font = `9.5px ${mono()}`;
  ctx.fillStyle = tok("--accent-soft", "#bbaffe");
  ctx.fillText(deepLink().replace(/^https?:\/\//, ""), padX, footY);

  const xs = u.data[0];
  const lastT = xs && xs.length ? (xs[xs.length - 1] as number) * 1000 : null;
  const clock =
    (lastT ? `data through ${fmtDay(lastT)} · ` : "") + `exported ${fmtStamp(Date.now())}`;
  ctx.fillStyle = tok("--faint", "#787f95");
  ctx.fillText(clock, W - padX - ctx.measureText(clock).width, footY);

  return cv;
}

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
export async function copyPng(cv: HTMLCanvasElement): Promise<boolean> {
  try {
    if (!navigator.clipboard || typeof ClipboardItem === "undefined") return false;
    await navigator.clipboard.write([new ClipboardItem({ "image/png": toBlob(cv) })]);
    return true;
  } catch {
    return false;
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
export async function nativeShare(cv: HTMLCanvasElement, meta: ExportMeta): Promise<boolean> {
  try {
    const blob = await toBlob(cv);
    const file = new File([blob], fileName(meta.title), { type: "image/png" });
    await navigator.share({ files: [file], title: meta.title, text: deepLink() });
    return true;
  } catch (e) {
    // the reader closing the sheet is not a failure worth reporting
    return (e as DOMException)?.name === "AbortError";
  }
}
