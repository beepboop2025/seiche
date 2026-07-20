/**
 * The Wave Tank — the brand made literal: a seiche sloshing in its basin.
 *
 * A 120-node string under the 1D wave equation with fixed-fixed boundaries
 * (the walls of the basin — the wave reflects, never leaves). Three standing
 * eigen-modes are driven gently all the time (a basin always hums at its
 * natural frequencies), and random impulses rain on the surface at a rate and
 * amplitude set by the live composite: CALM is a slow breathing slosh in deep
 * blue, STRESS is choppy, warm and red. Foam flecks gather at the antinodes
 * and slide with the surface slope.
 *
 * Honesty rules from the house motion charter apply: the sim pauses when
 * offscreen (IntersectionObserver) or when the tab is hidden, the canvas is
 * DPR-aware, the whole band is aria-hidden, and under DESK mode / reduced
 * motion the tank never mounts — a static tinted gradient stands in.
 */
import { useEffect, useRef, useState } from "react";
import { useMotion } from "./motionMode";
import { tintCss, BASIN_DEEP } from "./tint";

const N = 120;                 // string nodes
const SUBSTEPS = 5;            // physics substeps per frame (~60fps)
const DT = 0.9;                // substep dt — Courant-stable with c = 1, dx = 1
const DAMPING = 0.9985;        // per-substep velocity decay: a real basin loses energy
const MODES = [1, 2, 3];       // driven standing modes (fixed-fixed: sin(kπx/L))
const FOAM = 34;               // foam fleck pool size

interface Fleck { x: number; vx: number; age: number; life: number; size: number }

export default function WaveTank({ value, regime }: { value: number | null; regime: string | null }) {
  const { effective } = useMotion();
  // Lazy: the canvas and its rAF loop only exist after first paint — the
  // masthead paints as text first, the water fades in behind it.
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setReady(true), 90);
    return () => clearTimeout(t);
  }, []);

  const stress = Math.min(1, Math.max(0, (value ?? 20) / 100));

  if (effective === "desk") {
    // Static water: an elegant regime-tinted gradient, no canvas, no loop.
    return (
      <div
        className="wavetank wavetank-static"
        aria-hidden="true"
        style={{
          background: `linear-gradient(to bottom, ${tintCss(stress, 0.0)} 0%, ${tintCss(stress, 0.16)} 46%, ${BASIN_DEEP} 100%)`,
          borderTop: `1px solid ${tintCss(stress, 0.55)}`,
        }}
      />
    );
  }
  if (!ready) return <div className="wavetank" aria-hidden="true" />;
  return <TankCanvas stress={stress} regime={regime} />;
}

function TankCanvas({ stress, regime }: { stress: number; regime: string | null }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // The render loop reads targets through this ref so a new reading arrives
  // as a slow set change (lerped below), never a hard cut.
  const target = useRef({ stress });
  target.current = { stress };
  void regime; // tint follows the continuous reading; the regime word stays in markup

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let W = 0, H = 0, dpr = 1;
    const resize = () => {
      const r = canvas.getBoundingClientRect();
      dpr = Math.min(2, window.devicePixelRatio || 1);
      W = Math.max(1, Math.floor(r.width));
      H = Math.max(1, Math.floor(r.height));
      canvas.width = W * dpr;
      canvas.height = H * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    // --- physics state ------------------------------------------------------
    const u = new Float32Array(N);   // displacement
    const v = new Float32Array(N);   // velocity
    const phase = MODES.map(() => Math.random() * Math.PI * 2);
    let simT = 0;
    let gain = 1;                    // auto-gain: keeps the slosh inside the band
    let maxAbs = 1;
    let smoothed = target.current.stress;

    const flecks: Fleck[] = Array.from({ length: FOAM }, () => ({
      x: Math.random() * (N - 1), vx: 0, age: Math.random() * 4, life: 2.5 + Math.random() * 2.5, size: 1,
    }));

    const respawn = (f: Fleck) => {
      // gather at antinodes: rejection-sample toward large |u|
      let best = Math.random() * (N - 1);
      for (let k = 0; k < 6; k++) {
        const cand = Math.random() * (N - 1);
        if (Math.abs(u[Math.round(cand)]) > Math.abs(u[Math.round(best)])) best = cand;
      }
      f.x = best; f.vx = 0; f.age = 0;
      f.life = 2.5 + Math.random() * 2.5;
      f.size = 0.8 + Math.random() * 1.4;
    };

    const step = () => {
      const s = smoothed;
      // eigen-excitation: each driven mode pushes the string along its own
      // shape, amplitude swelling with stress, phase drifting slowly
      for (let m = 0; m < MODES.length; m++) {
        const k = MODES[m];
        const omega = (k * Math.PI) / (N - 1);           // fixed-fixed eigenfrequency (c = 1)
        phase[m] += omega * DT * (0.55 + 0.25 * Math.sin(simT * 0.013 + m));
        const amp = (0.06 + 0.20 * s) / k;               // higher modes ride lower
        const drive = amp * Math.sin(phase[m]);
        for (let i = 1; i < N - 1; i++) {
          v[i] += drive * Math.sin((k * Math.PI * i) / (N - 1)) * DT;
        }
      }
      // random impulses: Poisson rain on the surface, rate and force
      // scaling with the live composite (rate is per second; the per-substep
      // probability below averages out to it at 60fps)
      if (Math.random() < (0.22 + 2.6 * s) / 60 / SUBSTEPS) {
        const x0 = (0.10 + Math.random() * 0.80) * (N - 1);
        const amp = (0.5 + 2.0 * s) * (Math.random() < 0.5 ? -1 : 1);
        for (let i = 1; i < N - 1; i++) {
          const d = (i - x0) / 3.2;
          v[i] += amp * Math.exp(-d * d);
        }
      }
      // wave equation: fixed-fixed boundaries, energy slowly bleeding out
      for (let i = 1; i < N - 1; i++) v[i] += (u[i - 1] - 2 * u[i] + u[i + 1]) * DT;
      for (let i = 1; i < N - 1; i++) {
        v[i] *= DAMPING;
        u[i] += v[i] * DT;
      }
      u[0] = u[N - 1] = 0;
      simT += DT;
    };

    // --- render -------------------------------------------------------------
    const ys = new Float32Array(N);
    let raf = 0;
    let running = true;
    let visible = true;

    const frame = () => {
      raf = 0;
      if (!running || !visible) return;
      smoothed += (target.current.stress - smoothed) * 0.02;

      for (let s = 0; s < SUBSTEPS; s++) step();

      // auto-gain with slow attack: the slosh fills the band at any regime
      let cur = 0;
      for (let i = 0; i < N; i++) cur = Math.max(cur, Math.abs(u[i]));
      maxAbs = Math.max(maxAbs * 0.999, cur, 0.4);
      const wantGain = (H * 0.30) / maxAbs;
      gain += (wantGain - gain) * 0.01;

      const base = H * 0.52;
      for (let i = 0; i < N; i++) ys[i] = base - u[i] * gain;

      ctx.clearRect(0, 0, W, H);

      // water body
      const grad = ctx.createLinearGradient(0, base - H * 0.32, 0, H);
      grad.addColorStop(0, tintCss(smoothed, 0.30));
      grad.addColorStop(0.55, tintCss(smoothed, 0.10));
      grad.addColorStop(1, "rgba(6, 12, 22, 0.0)");
      ctx.beginPath();
      ctx.moveTo(0, ys[0]);
      for (let i = 1; i < N; i++) ctx.lineTo((i / (N - 1)) * W, ys[i]);
      ctx.lineTo(W, H);
      ctx.lineTo(0, H);
      ctx.closePath();
      ctx.fillStyle = grad;
      ctx.fill();

      // shimmering surface line: a solid stroke plus travelling glints
      const trace = () => {
        ctx.beginPath();
        ctx.moveTo(0, ys[0]);
        for (let i = 1; i < N; i++) ctx.lineTo((i / (N - 1)) * W, ys[i]);
      };
      trace();
      ctx.strokeStyle = tintCss(smoothed, 0.9);
      ctx.lineWidth = 1.3;
      ctx.stroke();
      ctx.save();
      ctx.setLineDash([3, 34]);
      ctx.lineDashOffset = -simT * 2.2;
      trace();
      ctx.strokeStyle = "rgba(237, 238, 244, 0.30)";
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.restore();

      // foam flecks riding the surface, sliding with its slope
      for (const f of flecks) {
        f.age += 1 / 60;
        if (f.age >= f.life) respawn(f);
        const i = Math.max(1, Math.min(N - 2, Math.round(f.x)));
        const slope = (ys[i + 1] - ys[i - 1]) * 0.5;
        f.vx += (-slope * 0.06 + (Math.random() - 0.5) * 0.02);
        f.vx *= 0.94;
        f.x = Math.max(1, Math.min(N - 2, f.x + f.vx));
        const p = f.age / f.life;
        const alpha = Math.sin(Math.PI * Math.min(1, p)) * 0.5;
        ctx.fillStyle = p < 1 ? `rgba(237, 238, 244, ${alpha.toFixed(3)})` : "transparent";
        ctx.fillRect((f.x / (N - 1)) * W, ys[Math.round(f.x)] - 1.5, f.size * 2, f.size);
      }

      raf = requestAnimationFrame(frame);
    };

    const kick = () => { if (!raf && running && visible) raf = requestAnimationFrame(frame); };

    // pause when the tab hides or the band scrolls offscreen — motion that
    // encodes nothing for no one is just battery drain
    const onVis = () => { running = document.visibilityState === "visible"; kick(); };
    document.addEventListener("visibilitychange", onVis);
    const io = new IntersectionObserver(
      (entries) => { visible = entries[0]?.isIntersecting ?? true; kick(); },
      { threshold: 0.02 },
    );
    io.observe(canvas);

    raf = requestAnimationFrame(frame);
    return () => {
      running = false;
      cancelAnimationFrame(raf);
      ro.disconnect();
      io.disconnect();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, []);

  return <canvas ref={canvasRef} className="wavetank wavetank-canvas" aria-hidden="true" />;
}
