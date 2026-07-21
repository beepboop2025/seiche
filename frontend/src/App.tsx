import { useEffect, useState, lazy, Suspense, type CSSProperties } from "react";
import { flushSync } from "react-dom";
import Lenis from "lenis";
import { API_BASE } from "./apiBase";
import { authHeaders } from "./auth";
import { Any } from "./lib";
import { AppSkeleton, TabSkeleton } from "./Skeleton";
import { Command } from "./commands";
import { useDepth, DepthDial } from "./depth";
import { useAttentionMarks } from "./attention";
import Basin from "./Basin";
import Tape from "./Tape";
import DepthRail from "./DepthRail";
import Descent, { shouldDescend } from "./Descent";
import { MotionProvider, MotionToggle } from "./motion/motionMode";
import WaveTank from "./motion/WaveTank";
import Gauge from "./motion/Gauge";
import Odo from "./motion/Odo";
import LivePulse from "./motion/LivePulse";
import { useChangeFlash } from "./motion/useLive";

const CommandPalette = lazy(() => import("./CommandPalette"));

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

// BOARD leads: the instrument itself is the front door — the index, the
// regime, the dive. Hash routing stays authoritative: any #tab in the URL
// wins, and GLOBAL is one keystroke away for arrivals who want their own
// water line first.
const TABS = [
  "GLOBAL", "BOARD", "FORECAST", "PHYSICS", "HELM", "MARKET", "CALENDAR", "POSITIONING",
  "RESONANCE", "TIME MACHINE", "PROOF", "SYSTEM", "ACCOUNT", "DISPATCHES",
] as const;
type Tab = (typeof TABS)[number];

const DEFAULT_TAB: Tab = "BOARD";

const hashToTab = (): Tab => {
  const raw = decodeURIComponent(window.location.hash.replace("#", ""));
  const h = raw.split("/")[0].toUpperCase();
  return (TABS as readonly string[]).includes(h) ? (h as Tab) : DEFAULT_TAB;
};

export default function App() {
  return (
    <MotionProvider>
      <AppInner />
    </MotionProvider>
  );
}

function AppInner() {
  const [snap, setSnap] = useState<Any | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const [tab, setTab] = useState<Tab>(hashToTab());
  const [palette, setPalette] = useState(false);
  const [help, setHelp] = useState(false);
  const [descending, setDescending] = useState(shouldDescend);
  const { setDepth, stepDepth } = useDepth();
  // direction-flash for the masthead composite (up = amber, down = blue)
  const flash = useChangeFlash(snap?.engines?.composite?.value);

  // unseen-panel marks re-arm on every tab visit
  useAttentionMarks(tab);

  // Tab switches ride the View Transitions API where it exists: the old view
  // cross-dissolves into the new one on the compositor. Falls back to the
  // plain state change everywhere else.
  const switchTab = (t: Tab) => {
    const doc = document as Any;
    if (doc.startViewTransition && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      doc.startViewTransition(() => flushSync(() => setTab(t)));
    } else {
      setTab(t);
    }
  };

  const goTab = (t: Tab, sub?: string) => {
    window.location.hash = sub ? `${t.toLowerCase()}/${sub}` : t.toLowerCase();
    switchTab(t);
  };

  const onCommand = (cmd: Command) => {
    if (cmd.type === "tab") goTab(cmd.tab as Tab);
    else if (cmd.type === "asof") goTab("TIME MACHINE", cmd.date);
    else if (cmd.type === "href") window.location.href = cmd.url;
    else if (cmd.type === "depth") setDepth(cmd.level);
  };

  // Live API first (dev / self-hosted); fall back to the full snapshot CI
  // bakes into the static build — the board should render even when the box
  // is unreachable, just marked "static snapshot" instead of "live".
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
      .catch((apiErr) =>
        fetch("/data/overview.json")
          .then((r) => {
            const ct = r.headers.get("content-type") ?? "";
            if (!r.ok || !(ct.includes("json") || ct.includes("octet"))) throw apiErr;
            return r.json();
          })
          .then((data) => { setSnap(data); setLive(false); setErr(null); })
          .catch(() => setErr(String(apiErr.message ?? apiErr))));

  const retry = () => { setErr(null); setSnap(null); load(); };

  useEffect(() => {
    load();
    const t = setInterval(load, 5 * 60 * 1000);
    const onHash = () => switchTab(hashToTab());
    window.addEventListener("hashchange", onHash);
    return () => { clearInterval(t); window.removeEventListener("hashchange", onHash); };
  }, []);

  // Momentum scroll: Lenis wraps native scroll (sticky, anchors and a11y keep
  // working) and gives the page its water weight. Reduced motion opts out.
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const lenis = new Lenis({ lerp: 0.12, wheelMultiplier: 0.9 });
    let raf = 0;
    const loop = (time: number) => { lenis.raf(time); raf = requestAnimationFrame(loop); };
    raf = requestAnimationFrame(loop);
    return () => { cancelAnimationFrame(raf); lenis.destroy(); };
  }, []);

  // The command line: ⌘K / Ctrl+K anywhere, `/` outside inputs, Ctrl+1..9 tabs,
  // `[` / `]` step the sounding shallower / deeper, `?` the shortcut overlay.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      const typing = el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((p) => !p);
      } else if (e.key === "/" && !typing) {
        e.preventDefault();
        setPalette(true);
      } else if (e.key === "?" && !typing) {
        e.preventDefault();
        setHelp((h) => !h);
      } else if (e.key === "Escape") {
        setHelp(false);
      } else if ((e.key === "[" || e.key === "]") && !typing && !e.ctrlKey && !e.metaKey) {
        stepDepth(e.key === "[" ? -1 : 1);
      } else if ((e.ctrlKey || e.metaKey) && e.key >= "1" && e.key <= "9") {
        const t = TABS[parseInt(e.key, 10) - 1];
        if (t) { e.preventDefault(); window.location.hash = t.toLowerCase(); setTab(t); }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Fully open: the whole terminal renders for everyone, no sign in.
  // Accounts exist only for optional email alerts (ACCOUNT tab).
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
          </div>
        </div>
      </div>
    );
  }
  if (!snap) return <div className="app"><AppSkeleton /></div>;

  const c = snap.engines?.composite ?? {};

  if (descending) {
    return (
      <>
        <Basin value={c.value ?? null} regime={c.regime ?? null} />
        <Descent snap={snap} onDone={() => setDescending(false)} />
      </>
    );
  }

  return (
    <div className="app">
      <Basin value={c.value ?? null} regime={c.regime ?? null} />
      <DepthRail />
      <div className="masthero">
        <WaveTank value={c.value ?? null} regime={c.regime ?? null} />
        <div className="masthead">
          <div className="wordmark">SEI<span>CHE</span></div>
          <div className="tagline">funding-stress &amp; leveraged-positioning early warning · free public data only</div>
          <a className="prolink" href="/guide.html">new? how to read this</a>
          <div className="mastindex">
            {/* sonar: ping period tightens as the composite rises — CALM pings
                lazily, STRESS pings urgently. The gauge needle sweeps up on
                first paint and the odometer digits roll on every refresh. */}
            <Gauge v={c.value ?? null} size={44} />
            <span
              className={`mastvalue sonar${flash ? ` flash-${flash}` : ""}`}
              style={{ "--ping": `${(3.6 - 2.6 * Math.min(1, (c.value ?? 20) / 100)).toFixed(2)}s` } as CSSProperties}
            >
              <Odo v={c.value} d={0} />
            </span>
            <span className={`regime ${c.regime}`} style={{ fontSize: 10, padding: "3px 8px" }}>{c.regime}</span>
            <DepthDial />
            <MotionToggle />
          </div>
          <div className="right">
            <LivePulse snap={snap} /><br />
            {live ? "live" : "static snapshot"} · generated {snap.generated_at?.slice(0, 16).replace("T", " ")}Z<br />
            FRED · NY Fed · OFR · FiscalData · CFTC · ECB<br />
            <a className="prolink" href="/support.html">free · support Seiche</a>
          </div>
        </div>
      </div>

      <Tape snap={snap} />

      <nav className="tabs">
        {TABS.map((t) => (
          <a
            key={t}
            href={`#${t.toLowerCase()}`}
            className={t === tab ? "active" : ""}
            onClick={(e) => { e.preventDefault(); goTab(t); }}
          >
            {t}
          </a>
        ))}
        <button className="cmdk" onClick={() => setPalette(true)} title="command line — function codes or search">⌘K</button>
        <button className="cmdk" onClick={() => setHelp(true)} title="keyboard shortcuts">?</button>
      </nav>

      {help && (
        <div className="kshort-backdrop" onClick={() => setHelp(false)}>
          <div className="kshort" role="dialog" aria-label="keyboard shortcuts" onClick={(e) => e.stopPropagation()}>
            <h2>Keyboard shortcuts</h2>
            <div className="row"><span>command line — function codes (BRD, FCT, SWELL…) or search</span><kbd>⌘K</kbd></div>
            <div className="row"><span>command line (same)</span><kbd>/</kbd></div>
            <div className="row"><span>this panel</span><kbd>?</kbd></div>
            <div className="row"><span>sounding shallower / deeper (glance · desk · deep)</span><kbd>[ &nbsp; ]</kbd></div>
            <div className="row"><span>jump to tab 1–9</span><kbd>Ctrl/⌘ 1–9</kbd></div>
            <div className="row"><span>replay a date — type it in the command line</span><kbd>ASOF 2019-09-16</kbd></div>
            <div className="row"><span>close</span><kbd>Esc</kbd></div>
            <div className="foot">hash routing is authoritative — any #tab in the URL wins.</div>
          </div>
        </div>
      )}

      {palette && (
        <Suspense fallback={null}>
          <CommandPalette onClose={() => setPalette(false)} onCommand={onCommand} />
        </Suspense>
      )}

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
        <a href="https://t.me/seiche_desk_bot" style={{ color: "var(--dim)" }}>@seiche_desk_bot on Telegram</a> ·{" "}
        <a href="/terms.html" style={{ color: "var(--faint)" }}>terms</a> ·{" "}
        <a href="/privacy.html" style={{ color: "var(--faint)" }}>privacy</a>
        <br />
        Built by the team behind <a href="https://liquilens.in" style={{ color: "var(--dim)" }}>LiquiLens</a>, the failure radar for banks and lenders.
        <br />
        New from the same lab: <a href="https://liquilens.in/undertow/" style={{ color: "var(--dim)" }}>Undertow</a>, the cross market liquidity map. Seiche watches the plumbing; Undertow watches whether the market will still be there when you exit.
      </div>
    </div>
  );
}
