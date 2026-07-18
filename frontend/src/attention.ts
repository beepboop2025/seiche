import { useEffect } from "react";

/**
 * Unseen-panel marks — the attention-aware layer (arXiv 2404.10732), scaled
 * to what that study's participants actually liked: a subtle border cue, no
 * colour hijack, no always-on overlay. Every card carries a quiet accent
 * point until the reader has dwelt on it (≥60% visible for two seconds, a
 * viewport-proxy for gaze); then it is marked .seen and the point goes out.
 * Re-arms per tab visit, so the marks answer "have I checked this panel on
 * this pass of the board".
 */
export function useAttentionMarks(active: unknown) {
  useEffect(() => {
    const timers = new Map<Element, number>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const en of entries) {
          if (en.isIntersecting) {
            if (!timers.has(en.target)) {
              timers.set(
                en.target,
                window.setTimeout(() => {
                  en.target.classList.add("seen");
                  io.unobserve(en.target);
                  timers.delete(en.target);
                }, 2000),
              );
            }
          } else {
            const t = timers.get(en.target);
            if (t != null) {
              clearTimeout(t);
              timers.delete(en.target);
            }
          }
        }
      },
      { threshold: 0.6 },
    );

    // cards render lazily behind Suspense; a debounced re-scan catches them
    let queued = false;
    const scan = () => {
      queued = false;
      document.querySelectorAll(".card:not(.seen)").forEach((el) => io.observe(el));
    };
    const queueScan = () => {
      if (!queued) {
        queued = true;
        requestAnimationFrame(scan);
      }
    };
    scan();
    const root = document.getElementById("root");
    const mo = new MutationObserver(queueScan);
    if (root) mo.observe(root, { childList: true, subtree: true });

    return () => {
      mo.disconnect();
      io.disconnect();
      timers.forEach((t) => clearTimeout(t));
    };
  }, [active]);
}
