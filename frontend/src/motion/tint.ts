/**
 * Regime tint — one continuous colour ramp for every stress-driven surface
 * (wave tank, gauge, flashes), so the whole cinema layer agrees on what the
 * water looks like at a given composite reading:
 *
 *   0   CALM    deep blue   — the basin at rest
 *   0.5 EROSION amber       — the colour of "pay attention" (never green/red:
 *                             up is bad here, the ramp warms as stress rises)
 *   1   STRESS  red         — the slosh over the edge
 *
 * Values mirror the CSS tokens (--calm is deliberately NOT used: the house
 * teal reads as "good", and this ramp encodes stress, not approval).
 */

type RGB = [number, number, number];

const DEEP_BLUE: RGB = [52, 96, 146];   // chart-slate pulled toward the abyss
const AMBER: RGB = [221, 179, 118];     // --erosion
const STRESS_RED: RGB = [239, 128, 120]; // --stress

const lerp = (a: RGB, b: RGB, t: number): RGB => [
  Math.round(a[0] + (b[0] - a[0]) * t),
  Math.round(a[1] + (b[1] - a[1]) * t),
  Math.round(a[2] + (b[2] - a[2]) * t),
];

/** t in 0..1 (composite / 100) → RGB on the stress ramp. */
export const tintFor = (t: number): RGB => {
  const x = Math.min(1, Math.max(0, t));
  return x < 0.5 ? lerp(DEEP_BLUE, AMBER, x * 2) : lerp(AMBER, STRESS_RED, (x - 0.5) * 2);
};

export const tintCss = (t: number, alpha = 1): string => {
  const [r, g, b] = tintFor(t);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
};

/** The calm-end water colour, for gradients that need a "deep" anchor. */
export const BASIN_DEEP = "rgba(18, 34, 56, 0.55)";
