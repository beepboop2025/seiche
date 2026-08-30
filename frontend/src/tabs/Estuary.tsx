import { useMemo, useState, type CSSProperties } from "react";
import Chart, { type ChartSeries } from "../Chart";
import { Any, Fault, fmt } from "../lib";
import { worldMarketSharePath } from "../shareRoutes";
import "../styles-estuary.css";

const C = {
  fx: "#83a9f8",
  copper: "#c99168",
  grain: "#d5c47c",
  cash: "#70c9b9",
  stress: "#ed8179",
  paper: "#e9ebf3",
  muted: "#7e8599",
};

const DOLLAR_SERIES: ChartSeries[] = [
  { label: "broad USD", color: C.paper },
  { label: "advanced-economy USD", color: C.fx },
  { label: "emerging-market USD", color: C.copper },
];
const MATERIAL_SERIES: ChartSeries[] = [
  { label: "WTI", color: C.copper },
  { label: "Henry Hub", color: C.fx },
  { label: "copper", color: C.cash },
  { label: "all commodities", color: C.grain, dash: [6, 4] },
];
const FUNDING_SERIES: ChartSeries[] = [
  { label: "SOFR − IORB", color: C.cash },
  { label: "nonfinancial CP − bill", color: C.copper },
  { label: "financial CP − bill", color: C.fx },
];
const PRESSURE_SERIES: ChartSeries[] = [
  { label: "daily upstream proxy", color: C.copper },
  { label: "funding priced", color: C.fx },
];
const GAP_SERIES: ChartSeries[] = [{ label: "daily passage gap", color: C.cash }];

const signed = (value: unknown, digits = 1, unit = "") => {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  const n = Number(value);
  return `${n > 0 ? "+" : n < 0 ? "−" : ""}${fmt(Math.abs(n), digits)}${unit}`;
};

const compactUsd = (value: number | null | undefined) => {
  if (value == null || !Number.isFinite(value)) return "—";
  const sign = value < 0 ? "−" : "";
  const abs = Math.abs(value);
  if (abs >= 1e12) return `${sign}$${fmt(abs / 1e12, 2)}tn`;
  if (abs >= 1e9) return `${sign}$${fmt(abs / 1e9, 2)}bn`;
  if (abs >= 1e6) return `${sign}$${fmt(abs / 1e6, 1)}mn`;
  return `${sign}$${fmt(abs, 0)}`;
};

const compactValue = (value: unknown) => {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  const n = Number(value);
  if (Math.abs(n) >= 1000) return n.toLocaleString("en-US", { maximumFractionDigits: 1 });
  if (Math.abs(n) >= 100) return fmt(n, 1);
  if (Math.abs(n) >= 10) return fmt(n, 2);
  return fmt(n, 3);
};

const pressureTone = (value: unknown) => {
  const n = Number(value);
  if (!Number.isFinite(n)) return "muted";
  if (n >= 80) return "stress";
  if (n >= 65) return "warm";
  if (n <= 30) return "cool";
  return "mid";
};

function ScoreLine({ label, value, tone, note }: { label: string; value: number | null; tone: string; note: string }) {
  return (
    <div className="est-scoreline">
      <div><span>{label}</span><small>{note}</small></div>
      <div className="est-scoreline__track"><i className={tone} style={{ width: `${Math.max(0, Math.min(100, value ?? 0))}%` }} /></div>
      <strong>{value == null ? "—" : fmt(value, 1)}</strong>
    </div>
  );
}

function PassageMap({ passage }: { passage: Any }) {
  const edges = (passage?.edges ?? []) as Any[];
  const [active, setActive] = useState(0);
  const geometry = useMemo(() => {
    const sources = edges.map((edge) => edge.source as string);
    const targets = Array.from(new Set(edges.map((edge) => edge.target as string)));
    const height = Math.max(350, sources.length * 56 + 54);
    const sourceY = new Map(sources.map((source, index) => [source, 46 + index * ((height - 92) / Math.max(1, sources.length - 1))]));
    const targetY = new Map(targets.map((target, index) => [target, 72 + index * ((height - 144) / Math.max(1, targets.length - 1))]));
    return { sources, targets, sourceY, targetY, height };
  }, [edges]);
  const selected = edges[active] ?? edges[0];

  return (
    <section className="est-passage" aria-labelledby="est-passage-title">
      <div className="est-section-head">
        <div>
          <span className="est-kicker">SEICHE DIFFERENTIATOR · DISCOVERY → HOLDOUT</span>
          <h2 id="est-passage-title">The Passage</h2>
        </div>
        <p>What moved upstream, where it historically landed, how long it took—and whether the relationship survived data it never saw.</p>
      </div>
      <div className="est-passage__legend" aria-label="Passage evidence legend">
        <span><i className="earned" /> earned in holdout</span>
        <span><i className="tentative" /> tentative</span>
        <span><i className="not_earned" /> not earned</span>
      </div>
      {edges.length ? (
        <>
          <div className="est-passage__readout" aria-live="polite">
            <div><span>ACTIVE PASSAGE</span><strong>{selected.source} → {selected.target}</strong></div>
            <div><span>DISCOVERY r</span><strong>{signed(selected.corr_discovery, 2)}</strong></div>
            <div><span>UNTOUCHED HOLDOUT r</span><strong className={selected.status}>{signed(selected.corr_holdout, 2)}</strong></div>
            <div><span>DELAY</span><strong>{selected.lag_bd}bd</strong></div>
            <div><span>HOLDOUT n</span><strong>{selected.n_holdout}</strong></div>
            <div><span>VERDICT</span><strong className={selected.status}>{String(selected.status).replace("_", " ")}</strong></div>
          </div>
          <div className="est-passage__plot">
            <svg viewBox={`0 0 1000 ${geometry.height}`} role="group" aria-label="FX and energy lead-lag evidence map into money-market funding spreads">
              <text x="28" y="20" className="est-passage__axis">UPSTREAM CASH PRESSURE</text>
              <text x="972" y="20" textAnchor="end" className="est-passage__axis">PRICE OF CASH</text>
              {geometry.targets.map((target) => {
                const y = geometry.targetY.get(target) ?? 0;
                return (
                  <g key={target} className="est-passage__target">
                    <line x1="787" x2="817" y1={y} y2={y} />
                    <circle cx="818" cy={y} r="4" />
                    <text x="834" y={y + 4}>{target}</text>
                  </g>
                );
              })}
              {edges.map((edge, index) => {
                const y1 = geometry.sourceY.get(edge.source) ?? 0;
                const y2 = geometry.targetY.get(edge.target) ?? 0;
                const strength = Math.abs(Number(edge.corr_holdout) || 0);
                return (
                  <g
                    key={`${edge.source}-${edge.target}`}
                    className={`est-passage__edge ${edge.status}${index === active ? " active" : ""}`}
                    tabIndex={0}
                    role="button"
                    aria-label={`${edge.source} to ${edge.target}, ${edge.status}, holdout correlation ${edge.corr_holdout}, lag ${edge.lag_bd} business days`}
                    onMouseEnter={() => setActive(index)}
                    onFocus={() => setActive(index)}
                    onClick={() => setActive(index)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        setActive(index);
                      }
                    }}
                  >
                    <path
                      d={`M 198 ${y1} C 420 ${y1}, 570 ${y2}, 786 ${y2}`}
                      style={{ strokeWidth: 1.25 + strength * 5 } as CSSProperties}
                    />
                    <circle cx="193" cy={y1} r="5" />
                    <text x="178" y={y1 + 4} textAnchor="end">{edge.source}</text>
                    <text x="520" y={(y1 + y2) / 2 - 7} textAnchor="middle" className="est-passage__lag">{edge.lag_bd}bd · r {signed(edge.corr_holdout, 2)}</text>
                    <title>{edge.search}</title>
                  </g>
                );
              })}
            </svg>
          </div>
          <details className="est-details">
            <summary>Inspect every discovery / holdout result</summary>
            <div className="est-table-scroll">
              <table className="mini">
                <thead><tr><th>source</th><th>lands in</th><th>lag</th><th>discovery r</th><th>holdout r</th><th>holdout n</th><th>status</th></tr></thead>
                <tbody>{edges.map((edge) => (
                  <tr key={`${edge.source}-${edge.target}`}>
                    <td>{edge.source}</td><td>{edge.target}</td><td className="num">{edge.lag_bd}bd</td>
                    <td className="num">{signed(edge.corr_discovery, 3)}</td><td className="num">{signed(edge.corr_holdout, 3)}</td>
                    <td className="num">{edge.n_holdout}</td><td className={edge.status}>{String(edge.status).replace("_", " ")}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </details>
        </>
      ) : <div className="est-empty">No passage earned enough aligned daily history to be tested.</div>}
      <div className="est-doctrine">{passage?.doctrine}</div>
    </section>
  );
}

function FxLedger({ data }: { data: Any }) {
  const [filter, setFilter] = useState("ALL");
  const rows = ((data?.currencies ?? []) as Any[]).filter((row) => filter === "ALL" || row.bucket === filter);
  return (
    <section className="est-ledger" aria-labelledby="est-fx-title">
      <div className="est-section-head">
        <div><span className="est-kicker est-kicker--fx">DAILY · FEDERAL RESERVE H.10</span><h2 id="est-fx-title">The dollar pressure book</h2></div>
        <div className="est-segmented" aria-label="Filter currencies">
          {["ALL", "AFE", "EM"].map((option) => <button key={option} className={filter === option ? "active" : ""} onClick={() => setFilter(option)}>{option}</button>)}
        </div>
      </div>
      <div className="est-table-scroll">
        <table className="est-market-table">
          <thead><tr><th>currency</th><th>local / USD</th><th>5d</th><th>20d</th><th>20d vol</th><th>depreciation pctl</th><th>policy − EFFR</th><th>clock</th></tr></thead>
          <tbody>{rows.map((row) => (
            <tr key={row.key}>
              <td><strong>{row.key}</strong><span>{row.label} · {row.bucket}</span></td>
              <td className="num">{compactValue(row.last_local_per_usd)}</td>
              <td className={`num ${Number(row.change_5d_pct) > 0 ? "up" : "down"}`}>{signed(row.change_5d_pct, 2, "%")}</td>
              <td className={`num ${Number(row.change_20d_pct) > 0 ? "up" : "down"}`}>{signed(row.change_20d_pct, 2, "%")}</td>
              <td className="num">{fmt(row.realized_vol_20d_pct, 1)}%</td>
              <td><div className="est-cell-score"><i className={pressureTone(row.depreciation_percentile)} style={{ width: `${row.depreciation_percentile ?? 0}%` }} /><b>{fmt(row.depreciation_percentile, 0)}</b></div></td>
              <td className="num">{row.policy_diff_vs_effr_bp == null ? "not covered" : `${signed(row.policy_diff_vs_effr_bp, 0)}bp`}</td>
              <td><time>{row.asof}</time><span>{row.policy_rate_label ? `${row.policy_rate_label} · ${row.policy_rate_cadence}` : row.source_id}</span></td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <p className="est-caption">Every pair is normalized to local currency per USD. Positive change therefore always means local-currency weakness; policy differentials are not forward points or hedged carry.</p>
    </section>
  );
}

function MaterialsBook({ data }: { data: Any }) {
  const categories = (data?.categories ?? []) as Any[];
  const rows = (data?.instruments ?? []) as Any[];
  return (
    <section className="est-materials" aria-labelledby="est-materials-title">
      <div className="est-section-head">
        <div><span className="est-kicker est-kicker--materials">DAILY ENERGY · MONTHLY IMF BREADTH</span><h2 id="est-materials-title">The physical cash book</h2></div>
        <p>Both tails matter: higher prices tie up working capital; lower prices can hit collateral and variation margin.</p>
      </div>
      <div className="est-category-rail">
        {categories.map((category) => (
          <div key={category.category}><span>{category.category}</span><strong>{fmt(category.pressure, 0)}</strong><i style={{ width: `${category.pressure ?? 0}%` }} /><small>{category.n} series · {category.leaders?.join(" / ")}</small></div>
        ))}
      </div>
      <div className="est-material-grid">
        {rows.map((row) => (
          <article key={row.key} className={`${pressureTone(row.pressure)}${row.cadence === "monthly" ? " lagged" : ""}`}>
            <header><span>{row.category}</span><time>{row.cadence} · {row.asof}</time></header>
            <h3>{row.label}</h3>
            <div className="est-material-grid__price">{compactValue(row.last)} <small>{row.unit}</small></div>
            <div className="est-material-grid__move"><span>{row.horizon}</span><strong>{signed(row.horizon_change, 2, row.change_unit === "%" ? "%" : ` ${row.change_unit}`)}</strong></div>
            <div className="est-material-grid__pressure"><i style={{ width: `${row.pressure ?? 0}%` }} /><b>{fmt(row.pressure, 0)}</b></div>
            <footer><span>{row.direction}</span><strong>{row.channel}</strong></footer>
          </article>
        ))}
      </div>
    </section>
  );
}

function AnalogLedger({ data }: { data: Any }) {
  if (!data?.ok) return (
    <section className="est-analogs"><div className="est-section-head"><div><span className="est-kicker">WHAT HAPPENED NEXT</span><h2>Cross-market analogs</h2></div></div><div className="est-empty">{data?.reason ?? "No analog ledger yet."}</div></section>
  );
  return (
    <section className="est-analogs" aria-labelledby="est-analogs-title">
      <div className="est-section-head">
        <div><span className="est-kicker">WHAT HAPPENED NEXT · DE-CLUSTERED</span><h2 id="est-analogs-title">Cross-market analogs</h2></div>
        <p>Prior daily states that looked like today before the funding outcome was revealed.</p>
      </div>
      <div className="est-analog-headline">
        <div><span>EVENT WITHIN {data.horizon_bd}bd</span><strong>{fmt(data.event_rate_pct, 1)}%</strong><small>Wilson 95% {data.event_rate_ci95_pct?.[0]}–{data.event_rate_ci95_pct?.[1]}%</small></div>
        <div><span>UNCONDITIONAL RATE</span><strong>{fmt(data.base_rate_pct, 1)}%</strong><small>{data.base_trials} non-overlapping trials</small></div>
        <div><span>LIFT</span><strong>{data.lift == null ? "—" : `${fmt(data.lift, 2)}×`}</strong><small>threshold ≥ {data.event_threshold_bp}bp widening</small></div>
        <div><span>NEIGHBORS</span><strong>{data.k}</strong><small>minimum 28 calendar days apart</small></div>
      </div>
      <div className="est-table-scroll">
        <table className="est-analog-table">
          <thead><tr><th>prior state</th><th>distance</th><th>event</th><th>max widening</th><th>SOFR−IORB</th><th>nonfin CP</th><th>financial CP</th></tr></thead>
          <tbody>{(data.analogs ?? []).map((row: Any) => (
            <tr key={row.date}><td>{row.date}</td><td className="num">{fmt(row.distance, 3)}</td><td className={row.funding_event_10bd ? "event" : "quiet"}>{row.funding_event_10bd ? "YES" : "NO"}</td><td className="num">{signed(row.max_widening_10bd_bp, 1, "bp")}</td><td className="num">{signed(row.by_market_bp?.["SOFR−IORB"], 1)}</td><td className="num">{signed(row.by_market_bp?.["Nonfinancial CP−bill"], 1)}</td><td className="num">{signed(row.by_market_bp?.["Financial CP−bill"], 1)}</td></tr>
          ))}</tbody>
        </table>
      </div>
      <div className="est-doctrine">{data.method}</div>
    </section>
  );
}

interface ScenarioState {
  fxObligations: number;
  grossBilateral: number;
  fxMove: number;
  inventory: number;
  materialMove: number;
  hedgeRatio: number;
  haircut: number;
  receivableDays: number;
  fundingRate: number;
}

const initialScenario = (engine: Any): ScenarioState => {
  const a = engine?.scenario?.assumptions ?? {};
  return {
    fxObligations: Number(a.daily_fx_obligations_usd_b ?? 5),
    grossBilateral: Number(a.gross_bilateral_share_pct ?? 10),
    fxMove: Number(a.adverse_fx_move_pct ?? 2),
    inventory: Number(a.commodity_inventory_usd_b ?? 0.5),
    materialMove: Number(a.commodity_price_move_pct ?? 10),
    hedgeRatio: Number(a.commodity_hedge_ratio_pct ?? 70),
    haircut: Number(a.haircut_increase_pct ?? 3),
    receivableDays: Number(a.receivable_days ?? 45),
    fundingRate: Number(a.funding_rate_pct ?? 5),
  };
};

const calculateScenario = (s: ScenarioState) => {
  const fxGross = s.fxObligations * 1e9;
  const principal = fxGross * s.grossBilateral / 100;
  const replacement = principal * Math.abs(s.fxMove) / 100;
  const inventory = s.inventory * 1e9;
  const move = Math.abs(s.materialMove) / 100;
  return {
    fxGross,
    principal,
    replacement,
    inventoryMove: inventory * move,
    hedgeMargin: inventory * s.hedgeRatio / 100 * move,
    haircut: inventory * s.haircut / 100,
    receivableCarry: inventory * s.fundingRate / 100 * s.receivableDays / 365,
  };
};

function Slider({ label, value, min, max, step, display, onChange }: { label: string; value: number; min: number; max: number; step: number; display: string; onChange: (value: number) => void }) {
  return (
    <label className="est-slider">
      <span>{label}</span><output>{display}</output>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(event) => onChange(Number(event.target.value))} />
    </label>
  );
}

function CashConversionLab({ engine }: { engine: Any }) {
  const defaults = useMemo(() => initialScenario(engine), [engine]);
  const [s, setS] = useState(defaults);
  const out = calculateScenario(s);
  const set = (key: keyof ScenarioState) => (value: number) => setS((current) => ({ ...current, [key]: value }));
  const materialChannel = s.materialMove >= 0 ? "working-capital expansion" : "collateral-value loss";
  return (
    <section className="est-lab" aria-labelledby="est-lab-title">
      <div className="est-section-head">
        <div><span className="est-kicker est-kicker--lab">EDITABLE IDENTITIES · NOT OBSERVED EXPOSURES</span><h2 id="est-lab-title">Cash-conversion lab</h2></div>
        <p>Turn a market move into the cash questions a treasury desk actually has to fund.</p>
      </div>
      <div className="est-lab__layout">
        <aside className="est-lab__controls">
          <div className="est-lab__control-head"><strong>ASSUMPTIONS</strong><button onClick={() => setS(defaults)}>RESET TO LIVE DEFAULTS</button></div>
          <h3>FX settlement</h3>
          <Slider label="daily gross obligations" value={s.fxObligations} min={0.5} max={50} step={0.5} display={`$${fmt(s.fxObligations, 1)}bn`} onChange={set("fxObligations")} />
          <Slider label="gross bilateral share" value={s.grossBilateral} min={0} max={30} step={1} display={`${fmt(s.grossBilateral, 0)}%`} onChange={set("grossBilateral")} />
          <Slider label="adverse FX move" value={s.fxMove} min={0} max={10} step={0.25} display={`${fmt(s.fxMove, 2)}%`} onChange={set("fxMove")} />
          <h3>Physical inventory</h3>
          <Slider label="financed inventory" value={s.inventory} min={0.05} max={10} step={0.05} display={`$${fmt(s.inventory, 2)}bn`} onChange={set("inventory")} />
          <Slider label="price move" value={s.materialMove} min={-30} max={30} step={1} display={signed(s.materialMove, 0, "%")} onChange={set("materialMove")} />
          <Slider label="hedge ratio" value={s.hedgeRatio} min={0} max={100} step={5} display={`${fmt(s.hedgeRatio, 0)}%`} onChange={set("hedgeRatio")} />
          <Slider label="haircut increase" value={s.haircut} min={0} max={15} step={0.5} display={`${fmt(s.haircut, 1)}%`} onChange={set("haircut")} />
          <Slider label="receivable days" value={s.receivableDays} min={0} max={180} step={5} display={`${fmt(s.receivableDays, 0)}d`} onChange={set("receivableDays")} />
          <Slider label="funding rate" value={s.fundingRate} min={0} max={15} step={0.25} display={`${fmt(s.fundingRate, 2)}%`} onChange={set("fundingRate")} />
        </aside>
        <div className="est-lab__results">
          <article className="est-lab__fx">
            <header><span>01 · FX SETTLEMENT WATERFALL</span><h3>Principal at risk is not the same as replacement cost.</h3></header>
            <div className="est-equation"><span>{compactUsd(out.fxGross)}<small>gross obligations</small></span><i>×</i><span>{fmt(s.grossBilateral, 0)}%<small>without PvP</small></span><i>=</i><strong>{compactUsd(out.principal)}<small>principal exposed</small></strong></div>
            <div className="est-output-grid">
              <div><span>principal without PvP</span><strong>{compactUsd(out.principal)}</strong><small>liquidity / principal exposure; not an expected loss</small></div>
              <div><span>replacement-cost shock</span><strong>{compactUsd(out.replacement)}</strong><small>{fmt(s.fxMove, 2)}% move on the unmitigated slice</small></div>
            </div>
            <p>The BIS structural share supplies the starting point. Your book, netting, cut-off misses and currency eligibility determine the real number.</p>
          </article>
          <article className="est-lab__materials">
            <header><span>02 · PHYSICAL CASH WATERFALL</span><h3>{s.materialMove >= 0 ? "Price up: more cash trapped in the same inventory." : "Price down: collateral and hedges ask for cash."}</h3></header>
            <div className="est-output-grid est-output-grid--four">
              <div><span>{materialChannel}</span><strong>{compactUsd(out.inventoryMove)}</strong><small>{fmt(Math.abs(s.materialMove), 0)}% of financed inventory</small></div>
              <div><span>hedge margin</span><strong>{compactUsd(out.hedgeMargin)}</strong><small>{fmt(s.hedgeRatio, 0)}% hedged · simplified one-for-one move</small></div>
              <div><span>haircut cash</span><strong>{compactUsd(out.haircut)}</strong><small>incremental collateral requirement</small></div>
              <div><span>receivable carry</span><strong>{compactUsd(out.receivableCarry)}</strong><small>{fmt(s.receivableDays, 0)}d at {fmt(s.fundingRate, 2)}%</small></div>
            </div>
            <div className="est-waterfall"><span style={{ width: `${Math.min(100, out.hedgeMargin / Math.max(1, out.hedgeMargin + out.haircut) * 100)}%` }} /><i style={{ width: `${Math.min(100, out.haircut / Math.max(1, out.hedgeMargin + out.haircut) * 100)}%` }} /></div>
            <p>Hedge margin and haircut calls can be same-day. Inventory value and receivable carry are balance-sheet stocks; they are shown separately so unlike cash demands are never summed into a theatrical total.</p>
          </article>
        </div>
      </div>
    </section>
  );
}

function DollarSystem({ data, structure }: { data: Any; structure: Any }) {
  return (
    <section className="est-system" aria-labelledby="est-system-title">
      <div className="est-section-head">
        <div><span className="est-kicker">BACKSTOPS + SETTLEMENT</span><h2 id="est-system-title">The pipes behind the quotes</h2></div>
        <p>Spot is only the surface. Dollar access, settlement method and offshore liabilities determine whether a move becomes a cash event.</p>
      </div>
      <div className="est-system__metrics">
        <div><span>SWAP LINES</span><strong>{data?.swap_lines?.outstanding_usd_m == null ? "—" : `$${fmt(data.swap_lines.outstanding_usd_m, 0)}M`}</strong><small>13w {signed(data?.swap_lines?.change_13w_usd_m, 0, "M")} · {data?.swap_lines?.asof}</small></div>
        <div><span>FIMA REPO</span><strong>{data?.fima_repo?.outstanding_usd_m == null ? "—" : `$${fmt(data.fima_repo.outstanding_usd_m, 0)}M`}</strong><small>foreign officials borrowing dollars · {data?.fima_repo?.asof}</small></div>
        <div><span>FOREIGN-OFFICIAL RRP</span><strong>{data?.foreign_official_rrp?.outstanding_usd_b == null ? "—" : `$${fmt(data.foreign_official_rrp.outstanding_usd_b, 1)}B`}</strong><small>13w {signed(data?.foreign_official_rrp?.change_13w_usd_b, 1, "B")} · {data?.foreign_official_rrp?.asof}</small></div>
        <div><span>OFFSHORE USD CREDIT</span><strong>{data?.offshore_dollar_credit?.outstanding_usd_t == null ? "—" : `$${fmt(data.offshore_dollar_credit.outstanding_usd_t, 2)}T`}</strong><small>{data?.offshore_dollar_credit?.asof} · quarterly / lagged</small></div>
      </div>
      <div className="est-settlement">
        <div className="est-settlement__headline"><span>APRIL 2025 STRUCTURAL BENCHMARK</span><strong>{fmt(structure?.gross_obligations_usd_t_per_day, 1)}T</strong><small>USD gross FX obligations settled per day</small></div>
        <div className="est-settlement__bar" aria-label="FX settlement method shares">
          <i className="pvp" style={{ width: `${structure?.pvp_share_pct ?? 0}%` }}><span>PvP {fmt(structure?.pvp_share_pct, 0)}%</span></i>
          <i className="net" style={{ width: `${structure?.pre_settlement_netting_share_pct ?? 0}%` }}><span>netting {fmt(structure?.pre_settlement_netting_share_pct, 0)}%</span></i>
          <i className="other" style={{ width: `${Math.max(0, 100 - (structure?.pvp_share_pct ?? 0) - (structure?.pre_settlement_netting_share_pct ?? 0) - (structure?.gross_bilateral_share_pct ?? 0))}%` }}><span>other mitigated</span></i>
          <i className="gross" style={{ width: `${structure?.gross_bilateral_share_pct ?? 0}%` }}><span>gross {fmt(structure?.gross_bilateral_share_pct, 0)}%</span></i>
        </div>
        <div className="est-settlement__risk"><strong>${fmt(structure?.gross_bilateral_usd_t_per_day, 1)}T/day</strong><span>settled gross bilateral, fully exposed to principal risk in the BIS survey</span><a href={structure?.url} target="_blank" rel="noreferrer">BIS method and source ↗</a></div>
      </div>
    </section>
  );
}

export default function Estuary({ snap }: { snap: Any }) {
  const e = snap.engines?.estuary ?? {};
  if (!e.ok) return <div className="grid"><Fault name="The Estuary" reason={e.reason} span={12} /></div>;
  const h = e.headline ?? {};
  const charts = e.charts ?? {};
  const pressureRows = (charts.daily_gap?.rows ?? []).map((row: Any[]) => [row[0], row[1], row[2]]);
  const gapRows = (charts.daily_gap?.rows ?? []).map((row: Any[]) => [row[0], row[3]]);

  return (
    <div className="est-page">
      <section className="est-hero" aria-labelledby="est-title">
        <div className="est-hero__copy">
          <span className="est-kicker">FX × MATERIALS × MONEY MARKETS</span>
          <h1 id="est-title">The Estuary.</h1>
          <p>Where exchange-rate settlement, physical inventory and the price of cash become one balance-sheet problem.</p>
          <div className="est-hero__regime"><i className={pressureTone(h.transmission_gap != null ? h.transmission_gap + 50 : null)} />{h.regime}</div>
          <blockquote>{h.verdict}</blockquote>
        </div>
        <div className="est-hero__gap">
          <span>THE PASSAGE GAP</span>
          <strong className={Number(h.transmission_gap) >= 10 ? "open" : Number(h.transmission_gap) <= -10 ? "reverse" : "sync"}>{signed(h.transmission_gap, 1)}</strong>
          <div className="est-hero__equation"><span>{fmt(h.upstream_pressure, 1)}<small>upstream pressure</small></span><i>−</i><span>{fmt(h.funding_priced, 1)}<small>funding priced</small></span></div>
          <p>Positive means FX/material cash pressure is running ahead of SOFR and CP. Context score, never a composite input.</p>
          <time>as of {e.asof} · {fmt(h.coverage_pct, 0)}% coverage</time>
        </div>
      </section>

      <section className="est-pressure" aria-label="Estuary pressure decomposition">
        <ScoreLine label="FX PRESSURE" value={h.fx_pressure} tone="fx" note="broad + EM dollar · pair depreciation · realized vol" />
        <ScoreLine label="MATERIALS PRESSURE" value={h.materials_pressure} tone="materials" note="energy · industrial metals · grains · broad index" />
        <ScoreLine label="FUNDING PRICED" value={h.funding_priced} tone="funding" note="SOFR−IORB · financial and nonfinancial CP−bill" />
      </section>

      <PassageMap passage={e.passage} />
      <FxLedger data={e.fx} />
      <MaterialsBook data={e.materials} />

      <section className="est-tape" aria-labelledby="est-tape-title">
        <div className="est-section-head"><div><span className="est-kicker">OBSERVED TAPE</span><h2 id="est-tape-title">Four views, four honest clocks</h2></div><p>Each chart stays in one interpretable unit. Monthly physical breadth never masquerades as a daily print.</p></div>
        <div className="est-chart-grid">
          <article><header><h3>Dollar regimes</h3><span>DAILY</span></header><Chart rows={charts.dollar?.rows ?? []} series={DOLLAR_SERIES} height={230} yLabel="Fed dollar indexes · Jan 2006 = 100" source="Federal Reserve H.10 via FRED" asOf={e.fx?.broad?.asof} note="A rise means a stronger dollar against the relevant trade-weighted currency set." sharePath={worldMarketSharePath("forex")} /></article>
          <article><header><h3>Physical-market breadth</h3><span>MONTHLY COMPARISON</span></header><Chart rows={charts.materials?.rows ?? []} series={MATERIAL_SERIES} height={230} yLabel="each series indexed to 100 in-window" source="EIA + IMF via FRED" note="Daily energy is sampled to month-end to compare with monthly IMF benchmarks. This chart is not the daily Passage." /></article>
          <article><header><h3>Funding landing zones</h3><span>DAILY</span></header><Chart rows={charts.funding?.rows ?? []} series={FUNDING_SERIES} height={230} yLabel="spread · basis points" source="NY Fed + Federal Reserve via FRED" note="CP uses 3m AA commercial paper minus 3m Treasury on actual CP print dates." sharePath={worldMarketSharePath("money-markets")} /></article>
          <article><header><h3>Daily upstream vs funding</h3><span>DAILY PROXY</span></header><Chart rows={pressureRows} series={PRESSURE_SERIES} height={230} yLabel="pressure percentile · 0–100" source="Seiche from H.10 / EIA / Fed" note={charts.daily_gap?.note} /></article>
          <article className="wide"><header><h3>The daily gap</h3><span>UPSTREAM − FUNDING</span></header><Chart rows={gapRows} series={GAP_SERIES} height={210} yLabel="passage gap · points" refLine={{ value: 0, color: C.muted, label: "in sync" }} source="Seiche daily proxy" note="Positive = upstream ahead; negative = funding ahead. The headline also carries slower monthly materials breadth and can differ." /></article>
        </div>
      </section>

      <AnalogLedger data={e.analogs} />
      <DollarSystem data={e.dollar_system} structure={e.settlement_structure} />
      <CashConversionLab engine={e} />

      <section className="est-scope" aria-labelledby="est-scope-title">
        <div className="est-section-head"><div><span className="est-kicker">NO SILENT GAPS</span><h2 id="est-scope-title">Coverage and competence boundary</h2></div><p>A blank live basis curve is more honest than a stale or licensed series presented as public.</p></div>
        <div className="est-table-scroll">
          <table className="est-scope-table"><thead><tr><th>aspect</th><th>FX</th><th>materials</th><th>status</th></tr></thead><tbody>{(e.coverage_matrix ?? []).map((row: Any) => <tr key={row.aspect}><td>{row.aspect}</td><td>{row.fx}</td><td>{row.materials}</td><td><span className={row.status}>{String(row.status).replace("_", " ")}</span></td></tr>)}</tbody></table>
        </div>
        <div className="est-source-caveat">
          <div><h3>Source clocks</h3><ul>{(e.sources ?? []).map((row: Any) => <li key={row.layer}><span>{row.layer}</span><strong>{row.source}</strong><time>{row.cadence}</time></li>)}</ul></div>
          <div><h3>Read before using</h3><ol>{(e.caveats ?? []).map((text: string) => <li key={text}>{text}</li>)}</ol></div>
        </div>
        <div className="est-method">{e.method}</div>
      </section>
    </div>
  );
}
