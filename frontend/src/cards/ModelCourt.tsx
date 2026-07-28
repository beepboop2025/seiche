/**
 * ModelCourt: the adjudication layer over the board's disagreeing forward odds.
 *
 * Seiche publishes several answers to the same question (P of a funding event
 * inside the horizon) and they routinely differ by an order of magnitude. The
 * board printed them side by side and never ruled. This card renders the
 * engine's ruling: who testified, what each one's own published evidence
 * actually supports, what weight that evidence earns, what the pool comes to,
 * and how far apart the members are (dispersion IS the model-risk statistic,
 * not a decoration). The live Brier ledger is the only like for like
 * scoreboard, so it is rendered with the engine's own accrual string until it
 * has the rows to rank.
 *
 * Nothing here is computed in the browser except two ratios over published
 * numbers (max over min, and the horizon label). Every verdict, weight, note
 * and caveat is the engine's own text.
 */
import { P } from "../palette";
import { Any, fmt, Method } from "../lib";

const pct = (v: number | null | undefined, d = 1) =>
  v == null ? fmt(null) : `${fmt(v * 100, d)}%`;

/** The pooling rule in words, from the engine's own rule token. */
function ruleWords(e: Any): string {
  if (e?.rule === "skill_weighted") {
    return `weighted mean, weight = max(Brier skill over climatology, 0) times an AUROC gate, ` +
      `so a model that cannot rank risk days earns nothing: ${e.n_weighted} of ${e.n_pooled} carry weight`;
  }
  if (e?.rule === "median") {
    return `plain median of the pool: no member beat climatology, so no member earned extra weight`;
  }
  return e?.rule ? String(e.rule) : "rule not published";
}

function MemberRows({ members }: { members: Any[] }) {
  return (
    <table className="mini">
      <thead>
        <tr>
          <th>model</th>
          <th>published p</th>
          <th>pool weight</th>
          <th>Brier</th>
          <th>climatology</th>
          <th>Brier skill</th>
          <th>AUROC</th>
          <th>scored</th>
          <th>why it weighs what it does</th>
        </tr>
      </thead>
      <tbody>
        {members.map((m: Any) => {
          const s = m.skill;
          const weighted = (m.weight ?? 0) > 0;
          return (
            <tr key={m.model} style={weighted ? { fontWeight: 600 } : undefined}>
              <td>{m.model}</td>
              <td className="num">{pct(m.p, 1)}</td>
              <td className="num" style={{ color: m.in_pool ? (weighted ? P.calm : P.ghost) : P.strain }}>
                {m.in_pool ? (m.weight == null ? fmt(null) : fmt(m.weight, 3)) : "not pooled"}
              </td>
              <td className="num">{fmt(s?.brier, 4)}</td>
              <td className="num">{fmt(s?.brier_climatology, 4)}</td>
              <td className="num" style={{ color: (s?.brier_skill ?? 0) > 0 ? P.calm : P.stress }}>
                {s ? fmt(s.brier_skill, 3) : fmt(null)}
              </td>
              <td className="num" style={{ color: s?.auroc != null && s.auroc < 0.5 ? P.stress : undefined }}>
                {fmt(s?.auroc, 3)}
              </td>
              <td className="num">
                {s?.n_scored == null ? fmt(null) : `${s.n_scored}d / ${s.n_events ?? "?"} ev`}
              </td>
              <td className="dimsmall">{m.note}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function CourtBlock({ court, status, horizon }: { court: Any; status: string; horizon: number }) {
  const scores: Any[] = court?.scores ?? [];
  return (
    <>
      <div className="sub" style={{ marginTop: 10 }}>
        the live Brier ledger: one row per model per day, realized flipped only after {horizon} business
        days have fully elapsed. This is the only like for like scoreboard, because the members' own
        backtests are not comparable, and it is deliberately slow.
      </div>
      <div className="kv">
        <div className="item">
          <div className="k">court</div>
          <div className="v" style={{ color: court?.in_session ? P.calm : undefined }}>
            {court?.in_session ? "in session" : "accruing"}
          </div>
        </div>
        <div className="item">
          <div className="k">rows needed per model</div>
          <div className="v">{court?.min_resolved ?? fmt(null)}</div>
        </div>
        <div className="item">
          <div className="k">models with rows</div>
          <div className="v">{scores.length}</div>
        </div>
      </div>
      {scores.length > 0 && (
        <table className="mini">
          <thead>
            <tr>
              <th>model</th><th>resolved</th><th>pending</th>
              <th>live Brier</th><th>climatology</th><th>skill</th><th>rank</th>
            </tr>
          </thead>
          <tbody>
            {scores.map((e: Any) => (
              <tr key={e.model}>
                <td>{e.model}</td>
                <td className="num">{e.n_resolved}</td>
                <td className="num">{e.n_pending}</td>
                <td className="num">{fmt(e.brier, 4)}</td>
                <td className="num">{fmt(e.brier_climatology, 4)}</td>
                <td className="num" style={{ color: (e.brier_skill ?? 0) > 0 ? P.calm : undefined }}>
                  {e.brier_skill == null ? fmt(null) : fmt(e.brier_skill, 3)}
                </td>
                <td className="num">{e.rank ?? "unranked"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="dimsmall" style={{ marginTop: 6 }}>{status}</div>
      {court?.verdict && <div className="crunch"><b>live verdict</b> {court.verdict}</div>}
    </>
  );
}

export default function ModelCourt({ snap }: { snap: Any }) {
  const c = snap.deep?.modelcourt;

  if (!c?.ok) {
    const absent: Any[] = c?.absent ?? [];
    return (
      <div className="card span12">
        <h2>Model Court</h2>
        <div className="sub">
          the board's several forward odds for one question, adjudicated in one place
        </div>
        <div className="faults">unavailable: {c?.reason ?? "not computed"}</div>
        {absent.length > 0 && (
          <table className="mini">
            <thead><tr><th>member</th><th>why it could not testify</th></tr></thead>
            <tbody>
              {absent.map((a: Any) => (
                <tr key={a.model}><td>{a.model}</td><td className="dimsmall">{a.reason}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    );
  }

  const d = c.dispersion ?? {};
  const e = c.ensemble ?? {};
  const members: Any[] = c.members ?? [];
  const absent: Any[] = c.absent ?? [];
  const ratio = d.min > 0 && d.max != null ? d.max / d.min : null;
  const nContext = members.length - (e.n_pooled ?? members.length);
  const contextClause =
    nContext > 0
      ? `, and ${nContext} more ${nContext === 1 ? "testifies" : "testify"} for context on a different question.`
      : ".";

  return (
    <div className="card span12">
      <h2>Model Court: the forward odds, adjudicated</h2>
      <div className="crunch"><b>ruling</b> {c.adjudication}</div>
      <div className="sub">
        {e.n_pooled} comparable models answer the same {c.horizon_bd}bd question and disagree by a
        factor of {ratio == null ? "more than one" : fmt(ratio, 1)}{contextClause} The Court pools
        only what it can defend pooling, weights each member by the out-of-sample evidence its own
        payload already carries, and keeps a live ledger so the ranking is eventually earned rather
        than argued.
      </div>

      <div className="kv">
        <div className="item">
          <div className="k">pooled P(event, {c.horizon_bd}bd)</div>
          <div className={`v ${e.p >= 0.5 ? "bad" : e.p >= 0.25 ? "warn" : ""}`}>{pct(e.p, 1)}</div>
        </div>
        <div className="item">
          <div className="k">pooling rule</div>
          <div className="v">{e.rule === "skill_weighted" ? "skill weighted" : e.rule}</div>
        </div>
        <div className="item">
          <div className="k">pooled / weighted</div>
          <div className="v">{e.n_pooled} / {e.n_weighted}</div>
        </div>
        <div className="item">
          <div className="k">asof</div>
          <div className="v" style={{ fontSize: 13 }}>{c.asof ?? fmt(null)}</div>
        </div>
      </div>
      <div className="dimsmall">{ruleWords(e)}</div>

      <MemberRows members={members} />

      <div className="sub" style={{ marginTop: 10 }}>
        each member's own out-of-sample verdict, in its own words, because the weights above are
        nothing more than these blocks read literally:
      </div>
      {members.map((m: Any) => {
        const od = m.odds_detail;
        return (
          <div className="caveat" key={m.model}>
            ▸ <b>{m.model}</b>
            {m.skill?.verdict
              ? <> ({m.skill.source}): {m.skill.verdict}</>
              : <>: no probabilistic validation published, so it carries no weight</>}
            {od && od.n != null && (
              <>
                {" · "}odds basis: {od.hits} of {od.n}, 95% CI {pct(od.ci95?.[0], 1)} to{" "}
                {pct(od.ci95?.[1], 1)}, base rate {pct(od.base_rate, 1)}, lift {fmt(od.lift, 2)}x
              </>
            )}
          </div>
        );
      })}

      {absent.length > 0 && (
        <div className="dimsmall" style={{ marginTop: 8 }}>
          not seated: {absent.map((a: Any) => `${a.model} (${a.reason})`).join(" · ")}
        </div>
      )}

      <div className="sub" style={{ marginTop: 12 }}>
        dispersion is the model-risk statistic: how far apart the seated models are on the same
        question, right now. A tight pool means the number is structural; a wide one means the pooled
        figure is a choice of weights as much as a reading.
      </div>
      <div className="kv">
        <div className="item"><div className="k">min</div><div className="v">{pct(d.min, 1)}</div></div>
        <div className="item"><div className="k">max</div><div className="v">{pct(d.max, 1)}</div></div>
        <div className="item">
          <div className="k">spread (model risk)</div>
          <div className={`v ${(d.spread ?? 0) >= 0.2 ? "bad" : (d.spread ?? 0) >= 0.08 ? "warn" : ""}`}>
            {pct(d.spread, 1)}
          </div>
        </div>
        <div className="item"><div className="k">stdev</div><div className="v">{pct(d.stdev, 2)}</div></div>
        <div className="item"><div className="k">n in pool</div><div className="v">{d.n}</div></div>
      </div>

      <CourtBlock court={c.court} status={c.ledger_status} horizon={c.horizon_bd} />

      {(c.caveats ?? []).map((cv: string, i: number) => (
        <div className="caveat" key={i}>▸ {cv}</div>
      ))}
      <Method>{c.method}</Method>
    </div>
  );
}
