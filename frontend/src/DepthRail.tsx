import { useEffect, useRef, useState } from "react";

// The sounding line: scroll position rendered as depth. A hairline on the
// right edge with a glowing bob that descends as the reader does, labelled
// in metres of water column (surface at the masthead, 4,000 m at the
// footer). Progress review for a long board (arXiv 2602.19853) that costs
// one line of paint.

export default function DepthRail() {
  const [p, setP] = useState(0);
  const [scrollable, setScrollable] = useState(false);
  const raf = useRef(0);

  useEffect(() => {
    const measure = () => {
      const doc = document.documentElement;
      const range = doc.scrollHeight - window.innerHeight;
      setScrollable(range > 240);
      setP(range > 0 ? Math.min(1, Math.max(0, window.scrollY / range)) : 0);
    };
    const onScroll = () => {
      cancelAnimationFrame(raf.current);
      raf.current = requestAnimationFrame(measure);
    };
    measure();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    const t = setInterval(measure, 2000); // content height changes as tabs stream in
    return () => {
      cancelAnimationFrame(raf.current);
      clearInterval(t);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);

  if (!scrollable) return null;
  const top = `${(p * 100).toFixed(2)}%`;
  return (
    <div className="depth-rail" aria-hidden="true">
      <div className="bob" style={{ top }} />
      <div className="fathom" style={{ top }}>−{Math.round(p * 4000).toLocaleString("en-US")} m</div>
    </div>
  );
}
