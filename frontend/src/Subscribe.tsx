/**
 * Subscribe: the optional email field for The Week Ahead.
 *
 * Optional means optional. Nothing on this terminal is behind it: every tab,
 * every dispatch, every CSV and every MCP tool reads the same whether or not an
 * address was ever typed here. If this component failed to render at all, a
 * reader would lose a convenience and no content.
 *
 * Three states, and the first one is the reason this is safe to merge before
 * Listmonk exists:
 *
 *   asking   GET /api/subscribe has not answered yet. Draw nothing rather than
 *            a form that might have nowhere to post.
 *   mailto   the list is not wired up (or there is no API at all, which is the
 *            case on the static GitHub Pages build). Draw a plain mailto link.
 *   form     the list is live. Draw the field.
 *
 * The reader never types an address into a form with no destination, and no
 * deploy order can produce that state: the front door asks first.
 */
import { useEffect, useId, useState } from "react";
import { API_BASE } from "./apiBase";

const DESK = "desk@seiche.info";
const MAILTO = `mailto:${DESK}?subject=${encodeURIComponent("The Week Ahead")}`;

type Status = { enabled: boolean; mailto?: string } | null;

export default function Subscribe({ compact = false }: { compact?: boolean }) {
  // BOARD and DISPATCHES each render one of these today, but a duplicate id
  // would silently break the label-to-input association if a second ever lands
  // on the same page. useId costs nothing and removes the trap.
  const fieldId = useId();
  const [status, setStatus] = useState<Status>(null);
  const [asked, setAsked] = useState(false);
  const [email, setEmail] = useState("");
  const [sending, setSending] = useState(false);
  const [said, setSaid] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);

  useEffect(() => {
    let live = true;
    fetch(`${API_BASE}/api/subscribe`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((j) => { if (live) setStatus(j); })
      // No API, an old build, or the route not yet at the edge: all the same
      // answer, which is the mailto link. Never an error in the reader's face.
      .catch(() => { if (live) setStatus({ enabled: false }); })
      .finally(() => { if (live) setAsked(true); });
    return () => { live = false; };
  }, []);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim() || sending) return;
    setSending(true);
    setSaid(null);
    fetch(`${API_BASE}/api/subscribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: email.trim() }),
    })
      .then(async (r) => {
        const body = await r.json().catch(() => ({}));
        if (r.status === 422) {
          setSaid({ tone: "bad", text: "That does not look like an email address." });
          return;
        }
        if (r.status === 429) {
          setSaid({ tone: "bad", text: "Too many tries in a row. Give it a minute." });
          return;
        }
        if (!r.ok) {
          setSaid({ tone: "bad", text: `Could not reach the desk. Mail ${DESK} instead.` });
          return;
        }
        setSaid({ tone: "ok", text: String(body.message ?? "Check your inbox for the confirmation link.") });
        setEmail("");
      })
      .catch(() => setSaid({ tone: "bad", text: `Could not reach the desk. Mail ${DESK} instead.` }))
      .finally(() => setSending(false));
  };

  if (!asked) return null;

  const pitch = (
    <p className="sub-pitch">
      <strong>The Week Ahead</strong> is the Monday letter: a set of pre-registered dated calls, each
      with the number the desk expects, the date it resolves and the rule that decides it. The next
      issue opens by grading them, misses first. It runs on the same free public data as the board.
    </p>
  );

  if (!status?.enabled) {
    return (
      <section className={`subscribe${compact ? " compact" : ""}`} aria-label="The Week Ahead by email">
        {pitch}
        <p className="sub-note">
          The list is not open yet. Mail <a href={MAILTO}>{DESK}</a> and you go on it the day it
          opens. Every issue is published here in full either way, free, no address needed.
        </p>
      </section>
    );
  }

  return (
    <section className={`subscribe${compact ? " compact" : ""}`} aria-label="The Week Ahead by email">
      {pitch}
      <form className="sub-form" onSubmit={submit}>
        <label className="sub-label" htmlFor={fieldId}>Email, optional</label>
        <div className="sub-row">
          <input
            id={fieldId}
            type="email"
            inputMode="email"
            autoComplete="email"
            placeholder="you@desk.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={sending}
          />
          <button type="submit" disabled={sending || !email.trim()}>
            {sending ? "sending…" : "send me Monday's"}
          </button>
        </div>
      </form>
      {said && (
        <div className="sub-said" role="status" aria-live="polite"
             style={{ color: said.tone === "ok" ? "var(--calm)" : "var(--stress)" }}>
          {said.text}
        </div>
      )}
      <p className="sub-note">
        Double opt-in: one confirmation link, and nothing else is ever sent unless you click it.
        One-click unsubscribe in every issue. The address goes to the list and nowhere else, and it
        unlocks nothing here, because there is nothing here to unlock. Prefer not to?{" "}
        <a href="#dispatches">Read every issue on the board</a> or mail <a href={MAILTO}>{DESK}</a>.
      </p>
    </section>
  );
}
