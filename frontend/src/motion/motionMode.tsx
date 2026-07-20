/**
 * The Motion Mode — how alive the terminal is allowed to be.
 *
 *   CINEMA — all motion: the wave tank, the marquee tape, needle sweeps,
 *            odometer rolls, the sonar ping. The brand made literal.
 *   DESK   — micro-interactions only. No wave sim, no marquee, no sweeps;
 *            colour crossfades and hover states stay (they cost nothing and
 *            carry state, per the motion charter in styles.css).
 *
 * prefers-reduced-motion forces DESK: the reader's OS setting outranks the
 * toggle, and the toggle says so instead of pretending to work. The choice
 * persists in localStorage and lives on <html data-motion> so plain CSS can
 * gate animations without per-component wiring (the same pattern as
 * <html data-depth> in depth.tsx).
 */
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type MotionPref = "cinema" | "desk";

const KEY = "seiche-motion";

const readPref = (): MotionPref => {
  try {
    return localStorage.getItem(KEY) === "desk" ? "desk" : "cinema";
  } catch {
    return "cinema";
  }
};

const mq = () => window.matchMedia("(prefers-reduced-motion: reduce)");

const Ctx = createContext<{
  /** the reader's stored choice (what the toggle shows) */
  pref: MotionPref;
  /** what actually renders — pref, unless reduced motion forces desk */
  effective: MotionPref;
  /** true when the OS asks for reduced motion (effective is forced) */
  reduced: boolean;
  setPref: (m: MotionPref) => void;
}>({ pref: "cinema", effective: "cinema", reduced: false, setPref: () => {} });

export const useMotion = () => useContext(Ctx);

export function MotionProvider({ children }: { children: ReactNode }) {
  const [pref, setPrefState] = useState<MotionPref>(readPref);
  const [reduced, setReduced] = useState(() => mq().matches);
  const effective: MotionPref = reduced ? "desk" : pref;

  const setPref = (m: MotionPref) => {
    setPrefState(m);
    try { localStorage.setItem(KEY, m); } catch { /* private mode */ }
  };

  useEffect(() => {
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches);
    const m = mq();
    m.addEventListener("change", onChange);
    return () => m.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.motion = effective;
  }, [effective]);

  return <Ctx.Provider value={{ pref, effective, reduced, setPref }}>{children}</Ctx.Provider>;
}

/** Masthead toggle — the same radiogroup idiom as the DepthDial. */
export function MotionToggle() {
  const { pref, effective, reduced, setPref } = useMotion();
  return (
    <div
      className="motiontoggle"
      role="radiogroup"
      aria-label="motion mode"
      title={
        reduced
          ? "motion: your OS asks for reduced motion — DESK is forced"
          : "motion — CINEMA: the full living board · DESK: micro-interactions only"
      }
    >
      {(["cinema", "desk"] as const).map((m) => (
        <button
          key={m}
          role="radio"
          aria-checked={m === effective}
          className={m === effective ? "on" : ""}
          onClick={() => setPref(m)}
        >
          {m}
        </button>
      ))}
      {reduced && <span className="motiontoggle-note">reduced-motion: desk</span>}
    </div>
  );
}
