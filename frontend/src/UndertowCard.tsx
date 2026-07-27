/**
 * UndertowCard — the fleet's markets layer, shown LIVE inside Seiche.
 *
 * Seiche answers "is the plumbing under stress"; Undertow answers "will the
 * MARKET still be there when you exit". The two are one lab, and the best
 * advertisement either can run is the other one's real board — so this card
 * fetches Undertow's published pack (free mirror, CORS-open) and renders the
 * actual cross-market tiers plus the sealed record's actual arithmetic,
 * misses included. Nothing invented: an unreachable pack says so and the
 * static pitch stands alone; an accruing segment prints ACCRUING, never calm.
 */
import { useEffect, useState } from "react";
import { P } from "./palette";
import { Any } from "./lib";

const PACK = "https://api.seiche.info/undertow/board.json";
const SITE = "https://liquilens-undertow.com";
const BOT = "https://t.me/undertow_LiquiLens_bot";
const RECORD = "https://api.seiche.info/undertow/sealed_calls.json";

const SEGS = ["UST", "IG", "HY", "EQUITY", "ETF", "FX", "CN", "CRYPTO", "BSTOCK"];

function tierColor(tier: string): string | undefined {
  switch (tier) {
    case "DEFICIENT": return P.stress;
    case "STRAINED": return P.strain;
    case "NORMAL": return P.calm;
    case "SURPLUS": return P.accentBright;
    default: return undefined;              // PARTIAL/UNAVAILABLE stay dim
  }
}

export default function UndertowCard() {
  const [board, setBoard] = useState<Any | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let dead = false;
    fetch(PACK, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`http ${r.status}`))))
      .then((j) => { if (!dead) setBoard(j); })
      .catch(() => { if (!dead) setFailed(true); });
    return () => { dead = true; };
  }, []);

  const segs = board?.segments ?? {};
  const rec = board?.informational?.sealed_calls?.record?.authors?.[0];
  const regime = board?.funding?.regime;

  return (
    <div className="card span12">
      <h2>Undertow — the markets layer</h2>
      <div className="sub">
        same lab, the other half of the question: Seiche watches the plumbing;
        Undertow watches whether the market will still be there when you exit —
        cross-market liquidity tiers, exit cost at your size, and a sealed,
        hash-chained record that keeps its own misses.
      </div>

      {board && (
        <>
          <div className="kv">
            {SEGS.filter((s) => segs[s]).map((s) => {
              const c = segs[s];
              const scored = ["SURPLUS", "NORMAL", "STRAINED", "DEFICIENT"].includes(c.tier);
              return (
                <div className="item" key={s}>
                  <div className="k">{s}</div>
                  <div className="v" style={{ color: tierColor(c.tier) }}>
                    {scored ? c.tier : "ACCRUING"}
                    {scored && c.score != null && (
                      <span className="dimsmall"> {Number(c.score).toFixed(2)}</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          <div className="coverage" style={{ marginTop: 8 }}>
            published board · asof {board.asof ?? "—"}
            {regime ? <> · funding overlay {regime}</> : null}
            {rec && rec.sealed > 0 ? (
              <>
                {" "}· sealed record {rec.hit}✓ {rec.miss}✗
                {rec.hit_rate != null ? ` · hit rate ${Math.round(rec.hit_rate * 100)}%` : ""}
                {rec.miss > 0 ? " — the misses stay published" : ""}
              </>
            ) : null}
          </div>
        </>
      )}
      {failed && (
        <div className="coverage" style={{ marginTop: 8 }}>
          Undertow's published board could not be reached from here just now —
          nothing is drawn in its place. The links below still work.
        </div>
      )}
      {!board && !failed && (
        <div className="coverage" style={{ marginTop: 8 }}>reading the published board…</div>
      )}

      <div style={{ marginTop: 10, fontSize: 13 }}>
        <a href={SITE} style={{ color: "var(--accent-bright)" }}>open the terminal</a>
        {" · "}
        <a href={BOT} style={{ color: "var(--dim)" }}>@undertow_LiquiLens_bot</a>
        {" · "}
        <a href={RECORD} style={{ color: "var(--dim)" }}>the raw record (JSON, no login)</a>
      </div>
    </div>
  );
}
