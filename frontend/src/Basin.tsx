import { useEffect, useRef } from "react";

// The living abyss: a full-viewport night-water field whose motion IS the
// reading. The composite stress value drives wave speed, chop and colour
// temperature — CALM is a slow indigo swell in the dark, STRESS is fast,
// short and warm. On the black ground every light behaves as bioluminescence:
// marine snow drifts at the stress rate, a soft glow follows the pointer,
// and a click sends a ring out through the water. Rendered as a single
// fragment shader at half resolution (<1ms/frame on integrated GPUs),
// paused whenever the tab is hidden, frozen to one frame under reduced motion.

const VERT = `
attribute vec2 p;
void main() { gl_Position = vec4(p, 0.0, 1.0); }
`;

const FRAG = `
precision mediump float;
uniform vec2 u_res;
uniform float u_time;
uniform float u_stress;   // 0..1 (composite / 100)
uniform float u_warm;     // 0..1 regime warmth (CALM 0 -> STRESS 1)
uniform vec2 u_mouse;     // pointer in uv space (y up), lerped in JS
uniform float u_hand;     // pointer presence 0..1 (fades out when idle)
uniform vec3 u_click;     // click x, y, seconds since click (999 = none)

float hash(vec2 q) { return fract(sin(dot(q, vec2(127.1, 311.7))) * 43758.5453); }

float noise(vec2 q) {
  vec2 i = floor(q), f = fract(q);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x),
             mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
}

float fbm(vec2 q) {
  float v = 0.0, a = 0.5;
  for (int k = 0; k < 4; k++) { v += a * noise(q); q *= 2.03; a *= 0.5; }
  return v;
}

// one layer of marine snow: sparse points drifting down-current, twinkling
float snow(vec2 q, float scale, float t, float speed) {
  vec2 g = q * scale + vec2(t * speed, t * speed * 0.55);
  vec2 cell = floor(g);
  vec2 pos = fract(g) - (vec2(hash(cell), hash(cell + 7.7)) * 0.8 + 0.1);
  float d = length(pos);
  float tw = 0.6 + 0.4 * sin(t * (1.5 + hash(cell) * 3.0) + hash(cell) * 40.0);
  return smoothstep(0.06, 0.0, d) * tw * step(0.72, hash(cell + 3.3));
}

void main() {
  vec2 uv = gl_FragCoord.xy / u_res;
  float aspect = u_res.x / u_res.y;
  vec2 q = vec2(uv.x * aspect, uv.y);
  vec2 m = vec2(u_mouse.x * aspect, u_mouse.y);

  // stress shortens the wavelength and speeds the water up
  float speed = 0.018 + 0.10 * u_stress;
  float scale = 2.2 + 3.4 * u_stress;
  vec2 drift = vec2(u_time * speed, u_time * speed * 0.35);

  // domain-warped fbm: the warp grows with stress = choppier surface
  vec2 warp = vec2(fbm(q * scale + drift), fbm(q * scale - drift.yx));
  float h = fbm(q * scale + warp * (0.9 + 1.8 * u_stress) + drift);

  // depth: darker toward the floor of the page
  float depth = mix(1.0, 0.45, uv.y * -1.0 + 1.0);

  // abyss water: near-black ground; crests glow indigo, warming with regime
  vec3 deep_water = vec3(0.0, 0.004, 0.012);
  vec3 indigo     = vec3(0.27, 0.24, 0.45);
  vec3 warm       = vec3(0.50, 0.27, 0.24);
  vec3 crest = mix(indigo, warm, u_warm);

  float band = smoothstep(0.42, 0.78, h);
  vec3 col = mix(deep_water, crest, band * (0.10 + 0.20 * u_stress)) * depth;

  // marine snow: two parallax layers, drift rate rises with stress
  float snowspeed = 0.010 + 0.06 * u_stress;
  col += crest * snow(q, 22.0, u_time, snowspeed) * 0.10;
  col += crest * snow(q + 4.7, 44.0, u_time, snowspeed * 1.8) * 0.05;

  // bioluminescence at the hand: a soft bloom that also lifts the local
  // wave detail, as if the water lights where it is disturbed
  float md = length(q - m);
  float glow = exp(-md * 5.5) * u_hand;
  col += crest * glow * (0.16 + 0.35 * band);

  // click ripple: one ring travelling out from the last touch
  float ct = u_click.z;
  if (ct < 3.0) {
    float cd = length(q - vec2(u_click.x * aspect, u_click.y));
    float r = ct * 0.55;
    float ring = smoothstep(0.045, 0.0, abs(cd - r)) * (1.0 - ct / 3.0);
    col += crest * ring * 0.35;
  }

  gl_FragColor = vec4(col, 1.0);
}
`;

const REGIME_WARMTH: Record<string, number> = { CALM: 0.0, EROSION: 0.35, STRAIN: 0.7, STRESS: 1.0 };

export default function Basin({ value, regime }: { value: number | null; regime: string | null }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const state = useRef({ stress: 0.2, warm: 0.0, mx: 0.5, my: 0.5, hand: 0 });

  // the live reading steers the water; lerped in the render loop so a regime
  // change arrives as a slow set change, not a cut. A ref keeps the render
  // loop reading the freshest values without re-running the GL effect.
  const target = useRef({ stress: 0.2, warm: 0.0, mx: 0.5, my: 0.5, hand: 0 });
  target.current = {
    ...target.current,
    stress: Math.min(1, Math.max(0, (value ?? 20) / 100)),
    warm: REGIME_WARMTH[regime ?? "CALM"] ?? 0.0,
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const gl = canvas.getContext("webgl", { alpha: false, antialias: false, powerPreference: "low-power" });
    if (!gl) return; // no WebGL: the CSS ground stays, nothing breaks

    const compile = (type: number, src: string) => {
      const sh = gl.createShader(type)!;
      gl.shaderSource(sh, src);
      gl.compileShader(sh);
      return sh;
    };
    const prog = gl.createProgram()!;
    gl.attachShader(prog, compile(gl.VERTEX_SHADER, VERT));
    gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return;
    gl.useProgram(prog);

    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, "p");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

    const uRes = gl.getUniformLocation(prog, "u_res");
    const uTime = gl.getUniformLocation(prog, "u_time");
    const uStress = gl.getUniformLocation(prog, "u_stress");
    const uWarm = gl.getUniformLocation(prog, "u_warm");
    const uMouse = gl.getUniformLocation(prog, "u_mouse");
    const uHand = gl.getUniformLocation(prog, "u_hand");
    const uClick = gl.getUniformLocation(prog, "u_click");

    // half resolution: the field is soft by design, and this keeps the whole
    // pass well under a millisecond
    const resize = () => {
      const w = Math.max(1, Math.floor(window.innerWidth * 0.5));
      const h = Math.max(1, Math.floor(window.innerHeight * 0.5));
      canvas.width = w;
      canvas.height = h;
      gl.viewport(0, 0, w, h);
      gl.uniform2f(uRes, w, h);
    };
    resize();
    window.addEventListener("resize", resize);

    // the hand in the water: pointer position in uv space (y up); presence
    // fades in on movement and back out after a few idle seconds
    let idleTimer: ReturnType<typeof setTimeout> | undefined;
    const onMove = (e: PointerEvent) => {
      target.current.mx = e.clientX / window.innerWidth;
      target.current.my = 1 - e.clientY / window.innerHeight;
      target.current.hand = 1;
      clearTimeout(idleTimer);
      idleTimer = setTimeout(() => { target.current.hand = 0; }, 2600);
    };
    const click = { x: 0.5, y: 0.5, at: -999 };
    const onDown = (e: PointerEvent) => {
      click.x = e.clientX / window.innerWidth;
      click.y = 1 - e.clientY / window.innerHeight;
      click.at = performance.now();
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("pointerdown", onDown, { passive: true });

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    let raf = 0;
    let running = true;
    const t0 = performance.now();

    const frame = () => {
      const s = state.current;
      s.stress += (target.current.stress - s.stress) * 0.02;
      s.warm += (target.current.warm - s.warm) * 0.02;
      s.mx += (target.current.mx - s.mx) * 0.08;
      s.my += (target.current.my - s.my) * 0.08;
      s.hand += (target.current.hand - s.hand) * 0.04;
      gl.uniform1f(uTime, (performance.now() - t0) / 1000);
      gl.uniform1f(uStress, s.stress);
      gl.uniform1f(uWarm, s.warm);
      gl.uniform2f(uMouse, s.mx, s.my);
      gl.uniform1f(uHand, s.hand);
      gl.uniform3f(uClick, click.x, click.y, (performance.now() - click.at) / 1000);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      if (running && !reduced.matches) raf = requestAnimationFrame(frame);
    };

    const onVisibility = () => {
      running = document.visibilityState === "visible";
      if (running && !reduced.matches) {
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(frame);
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    raf = requestAnimationFrame(frame); // reduced motion still paints frame one

    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(idleTimer);
      window.removeEventListener("resize", resize);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerdown", onDown);
      document.removeEventListener("visibilitychange", onVisibility);
      gl.getExtension("WEBGL_lose_context")?.loseContext();
    };
  }, []);

  return <canvas ref={canvasRef} className="basin-field" aria-hidden="true" />;
}
