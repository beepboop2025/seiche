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
  get calm() { return tok("--calm", "#79c2ad"); },
  get erosion() { return tok("--erosion", "#d3ab6e"); },
  get strain() { return tok("--strain", "#d99274"); },
  get stress() { return tok("--stress", "#dd7a72"); },

  // the accent family
  get accent() { return tok("--accent", "#9184d9"); },
  get accentSoft() { return tok("--accent-soft", "#b5abfc"); },
  get accentBright() { return tok("--accent-bright", "#d2cefd"); },

  // neutrals
  get bg() { return tok("--bg", "#161826"); },
  get faint() { return tok("--faint", "#75798c"); },
  get ghost() { return tok("--ghost", "#595d6c"); },

  // chart-only extensions (series that need to be distinct from status hues)
  get gold() { return tok("--chart-gold", "#c99c50"); },
  get slate() { return tok("--chart-slate", "#7f95cc"); },
  get amber() { return tok("--chart-amber", "#d9b274"); },
  get ink() { return tok("--chart-ink", "#b2b6ca"); },
  get inkBright() { return tok("--chart-ink-bright", "#cfd3e5"); },
  get grid() { return tok("--chart-grid", "rgba(233,233,237,0.07)"); },
};
