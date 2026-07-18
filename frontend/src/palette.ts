/**
 * The Nocturne palette, resolved from the CSS tokens in styles.css so canvas
 * charts (uPlot/SVG can't use var(--x) strings) and inline styles share ONE
 * source of truth: change styles.css and every chart follows.
 *
 * Getters resolve lazily on first read (styles are guaranteed loaded by the
 * time anything renders) and cache; the fallbacks mirror styles.css for
 * non-DOM contexts.
 */
const cache: Record<string, string> = {};

const tok = (name: string, fallback: string): string => {
  if (!(name in cache)) {
    const v =
      typeof window !== "undefined"
        ? getComputedStyle(document.documentElement).getPropertyValue(name).trim()
        : "";
    cache[name] = v || fallback;
  }
  return cache[name];
};

export const P = {
  // status ramp — the regime colours
  get calm() { return tok("--calm", "#7ccdb4"); },
  get erosion() { return tok("--erosion", "#ddb376"); },
  get strain() { return tok("--strain", "#e59a7a"); },
  get stress() { return tok("--stress", "#ef8078"); },

  // the accent family
  get accent() { return tok("--accent", "#9c8fe8"); },
  get accentSoft() { return tok("--accent-soft", "#bbaffe"); },
  get accentBright() { return tok("--accent-bright", "#d9d4ff"); },

  // neutrals
  get bg() { return tok("--bg", "#000000"); },
  get faint() { return tok("--faint", "#787f95"); },
  get ghost() { return tok("--ghost", "#5a5f70"); },

  // chart-only extensions (series that need to be distinct from status hues)
  get gold() { return tok("--chart-gold", "#d3a558"); },
  get slate() { return tok("--chart-slate", "#879ed8"); },
  get amber() { return tok("--chart-amber", "#e2bb7c"); },
  get ink() { return tok("--chart-ink", "#b7bbd0"); },
  get inkBright() { return tok("--chart-ink-bright", "#d4d8ea"); },
  get grid() { return tok("--chart-grid", "rgba(237,238,244,0.08)"); },
};
