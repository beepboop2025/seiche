import { Fragment, useEffect, useRef, useState, lazy, Suspense, type CSSProperties } from "react";
import { flushSync } from "react-dom";
import { API_BASE } from "./apiBase";
import { authHeaders } from "./auth";
import { Any } from "./lib";
import { AppSkeleton, TabSkeleton } from "./Skeleton";
import type { Command } from "./commands";
import { useDepth, DepthDial } from "./depth";
import { useAttentionMarks } from "./attention";
import Tape from "./Tape";
import DepthRail from "./DepthRail";
import { shouldDescend } from "./descentGate";
import { MotionProvider, MotionToggle } from "./motion/motionMode";
import Gauge from "./motion/Gauge";
import Odo from "./motion/Odo";
import LivePulse from "./motion/LivePulse";
import { useChangeFlash } from "./motion/useLive";

const CommandPalette = lazy(() => import("./CommandPalette"));
const Basin = lazy(() => import("./Basin"));
const Descent = lazy(() => import("./Descent"));
const WaveTank = lazy(() => import("./motion/WaveTank"));

const COMPACT_DEVICE_QUERY = "(max-width: 800px), (pointer: coarse)";
const LIVE_UPGRADE_DELAY_MS = 1500;
const BOARD_REFRESH_MS = 60_000;

const runWhenIdle = (task: () => void, timeout: number): (() => void) => {
  const requestIdle = window.requestIdleCallback?.bind(window);
  if (typeof requestIdle === "function") {
    const idleId = requestIdle(task, { timeout });
    return () => window.cancelIdleCallback(idleId);
  }
  const timer = globalThis.setTimeout(task, Math.min(timeout, 1200));
  return () => globalThis.clearTimeout(timer);
};

// Tabs are code-split: only the one you open ships its JS. This keeps the
// first paint small and fast; each chunk streams in behind a skeleton.
const Dispatches = lazy(() => import("./tabs/Dispatches"));
const Today = lazy(() => import("./tabs/Today"));
const Board = lazy(() => import("./tabs/Board"));
const Forecast = lazy(() => import("./tabs/Forecast"));
const Physics = lazy(() => import("./tabs/Physics"));
const Helm = lazy(() => import("./tabs/Helm"));
const Market = lazy(() => import("./tabs/Market"));
const Global = lazy(() => import("./tabs/Global"));
const Estuary = lazy(() => import("./tabs/Estuary"));
const OilFunding = lazy(() => import("./tabs/OilFunding"));
const Calendar = lazy(() => import("./tabs/Calendar"));
const Scarcity = lazy(() => import("./tabs/Scarcity"));
const Supply = lazy(() => import("./tabs/Supply"));
const Positioning = lazy(() => import("./tabs/Positioning"));
const Resonance = lazy(() => import("./tabs/Resonance"));
const TimeMachine = lazy(() => import("./tabs/TimeMachine"));
const Proof = lazy(() => import("./tabs/Proof"));
const Referee = lazy(() => import("./tabs/Referee"));
const System = lazy(() => import("./tabs/System"));
const Account = lazy(() => import("./tabs/Account"));

// TODAY leads with the argument, its evidence and its countercase. The full
// terminal remains one click away in BOARD, but a reader no longer has to
// reverse-engineer the desk's view from seventeen instruments before learning
// what matters. Hash routing stays authoritative, so every existing deep link
// continues to resolve by name.
// DISPATCHES sits second because the frozen letters are the public record of
// what the desk said before outcomes arrived.
// GLOBAL, FX×MATERIALS and OIL×FUNDING follow the core board: offshore-dollar
// coupling, the physical-cash transmission channel and the barrel's
// bidirectional funding loop are context surfaces, never hidden composite
// inputs. SCARCITY and SUPPLY then carry the two forward-looking Fed plumbing
// views. Digit shortcuts index TABS positionally; hash routes remain name-based
// and therefore stable.
const TABS = [
  "TODAY", "DISPATCHES", "BOARD", "GLOBAL", "FX×MATERIALS", "OIL×FUNDING", "SCARCITY", "SUPPLY", "FORECAST", "PHYSICS", "HELM", "MARKET",
  "CALENDAR", "POSITIONING", "RESONANCE", "TIME MACHINE", "PROOF", "REFEREE", "SYSTEM", "ACCOUNT",
] as const;
type Tab = (typeof TABS)[number];

// Unchanged, and deliberately: every existing deep link is a #tab hash that
// hashToTab resolves by NAME, so reordering above moves nothing. Changing this
// would silently redirect every bare seiche.info/ bookmark that expects the
// board, which is a different decision from promoting a tab.
const DEFAULT_TAB: Tab = "TODAY";

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
  const [compactDevice] = useState(() => window.matchMedia(COMPACT_DEVICE_QUERY).matches);
  const { setDepth, stepDepth } = useDepth();
  // direction-flash for the masthead composite (up = amber, down = blue)
  const flash = useChangeFlash(snap?.engines?.composite?.value);

  // unseen-panel marks re-arm on every tab visit
  useAttentionMarks(tab);

  // Sharing is useful after the board is readable, not while it is becoming
  // readable. Keep its canvas/composition code out of the entry bundle and
  // mount the card observer only after the browser has an idle window.
  useEffect(() => {
    let disposed = false;
    let unmount: (() => void) | undefined;
    let cancelIdle: () => void = () => undefined;
    const delay = window.setTimeout(() => {
      cancelIdle = runWhenIdle(() => {
        void import("./cardShare").then(({ mountCardShare }) => {
          if (!disposed) unmount = mountCardShare();
        });
      }, 2000);
    }, compactDevice ? 10_000 : 800);
    return () => {
      disposed = true;
      window.clearTimeout(delay);
      cancelIdle();
      unmount?.();
    };
  }, [compactDevice]);

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

  // Boot from the snapshot CI bakes into the static build, then upgrade to
  // live after the snapshot has had a paint opportunity. The two payloads are
  // each ~150 KB; racing them made the API and the same-origin fallback fight
  // for the mobile critical path even though either one can draw the board.
  // A missing snapshot still falls straight through to the live API.
  const gotLive = useRef(false);
  const liveUpgradeTimer = useRef<number | null>(null);

  const loadSnapshot = () =>
    fetch("/data/overview.json", { credentials: "omit" })
      .then((r) => {
        const ct = r.headers.get("content-type") ?? "";
        if (!r.ok || !(ct.includes("json") || ct.includes("octet"))) throw new Error("snapshot unavailable");
        return r.json();
      })
      .then((data) => {
        if (gotLive.current) return; // never replace live data with the baked copy
        setSnap(data); setLive(false); setErr(null);
      });

  const loadApi = (timeoutMs = 6000) => {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeoutMs);
    return fetch(`${API_BASE}/api/overview`, { headers: authHeaders(), signal: ctl.signal })
      .then((r) => {
        if (r.status === 401) throw new Error("session expired — sign in again");
        const ct = r.headers.get("content-type") ?? "";
        if (!r.ok || !ct.includes("json")) throw new Error("the board is temporarily unreachable — retry in a moment");
        return r.json();
      })
      .then((data) => { gotLive.current = true; setSnap(data); setLive(true); setErr(null); })
      .finally(() => clearTimeout(timer));
  };

  const boot = () => {
    void loadSnapshot().then(
      () => {
        // Give the snapshot and Week Ahead the first-paint window before the
        // cross-origin refresh competes for mobile bandwidth and parse time.
        // Failure remains non-fatal: the visible snapshot is timestamped.
        liveUpgradeTimer.current = window.setTimeout(() => {
          void loadApi().catch(() => undefined);
        }, LIVE_UPGRADE_DELAY_MS);
      },
      () => {
        void loadApi().catch((reason) => {
          setErr(String((reason as Error)?.message ?? reason));
        });
      },
    );
  };

  const retry = () => {
    if (liveUpgradeTimer.current !== null) clearTimeout(liveUpgradeTimer.current);
    setErr(null); setSnap(null); boot();
  };

  useEffect(() => {
    boot();
    // The server's ETag + 60-second public cache make unchanged checks cheap.
    // Visible tabs learn about a newly assembled official-data snapshot within
    // a minute; hidden tabs skip the request entirely.
    const t = setInterval(() => {
      if (document.visibilityState === "visible") void loadApi().catch(() => undefined);
    }, BOARD_REFRESH_MS);
    const onHash = () => switchTab(hashToTab());
    window.addEventListener("hashchange", onHash);
    return () => {
      clearInterval(t);
      if (liveUpgradeTimer.current !== null) clearTimeout(liveUpgradeTimer.current);
      window.removeEventListener("hashchange", onHash);
    };
  }, []);

  // Momentum scroll: Lenis wraps native scroll (sticky, anchors and a11y keep
  // working) and gives the page its water weight. Native scrolling is faster
  // and more predictable on compact/coarse-pointer devices; desktop loads the
  // enhancement after the core terminal has yielded once.
  useEffect(() => {
    if (compactDevice || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let disposed = false;
    let stop: (() => void) | undefined;
    const cancelIdle = runWhenIdle(() => {
      void import("lenis").then(({ default: Lenis }) => {
        if (disposed) return;
        const lenis = new Lenis({ lerp: 0.12, wheelMultiplier: 0.9 });
        let raf = 0;
        const loop = (time: number) => { lenis.raf(time); raf = requestAnimationFrame(loop); };
        raf = requestAnimationFrame(loop);
        stop = () => { cancelAnimationFrame(raf); lenis.destroy(); };
      });
    }, 1800);
    return () => {
      disposed = true;
      cancelIdle();
      stop?.();
    };
  }, [compactDevice]);

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
      <main className="app">
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
      </main>
    );
  }
  if (!snap) return <main className="app"><AppSkeleton /></main>;

  const c = snap.engines?.composite ?? {};
  const compositeCoverage = typeof c.coverage_pct === "number" ? c.coverage_pct : null;
  const compositeUnavailable = c.ok === false;
  const compositeFaulted = compositeUnavailable || (c.dead_inputs?.length ?? 0) > 0
    || (compositeCoverage !== null && compositeCoverage < 100);
  const faultImpact = compositeUnavailable
    ? `composite unavailable${c.reason ? `: ${c.reason}` : ""}`
    : compositeFaulted
    ? `affected composite inputs degraded or dead; composite coverage${compositeCoverage === null ? " reduced" : ` reduced to ${compositeCoverage.toFixed(1)}%`}`
    : compositeCoverage === null
      ? "dependent views may be degraded"
      : `dependent views may be degraded; composite coverage remains ${compositeCoverage.toFixed(1)}%`;

  if (descending) {
    return (
      <>
        {!compactDevice && <Suspense fallback={null}><Basin value={c.value ?? null} regime={c.regime ?? null} /></Suspense>}
        <Suspense fallback={<div className="app"><AppSkeleton /></div>}>
          <Descent snap={snap} onDone={() => setDescending(false)} />
        </Suspense>
      </>
    );
  }

  return (
    <main className="app">
      {!compactDevice && tab !== "TODAY" && <Suspense fallback={null}><Basin value={c.value ?? null} regime={c.regime ?? null} /></Suspense>}
      <DepthRail />
      <div className={`masthero${tab === "TODAY" ? " masteditorial" : ""}`}>
        {tab === "TODAY" ? null : compactDevice
          ? <div className="wavetank" aria-hidden="true" />
          : <Suspense fallback={<div className="wavetank" aria-hidden="true" />}>
              <WaveTank value={c.value ?? null} regime={c.regime ?? null} />
            </Suspense>}
        <div className="masthead">
          <div className="wordmark">SEI<span>CHE</span></div>
          <div className="tagline">funding-stress &amp; leveraged-positioning early warning · free public data only</div>
          <a className="prolink" href="/guide">new? how to read this</a>
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
            <a className="prolink" href="/support">free · support Seiche</a>
          </div>
        </div>
      </div>

      {tab !== "TODAY" && <Tape snap={snap} />}

      {tab !== "TODAY" && <aside className="agent-launch" aria-label="Seiche API and MCP access">
        <span className="agent-launch__eyebrow">BUILD WITH THE LIVE BOARD</span>
        <span className="agent-launch__copy">Give an AI agent the current funding-stress regime, analogs and published track record.</span>
        <a href="/developers">Connect the free MCP or API →</a>
      </aside>}

      <nav className="tabs">
        <a href="/investigations/" aria-label="Seiche articles, daily analysis and reviewed investigations">
          ARTICLES
        </a>
        {TABS.map((t) => (
          <Fragment key={t}>
            <a
              href={`#${t.toLowerCase()}`}
              className={t === tab ? "active" : ""}
              onClick={(e) => { e.preventDefault(); goTab(t); }}
            >
              {t}
            </a>
            {t === "BOARD" && (
              <a href="/use-cases" aria-label="Seiche use cases and selection guide">
                USE CASES
              </a>
            )}
          </Fragment>
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
          {snap.faults.length} source fault(s): {snap.faults.map((f: Any) => f.source).join(", ")}{" — "}
          {faultImpact} (details in SYSTEM)
        </div>
      )}

      <Suspense fallback={<TabSkeleton />}>
        <div className="tabview" key={tab}>
          {tab === "TODAY" && <Today snap={snap} live={live} />}
          {tab === "DISPATCHES" && <Dispatches />}
          {tab === "BOARD" && <Board snap={snap} live={live} />}
          {tab === "SCARCITY" && <Scarcity snap={snap} />}
          {tab === "SUPPLY" && <Supply snap={snap} />}
          {tab === "FORECAST" && <Forecast snap={snap} />}
          {tab === "PHYSICS" && <Physics snap={snap} />}
          {tab === "HELM" && <Helm snap={snap} />}
          {tab === "MARKET" && <Market snap={snap} />}
          {tab === "GLOBAL" && <Global snap={snap} />}
          {tab === "FX×MATERIALS" && <Estuary snap={snap} />}
          {tab === "OIL×FUNDING" && <OilFunding snap={snap} />}
          {tab === "CALENDAR" && <Calendar snap={snap} />}
          {tab === "POSITIONING" && <Positioning snap={snap} />}
          {tab === "RESONANCE" && <Resonance snap={snap} />}
          {tab === "TIME MACHINE" && <TimeMachine live={live} />}
          {tab === "PROOF" && <Proof snap={snap} />}
          {tab === "REFEREE" && <Referee snap={snap} />}
          {tab === "SYSTEM" && <System snap={snap} live={live} />}
          {tab === "ACCOUNT" && <Account />}
        </div>
      </Suspense>

      <aside className="network-rail" aria-labelledby="network-rail-title">
        <div className="network-rail__head">
          <span>THE STRESS CHAIN</span>
          <strong id="network-rail-title">Follow the pressure after it leaves the plumbing.</strong>
        </div>
        <div className="network-rail__links">
          <a href="https://liquilens.in/">
            <b>02 / INSTITUTION</b>
            <span>LiquiLens</span>
            <small>See which lender should feel funding stress first.</small>
          </a>
          <a href="https://liquilens-undertow.com/">
            <b>03 / MARKET</b>
            <span>Undertow</span>
            <small>See what a position-sized exit could cost.</small>
          </a>
          <a href="https://myquantdoesntspeakenglish.com/">
            <b>04 / EDITORIAL WIRE</b>
            <span>My Quant Doesn’t Speak English</span>
            <small>Read every Seiche, LiquiLens and Undertow dispatch together.</small>
          </a>
        </div>
      </aside>

      <aside className="telegram-rail" aria-labelledby="telegram-rail-title">
        <div className="telegram-rail__head">
          <span>THE SONAR INBOX</span>
          <strong id="telegram-rail-title">Choose the alert surface that matches the question.</strong>
          <small>Every destination is opt-in. The website shares no visitor data with Telegram.</small>
        </div>
        <div className="telegram-rail__links">
          <a className="primary" href="https://t.me/seiche_desk_bot?start=site_ecosystem" target="_blank" rel="noopener noreferrer">
            <b>THIS INSTRUMENT</b><span>@seiche_desk_bot</span><small>Funding regime, week ahead, sources, and the public record.</small>
          </a>
          <a href="https://t.me/LiquiLens_bot?start=seiche_ecosystem" target="_blank" rel="noopener noreferrer">
            <b>INSTITUTIONS</b><span>@LiquiLens_bot</span><small>Which lender should feel the pressure first?</small>
          </a>
          <a href="https://t.me/undertow_LiquiLens_bot?start=ref_seiche_ecosystem" target="_blank" rel="noopener noreferrer">
            <b>MARKET EXITS</b><span>@undertow_LiquiLens_bot</span><small>What could a position-sized exit cost?</small>
          </a>
          <a href="https://t.me/LiquidityLabDesk" target="_blank" rel="noopener noreferrer">
            <b>FREE DAILY CHANNEL</b><span>@LiquidityLabDesk</span><small>One reviewed read across the whole stress chain.</small>
          </a>
        </div>
      </aside>

      <div className="footer">
        SEICHE — a standing wave in an enclosed basin, invisible until it sloshes over the edge. ·
        Not investment advice. All data from free public APIs with their native lags (COT is T+3 by construction; that lag is shown, never hidden). ·
        Composite weights are editorial and live in backend/seiche/config.py.
        <br />
        <a href="mailto:desk@seiche.info" style={{ color: "var(--dim)" }}>desk@seiche.info</a> ·{" "}
        <a href="/guide" style={{ color: "var(--dim)" }}>guide</a> ·{" "}
        <a href="/methodology" style={{ color: "var(--dim)" }}>methodology</a> ·{" "}
        <a href="/ampleness" style={{ color: "var(--dim)" }}>ampleness check</a> ·{" "}
        <a href="/skeptic" style={{ color: "var(--dim)" }}>skeptic pack</a> ·{" "}
        <a href="/developers" style={{ color: "var(--dim)" }}>API + MCP</a> ·{" "}
        <a href="/use-cases" style={{ color: "var(--dim)" }}>when to use Seiche</a> ·{" "}
        <a href="/support" style={{ color: "var(--dim)" }}>support</a> ·{" "}
        <a href="https://t.me/seiche_desk_bot" style={{ color: "var(--dim)" }}>@seiche_desk_bot on Telegram</a> ·{" "}
        <a href="https://t.me/LiquidityLabDesk" style={{ color: "var(--dim)" }}>the daily read on Telegram</a> ·{" "}
        <a href="/terms" style={{ color: "var(--faint)" }}>terms</a> ·{" "}
        <a href="/privacy" style={{ color: "var(--faint)" }}>privacy</a>
        <br />
        Built by the team behind <a href="https://liquilens.in" style={{ color: "var(--dim)" }}>LiquiLens</a>, the failure radar for financial institutions.
        <br />
        From the same lab: <a href="https://liquilens-undertow.com" style={{ color: "var(--dim)" }}>Undertow</a>, the cross-market liquidity terminal — exit cost at your size, tiers across nine segments, and a sealed record that keeps its own misses. Seiche watches the plumbing; Undertow watches whether the market will still be there when you exit. Live on the MARKET tab.
        <br />
        The full editorial wire: <a href="https://myquantdoesntspeakenglish.com/" style={{ color: "var(--dim)" }}>My Quant Doesn’t Speak English</a> consolidates every Seiche, LiquiLens and Undertow dispatch, plus original investigations.
        <br />
        Sibling project: <a href="https://palimpsest.info" style={{ color: "var(--dim)" }}>Palimpsest</a>, which works the opposite problem. Seiche exists because the Fed publishes its plumbing every week; Palimpsest measures what happens when a state stops publishing, watching China's information controls and the money-market series that go quiet under stress. The CHINA row on this board already reads its keyless CFETS feed.
      </div>
    </main>
  );
}
