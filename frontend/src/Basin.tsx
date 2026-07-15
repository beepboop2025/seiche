import { useEffect, useRef } from "react";

// The living basin: a full-viewport water field whose motion IS the reading.
// The composite stress value drives wave speed, chop and colour temperature —
// CALM is a slow indigo swell, STRESS is fast, short and warm. Rendered as a
// single fragment shader at half resolution (<1ms/frame on integrated GPUs),
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

void main() {
  vec2 uv = gl_FragCoord.xy / u_res;
  float aspect = u_res.x / u_res.y;
  vec2 q = vec2(uv.x * aspect, uv.y);

  // stress shortens the wavelength and speeds the water up
  float speed = 0.018 + 0.10 * u_stress;
  float scale = 2.2 + 3.4 * u_stress;
  vec2 drift = vec2(u_time * speed, u_time * speed * 0.35);

  // domain-warped fbm: the warp grows with stress = choppier surface
  vec2 warp = vec2(fbm(q * scale + drift), fbm(q * scale - drift.yx));
  float h = fbm(q * scale + warp * (0.9 + 1.8 * u_stress) + drift);

  // depth: darker toward the floor of the page
  float depth = mix(1.0, 0.45, uv.y * -1.0 + 1.0);

  // Nocturne water: indigo ground swelling toward the accent; stress warms it
  vec3 deep_water = vec3(0.086, 0.094, 0.149);              // #161826
  vec3 indigo     = vec3(0.267, 0.243, 0.416);              // accent-deep-ish
  vec3 warm       = vec3(0.475, 0.271, 0.243);              // strain undertone
  vec3 crest = mix(indigo, warm, u_warm);

  float band = smoothstep(0.42, 0.78, h);
  vec3 col = mix(deep_water, crest, band * (0.16 + 0.22 * u_stress)) * depth;

  gl_FragColor = vec4(col, 1.0);
}
`;

const REGIME_WARMTH: Record<string, number> = { CALM: 0.0, EROSION: 0.35, STRAIN: 0.7, STRESS: 1.0 };

export default function Basin({ value, regime }: { value: number | null; regime: string | null }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const state = useRef({ stress: 0.2, warm: 0.0 });

  // the live reading steers the water; lerped in the render loop so a regime
  // change arrives as a slow set change, not a cut. A ref keeps the render
  // loop reading the freshest values without re-running the GL effect.
  const target = useRef({ stress: 0.2, warm: 0.0 });
  target.current = {
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

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    let raf = 0;
    let running = true;
    const t0 = performance.now();

    const frame = () => {
      const s = state.current;
      s.stress += (target.current.stress - s.stress) * 0.02;
      s.warm += (target.current.warm - s.warm) * 0.02;
      gl.uniform1f(uTime, (performance.now() - t0) / 1000);
      gl.uniform1f(uStress, s.stress);
      gl.uniform1f(uWarm, s.warm);
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
      window.removeEventListener("resize", resize);
      document.removeEventListener("visibilitychange", onVisibility);
      gl.getExtension("WEBGL_lose_context")?.loseContext();
    };
  }, []);

  return <canvas ref={canvasRef} className="basin-field" aria-hidden="true" />;
}
