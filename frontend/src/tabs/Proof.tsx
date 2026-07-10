import Chart from "../Chart";
import { Any, fmt, Fault, Method } from "../lib";

function OrthogonalCard({ o }: { o: Any }) {
  if (!o?.ok) {
    return (
      <div className="card span12">
        <h2>Orthogonal Signal Test</h2>
        <div className="faults">unavailable — {o?.reason ?? "not computed"}</div>
      </div>
    );
  }
  const c = o.event_capture ?? {};
  return (
    <div className="card span12">
      <h2>Orthogonal Signal Test</h2>
      <div className="sub">
        the skeptic's question answered: the index contains spread/tail terms and the event IS a spread
        spike — so here is the same test with the target's own variable family REMOVED from the signal
        (components: {Object.keys(o.weights ?? {}).join(", ")})
      </div>
      <div className="kv">
        <div className="item"><div className="k">recall (95% CI)</div>
          <div className="v">{fmt((c.recall ?? 0) * 100, 0)}%
            <span className="dimsmall"> [{fmt((c.recall_ci95?.[0] ?? 0) * 100, 0)}–{fmt((c.recall_ci95?.[1] ?? 0) * 100, 0)}]</span>
          </div></div>
        <div className="item"><div className="k">run-precision (95% CI)</div>
          <div className="v">{fmt((c.precision_runs ?? 0) * 100, 0)}%
            <span className="dimsmall"> [{fmt((c.precision_runs_ci95?.[0] ?? 0) * 100, 0)}–{fmt((c.precision_runs_ci95?.[1] ?? 0) * 100, 0)}] · {c.runs_hit}/{c.n_alert_runs} runs</span>
          </div></div>
        <div className="item"><div className="k">base rate</div><div className="v">{fmt((c.base_rate ?? 0) * 100, 0)}%</div></div>
        <div className="item"><div className="k">median run-up</div><div className="v">{fmt(c.median_lead_d, 0)}d</div></div>
      </div>
      <table className="mini">
        <thead><tr><th>episode</th><th>max pctl (T−30…T−1)</th><th>first alert</th></tr></thead>
        <tbody>
          {(o.episodes ?? []).filter((ep: Any) => ep.in_sample).map((ep: Any) => (
            <tr key={ep.date}>
              <td>{ep.episode}</td>
              <td className="num">{fmt(ep.max_pctl_30d_before, 0)}</td>
              <td className="num" style={{ color: ep.first_alert_lead_d ? "#37c88b" : "#e5484d" }}>
                {ep.first_alert_lead_d ? `${ep.first_alert_lead_d}d early` : "not alerted"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <Method>{o.why} — if this still leads events, the claim is causal structure, not autocorrelation</Method>
    </div>
  );
}

function MLCard({ ml }: { ml: Any }) {
  if (!ml?.ok) {
    return (
      <div className="card span12">
        <h2>ML Lab</h2>
        <div className="faults">unavailable — {ml?.reason ?? "not computed"}</div>
      </div>
    );
  }
  const v = ml.validation ?? {};
  return (
    <div className="card span12">
      <h2>ML Lab</h2>
      <div className="sub">
        learned P(funding event within 5bd) — walk-forward only, benchmarked against climatology AND the
        rule-based index · <b>{ml.verdict}</b>
      </div>
      <div className="kv">
        <div className="item"><div className="k">P(event, 5bd) now</div>
          <div className={`v ${ml.p_event_5bd >= 0.5 ? "bad" : ml.p_event_5bd >= 0.25 ? "warn" : ""}`}>
            {fmt(ml.p_event_5bd * 100, 1)}%
          </div></div>
        <div className="item"><div className="k">OOS AUROC</div><div className="v">{fmt(v.auroc, 3)}</div></div>
        <div className="item"><div className="k">rule-based AUROC</div><div className="v">{fmt(v.auroc_rule_based, 3)}</div></div>
        <div className="item"><div className="k">Brier / climatology</div><div className="v">{fmt(v.brier, 4)} / {fmt(v.brier_climatology, 4)}</div></div>
        <div className="item"><div className="k">OOS sample</div><div className="v">{v.oos_days}d · {v.oos_events} events</div></div>
        <div className="item"><div className="k">base rate</div><div className="v">{fmt((v.base_rate ?? 0) * 100, 1)}%</div></div>
        <div className="item"><div className="k">embargo</div><div className="v">{v.embargo_bd}bd</div></div>
      </div>
      {ml.utility && (
        <div className="sub" style={{ marginTop: 4 }}>
          decision utility (net caught-events/yr, false alarm −{ml.utility.cost_per_false_alarm}):
          {" "}ML@25% <b>{fmt(ml.utility.ml_at_25pct, 2)}</b> · ML@50% <b>{fmt(ml.utility.ml_at_50pct, 2)}</b> ·
          rule@80th <b style={{ color: (ml.utility.rule_at_80pctl ?? 0) < 0 ? "#e5484d" : undefined }}>{fmt(ml.utility.rule_at_80pctl, 2)}</b> —
          the rule index is a regime gauge; the model is the better action filter
        </div>
      )}
      {ml.orthogonal?.auroc != null && (
        <div className="sub">
          orthogonal feature set (no spread/tail family): AUROC <b>{fmt(ml.orthogonal.auroc, 3)}</b> ·
          utility@25% <b>{fmt(ml.orthogonal.utility?.ml_at_25pct, 2)}</b> — ranking survives without the target's own variables
        </div>
      )}
      <Chart rows={ml.p_series} series={[{ label: "P(event, 5bd) — walk-forward OOS", color: "#e88a3a" }]} height={150} />
      <div className="warehouse-row" style={{ marginTop: 8 }}>
        <table className="mini" style={{ maxWidth: 380 }}>
          <thead><tr><th colSpan={4}>reliability (trust a level only if these match)</th></tr>
            <tr><th>bin</th><th>predicted</th><th>realized</th><th>n</th></tr></thead>
          <tbody>
            {(ml.reliability ?? []).map((r: Any) => (
              <tr key={r.bin}><td>{r.bin}</td><td className="num">{fmt(r.mean_pred, 3)}</td>
                <td className="num">{fmt(r.realized, 3)}</td><td className="num">{r.n}</td></tr>
            ))}
          </tbody>
        </table>
        <table className="mini" style={{ maxWidth: 320 }}>
          <thead><tr><th colSpan={2}>top features (permutation)</th></tr></thead>
          <tbody>
            {(ml.top_features ?? []).slice(0, 8).map((f: Any) => (
              <tr key={f.feature}><td>{f.feature}</td><td className="num">{fmt(f.importance, 4)}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      {(ml.caveats ?? []).map((c: string, i: number) => <div className="caveat" key={i}>▸ {c}</div>)}
      <Method>{ml.method}</Method>
    </div>
  );
}

function LeakAuditCard({ a }: { a: Any }) {
  if (!a?.ok) return <Fault name="Leak Audit" reason={a?.reason} span={12} />;
  return (
    <div className="card span12">
      <h2>Leak Audit — the one-switch protocol, run against ourselves</h2>
      <div className="sub">
        the same lite index rebuilt with exactly ONE discipline deliberately broken, scored on the
        identical events — the gains above the clean row are look-ahead this pipeline refuses to
        claim (protocol: arXiv:2605.23959; determinism bar: arXiv:2603.20319)
      </div>
      <div className="kv" style={{ marginBottom: 8 }}>
        <div className="item"><div className="k">bit-reproducible</div>
          <div className={`v ${a.bit_reproducible ? "" : "bad"}`}>
            {a.bit_reproducible ? "yes — two builds hash identically" : "NO — investigate"}</div></div>
        <div className="item"><div className="k">clean index sha256</div>
          <div className="v" style={{ fontFamily: "SF Mono, monospace" }}>{a.clean_index_sha256}…</div></div>
      </div>
      <table className="mini">
        <thead><tr><th>toggle</th><th>what breaks</th><th>AUROC</th><th>ΔAUROC</th><th>recall</th><th>run-precision</th></tr></thead>
        <tbody>
          {(a.rows ?? []).map((r: Any) => (
            <tr key={r.toggle} style={r.toggle === "clean" ? { fontWeight: 600 } : undefined}>
              <td>{r.toggle}</td>
              <td className="dimsmall">{r.what_breaks}</td>
              <td className="num">{r.auroc != null ? fmt(r.auroc, 3) : "—"}</td>
              <td className="num" style={{ color: (r.lg_auroc ?? 0) > 0 ? "#e5484d" : undefined }}>
                {r.lg_auroc != null ? `${r.lg_auroc > 0 ? "+" : ""}${fmt(r.lg_auroc, 3)}` : "—"}</td>
              <td className="num">{r.recall != null ? `${fmt(r.recall * 100, 0)}%` : "—"}</td>
              <td className="num">{r.precision_runs != null ? `${fmt(r.precision_runs * 100, 0)}%` : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="dimsmall">{a.reading}</div>
      {(a.caveats ?? []).map((c: string, i: number) => <div className="caveat" key={i}>▸ {c}</div>)}
      <Method>{a.method}</Method>
    </div>
  );
}

function RegattaCard({ r }: { r: Any }) {
  if (!r?.ok) return <Fault name="Regatta" reason={r?.reason} span={12} />;
  return (
    <div className="card span12">
      <h2>Regatta — the fleet raced honestly</h2>
      <div className="sub">
        with this many boats, one HAD to look good — the Model Confidence Set (Hansen–Lunde–Nason)
        eliminates entrants statistically worse than the best over {r.n_days} common scored days;
        what survives is indistinguishable from the leader at {fmt((1 - (r.size ?? 0.1)) * 100, 0)}%
        confidence, snoop-corrected
      </div>
      <table className="mini">
        <thead><tr><th>entrant</th><th>Brier</th><th>MCS p</th><th>in the set</th></tr></thead>
        <tbody>
          {(r.rows ?? []).map((row: Any) => (
            <tr key={row.model} style={row.in_set ? { fontWeight: 600 } : undefined}>
              <td>{row.label}</td>
              <td className="num">{fmt(row.brier, 4)}</td>
              <td className="num">{fmt(row.mcs_pvalue, 3)}</td>
              <td style={{ color: row.in_set ? "#37c88b" : "#e5484d" }}>{row.in_set ? "YES" : "out"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="dimsmall">{r.verdict}</div>
      {r.duplicates_merged && (
        <div className="dimsmall">
          merged as numerically identical: {Object.entries(r.duplicates_merged).map(([a, b]) => `${a} ≡ ${b}`).join(", ")}
        </div>
      )}
      {(r.caveats ?? []).map((c: string, i: number) => <div className="caveat" key={i}>▸ {c}</div>)}
      <Method>{r.method}</Method>
    </div>
  );
}

export default function Proof({ snap }: { snap: Any }) {
  const bt = snap.deep?.backtest ?? {};
  const hist = snap.deep?.history ?? {};
  if (!bt.ok) return <div className="grid"><Fault name="PROOF" reason={bt.reason} span={12} /></div>;
  const cap = bt.event_capture ?? {};
  const s = bt.sample ?? {};

  return (
    <div className="grid">
      <div className="card span12">
        <h2>PROOF — the page that earns the right to be believed</h2>
        <div className="sub">
          Seiche-lite index rebuilt with expanding-window statistics only (no look-ahead), tested against
          {" "}{s.n_events} funding events over {s.start} → {s.end}. If the numbers were unimpressive, they'd publish anyway.
        </div>
        <div className="kv">
          <div className="item"><div className="k">recall (95% CI)</div>
            <div className="v">{fmt((cap.recall ?? 0) * 100, 0)}%
              <span className="dimsmall"> [{fmt((cap.recall_ci95?.[0] ?? 0) * 100, 0)}–{fmt((cap.recall_ci95?.[1] ?? 0) * 100, 0)}]</span>
            </div></div>
          <div className="item"><div className="k">run-precision (95% CI)</div>
            <div className="v">{fmt((cap.precision_runs ?? 0) * 100, 0)}%
              <span className="dimsmall"> [{fmt((cap.precision_runs_ci95?.[0] ?? 0) * 100, 0)}–{fmt((cap.precision_runs_ci95?.[1] ?? 0) * 100, 0)}] · {cap.runs_hit}/{cap.n_alert_runs} runs</span>
            </div></div>
          <div className="item"><div className="k">base rate</div><div className="v">{fmt((cap.base_rate ?? 0) * 100, 0)}%</div></div>
          <div className="item"><div className="k">day precision</div>
            <div className="v">{fmt((cap.precision ?? 0) * 100, 0)}% <span className="dimsmall">(serially correlated)</span></div></div>
          <div className="item"><div className="k">median alert run-up</div><div className="v">{fmt(cap.median_lead_d, 0)}d</div></div>
          <div className="item"><div className="k">alert line</div><div className="v">≥{fmt(cap.alert_pctl, 0)}th pctl</div></div>
          <div className="item"><div className="k">event def</div><div className="v">+{fmt(cap.spike_def_bp, 0)}bp · n={cap.n_events}</div></div>
        </div>
        <Chart
          rows={bt.signal_series}
          series={[{ label: "Seiche-lite expanding pctl", color: "#4cc3ff" }]}
          refLine={{ value: cap.alert_pctl ?? 80, color: "#e5484d", label: "alert line" }}
          vlines={{ dates: s.event_dates ?? [], color: "rgba(229,72,77,.5)" }}
          height={200}
        />
        <Method>red verticals = funding events · {bt.method}</Method>
      </div>

      <div className="card span7">
        <h2>Episode ledger</h2>
        <div className="sub">the six labeled breaks — including the ones it did NOT catch</div>
        <table className="mini">
          <thead><tr><th>episode</th><th>date</th><th>max pctl (T−30…T−1)</th><th>first alert</th></tr></thead>
          <tbody>
            {(bt.episodes ?? []).map((ep: Any) => (
              <tr key={ep.date}>
                <td>{ep.episode}</td>
                <td className="num">{ep.date}</td>
                <td className="num">{ep.in_sample ? fmt(ep.max_pctl_30d_before, 0) : "out of sample"}</td>
                <td className="num" style={{ color: ep.in_sample && !ep.first_alert_lead_d ? "#e5484d" : "#37c88b" }}>
                  {!ep.in_sample ? "—" : ep.first_alert_lead_d ? `${ep.first_alert_lead_d}d early` : "not alerted"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <Method>
          Mar-2020 and Apr-2025 were exogenous shocks, not plumbing events — an honest funding gauge
          should NOT claim them. Catching the mechanical squeezes (Sep/Dec-2025, SVB) with weeks of
          run-up is the actual claim.
        </Method>
      </div>

      <div className="card span5">
        <h2>Reconstruction contract</h2>
        <div className="sub">what the backtest index is — and is not</div>
        <div className="kv" style={{ marginBottom: 8 }}>
          <div className="item"><div className="k">live index now</div><div className="v">{fmt(snap.engines?.composite?.value, 1)}</div></div>
          <div className="item"><div className="k">lite index now</div><div className="v">{fmt(hist.current?.value, 1)}</div></div>
          <div className="item"><div className="k">lite pctl</div><div className="v">{fmt(hist.current?.pctl, 0)}th</div></div>
        </div>
        <table className="mini">
          <thead><tr><th>lite component</th><th>weight</th></tr></thead>
          <tbody>
            {Object.entries<number>(hist.weights ?? {}).map(([k, w]) => (
              <tr key={k}><td>{k}</td><td className="num">{fmt(w, 3)}</td></tr>
            ))}
          </tbody>
        </table>
        <div className="sub" style={{ marginTop: 6 }}>excluded (live-only): {(hist.excluded ?? []).join(", ")}</div>
        {(bt.caveats ?? []).map((c: string, i: number) => (
          <div className="caveat" key={i}>▸ {c}</div>
        ))}
      </div>

      <OrthogonalCard o={bt.orthogonal} />

      <LeakAuditCard a={snap.deep?.leakaudit} />

      <RegattaCard r={snap.deep?.regatta} />

      <MLCard ml={snap.deep?.ml} />

      {(bt.outcome_tables ?? []).length > 0 && (
        <div className="card span12">
          <h2>Market outcomes by signal bucket</h2>
          <div className="sub">forward moves conditioned on the index percentile — The Tell's evidence base</div>
          <div className="outgrid">
            {bt.outcome_tables.map((t: Any, i: number) => (
              <table className="mini" key={i}>
                <thead>
                  <tr><th colSpan={4}>{t.outcome} · {t.horizon_bd}d fwd</th></tr>
                  <tr><th>pctl bucket</th><th>median</th><th>%+</th><th>n(indep)</th></tr>
                </thead>
                <tbody>
                  {t.buckets.map((b: Any) => (
                    <tr key={b.bucket}>
                      <td>{b.bucket}</td>
                      <td className="num" style={{ color: (b.median ?? 0) > 0 ? "#37c88b" : (b.median ?? 0) < 0 ? "#e5484d" : undefined }}>
                        {b.median == null ? "—" : `${b.median > 0 ? "+" : ""}${fmt(b.median, 2)}`}
                      </td>
                      <td className="num">{fmt(b.pct_positive, 0)}%</td>
                      <td className="num">{b.n_days}({b.n_independent})</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
