import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Command, ENTRIES, parseCode, score } from "./commands";

// The command line (⌘K / Ctrl+K / `/`). Dual grammar: an uppercase function
// code or `ASOF 2019-09-12` executes directly on Enter; anything else
// fuzzy-matches the registry. Ported from LiquiLens's palette, Nocturne skin.

export default function CommandPalette({ onClose, onCommand }: {
  onClose: () => void;
  onCommand: (cmd: Command) => void;
}) {
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const t = setTimeout(() => inputRef.current?.focus(), 30);
    return () => clearTimeout(t);
  }, []);

  const coded = parseCode(query);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    return ENTRIES.map((e) => ({ e, s: score(q, e) }))
      .filter((r) => r.s >= 0)
      .sort((a, b) => b.s - a.s)
      .slice(0, 9)
      .map((r) => r.e);
  }, [query]);

  const run = useCallback((cmd: Command | undefined) => {
    if (!cmd) return;
    onCommand(cmd);
    onClose();
  }, [onCommand, onClose]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); onClose(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); setCursor((c) => Math.min(c + 1, results.length - 1)); }
      else if (e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)); }
      else if (e.key === "Enter") { e.preventDefault(); run(coded ?? results[cursor]?.run); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [results, cursor, coded, run, onClose]);

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-idx="${cursor}"]`)?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  return (
    <div className="palette-overlay" onClick={onClose}>
      <div className="palette" role="dialog" aria-label="Command line" onClick={(e) => e.stopPropagation()}>
        <div className="palette-inputrow">
          <span className="palette-prompt">›</span>
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => { setQuery(e.target.value); setCursor(0); }}
            placeholder="function code (FCT, SWELL, ASOF 2019-09-12) or search…"
            aria-label="Command"
            spellCheck={false}
          />
          {coded && <span className="palette-go">↵ GO</span>}
          <kbd>esc</kbd>
        </div>

        <div className="palette-list" ref={listRef}>
          {results.length === 0 && !coded && (
            <div className="palette-empty">nothing matches "{query}" — try a tab name or a code like PRF</div>
          )}
          {results.map((e, i) => (
            <button
              key={e.code}
              data-idx={i}
              className={`palette-item${i === cursor && !coded ? " active" : ""}`}
              onClick={() => run(e.run)}
              onMouseMove={() => setCursor(i)}
            >
              <span className="palette-code">{e.code}</span>
              <span className="palette-body">
                <span className="palette-title">{e.title}</span>
                <span className="palette-hint">{e.hint}</span>
              </span>
              {i === cursor && !coded && <span className="palette-enter">↵</span>}
            </button>
          ))}
        </div>

        <div className="palette-foot">
          <span>↑↓ move · ↵ go</span>
          <span>codes execute directly — <span className="palette-code" style={{ padding: "1px 5px" }}>ASOF 2019-09-12</span> replays the board</span>
        </div>
      </div>
    </div>
  );
}
