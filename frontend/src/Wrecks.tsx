import { useEffect, useState } from "react";
import { API_BASE } from "./apiBase";

type Any = Record<string, any>;

// Wrecks — the crypto stress episodes read against the funding board.
// Self-fetching (public endpoint), shown on the free page and the PROOF tab.
// The table publishes what it shows either way: external wrecks test
// co-movement, crypto-native wrecks test specificity, and the readings
// refuse credit the board didn't earn.

const REGIME_VAR: Record<string, string> = {
  CALM: "var(--calm)", EROSION: "var(--erosion)",
  STRAIN: "var(--strain)", STRESS: "var(--stress)",
};

function Ladder({ board }: { board: Any[] }) {
  return (
    <>
      {board.map((r) => (
        <td className="num" key={r.offset_bd}
            title={r.regime ? `${r.date}: ${Math.round(r.value)} ${r.regime}` : "no coverage"}
            style={{ color: r.regime ? REGIME_VAR[r.regime] : "var(--faint)" }}>
          {r.value != null ? Math.round(r.value) : "—"}
        </td>
      ))}
    </>
  );
}

export default function Wrecks({ variant }: { variant: "card" | "free" }) {
  const [w, setW] = useState<Any | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    fetch(`${API_BASE}/api/wrecks`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setW)
      .catch(() => setErr(true));
  }, []);

  if (err) {
    // free page: say nothing rather than break the reading flow.
    // PROOF tab: unavailability is information — print it.
    return variant === "card" ? (
      <div className="card span12">
        <h2>Wrecks — the crypto record</h2>
        <div className="faults">unavailable — not computed on this deployment</div>
      </div>
    ) : null;
  }
  if (!w) return null;

  const offsets: number[] = w.offsets_bd ?? [];
  const table = (
    <>
      <table className="mini">
        <thead>
          <tr>
            <th>wreck</th><th>origin</th>
            {offsets.map((k) => <th key={k}>T−{k}</th>)}
            <th>peak</th>
          </tr>
        </thead>
        <tbody>
          {(w.episodes ?? []).map((ep: Any) => (
            <tr key={ep.date} title={ep.reading}>
              <td>
                {ep.episode}
                <span className="dimsmall"> · {ep.date}{ep.date_approximate ? " (approx.)" : ""}</span>
              </td>
              <td className="dimsmall">{ep.class === "external" ? "external" : "crypto-native"}</td>
              <Ladder board={ep.board ?? []} />
              <td className="num" style={{ color: ep.peak_regime ? REGIME_VAR[ep.peak_regime] : "var(--faint)" }}>
                {ep.peak_regime ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="dimsmall" style={{ marginTop: 6 }}>
        cells = the index, point-in-time, colored by regime · external wrecks with the board off CALM:{" "}
        <b>{w.summary?.external_with_board_elevated}</b> · crypto-native met by a quiet board:{" "}
        <b>{w.summary?.crypto_native_board_quiet}</b> — the board earns no credit for crypto-native
        failures, and this table says so. Hover a row for its honest reading.
      </div>
      {(w.caveats ?? []).map((c: string, i: number) => (
        <div className="caveat" key={i}>▸ {c}</div>
      ))}
    </>
  );

  if (variant === "free") {
    return (
      <div className="free-proof">
        <div className="free-kicker">THE CRYPTO RECORD · what the board read as crypto broke</div>
        <div className="dimsmall" style={{ marginBottom: 8 }}>
          Six labelled crypto stress episodes, the whole board replayed as it stood at T−21 to T−0
          business days — no lookahead. When the shock came from outside crypto (a pandemic, a bank
          run, a tariff cascade), the dollar-funding board was already off CALM. When crypto broke for
          its own reasons (Terra, FTX, a carry unwind), no lead is claimed.
        </div>
        {table}
      </div>
    );
  }
  return (
    <div className="card span12">
      <h2>Wrecks — the crypto record</h2>
      <div className="sub">
        the six crypto shipwrecks replayed point-in-time against the funding board — external wrecks
        test transmission, crypto-native wrecks test specificity; context, not leads · raw JSON at{" "}
        <span className="dimsmall">/api/wrecks</span>
      </div>
      {table}
    </div>
  );
}
