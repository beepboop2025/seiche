/**
 * WeekAhead: the current Monday issue, on the board's default view.
 *
 * The Week Ahead is the one artifact here that travels: it says a number, names
 * the date that settles it, and the next issue grades it in public. It used to
 * sit behind DISPATCHES, which was the last of seventeen tabs. This card puts
 * the live issue where a first-time reader lands.
 *
 * Where the calls come from
 * -------------------------
 * The published issue itself, `dispatches/<slug>.md`, the same file the
 * DISPATCHES tab renders. Not a new endpoint: the structured call ledger lives
 * in the desk's private state file and the letter is the public artifact, so
 * the letter is what this reads. Section 5 is machine-written by
 * `dispatch_weekly._calls_section` in one fixed shape:
 *
 *   - **W1-2** · <claim> Expected: <expected>. Resolves <YYYY-MM-DD>, <rule>.
 *
 * Parsing generated text is a contract with a generator, so this treats a
 * parse miss as normal rather than exceptional: if section 5 ever changes
 * shape, the card still renders the issue, its summary and the link, just
 * without the call list. A degraded card beats a blank one, and it beats a
 * card confidently showing nothing.
 *
 * Nothing here is gated, and this component reads no identity: it fetches two
 * static files that any anonymous visitor can already open directly.
 */
import { useEffect, useState } from "react";
import Subscribe from "./Subscribe";

type Entry = { slug: string; title: string; date: string; summary: string; tag?: string };
type Call = { id: string; claim: string; resolves: string | null };

// The desk's own marker for its continuation. Never shown in a card.
const DESK_MARKERS = ["<!--HAS-DESK-->", "<!--HAS-PAID-->"];

const stripMd = (s: string) =>
  s.replace(/\*\*(.+?)\*\*/g, "$1").replace(/[*_`]/g, "").replace(/\s+/g, " ").trim();

/** Section 5 of the issue, as written by dispatch_weekly._calls_section. */
export function parseCalls(md: string): Call[] {
  const body = DESK_MARKERS.reduce((m, marker) => m.replace(marker, ""), md);
  // Take everything from the calls heading to the next heading. The section
  // numbers are stable but the heading text is what identifies it.
  const start = body.search(/^##\s.*Pre-registered calls\s*$/im);
  if (start < 0) return [];
  const rest = body.slice(start);
  const nextHeading = rest.slice(1).search(/^##\s/m);
  const section = nextHeading < 0 ? rest : rest.slice(0, nextHeading + 1);

  const calls: Call[] = [];
  for (const line of section.split("\n")) {
    const m = line.match(/^-\s+\*\*(.+?)\*\*\s*·\s*(.+)$/);
    if (!m) continue;
    const tail = m[2];
    const resolves = tail.match(/Resolves\s+(\d{4}-\d{2}-\d{2})/)?.[1] ?? null;
    // The claim is everything before the desk's own "Expected:" hinge. Without
    // the hinge, fall back to the first sentence rather than dumping the rule.
    const claim = tail.split(/\s+Expected:\s+/)[0] ?? tail;
    calls.push({ id: stripMd(m[1]), claim: stripMd(claim), resolves });
  }
  return calls;
}

/** The issue number, from the generated title ("The Week Ahead 3: …"). */
export function issueNo(title: string): string | null {
  return title.match(/The Week Ahead\s+(\d+)/i)?.[1] ?? null;
}

export default function WeekAhead() {
  const [entry, setEntry] = useState<Entry | null>(null);
  const [calls, setCalls] = useState<Call[]>([]);
  const [done, setDone] = useState(false);

  useEffect(() => {
    let live = true;
    fetch("dispatches/index.json")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("no index"))))
      .then((index: Entry[]) => {
        // Newest first is how the generator writes the index, but sort anyway
        // rather than trusting file order for the thing on the front page.
        const latest = (Array.isArray(index) ? index : [])
          .filter((e) => typeof e?.slug === "string" && e.slug.endsWith("-week-ahead"))
          .sort((a, b) => String(b.date).localeCompare(String(a.date)))[0];
        if (!latest) throw new Error("no week-ahead issue yet");
        if (live) setEntry(latest);
        return fetch(`dispatches/${latest.slug}.md`);
      })
      .then((r) => (r && r.ok ? r.text() : Promise.reject(new Error("no body"))))
      .then((md) => { if (live) setCalls(parseCalls(md)); })
      .catch(() => { /* no issue, or an unreadable one: the card just omits it */ })
      .finally(() => { if (live) setDone(true); });
    return () => { live = false; };
  }, []);

  if (!done) return null;

  // Every call in an issue resolves on the same Monday in practice, but the
  // ledger allows carried calls with their own dates, so only claim a single
  // resolve date when they genuinely agree.
  const dates = Array.from(new Set(calls.map((c) => c.resolves).filter(Boolean)));
  const oneDate = dates.length === 1 ? dates[0] : null;
  const n = issueNo(entry?.title ?? "");

  return (
    <div className="weekahead">
      {entry && (
        <div className="wa-card">
          <div className="wa-head">
            <div className="wa-kicker">
              The Week Ahead{n ? ` · issue ${n}` : ""} · {entry.date}
            </div>
            <a className="wa-open" href={`#dispatches/${entry.slug}`}>read the full issue →</a>
          </div>

          <div className="wa-title">{entry.title}</div>

          {calls.length > 0 ? (
            <>
              <div className="wa-callhead">
                {calls.length} pre-registered call{calls.length === 1 ? "" : "s"}
                {oneDate ? <> · all resolve <span className="wa-when">{oneDate}</span></> : null}
              </div>
              <ol className="wa-calls">
                {calls.map((c) => (
                  <li key={c.id}>
                    <span className="wa-id">{c.id}</span>
                    <span className="wa-claim">{c.claim}</span>
                    {!oneDate && c.resolves && <span className="wa-when">resolves {c.resolves}</span>}
                  </li>
                ))}
              </ol>
              <div className="wa-foot">
                Each call carries the number the desk expects and the rule that decides it. Next
                Monday's issue opens by grading these, misses first.
              </div>
            </>
          ) : (
            <div className="wa-foot">{entry.summary}</div>
          )}
        </div>
      )}
      <Subscribe compact />
    </div>
  );
}
