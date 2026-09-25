/** Presentation helpers preserve observation clocks and never infer a score. */
export function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function dateOnly(value: unknown): string | null {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value ? value : null;
}

export function dateText(value: string): string {
  const date = dateOnly(value);
  return date ? new Intl.DateTimeFormat("en-GB", {
    day: "numeric", month: "short", year: "numeric", timeZone: "UTC",
  }).format(new Date(`${date}T00:00:00Z`)) : "Date unavailable";
}

export function element<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string, className?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

export function link(label: string, url: string): HTMLAnchorElement {
  const node = element("a", label);
  node.href = url;
  return node;
}

export async function readJSON(url: string, signal: AbortSignal, limit = 2 * 1024 * 1024): Promise<unknown> {
  const response = await fetch(url, {signal, credentials: "omit", headers: {Accept: "application/json"}});
  if (!response.ok) throw new Error(`The source returned HTTP ${response.status}.`);
  const reader = response.body?.getReader();
  if (!reader) throw new Error("The response could not be read.");
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      length += part.value.byteLength;
      if (length > limit) throw new Error("The response exceeds the display limit.");
      chunks.push(part.value);
    }
  } catch (error) {
    await reader.cancel().catch(() => {});
    throw error;
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {bytes.set(chunk, offset); offset += chunk.length;}
  return JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(bytes));
}

export function visibleRefresh(refresh: () => Promise<void>, interval = 300000): () => void {
  let disposed = false, last = 0, busy = false;
  const run = async () => {
    if (disposed || busy || document.hidden || Date.now() - last < interval) return;
    busy = true; last = Date.now();
    try {await refresh();} finally {busy = false;}
  };
  const timer = setInterval(run, interval);
  document.addEventListener("visibilitychange", run);
  void run();
  return () => {disposed = true; clearInterval(timer); document.removeEventListener("visibilitychange", run);};
}

export function svgNode<K extends keyof SVGElementTagNameMap>(tag: K, attributes: Record<string, string | number>, text?: string): SVGElementTagNameMap[K] {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  if (text !== undefined) node.textContent = text;
  return node;
}
