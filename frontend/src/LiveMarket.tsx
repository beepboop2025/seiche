import { useEffect, useMemo, useState } from "react";
import "./styles-editorial.css";

const RELAY_URL = "https://api.seiche.info/undertow/live/quotes.json";
const ORDER = ["BTC", "ETH", "SOL", "BNB", "XRP"];

type RelaySymbol = {
  book?: { mid?: number; spread_bps?: number; age_ms?: number };
  perp?: { mark?: number; funding?: number; age_ms?: number };
  stats24h?: { chg_open?: number; age_ms?: number };
};

type Relay = {
  schema?: string;
  generated_at?: string;
  source?: string;
  relay?: { cadence_ms?: number; note?: string };
  streams?: Record<string, { connected?: boolean; age_ms?: number }>;
  symbols?: Record<string, RelaySymbol>;
};

const px = (value: number | undefined) => {
  if (value == null || !Number.isFinite(value)) return "—";
  const digits = value >= 1000 ? 2 : value >= 10 ? 3 : 5;
  return value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
};

const signed = (value: number | undefined, digits = 2, suffix = "") => {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}${suffix}`;
};

export default function LiveMarket() {
  const [relay, setRelay] = useState<Relay | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let disposed = false;
    let controller: AbortController | null = null;

    const load = async () => {
      if (document.visibilityState === "hidden") return;
      controller?.abort();
      controller = new AbortController();
      try {
        const response = await fetch(RELAY_URL, { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error("relay unavailable");
        const payload = await response.json();
        if (!disposed) { setRelay(payload); setFailed(false); }
      } catch (error) {
        if (!disposed && (error as Error).name !== "AbortError") setFailed(true);
      }
    };

    void load();
    const timer = window.setInterval(() => void load(), 3000);
    return () => { disposed = true; controller?.abort(); window.clearInterval(timer); };
  }, []);

  const packetAgeMs = relay?.generated_at
    ? Math.max(0, Date.now() - Date.parse(relay.generated_at))
    : null;
  const streamRows = relay?.streams ? Object.values(relay.streams) : [];
  const streamAges = streamRows
    .map((stream) => stream.age_ms)
    .filter((age): age is number => age != null && Number.isFinite(age));
  const slowestStreamAgeMs = streamAges.length ? Math.max(...streamAges) : null;
  const connected = streamRows.length > 0
    && streamRows.every((stream) => stream.connected !== false);
  const state = !relay
    ? (failed ? "offline" : "connecting")
    : connected && (packetAgeMs ?? Infinity) < 10_000 && (slowestStreamAgeMs ?? Infinity) < 15_000
      ? "live"
      : "delayed";
  const rows = useMemo(
    () => ORDER.flatMap((symbol) => relay?.symbols?.[symbol] ? [[symbol, relay.symbols[symbol]] as const] : []),
    [relay],
  );

  return (
    <section className="relay" aria-label="Real-time crypto venue relay" aria-busy={!relay}>
      <div className="relay-head">
        <div>
          <span className={`relay-state relay-state--${state}`}><i />{state}</span>
          <span className="relay-title">Venue microstructure</span>
        </div>
        <div className="relay-meta">
          Binance spot + USD-M futures · relayed by Undertow · {packetAgeMs == null
            ? "waiting for first tick"
            : `packet ${(packetAgeMs / 1000).toFixed(1)}s · slowest stream ${slowestStreamAgeMs == null ? "unknown" : `${(slowestStreamAgeMs / 1000).toFixed(1)}s`}`}
        </div>
      </div>
      {rows.length ? (
        <div className="relay-grid">
          {rows.map(([symbol, quote]) => {
            const mid = quote.book?.mid;
            const change = quote.stats24h?.chg_open == null ? undefined : quote.stats24h.chg_open * 100;
            const basis = mid && quote.perp?.mark != null ? (quote.perp.mark / mid - 1) * 10_000 : undefined;
            const funding = quote.perp?.funding == null ? undefined : quote.perp.funding * 10_000;
            return (
              <div className="relay-quote" key={symbol}>
                <div className="relay-symbol">{symbol}<span>/USDT</span></div>
                <div className="relay-price">{px(mid)}</div>
                <div className="relay-detail">
                  <span className={(change ?? 0) >= 0 ? "up" : "down"}>{signed(change, 2, "%")} 24h</span>
                  <span>{signed(quote.book?.spread_bps, 3)}bp spread</span>
                  <span>{signed(basis, 2)}bp basis</span>
                  <span>{signed(funding, 3)}bp funding</span>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="relay-empty">{failed ? "The venue relay is unavailable. Official-data engines remain intact." : "Opening the relay…"}</div>
      )}
      <div className="relay-foot">
        This is the genuinely real-time layer. Funding, reserves, Treasury cash and positioning retain their publishers' native daily or weekly clocks.
      </div>
    </section>
  );
}
