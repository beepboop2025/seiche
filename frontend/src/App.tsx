import { useEffect, useState } from "react";
import { API_BASE } from "./apiBase";
import { Any, fmt } from "./lib";
import Board from "./tabs/Board";
import Helm from "./tabs/Helm";
import Market from "./tabs/Market";
import Forecast from "./tabs/Forecast";
import Physics from "./tabs/Physics";
import Global from "./tabs/Global";
import Calendar from "./tabs/Calendar";
import Positioning from "./tabs/Positioning";
import Resonance from "./tabs/Resonance";
import TimeMachine from "./tabs/TimeMachine";
import Proof from "./tabs/Proof";
import System from "./tabs/System";

const TABS = [
  "BOARD", "FORECAST", "PHYSICS", "HELM", "MARKET", "GLOBAL", "CALENDAR", "POSITIONING",
  "RESONANCE", "TIME MACHINE", "PROOF", "SYSTEM",
] as const;
type Tab = (typeof TABS)[number];

const hashToTab = (): Tab => {
  const h = decodeURIComponent(window.location.hash.replace("#", "")).toUpperCase();
  return (TABS as readonly string[]).includes(h) ? (h as Tab) : "BOARD";
};

export default function App() {
  const [snap, setSnap] = useState<Any | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const [tab, setTab] = useState<Tab>(hashToTab());

  // Live API first (dev / self-hosted); fall back to the static snapshot
  // published by CI (Cloudflare Pages deploy has no backend process).
  const load = () =>
    fetch(`${API_BASE}/api/overview`)
      .then((r) => {
        const ct = r.headers.get("content-type") ?? "";
        if (!r.ok || !ct.includes("json")) throw new Error("no live api");
        setLive(true);
        return r.json();
      })
      .catch(() =>
        fetch("data/overview.json").then((r) => {
          if (!r.ok) throw new Error("no live API and no static snapshot");
          setLive(false);
          return r.json();
        })
      )
      .then(setSnap)
      .catch((e) => setErr(String(e)));

  useEffect(() => {
    load();
    const t = setInterval(load, 5 * 60 * 1000);
    const onHash = () => setTab(hashToTab());
    window.addEventListener("hashchange", onHash);
    return () => { clearInterval(t); window.removeEventListener("hashchange", onHash); };
  }, []);

  if (err) return <div className="app"><div className="faults">API unreachable: {err}</div></div>;
  if (!snap) return <div className="app"><div className="loading">SEICHE · sounding the basin…</div></div>;

  const c = snap.engines?.composite ?? {};

  return (
    <div className="app">
      <div className="masthead">
        <div className="wordmark">SEI<span>CHE</span></div>
        <div className="tagline">funding-stress &amp; leveraged-positioning early warning · free public data only</div>
        <div className="mastindex">
          <span className="mastvalue">{fmt(c.value, 0)}</span>
          <span className={`regime ${c.regime}`} style={{ fontSize: 10, padding: "3px 8px" }}>{c.regime}</span>
        </div>
        <div className="right">
          {live ? "live" : "static snapshot"} · generated {snap.generated_at?.slice(0, 16).replace("T", " ")}Z<br />
          FRED · NY Fed · OFR · FiscalData · CFTC · ECB
        </div>
      </div>

      <nav className="tabs">
        {TABS.map((t) => (
          <a
            key={t}
            href={`#${t.toLowerCase()}`}
            className={t === tab ? "active" : ""}
            onClick={() => setTab(t)}
          >
            {t}
          </a>
        ))}
      </nav>

      {snap.faults?.length > 0 && tab !== "SYSTEM" && (
        <div className="faults">
          {snap.faults.length} source fault(s): {snap.faults.map((f: Any) => f.source).join(", ")} —
          affected inputs degraded or dead, composite coverage reduced accordingly (details in SYSTEM)
        </div>
      )}

      {tab === "BOARD" && <Board snap={snap} live={live} />}
      {tab === "FORECAST" && <Forecast snap={snap} />}
      {tab === "PHYSICS" && <Physics snap={snap} />}
      {tab === "HELM" && <Helm snap={snap} />}
      {tab === "MARKET" && <Market snap={snap} />}
      {tab === "GLOBAL" && <Global snap={snap} />}
      {tab === "CALENDAR" && <Calendar snap={snap} />}
      {tab === "POSITIONING" && <Positioning snap={snap} />}
      {tab === "RESONANCE" && <Resonance snap={snap} />}
      {tab === "TIME MACHINE" && <TimeMachine live={live} />}
      {tab === "PROOF" && <Proof snap={snap} />}
      {tab === "SYSTEM" && <System snap={snap} live={live} />}

      <div className="footer">
        SEICHE — a standing wave in an enclosed basin, invisible until it sloshes over the edge. ·
        Not investment advice. All data from free public APIs with their native lags (COT is T+3 by construction; that lag is shown, never hidden). ·
        Composite weights are editorial and live in backend/seiche/config.py.
      </div>
    </div>
  );
}
