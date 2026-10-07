/**
 * Riptide's public paper book, beside Seiche's own gauge and Undertow's board.
 * A failed read stays blank. Real orders that are not confirmed off stay blank.
 */
import { useEffect, useState } from "react";
import { loadPaperNote } from "./riptidePaperView.mjs";

const UNREADABLE = "Riptide's public paper update could not be read. Nothing is filled in.";

export default function RiptidePaperNote() {
  const [lines, setLines] = useState<string[] | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    loadPaperNote((url, init) => fetch(url, {
      ...init,
      signal: init?.signal ? AbortSignal.any([controller.signal, init.signal]) : controller.signal,
    }), { lead: "seiche" })
      .then((note) => { if (!controller.signal.aborted) setLines(note.lines); })
      .catch(() => { if (!controller.signal.aborted) setLines([UNREADABLE]); });
    return () => controller.abort();
  }, []);

  return (
    <div className="card span12">
      <h2>Riptide paper book</h2>
      <div className="sub">
        Riptide publishes a dated paper simulation. Read its verified execution
        state and allocation below, alongside Seiche's funding evidence.
      </div>
      {!lines && (
        <div className="coverage" style={{ marginTop: 8 }}>Reading the public paper snapshot.</div>
      )}
      {lines && lines.map((line, index) => (
        <p className="coverage" style={{ marginTop: 8 }} key={index}>{line}</p>
      ))}
    </div>
  );
}
