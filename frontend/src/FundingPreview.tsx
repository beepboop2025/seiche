import { useEffect, useRef, useState } from "react";
import { fundingPreview } from "./fundingSnapshot";

type Snapshot = ReturnType<typeof fundingPreview>;
type Receipt = { snapshot: Snapshot; elapsed: number; retrievedAt: string };

export default function FundingPreview() {
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  const read = async () => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setBusy(true); setError(null);
    const started = performance.now();
    const timer = window.setTimeout(() => controller.abort(), 12_000);
    try {
      const response = await fetch("/data/overview.json", { signal: controller.signal, credentials: "omit" });
      if (!response.ok) throw new Error("The published funding snapshot is unavailable.");
      const text = await response.text();
      if (text.length > 4_000_000) throw new Error("The published snapshot could not be read.");
      const snapshot = fundingPreview(JSON.parse(text));
      if (!controller.signal.aborted) setReceipt({ snapshot, elapsed: Math.round(performance.now() - started), retrievedAt: new Date().toISOString() });
    } catch {
      if (request.current === controller) setError(`The snapshot could not be loaded. Try again or open the funding desk.${receipt ? " The previous preview remains below." : ""}`);
    } finally {
      window.clearTimeout(timer);
      if (request.current === controller) setBusy(false);
    }
  };
  return <section className="product-section product-peek" aria-labelledby="funding-peek-heading">
    <div className="product-section__intro"><h2 id="funding-peek-heading">A closer look.<br/>One request.</h2><div><p>Open a few readings from Seiche’s published funding snapshot. See the figures, their dates and how long this request takes in your browser.</p><button className="product-button" onClick={read} disabled={busy}>{busy ? "Loading snapshot…" : receipt ? "Refresh the preview" : "Preview funding data"}</button></div></div>
    <div className="product-peek__result" aria-live="polite" aria-busy={busy}>
      {error && <p role="status" className="product-peek__error">{error}</p>}
      {receipt && <>
        <div className="product-peek__readings">{receipt.snapshot.observations.map((row) => <article key={row.key}><h3>{row.label}</h3><p className="product-peek__value">{row.value === null ? "Unavailable" : row.value.toLocaleString("en-US", { maximumFractionDigits: 2 })}{row.value !== null && <span>{row.unit}</span>}</p><p>{row.name}</p><p>Observation: {row.observedAt ?? "date unavailable"}</p></article>)}</div>
        <div className="product-peek__receipt"><p>Snapshot assembled: {receipt.snapshot.generatedAt ?? "time unavailable"}<br/>Retrieved: {receipt.retrievedAt}<br/>This request: {receipt.elapsed.toLocaleString("en-US")} ms in your browser.</p><p>{receipt.snapshot.faultsReported === null ? "Source-health status unavailable." : receipt.snapshot.faultsReported ? `${receipt.snapshot.faultsReported} source issue${receipt.snapshot.faultsReported === 1 ? "" : "s"} reported in this snapshot.` : "No source faults reported in this snapshot."}<br/>Retrieval speed does not change the observations’ dates.</p></div>
        <a className="product-link" href="#board">Inspect the full funding board ↗</a>
      </>}
    </div>
  </section>;
}
