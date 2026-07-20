/**
 * Live-data hooks — the countdown, the clock and the change-flash.
 *
 * The terminal reloads the overview every five minutes (see App.tsx); these
 * hooks make that rhythm visible. The countdown anchors on the last moment
 * the snapshot IDENTITY changed (a load actually landed), so it stays honest
 * even if a fetch fails and the old data keeps showing.
 */
import { useEffect, useRef, useState } from "react";

export const REFRESH_MS = 5 * 60 * 1000;

/** Ticking clock. stepMs=1000 gives a once-a-second "now". */
export const useNow = (stepMs = 1000): number => {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), stepMs);
    return () => clearInterval(t);
  }, [stepMs]);
  return now;
};

const two = (n: number) => String(n).padStart(2, "0");

/** "HH:MM:SS" in UTC — the board runs on UTC, say so in the label. */
export const utcHMS = (ms: number): string => {
  const d = new Date(ms);
  return `${two(d.getUTCHours())}:${two(d.getUTCMinutes())}:${two(d.getUTCSeconds())}`;
};

/**
 * Seconds until the next 5-minute refresh, anchored on the last snap change.
 * Returns the last-landed timestamp too, for the "data as of" line.
 */
export const useRefreshCountdown = (snap: unknown, now: number): { lastLanding: number; secondsLeft: number } => {
  const last = useRef<number>(Date.now());
  useEffect(() => {
    if (snap != null) last.current = Date.now();
  }, [snap]);
  const elapsed = now - last.current;
  return { lastLanding: last.current, secondsLeft: Math.max(0, Math.ceil((REFRESH_MS - elapsed) / 1000)) };
};

/**
 * Direction of the last change in a reading, held for ~1.4s so CSS can flash
 * it. Up flashes amber, down flashes blue (styled in styles-cinema.css) —
 * never green/red: on a stress gauge up is bad and the ramp must not read as
 * approval/disapproval. First paint never flashes.
 */
export const useChangeFlash = (v: number | null | undefined): "up" | "down" | null => {
  const [flash, setFlash] = useState<"up" | "down" | null>(null);
  const prev = useRef(v);
  useEffect(() => {
    const from = prev.current;
    prev.current = v;
    if (from == null || v == null || from === v) return;
    setFlash(v > from ? "up" : "down");
    const t = setTimeout(() => setFlash(null), 1400);
    return () => clearTimeout(t);
  }, [v]);
  return flash;
};
