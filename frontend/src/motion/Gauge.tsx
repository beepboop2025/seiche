/**
 * The Gauge — the composite as a sprung needle on an arc.
 *
 * On mount the needle sits at zero and sweeps to the reading on a slightly
 * underdamped spring: it arrives like water finding its level, one soft
 * overshoot, no cartoon bounce. When fresh data lands (the 5-minute refresh)
 * the retarget plus a small impulse makes the needle twitch — direction and
 * magnitude of the change read straight off the dial. The arc and needle tint
 * ride the same continuous stress ramp as the wave tank (tint.ts), so colour
 * crossfades as the regime moves.
 *
 * DESK mode / reduced motion: no spring, no sweep — the needle simply points.
 */
import { useEffect, useRef, useState } from "react";
import { useMotion } from "./motionMode";
import { tintCss } from "./tint";

// critically-soft spring: fast enough to feel live, damped enough to feel wet
const K = 110;            // stiffness
const C = 2 * Math.sqrt(K) * 0.82;  // slightly underdamped — one gentle overshoot

const A0 = -210, A1 = 30; // arc sweep in degrees (gap at the bottom)

export default function Gauge({ v, size = 46 }: { v: number | null | undefined; size?: number }) {
  const { effective } = useMotion();
  const target = Math.min(100, Math.max(0, v ?? 0));
  const [angle, setAngle] = useState(0); // always starts at 0: the sweep is the point
  const sim = useRef({ x: 0, vel: 0 });
  const raf = useRef(0);
  const first = useRef(true);

  useEffect(() => {
    if (effective === "desk" || v == null) {
      cancelAnimationFrame(raf.current);
      sim.current = { x: target, vel: 0 };
      setAngle(target);
      return;
    }
    // a subtle twitch on new data: the needle flinches toward the change
    if (!first.current) sim.current.vel += (target - sim.current.x) * 0.35 + (Math.random() - 0.5) * 6;
    first.current = false;

    let last = performance.now();
    const tick = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const s = sim.current;
      const acc = K * (target - s.x) - C * s.vel;
      s.vel += acc * dt;
      s.x += s.vel * dt;
      setAngle(s.x);
      if (Math.abs(target - s.x) > 0.02 || Math.abs(s.vel) > 0.02) {
        raf.current = requestAnimationFrame(tick);
      } else {
        s.x = target; s.vel = 0;
        setAngle(target);
      }
    };
    cancelAnimationFrame(raf.current);
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, effective, v == null]);

  const t = Math.min(1, Math.max(0, angle / 100));
  const tint = tintCss(t);
  const tintSoft = tintCss(t, 0.35);

  const R = 100;
  const pt = (deg: number, r: number) => {
    const rad = (deg * Math.PI) / 180;
    return `${(R + r * Math.cos(rad)).toFixed(1)} ${(R + r * Math.sin(rad)).toFixed(1)}`;
  };
  const arc = (a0: number, a1: number, r: number) =>
    `M ${pt(a0, r)} A ${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${pt(a1, r)}`;

  const shown = A0 + (Math.min(100, Math.max(0, angle)) / 100) * (A1 - A0);

  return (
    <svg
      viewBox="0 0 200 200"
      className="gauge"
      style={{ width: size, height: size }}
      role="img"
      aria-label={`composite gauge: ${v == null ? "no reading" : Math.round(target)} of 100`}
    >
      {/* track */}
      <path d={arc(A0, A1, 78)} fill="none" stroke="var(--panel-edge-2)" strokeWidth={10} strokeLinecap="round" />
      {/* value arc — follows the needle, tinted on the stress ramp */}
      <path
        d={arc(A0, shown, 78)} fill="none" stroke={tint} strokeWidth={10} strokeLinecap="round"
        className="gauge-value"
      />
      {/* needle */}
      <g transform={`rotate(${shown} 100 100)`}>
        <line x1={100} y1={100} x2={166} y2={100} stroke={tint} strokeWidth={4.5} strokeLinecap="round" className="gauge-needle" />
        <line x1={100} y1={100} x2={78} y2={100} stroke={tintSoft} strokeWidth={4.5} strokeLinecap="round" />
      </g>
      <circle cx={100} cy={100} r={9} fill="var(--panel)" stroke={tint} strokeWidth={3} className="gauge-hub" />
    </svg>
  );
}
