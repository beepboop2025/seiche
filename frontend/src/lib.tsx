/** Shared primitives for all tabs. */

export type Any = Record<string, any>;

export const fmt = (v: number | null | undefined, d = 1, unit = "") =>
  v == null
    ? "—"
    : `${v.toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d })}${unit}`;

export function Stat({ k, blk, unit, d = 2 }: { k: string; blk: Any; unit: string; d?: number }) {
  return (
    <div className="stat">
      <div className="k">{k}</div>
      <div className="v">{blk ? fmt(blk.value, d, unit) : "—"}</div>
      <div className="asof">{blk?.asof ?? "no data"}</div>
    </div>
  );
}

export function Fault({ name, reason, span = 6 }: { name: string; reason?: string; span?: number }) {
  return (
    <div className={`card span${span}`}>
      <h2>{name}</h2>
      <div className="faults">ENGINE DOWN — {reason ?? "unknown"}</div>
    </div>
  );
}

export function Method({ children }: { children: any }) {
  return <div className="method">{children}</div>;
}

export function Decomp({ composite }: { composite: Any }) {
  return (
    <div className="decomp">
      {(composite.decomposition ?? []).map((d: Any) => (
        <div className="row" key={d.component}>
          <span className={d.status === "DEAD" ? "dead" : ""}>{d.component}</span>
          <div className="bar">
            <div
              style={{
                width: `${d.score ?? 0}%`,
                background: d.status === "DEAD" ? "#e5484d" : undefined,
              }}
            />
          </div>
          <span className={d.status === "DEAD" ? "dead" : ""}>
            {d.status === "DEAD" ? "DEAD" : fmt(d.score, 0)}
          </span>
        </div>
      ))}
    </div>
  );
}
