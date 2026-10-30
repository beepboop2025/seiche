import { useEffect, useState } from "react";
import { API_BASE } from "./apiBase";
import { fundingValue, usdFundingCards, usdMarket } from "./usdFunding";
import "./styles-usd-funding.css";

export default function UsdFundingSummary({ snap, atlas }: { snap: unknown; atlas?: unknown }) {
  const [lastAtlas, setLastAtlas] = useState<unknown>(null);
  const [checkedAt, setCheckedAt] = useState<string | null>(null);
  const [refreshFailed, setRefreshFailed] = useState(false);
  useEffect(() => {
    // Money Markets already owns this request. The opening page polls independently.
    if (atlas !== undefined) return;
    let disposed = false;
    let pending = false;
    let controller: AbortController | null = null;
    const refresh = async () => {
      if (pending || document.visibilityState === "hidden") return;
      pending = true;
      controller = new AbortController();
      const timeout = window.setTimeout(() => controller?.abort(), 25000);
      try {
        const response = await fetch(API_BASE + "/api/v2/money-markets", {
          credentials: "omit", cache: "no-cache", signal: controller.signal,
        });
        if (!response.ok || !(response.headers.get("content-type") || "").includes("json")) throw new Error("refresh unavailable");
        const payload: unknown = await response.json();
        if (!usdMarket(payload)) throw new Error("USD observations unavailable");
        if (!disposed) { setLastAtlas(payload); setCheckedAt(new Date().toISOString()); setRefreshFailed(false); }
      } catch {
        if (!disposed) setRefreshFailed(true);
      } finally { window.clearTimeout(timeout); pending = false; }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 60000);
    const visible = () => { if (document.visibilityState === "visible") void refresh(); };
    document.addEventListener("visibilitychange", visible);
    return () => { disposed = true; controller?.abort(); window.clearInterval(timer); document.removeEventListener("visibilitychange", visible); };
  }, [atlas]);
  const cards = usdFundingCards(snap, usdMarket(atlas === undefined ? lastAtlas : atlas));
  return (
    <section className="usd-funding" aria-label="US dollar funding data">
      <header className="usd-funding__head">
        <div><span>US DOLLAR FUNDING</span><h2>Rates, reserves and cash balances</h2></div>
        <a href="#money%20markets">Open the full USD funding desk →</a>
      </header>
      <div className="usd-funding__grid">
        {cards.map(card => (
          <article className="usd-funding__card" key={card.id}>
            <h3>{card.label}</h3><strong>{fundingValue(card)}</strong>
            <span>{card.asof ? (card.id === "liquidity.reserves" ? "Week ended " : "As of ") + card.asof : "Observation date unavailable"}</span>
            <small>{card.cadence}{card.status.toLowerCase() === "stale" ? " · behind schedule" : ""}</small>
            <small>{card.source}</small>
            <small>{card.origin === "live" ? "Latest collected observation" : card.origin === "published" ? "Published board observation" : "No observation supplied"}</small>
          </article>
        ))}
      </div>
      <p className="usd-funding__clock">Dates identify the observations. Daily rates and weekly reserve balances follow their own release schedules; SOFR − IORB uses matching dates.
        {checkedAt && " Feed checked " + checkedAt.slice(0, 16).replace("T", " ") + " UTC."}
        {refreshFailed && " The latest refresh failed; dated observations remain visible."}
      </p>
    </section>
  );
}
