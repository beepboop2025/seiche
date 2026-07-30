import { P } from "../palette";
import Chart from "../Chart";
import { Any, fmt, Fault, Method } from "../lib";

// Verdict rules mirror backend/seiche/referee.py exactly — the tab must
// never disagree with the published page about what the numbers mean.
const excludesZero = (c: Any) => !!c && ((c.ci95?.[0] ?? -1) > 0 || (c.ci95?.[1] ?? 1) < 0);

function claim1Verdict(c1: Any): string {
  const cells = ["fwd_3m", "fwd_6m", "fwd_9m"].map((k) => c1?.forward_return_corr?.[k]).filter(Boolean);
  if (!cells.length) return "NOT AVAILABLE";
  if (cells.some((c: Any) => (c.ci95?.[0] ?? -1) > 0)) return "WRONG WINDOW";
  return "NOT REPRODUCIBLE";
}

function claim2Verdict(c2: Any): string {
  const pk = c2?.peak_lead_months;
  if (pk == null || !c2?.peak_detail) return "NOT AVAILABLE";
  if (!excludesZero(c2.peak_detail)) return "NOT REPRODUCIBLE";
  return pk >= 12 && pk <= 15 ? "REPRODUCED" : "WRONG WINDOW";
}

function claim3Verdict(c3: Any): string {
  const res = c3?.spectral_resolution_at_65m_pm_months;
  const dom = c3?.dominant_period_months;
  if (res == null || dom == null) return "NOT AVAILABLE";
  if (res >= 10) return "UNTESTABLE HERE";
  return Math.abs(dom - 65) <= res ? "REPRODUCED" : "NOT REPRODUCIBLE";
}

function tokColor(v: string): string {
  if (v === "NOT REPRODUCIBLE") return P.stress;
  if (v === "WRONG WINDOW") return P.erosion;
  if (v === "REPRODUCED") return P.calm;
  return P.faint; // UNTESTABLE HERE / NOT AVAILABLE — an open question, not a loss
}

function Tok({ v }: { v: string }) {
  const c = tokColor(v);
  return (
    <span style={{
      fontFamily: "var(--mono)", fontSize: 10.5, letterSpacing: ".14em", fontWeight: 600,
      padding: "3px 9px", borderRadius: 6, border: `1px solid ${c}`, color: c, whiteSpace: "nowrap",
    }}>{v}</span>
  );
}

const corr = (c: Any) =>
  c == null ? "—" : `${c.corr > 0 ? "+" : ""}${fmt(c.corr, 2)} [${fmt(c.ci95?.[0], 2)}, ${fmt(c.ci95?.[1], 2)}] · n ${c.n}`;

function CorrTable({ cells, horizons, title }: { cells: Any; horizons: number[]; title: string }) {
  return (
    <table className="mini" style={{ maxWidth: 380 }}>
      <thead><tr><th>{title}</th><th>corr with forward return (95% CI)</th></tr></thead>
      <tbody>
        {horizons.map((h) => (
          <tr key={h}><td>{h}m ahead</td><td className="num">{corr(cells?.[`fwd_${h}m`])}</td></tr>
        ))}
      </tbody>
    </table>
  );
}

export default function Referee({ snap }: { snap: Any }) {
  const r = snap.deep?.refereegli ?? {};
  if (!r.ok) return <div className="grid"><Fault name="REFEREE" reason={r.reason} span={12} /></div>;

  const c1 = r.claim1 ?? {}, c2 = r.claim2 ?? {}, c3 = r.claim3 ?? {};
  const v1 = claim1Verdict(c1), v2 = claim2Verdict(c2), v3 = claim3Verdict(c3);
  const oos = c1.walkforward_oos, gr = c1.granger ?? {};
  const nl = r.robustness_net_liquidity ?? {};
  const comp = r.latest?.components_usd_tn ?? {};
  const yoyRows = (r.series ?? []).filter((row: Any) => row[2] != null).map((row: Any) => [row[0], row[2]]);

  return (
    <div className="grid">
      <div className="card span12">
        <h2>The Referee — "global liquidity", tested on the layer anyone can check</h2>
        <div className="sub">
          the category's proprietary flagship publishes three quantitative claims with no error bars, no
          out-of-sample tests, no revision record — so this page rebuilds the one component everyone
          agrees on (G3 central bank assets in USD, monthly, 2003 onward) and referees the claims under
          the board's own standards. This is NOT a test of the proprietary index: if the full index beats
          its public layer, that performance lives entirely in the unpublished layers — a claim its
          publisher could settle tomorrow by releasing an error distribution.
        </div>
        <div className="kv">
          <div className="item"><div className="k">G3 today</div><div className="v">${fmt(r.latest?.g3_usd_tn, 2)}tn</div></div>
          <div className="item"><div className="k">Fed / ECB / BoJ</div>
            <div className="v">{fmt(comp.fed, 2)} / {fmt(comp.ecb, 2)} / {fmt(comp.boj, 2)}</div></div>
          <div className="item"><div className="k">yoy growth</div>
            <div className={`v ${(r.latest?.yoy_pct ?? 0) < 0 ? "warn" : ""}`}>
              {r.latest?.yoy_pct != null ? `${r.latest.yoy_pct > 0 ? "+" : ""}${fmt(r.latest.yoy_pct, 1)}%` : "—"}</div></div>
          <div className="item"><div className="k">window</div>
            <div className="v">{r.window?.[0]} → {r.window?.[1]} <span className="dimsmall">· {r.n_months}mo</span></div></div>
        </div>
        <Chart rows={yoyRows} series={[{ label: "G3 central bank assets, yoy %", color: P.accent }]} height={170} />
        <Method>
          G3 only — no keyless PBoC or BoE feed exists, and the gap is stated, not interpolated over.
          Indexable twin of this tab with full prose: <a href="/referee.html">/referee.html</a>
        </Method>
      </div>

      <div className="card span12">
        <h2>Claim 1 · "liquidity leads asset prices by 3–6 months" <Tok v={v1} /></h2>
        <div className="sub">
          yoy G3 growth against forward equity returns (Nasdaq Composite — the only broad index with
          full-window public history); circular moving-block bootstrap on every interval
        </div>
        <div className="warehouse-row">
          <CorrTable cells={c1.forward_return_corr} horizons={[1, 3, 6, 9, 12, 18]} title="gross G3, yoy" />
          {nl.ok && (
            <table className="mini" style={{ maxWidth: 380 }}>
              <thead><tr><th>net liquidity (Fed − TGA − RRP)</th><th>corr (95% CI)</th></tr></thead>
              <tbody>
                {[3, 6, 12].map((h) => (
                  <tr key={h}><td>{h}m ahead</td><td className="num">{corr(nl.forward_return_corr?.[`fwd_${h}m`])}</td></tr>
                ))}
                <tr><td>6m ahead · 2015→</td><td className="num">{corr(nl.fwd6_since_2015)}</td></tr>
              </tbody>
            </table>
          )}
        </div>
        {oos && (
          <div className="kv" style={{ marginTop: 8 }}>
            <div className="item"><div className="k">walk-forward eval</div>
              <div className="v">{oos.eval_window?.[0]} → {oos.eval_window?.[1]}</div></div>
            <div className="item"><div className="k">fwd-6m after high liq</div>
              <div className="v">{fmt(oos.mean_fwd6m_logret_high_liq * 100, 1)}%</div></div>
            <div className="item"><div className="k">after low liq</div>
              <div className="v">{fmt(oos.mean_fwd6m_logret_low_liq * 100, 1)}%</div></div>
            <div className="item"><div className="k">spread (95% CI)</div>
              <div className="v">{fmt(oos.spread_6m_logret * 100, 1)}pp
                <span className="dimsmall"> [{fmt(oos.spread_ci95?.[0] * 100, 1)}, {fmt(oos.spread_ci95?.[1] * 100, 1)}]</span></div></div>
          </div>
        )}
        {nl.ok && nl.walkforward_oos && (
          <div className="sub" style={{ marginTop: 4 }}>
            net liquidity's walk-forward lands on exactly the era its famous level chart is drawn from
            ({nl.walkforward_oos.eval_window?.[0]} →): spread <b>{fmt(nl.walkforward_oos.spread_6m_logret * 100, 1)}pp</b>
            {" "}[{fmt(nl.walkforward_oos.spread_ci95?.[0] * 100, 1)}, {fmt(nl.walkforward_oos.spread_ci95?.[1] * 100, 1)}] —
            the level chart is the exhibit; growth out of sample is the honest test
          </div>
        )}
        {gr.liq_to_returns && gr.returns_to_liq && (
          <div className="sub">
            Granger, both directions: liquidity→returns p {fmt(gr.liq_to_returns.p, 2)} ·
            returns→liquidity p {fmt(gr.returns_to_liq.p, 2)} ({gr.liq_to_returns.lags} lags) —
            no predictive content either way in this layer
          </div>
        )}
        <Method>whatever the 3–6 month lead is, it is not in the central bank layer, gross or net</Method>
      </div>

      <div className="card span6">
        <h2>Claim 2 · "leads the business cycle by 12–15 months" <Tok v={v2} /></h2>
        <div className="sub">against US industrial production growth, 0–24 month leads</div>
        <div className="kv">
          <div className="item"><div className="k">contemporaneous</div>
            <div className="v" style={{ color: (c2.contemporaneous ?? 0) < 0 ? P.stress : undefined }}>
              {c2.contemporaneous != null ? fmt(c2.contemporaneous, 2) : "—"}</div></div>
          <div className="item"><div className="k">peak lead</div>
            <div className="v">{c2.peak_lead_months != null ? `${c2.peak_lead_months}mo` : "—"}</div></div>
          <div className="item"><div className="k">corr at peak</div><div className="v">{corr(c2.peak_detail)}</div></div>
          <div className="item"><div className="k">at the claimed 13mo</div><div className="v">{corr(c2.corr_at_claimed_13m)}</div></div>
        </div>
        <Method>
          two honest readings: either the lead is real but sits well past the claimed window, or the
          negative contemporaneous sign is a policy reaction function — central banks expand when the
          economy contracts, recoveries follow recessions, and the apparent long lead is partly the economy
          forecasting the central bank. Separating those needs exactly the private-credit layer that is not public.
        </Method>
      </div>

      <div className="card span6">
        <h2>Claim 3 · "a ~65-month cycle" <Tok v={v3} /></h2>
        <div className="sub">detrended periodogram of yoy growth, with the resolution limit stated</div>
        <div className="kv">
          <div className="item"><div className="k">dominant period</div>
            <div className="v">{c3.dominant_period_months != null ? `${fmt(c3.dominant_period_months, 0)}mo` : "—"}</div></div>
          <div className="item"><div className="k">resolution at 65mo</div>
            <div className="v">±{fmt(c3.spectral_resolution_at_65m_pm_months, 0)}mo</div></div>
          <div className="item"><div className="k">cycles in sample if true</div>
            <div className="v">{fmt(c3.n_complete_cycles_in_sample_if_65m, 1)}</div></div>
          {/* dimsmall rides the .item, not the .v: `.kv .item .v` outranks it, so on the
              value it would be inert at DESK and orphan its own label at GLANCE. */}
          <div className="item dimsmall"><div className="k">peak-to-peak spacings</div>
            <div className="v"><span className="dimsmall">{(c3.peak_to_peak_spacings_months ?? []).join(", ")}mo</span></div></div>
        </div>
        <Method>
          untestable on post-2003 data — said with respect: the 1974-onward panel is the one genuinely
          irreplaceable thing the category's founder owns, and the only instrument that could settle
          their own headline claim. We would rather they published the test than the assertion.
        </Method>
      </div>

      <div className="card span12">
        <h2>What would change this page</h2>
        <div className="sub">
          a published lead-lag distribution on the full index, with revision errors and out-of-sample
          splits — the standard this board holds itself to: every reading sealed before outcomes, every
          miss on the public record. Subsample stability of the fwd-6m correlation:
          full {corr(r.subsample_stability_fwd6m?.full)} · 2003–14 {corr(r.subsample_stability_fwd6m?.first_half)} ·
          2015→ {corr(r.subsample_stability_fwd6m?.second_half)}
        </div>
        <Method>{r.method}</Method>
      </div>
    </div>
  );
}
