/**
 * The Live Pulse — the terminal's heartbeat line in the masthead.
 *
 * A soft pulse dot (the rhythm tightens with stress, like the sonar ping),
 * a ticking "data as of HH:MM:SS UTC" clock so staleness is always one
 * glance away, and the visible countdown to the next 5-minute refresh.
 * The pulse animation is CSS-only; DESK mode and reduced motion still it.
 */
import { useNow, useRefreshCountdown, utcHMS } from "./useLive";

export default function LivePulse({ snap }: { snap: Record<string, any> }) {
  const now = useNow(1000);
  const { secondsLeft } = useRefreshCountdown(snap, now);
  const stress = Math.min(1, Math.max(0, (snap?.engines?.composite?.value ?? 20) / 100));

  const asOf = snap?.generated_at ? Date.parse(snap.generated_at) : null;
  const mm = Math.floor(secondsLeft / 60);
  const ss = String(secondsLeft % 60).padStart(2, "0");

  return (
    <span className="livepulse" title="the board reloads every five minutes; the as-of time is when the snapshot was generated">
      <span
        className="livepulse-dot"
        style={{ ["--pulse-t" as string]: `${(3.4 - 2.2 * stress).toFixed(2)}s` }}
        aria-hidden="true"
      />
      <span className="livepulse-txt">
        data as of {asOf != null && !Number.isNaN(asOf) ? utcHMS(asOf) : "—"} UTC · refresh in {mm}:{ss}
      </span>
    </span>
  );
}
