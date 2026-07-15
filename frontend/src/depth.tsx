/**
 * The Sounding — the terminal's reading depth.
 *
 * One board, three depths (the drillboard idea: a hierarchy of views over the
 * same data, not different dashboards):
 *   GLANCE — the verdict and the Tell, nothing else. The skim read.
 *   DESK   — the working board. Default.
 *   DEEP   — full fathom: every method note and engine internal surfaces.
 *
 * The level lives on <html data-depth> so plain CSS can gate density in every
 * tab without per-tab wiring; React reads it via useDepth for structural
 * changes (the Board's glance layout). Switches ride the View Transitions API
 * like tab changes, so the reader's mental map survives the re-layout.
 */
import { createContext, useCallback, useContext, useEffect, useRef, useState, ReactNode } from "react";
import { flushSync } from "react-dom";

export type Depth = "glance" | "desk" | "deep";
export const DEPTHS: readonly Depth[] = ["glance", "desk", "deep"] as const;

const KEY = "seiche-depth";

const read = (): Depth => {
  // ?depth= wins so a reading depth can be shared in a link; localStorage
  // remembers the reader's own setting between visits.
  const q = new URLSearchParams(window.location.search).get("depth");
  if (q === "glance" || q === "desk" || q === "deep") return q;
  try {
    const v = localStorage.getItem(KEY);
    return v === "glance" || v === "deep" ? v : "desk";
  } catch {
    return "desk";
  }
};

const Ctx = createContext<{
  depth: Depth;
  setDepth: (d: Depth) => void;
  stepDepth: (dir: -1 | 1) => void;
}>({ depth: "desk", setDepth: () => {}, stepDepth: () => {} });

export const useDepth = () => useContext(Ctx);

export function DepthProvider({ children }: { children: ReactNode }) {
  const [depth, set] = useState<Depth>(read);
  const cur = useRef(depth);
  cur.current = depth;

  // Stable identity: key handlers registered once can hold this safely.
  const setDepth = useCallback((d: Depth) => {
    if (d === cur.current) return;
    try { localStorage.setItem(KEY, d); } catch { /* private mode */ }
    const doc = document as any;
    if (doc.startViewTransition && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      doc.startViewTransition(() => flushSync(() => set(d)));
    } else {
      set(d);
    }
  }, []);

  // Step one level shallower ("[") or deeper ("]").
  const stepDepth = useCallback((dir: -1 | 1) => {
    const i = DEPTHS.indexOf(cur.current) + dir;
    setDepth(DEPTHS[Math.max(0, Math.min(DEPTHS.length - 1, i))]);
  }, [setDepth]);

  useEffect(() => {
    document.documentElement.dataset.depth = depth;
  }, [depth]);

  return <Ctx.Provider value={{ depth, setDepth, stepDepth }}>{children}</Ctx.Provider>;
}

export function DepthDial() {
  const { depth, setDepth } = useDepth();
  return (
    <div
      className="sounding"
      role="radiogroup"
      aria-label="reading depth"
      title="sounding — how much of the board to surface ( [ shallower · ] deeper )"
    >
      {DEPTHS.map((d) => (
        <button
          key={d}
          role="radio"
          aria-checked={d === depth}
          className={d === depth ? "on" : ""}
          onClick={() => setDepth(d)}
        >
          {d}
        </button>
      ))}
    </div>
  );
}
