import { useEffect, useRef, useState } from "react";
import { Any, fmt } from "./lib";

// The Descent: a first-visit scroll scene that lowers a new reader from the
// surface (what markets price) past the plumbing (what the floor knows) down
// to the record (why to believe it), then releases them into the terminal.
// Scrubbed with a damped follow so the visuals trail the wheel like water,
// not like a scrollbar. Shown once; any deep link or reduced-motion skips it.

const SEEN_KEY = "seiche_descended";

export const shouldDescend = (): boolean => {
  try {
    if (localStorage.getItem(SEEN_KEY)) return false;
  } catch {
    return false;
  }
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return false;
  const h = window.location.hash.replace("#", "");
  return h === "" || h === "global"; // never intercept a deep link
};

export const markDescended = () => {
  try {
    localStorage.setItem(SEEN_KEY, "1");
  } catch {
    /* private mode: the descent just plays again next time */
  }
};

type Scene = { at: number; depth: string; kicker: string };
const SCENES: Scene[] = [
  { at: 0.0, depth: "0 m", kicker: "THE SURFACE" },
  { at: 0.33, depth: "40 m", kicker: "THE PLUMBING" },
  { at: 0.66, depth: "200 m", kicker: "THE RECORD" },
];

export default function Descent({ snap, onDone }: { snap: Any; onDone: () => void }) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const progress = useRef(0); // damped
  const raw = useRef(0);
  const [scene, setScene] = useState(0);
  const frameEls = useRef<(HTMLDivElement | null)[]>([]);

  const c = snap?.engines?.composite ?? {};
  const tell = snap?.deep?.tell ?? {};
  const bt = snap?.deep?.backtest?.event_capture ?? {};

  const leave = () => {
    markDescended();
    onDone();
  };

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    let raf = 0;
    const onScroll = () => {
      const max = el.scrollHeight - el.clientHeight;
      raw.current = max > 0 ? el.scrollTop / max : 1;
    };
    el.addEventListener("scroll", onScroll, { passive: true });

    const tick = () => {
      // the damped follow: the scene chases the wheel and settles ~600ms later
      progress.current += (raw.current - progress.current) * 0.08;
      const p = progress.current;

      let active = 0;
      SCENES.forEach((s, i) => {
        const next = SCENES[i + 1]?.at ?? 1.01;
        if (p >= s.at) active = i;
        const f = frameEls.current[i];
        if (!f) return;
        // each scene fades and rises through its own window of the descent
        const span = next - s.at;
        const local = Math.min(1, Math.max(0, (p - s.at) / span));
        const enter = i === 0 ? 1 : Math.min(1, local * 5); // scene 1 opens visible
        const exit = 1 - Math.max(0, (local - 0.7) / 0.3); // hand over late
        const o = Math.max(0, Math.min(enter, exit));
        f.style.opacity = String(o);
        f.style.transform = `translateY(${(1 - o) * (local > 0.5 ? -26 : 26)}px)`;
      });
      setScene((s) => (s === active ? s : active));

      if (raw.current > 0.985) {
        leave();
        return;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Enter" || e.key === "Escape") leave();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      cancelAnimationFrame(raf);
      el.removeEventListener("scroll", onScroll);
      window.removeEventListener("keydown", onKey);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="descent" role="dialog" aria-label="Introduction to the Seiche terminal">
      <div className="descent-scroller" ref={scrollerRef}>
        <div className="descent-track" />
      </div>

      <div className="descent-stage">
        <div className="descent-gauge" aria-hidden="true">
          {SCENES.map((s, i) => (
            <div key={s.kicker} className={`descent-mark ${i === scene ? "on" : ""}`}>
              <span className="d-depth">{s.depth}</span>
              <span className="d-name">{s.kicker}</span>
            </div>
          ))}
        </div>

        <div className="descent-frame" ref={(el) => (frameEls.current[0] = el)}>
          <div className="descent-kicker">THE SURFACE · what markets price</div>
          <div className="descent-big">
            {fmt(c.value, 0)} <span className={`regime ${c.regime}`}>{c.regime}</span>
          </div>
          <p className="descent-line">
            One number for the state of dollar funding, composed from the Fed's own
            plumbing data. Right now the basin reads <b>{c.regime ?? "—"}</b>. Scroll to
            see where that number comes from.
          </p>
        </div>

        <div className="descent-frame" ref={(el) => (frameEls.current[1] = el)}>
          <div className="descent-kicker">THE PLUMBING · what the floor knows</div>
          <div className="descent-big">
            {tell.tell > 0 ? "+" : ""}
            {fmt(tell.tell, 0)}
          </div>
          <p className="descent-line">
            The Tell: plumbing percentile minus market percentile. Positive means the
            pipes are tighter than prices admit. {tell.reading ? <b>{tell.reading}.</b> : null}{" "}
            Every stress event of 2025 and 2026 was led by this side of the water.
          </p>
        </div>

        <div className="descent-frame" ref={(el) => (frameEls.current[2] = el)}>
          <div className="descent-kicker">THE RECORD · why believe any of it</div>
          <div className="descent-big">
            {bt.recall != null ? `${fmt(bt.recall * 100, 0)}%` : "—"}
            <span className="descent-unit"> of spikes alerted early</span>
          </div>
          <p className="descent-line">
            Backtested with no look ahead, misses printed, every claim reproducible from
            free public data. Median lead {bt.median_lead_d != null ? `${fmt(bt.median_lead_d, 0)} days` : "—"}.
            The scoreboard lives on the PROOF tab, failures included.
          </p>
        </div>

        <button className="descent-enter" onClick={leave}>
          enter the terminal <span aria-hidden="true">→</span>
        </button>
        <div className="descent-hint">scroll to descend · Enter to skip</div>
      </div>
    </div>
  );
}
