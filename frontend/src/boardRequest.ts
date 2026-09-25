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
    if (response.status === 401) throw new Error("Your session expired. Sign in again to refresh.");
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
    validateBoard(data);
    return data;
  } catch (error) {
    if (timedOut) throw new Error("The funding source took too long to respond. Please retry.");
    throw error;
  } finally { clearTimeout(timer); signal.removeEventListener("abort", abort); }
}
