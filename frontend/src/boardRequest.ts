export class BoardRequestError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

export const WITHHELD_BOARD_MESSAGE = "The legacy funding board, its composite reading, deep layer and derived "
  + "narratives are withheld pending source-rights qualification. Values are not reconstructed, and a withheld "
  + "reading is not a calm, zero or last-known reading. Money markets, Market workbench and Research remain available.";

/** The publisher served the source-rights restriction instead of a board. */
export class BoardWithheldError extends BoardRequestError {
  constructor(readonly policy: string | null) { super(WITHHELD_BOARD_MESSAGE, 403); }
}

export function sourceRightsRestriction(value: unknown): { policy: string | null } | null {
  const body = value as Record<string, unknown> | null;
  if (!body || typeof body !== "object" || Array.isArray(body)
      || body.schema !== "seiche.source-rights-restriction.v1"
      || body.category !== "source_rights_restricted") return null;
  return { policy: typeof body.policy === "string" ? body.policy : null };
}

export function validateBoard(value: unknown): asserts value is Record<string, any> {
  const board = value as Record<string, any> | null;
  if (!board || Array.isArray(board) || typeof board.generated_at !== "string"
      || !Number.isFinite(Date.parse(board.generated_at))
      || !board.engines || typeof board.engines !== "object" || Array.isArray(board.engines)) {
    throw new Error("The funding snapshot could not be verified.");
  }
}

export async function requestBoard(url: string, {
  signal, headers = {}, timeoutMs = 15000, fetcher = fetch,
}: { signal: AbortSignal; headers?: HeadersInit; timeoutMs?: number; fetcher?: typeof fetch }) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener("abort", abort, { once: true });
  if (signal.aborted) controller.abort();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  try {
    const response = await fetcher(url, { signal: controller.signal, headers, credentials: "same-origin" });
    if (response.status === 401) throw new BoardRequestError("Your session expired. Sign in again to refresh.", 401);
    if (response.status === 403) throw new BoardRequestError("Live board refresh is restricted. The published board keeps its original observation dates.", 403);
    if (!response.ok) throw new Error("The funding source is temporarily unavailable. Please retry.");
    if (!/json|octet-stream/.test(response.headers.get("content-type") || "")) throw new Error("The funding source returned an unexpected response.");
    const reader = response.body?.getReader();
    if (!reader) throw new Error("The funding snapshot could not be read.");
    let length = 0;
    const chunks: Uint8Array[] = [];
    try {
      while (true) {
        const part = await reader.read();
        if (part.done) break;
        length += part.value.byteLength;
        if (length > 4 * 1024 * 1024) throw new Error("The funding snapshot exceeds the display limit.");
        chunks.push(part.value);
      }
    } catch (error) { await reader.cancel().catch(() => undefined); throw error; }
    finally { reader.releaseLock(); }
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    const data: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    const restriction = sourceRightsRestriction(data);
    if (restriction) throw new BoardWithheldError(restriction.policy);
    validateBoard(data);
    return data;
  } catch (error) {
    if (timedOut) throw new Error("The funding source took too long to respond. Please retry.");
    throw error;
  } finally { clearTimeout(timer); signal.removeEventListener("abort", abort); }
}


type BoardUpdate = { data: Record<string, any>; live: boolean };
type BoardRefreshOptions = {
  signal: AbortSignal;
  headers?: HeadersInit;
  apiEnabled?: boolean;
  fetcher?: typeof fetch;
  onSnapshot: (snapshot: BoardUpdate) => void;
};

/** Render published evidence first; an API failure cannot erase that evidence. */
export async function refreshBoardSources(
  publishedUrl: string, apiUrl: string,
  { signal, headers = {}, apiEnabled = true, fetcher = fetch, onSnapshot }: BoardRefreshOptions,
): Promise<{ apiRestricted: boolean; refreshError: string | null; apiChecked: boolean }> {
  let published: Record<string, any> | null = null;
  let publishedError: unknown;
  try {
    published = await requestBoard(publishedUrl, { signal, timeoutMs: 4000, fetcher });
    if (signal.aborted) throw new DOMException("Aborted", "AbortError");
    onSnapshot({ data: published, live: false });
  } catch (error) {
    if (signal.aborted) throw error;
    publishedError = error;
  }
  if (!apiEnabled) {
    if (!published) throw publishedError;
    return { apiRestricted: true, refreshError: null, apiChecked: false };
  }
  try {
    const data = await requestBoard(apiUrl, { signal, headers, fetcher });
    if (signal.aborted) throw new DOMException("Aborted", "AbortError");
    // The API and publisher can briefly serve different generations.
    if (!published || Date.parse(data.generated_at) >= Date.parse(published.generated_at)) {
      onSnapshot({ data, live: true });
    }
    return { apiRestricted: false, refreshError: null, apiChecked: true };
  } catch (error) {
    if (signal.aborted) throw error;
    // A withheld publication outranks any live failure message.
    if (!published) throw publishedError instanceof BoardWithheldError ? publishedError : error;
    return {
      apiRestricted: error instanceof BoardRequestError && error.status === 403,
      refreshError: error instanceof Error ? error.message : "Live refresh is unavailable.",
      apiChecked: false,
    };
  }
}
