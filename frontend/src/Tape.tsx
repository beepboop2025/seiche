import type { CSSProperties } from "react";
import { Any, fmt } from "./lib";

// The tape: the ten headline gauges drifting under the masthead, terminal
// style. The drift is data too — the loop tightens as the composite rises
// (CALM ~95s per pass, STRESS ~30s). Hover holds it still to read; the
// second copy of the list exists only to make the loop seamless.

interface Item {
  k: string;
  v: string;
  tone?: "warn" | "bad";
}

const val = (b: Any) => (b && b.value != null ? (b.value as number) : null);

function buildItems(snap: Any): Item[] {
  const h = snap.headline ?? {};
  const sofr = val(h.sofr_pct), iorb = val(h.iorb_pct);
  const spreadBp = sofr != null && iorb != null ? (sofr - iorb) * 100 : null;
  const srf = val(h.srf_accepted_b);
  const vix = val(h.vix);
  const hy = val(h.hy_oas_pct);
  const items: Item[] = [
    { k: "SOFR", v: fmt(sofr, 2, "%") },
    { k: "EFFR", v: fmt(val(h.effr_pct), 2, "%") },
    { k: "IORB", v: fmt(val(h.iorb_pct), 2, "%") },
    {
      k: "SOFR−IORB",
      v: spreadBp == null ? "—" : `${spreadBp > 0 ? "+" : ""}${fmt(spreadBp, 0, "bp")}`,
      tone: spreadBp != null && spreadBp >= 5 ? "bad" : spreadBp != null && spreadBp > 0 ? "warn" : undefined,
    },
    { k: "RESERVES", v: `$${fmt(val(h.reserves_b), 0, "B")}` },
    { k: "ON RRP", v: `$${fmt(val(h.rrp_b), 1, "B")}` },
    { k: "TGA", v: `$${fmt(val(h.tga_b), 0, "B")}` },
    {
      k: "SRF",
      v: `$${fmt(srf, 1, "B")}`,
      tone: srf != null && srf >= 25 ? "bad" : srf != null && srf >= 5 ? "warn" : undefined,
    },
    { k: "DISC WIN", v: `$${fmt(val(h.dw_b), 1, "B")}` },
    {
      k: "VIX",
      v: fmt(vix, 1),
      tone: vix != null && vix >= 30 ? "bad" : vix != null && vix >= 20 ? "warn" : undefined,
    },
    { k: "HY OAS", v: fmt(hy, 2, "%"), tone: hy != null && hy >= 4 ? "warn" : undefined },
  ];
  return items;
}

export default function Tape({ snap }: { snap: Any }) {
  const items = buildItems(snap);
  const stress = Math.min(1, Math.max(0, (snap.engines?.composite?.value ?? 20) / 100));
  const loopSeconds = Math.round(95 - 65 * stress);

  const run = (hidden: boolean) => (
    <span aria-hidden={hidden || undefined}>
      {items.map((it, i) => (
        <span className="tape-item" key={`${hidden ? "b" : "a"}-${i}`}>
          <span className="tk">{it.k}</span>
          <span className={`tv ${it.tone ?? ""}`}>{it.v}</span>
          <span className="tape-sep">·</span>
        </span>
      ))}
    </span>
  );

  return (
    <div className="tape" role="marquee" aria-label="headline money market readings">
      <div className="tape-inner" style={{ "--tape-t": `${loopSeconds}s` } as CSSProperties}>
        {run(false)}
        {run(true)}
      </div>
    </div>
  );
}
