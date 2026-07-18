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

export function Decomp({ composite }: { composite: Any }) {
  return (
    <div className="decomp">
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
