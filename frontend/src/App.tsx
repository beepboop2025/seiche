import { useEffect, useState, lazy, Suspense } from "react";
import { API_BASE } from "./apiBase";
import { authHeaders, getToken } from "./auth";
import { Any, fmt } from "./lib";
import FreePage from "./tabs/FreePage";
import { AppSkeleton, TabSkeleton } from "./Skeleton";

// Tabs are code-split: only the one you open ships its JS. This keeps the
// first paint small and fast; each chunk streams in behind a skeleton.
const Dispatches = lazy(() => import("./tabs/Dispatches"));
const Board = lazy(() => import("./tabs/Board"));
const Forecast = lazy(() => import("./tabs/Forecast"));
const Physics = lazy(() => import("./tabs/Physics"));
const Helm = lazy(() => import("./tabs/Helm"));
const Market = lazy(() => import("./tabs/Market"));
const Global = lazy(() => import("./tabs/Global"));
const Calendar = lazy(() => import("./tabs/Calendar"));
const Positioning = lazy(() => import("./tabs/Positioning"));
const Resonance = lazy(() => import("./tabs/Resonance"));
const TimeMachine = lazy(() => import("./tabs/TimeMachine"));
const Proof = lazy(() => import("./tabs/Proof"));
const System = lazy(() => import("./tabs/System"));
const Account = lazy(() => import("./tabs/Account"));

const TABS = [
  "DISPATCHES", "BOARD", "FORECAST", "PHYSICS", "HELM", "MARKET", "GLOBAL", "CALENDAR", "POSITIONING",
  "RESONANCE", "TIME MACHINE", "PROOF", "SYSTEM", "ACCOUNT",
] as const;
type Tab = (typeof TABS)[number];

const hashToTab = (): Tab => {
  const raw = decodeURIComponent(window.location.hash.replace("#", ""));
  const h = raw.split("/")[0].toUpperCase();
  return (TABS as readonly string[]).includes(h) ? (h as Tab) : "DISPATCHES";
};

export default function App() {
  const [snap, setSnap] = useState<Any | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const [tab, setTab] = useState<Tab>(hashToTab());
  const [signedIn, setSignedIn] = useState<boolean>(() => getToken() !== null);

  // Live API first (dev / self-hosted); fall back to the static snapshot
  // published by CI (Cloudflare Pages deploy has no backend process).
  const load = () =>
    fetch(`${API_BASE}/api/overview`, { headers: authHeaders() })
      .then((r) => {
        if (r.status === 401) throw new Error("session expired — sign in again");
        const ct = r.headers.get("content-type") ?? "";
        if (!r.ok || !ct.includes("json")) throw new Error("the board is temporarily unreachable — retry in a moment");
        setLive(true);
        return r.json();
      })
      .then((data) => { setSnap(data); setErr(null); })
      .catch((e) => setErr(String(e.message ?? e)));

  const retry = () => { setErr(null); setSnap(null); load(); };

  useEffect(() => {
    load();
    const t = setInterval(load, 5 * 60 * 1000);
    const onHash = () => setTab(hashToTab());
    window.addEventListener("hashchange", onHash);
    return () => { clearInterval(t); window.removeEventListener("hashchange", onHash); };
  }, []);

  // Not signed in: only the free surface (conclusion + PROOF record).
  if (!signedIn) {
    return <div className="app"><FreePage onSignedIn={() => { setSignedIn(true); load(); }} /></div>;
  }
  if (err) {
    return (
      <div className="app">
        <div className="masthead">
          <div className="wordmark">SEI<span>CHE</span></div>
          <div className="tagline">funding-stress &amp; leveraged-positioning early warning</div>
        </div>
        <div className="errbox">
          <div className="errtitle">The board is temporarily unreachable</div>
          <div className="errmsg">{err}</div>
          <div className="erractions">
            <button className="btn-accent" onClick={retry}>Retry</button>
            <a className="prolink" href="#" onClick={(e) => { e.preventDefault(); setSignedIn(false); }}>← free view</a>
          </div>
        </div>
      </div>
    );
  }
  if (!snap) return <div className="app"><AppSkeleton /></div>;

  const c = snap.engines?.composite ?? {};

  return (
    <div className="app">
      <div className="masthead">
        <div className="wordmark">SEI<span>CHE</span></div>
        <div className="tagline">funding-stress &amp; leveraged-positioning early warning · free public data only</div>
        <a className="prolink" href="/guide.html">new? how to read this</a>
        <div className="mastindex">
          <span className="mastvalue">{fmt(c.value, 0)}</span>
          <span className={`regime ${c.regime}`} style={{ fontSize: 10, padding: "3px 8px" }}>{c.regime}</span>
        </div>
        <div className="right">
          {live ? "live" : "static snapshot"} · generated {snap.generated_at?.slice(0, 16).replace("T", " ")}Z<br />
          FRED · NY Fed · OFR · FiscalData · CFTC · ECB<br />
          <a className="prolink" href="#timemachine">
            {localStorage.getItem("seiche_token") ? "signed in" : "sign in"}
          </a>
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

      <Suspense fallback={<TabSkeleton />}>
        <div className="tabview" key={tab}>
          {tab === "DISPATCHES" && <Dispatches />}
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
          {tab === "ACCOUNT" && <Account />}
        </div>
      </Suspense>

      <div className="footer">
        SEICHE — a standing wave in an enclosed basin, invisible until it sloshes over the edge. ·
        Not investment advice. All data from free public APIs with their native lags (COT is T+3 by construction; that lag is shown, never hidden). ·
        Composite weights are editorial and live in backend/seiche/config.py.
        <br />
        <a href="mailto:desk@seiche.info" style={{ color: "var(--dim)" }}>desk@seiche.info</a> ·{" "}
        <a href="/guide.html" style={{ color: "var(--dim)" }}>guide</a> ·{" "}
        <a href="/support.html" style={{ color: "var(--dim)" }}>support</a> ·{" "}
        <a href="/terms.html" style={{ color: "var(--faint)" }}>terms</a> ·{" "}
        <a href="/privacy.html" style={{ color: "var(--faint)" }}>privacy</a>
      </div>
    </div>
  );
}
