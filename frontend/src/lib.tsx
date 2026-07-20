/** Shared primitives for all tabs. */
import { P } from "./palette";
import { useEffect, useRef, useState } from "react";

export type Any = Record<string, any>;

export const fmt = (v: number | null | undefined, d = 1, unit = "") =>
  v == null
    ? "—"
    : `${v.toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d })}${unit}`;

/**
 * A number that moves instead of swapping. On the five-minute refresh (or a
 * Time-Machine jump) the displayed value tweens to the new reading, so the
 * eye sees the direction of the change, not a flicker. Tabular numerals in
 * the base styles keep the width from jittering mid-tween.
 */
export function Num({ v, d = 1, unit = "", signed = false }:
  { v: number | null | undefined; d?: number; unit?: string; signed?: boolean }) {
  const [shown, setShown] = useState(v);
  const prev = useRef(v);
  const raf = useRef(0);

  useEffect(() => {
    const from = prev.current;
    prev.current = v;
    if (from == null || v == null || from === v ||
        window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setShown(v);
      return;
    }
    const t0 = performance.now();
    const dur = 700;
    const tick = (t: number) => {
      const p = Math.min(1, (t - t0) / dur);
      const e = 1 - Math.pow(1 - p, 3);
      setShown(from + (v - from) * e);
      if (p < 1) raf.current = requestAnimationFrame(tick);
    };
    cancelAnimationFrame(raf.current);
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [v]);

  const sign = signed && shown != null && shown > 0 ? "+" : "";
  return <>{shown == null ? "—" : `${sign}${fmt(shown, d, unit)}`}</>;
}

/**
 * A reading that rolls up from zero on first paint — the "live counter"
 * moment (arXiv 2602.19853: motion as progress review, kept short enough to
 * never be tedious). After the landing it behaves like Num: later refreshes
 * tween in place. Lands with a brief glow exhale (.roll-done).
 */
export function Roll({ v, d = 1, unit = "", dur = 950 }:
  { v: number | null | undefined; d?: number; unit?: string; dur?: number }) {
  const [shown, setShown] = useState<number | null | undefined>(
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches ? v : v == null ? v : 0,
  );
  const [landed, setLanded] = useState(false);
  const raf = useRef(0);
  const first = useRef(true);

  useEffect(() => {
    if (v == null || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setShown(v);
      setLanded(true);
      return;
    }
    const from = first.current ? 0 : (shown ?? 0);
    first.current = false;
    const t0 = performance.now();
    const tick = (t: number) => {
      const p = Math.min(1, (t - t0) / dur);
      const e = 1 - Math.pow(1 - p, 4);
      setShown(from + (v - from) * e);
      if (p < 1) raf.current = requestAnimationFrame(tick);
      else setLanded(true);
    };
    cancelAnimationFrame(raf.current);
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [v]);

  return <span className={landed ? "roll-done" : ""}>{shown == null ? "—" : fmt(shown, d, unit)}</span>;
}

export function Stat({ k, blk, unit, d = 2 }: { k: string; blk: Any; unit: string; d?: number }) {
  return (
    <div className="stat">
      <div className="k">{k}</div>
      <div className="v">{blk ? <Roll v={blk.value} d={d} unit={unit} /> : "—"}</div>
      <div className="asof">{blk?.asof ?? "no data"}</div>
    </div>
  );
}

export function Fault({ name, reason, span = 6 }: { name: string; reason?: string; span?: number }) {
  return (
    <div className={`card span${span}`}>
      <h2>{name}</h2>
      <div className="faults">ENGINE DOWN — {reason ?? "unknown"}</div>
    </div>
  );
}

export function Method({ children }: { children: any }) {
  return <div className="method">{children}</div>;
}

export function Decomp({ composite, compact = false }: { composite: Any; compact?: boolean }) {
  return (
    <div className={compact ? "decomp compact" : "decomp"}>
      {(composite.decomposition ?? []).map((d: Any) => (
        <div className="row" key={d.component}>
          <span className={d.status === "DEAD" ? "dead" : ""}>{d.component}</span>
          <div className="bar">
            <div
              style={{
                width: `${d.score ?? 0}%`,
                background: d.status === "DEAD" ? P.stress : undefined,
              }}
            />
          </div>
          <span className={d.status === "DEAD" ? "dead" : ""}>
            {d.status === "DEAD" ? "DEAD" : fmt(d.score, 0)}
          </span>
        </div>
      ))}
    </div>
  );
}

/**
 * One media-query check, shared. Num/Roll read matchMedia inline on each
 * tween; components that gate entrance animation or chrome on motion
 * preference subscribe here so a runtime change flips them too.
 */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = () => setReduced(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return reduced;
}

/**
 * Honesty is the brand: an engine whose asof lags the snapshot by more than
 * ten days wears the stale chip (styles.css .chip.stale) wherever it is
 * quoted. Returns null when fresh or when either date is missing/unparseable —
 * absence of a chip must never imply freshness we cannot verify.
 */
export function stalenessChip(
  asof: string | null | undefined,
  generatedAt: string | null | undefined,
) {
  if (!asof || !generatedAt) return null;
  const a = new Date(asof).getTime();
  const g = new Date(generatedAt).getTime();
  if (!isFinite(a) || !isFinite(g)) return null;
  const days = Math.floor((g - a) / 86400000);
  if (days <= 10) return null;
  return (
    <span className="chip stale" title={`engine data is ${days} days behind the snapshot`}>
      stale · {days}d
    </span>
  );
}

/**
 * The 'as of' line every quoted engine figure carries: the engine's own asof
 * plus the stale chip when it has fallen behind. Renders nothing without an
 * asof — a missing date stays visible as absence, not as a guess.
 */
export function AsOf({ asof, generatedAt }: { asof?: string | null; generatedAt?: string | null }) {
  if (!asof) return null;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 7, marginTop: 6, fontSize: 10, color: "var(--ghost)" }}>
      <span>as of {asof}</span>
      {stalenessChip(asof, generatedAt)}
    </div>
  );
}

/* ---------- copy csv -------------------------------------------------------
   table.mini companion: any table the desk renders can be lifted straight
   into a spreadsheet. Rows are plain arrays (header row included by the
   caller); cells are quoted only when they need it. */

const csvCell = (v: unknown): string => {
  const s = v == null ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

export const toCSV = (rows: unknown[][]): string =>
  rows.map((r) => r.map(csvCell).join(",")).join("\n");

export async function copyCSV(rows: unknown[][]): Promise<boolean> {
  const text = toCSV(rows);
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // non-secure context or denied permission: the old-school fallback
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      return ok;
    } catch {
      return false;
    }
  }
}

export function CopyCSV({ rows, label = "copy csv" }: { rows: unknown[][]; label?: string }) {
  const [state, setState] = useState<"idle" | "ok" | "err">("idle");
  const timer = useRef(0);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  return (
    <button
      type="button"
      className="copycsv"
      onClick={async () => {
        const ok = await copyCSV(rows);
        setState(ok ? "ok" : "err");
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => setState("idle"), 1400);
      }}
    >
      {state === "ok" ? "copied ✓" : state === "err" ? "copy failed" : label}
    </button>
  );
}
