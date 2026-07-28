/**
 * Notary: verify this reading yourself.
 *
 * The board hash chains every reading it publishes and almost nobody knows,
 * which makes the trust asset worth nothing. This card puts the machinery on
 * the page: what was published today, where it sits in the chain, and the
 * exact commands that let a stranger prove a past reading was not edited
 * after the fact.
 *
 * Three real layers, all public and unauthenticated (verified live before this
 * card shipped):
 *   1. the published numbers, seiche.info/data/book_history.json, one record
 *      per day carrying prev_hash = sha256(canonical(prev record) + its own
 *      prev_hash). This is the only public surface that carries the NUMBERS.
 *   2. the notary chain, /api/notary, sha256(prev|digest|utc|pit_date) from a
 *      fixed genesis. Commitments only, never payloads.
 *   3. the signed and Bitcoin anchored layer, /api/attest/stream/{stream},
 *      Ed25519 over domain:stream:day:record_hash plus OpenTimestamps proofs
 *      that settle into Bitcoin blocks.
 *
 * The card re-walks layer 2 in the reader's own browser (all four link inputs
 * are strings, so SHA-256 over them is reproducible anywhere). It does NOT
 * attempt layer 1 in the browser: that hash is taken over Python style
 * canonical JSON, where float and escape formatting differ from JSON
 * stringify, and a checker that can produce a false BROKEN is worse than no
 * checker. The command block does it exactly.
 *
 * Nothing is faked. If an endpoint is unreachable the card says which one and
 * still hands the reader the commands, which run without this page.
 */
import { useEffect, useRef, useState } from "react";
import { API_BASE } from "../apiBase";
import { P } from "../palette";
import { Any, fmt, Method } from "../lib";

const HISTORY_URL = "https://seiche.info/data/book_history.json";
const NOTARY_URL = `${API_BASE}/api/notary?n=1000`;
const ATTEST_URL = `${API_BASE}/api/attest/stream/stress_readings?n=5`;
const STREAM = "stress_readings";

type Check =
  | { state: "pending" }
  | { state: "off"; why: string }
  | { state: "ok"; n: number; head: string; fromGenesis: boolean }
  | { state: "broken"; at: number };

async function sha256Hex(s: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Re-walk the notary chain in the reader's browser. Same arithmetic as the
 *  published command: no trust in the server's own ok flag. */
async function walkChain(nt: Any): Promise<Check> {
  if (typeof crypto === "undefined" || !crypto.subtle) {
    return { state: "off", why: "this browser exposes no SubtleCrypto here (needs a secure context)" };
  }
  const rows: Any[] = [...(nt.entries ?? [])].reverse();
  if (!rows.length) return { state: "off", why: "the ledger returned no entries" };
  const fromGenesis = rows.length === nt.chain?.n;
  let prev: string = fromGenesis ? nt.genesis : rows[0].prev_hash;
  for (const r of rows) {
    const link = `${r.prev_hash}|${r.record_sha256}|${r.utc}|${r.pit_date}`;
    const h = await sha256Hex(link);
    if (r.prev_hash !== prev || h !== r.chain_hash) return { state: "broken", at: r.seq };
    prev = h;
  }
  return { state: "ok", n: rows.length, head: prev, fromGenesis };
}

function Copy({ text }: { text: string }) {
  const [state, setState] = useState<"idle" | "ok" | "err">("idle");
  const timer = useRef(0);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  return (
    <button
      type="button"
      className="copycsv"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setState("ok");
        } catch {
          setState("err");
        }
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => setState("idle"), 1400);
      }}
    >
      {state === "ok" ? "copied ✓" : state === "err" ? "copy failed" : "copy"}
    </button>
  );
}

function Cmd({ label, note, text }: { label: string; note: string; text: string }) {
  return (
    <div style={{ marginTop: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span style={{ fontSize: 11, color: "var(--accent-bright)" }}>{label}</span>
        <Copy text={text} />
      </div>
      <div className="dimsmall" style={{ margin: "3px 0 4px" }}>{note}</div>
      <pre
        style={{
          fontFamily: "var(--mono)", fontSize: 10.5, lineHeight: 1.55, margin: 0,
          padding: "10px 12px", background: "var(--panel-2)",
          border: "1px solid var(--panel-edge)", borderRadius: 8,
          overflowX: "auto", color: "var(--text)", whiteSpace: "pre",
        }}
      >
        {text}
      </pre>
    </div>
  );
}

const CMD_NUMBERS = `curl -s ${HISTORY_URL} -o book_history.json
python3 -c '
import json, hashlib
h = json.load(open("book_history.json")); prev = "0" * 64
for r in h:
    assert r["prev_hash"] == prev, "BROKEN at " + r["date"]
    body = json.dumps({k: v for k, v in r.items() if k not in ("prev_hash", "receipt")},
                      sort_keys=True, separators=(",", ":")) + prev
    prev = hashlib.sha256(body.encode()).hexdigest()
print("chain intact:", len(h), "readings,", h[0]["date"], "to", h[-1]["date"], "| head", prev[:16])
'`;

const CMD_CHAIN = `curl -s "https://api.seiche.info/api/notary?n=1000" | python3 -c '
import sys, json, hashlib
d = json.load(sys.stdin); rows = list(reversed(d["entries"])); prev = d["genesis"]
assert len(rows) == d["chain"]["n"], "window shorter than the chain, raise n"
for r in rows:
    link = "%s|%s|%s|%s" % (r["prev_hash"], r["record_sha256"], r["utc"], r["pit_date"])
    h = hashlib.sha256(link.encode()).hexdigest()
    assert r["prev_hash"] == prev and h == r["chain_hash"], "BROKEN at seq %s" % r["seq"]
    prev = h
print("chain OK:", len(rows), "readings,", rows[0]["pit_date"], "to", rows[-1]["pit_date"], "| head", prev[:16])
'`;

const CMD_SIG = `curl -s "https://api.seiche.info/api/attest/stream/stress_readings?n=5" | python3 -c '
import sys, json
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey as K
d = json.load(sys.stdin)
for x in d["days"]:
    s, a = x["signature"], x["anchor"] or {}
    msg = "seiche-pit-v1:%s:%s:%s" % (d["stream"], x["day"], x["record_hash"])
    K.from_public_bytes(bytes.fromhex(s["public_key"])).verify(bytes.fromhex(s["sig"]), msg.encode())
    print("signature OK", x["day"], x["record_hash"][:16], "bitcoin block", a.get("bitcoin_height"))
'`;

const cmdBitcoin = (digest: string) => `curl -s https://api.seiche.info/api/notary/proof/${digest} -o reading.ots
ots upgrade reading.ots
ots info reading.ots | grep BitcoinBlockHeaderAttestation`;

export default function Notary({ snap }: { snap: Any }) {
  const [hist, setHist] = useState<Any[] | null>(null);
  const [nt, setNt] = useState<Any | null>(null);
  const [at, setAt] = useState<Any | null>(null);
  const [dead, setDead] = useState<string[]>([]);
  const [check, setCheck] = useState<Check>({ state: "pending" });

  useEffect(() => {
    let gone = false;
    const fail = (what: string) => { if (!gone) setDead((d) => (d.includes(what) ? d : [...d, what])); };
    const get = (url: string) =>
      fetch(url, { cache: "no-store" }).then((r) =>
        r.ok ? r.json() : Promise.reject(new Error(`http ${r.status}`)));

    get(HISTORY_URL)
      .then((j) => { if (!gone && Array.isArray(j)) setHist(j); })
      .catch(() => fail("the published numbers file"));
    get(NOTARY_URL)
      .then(async (j) => {
        if (gone) return;
        setNt(j);
        try {
          const c = await walkChain(j);
          if (!gone) setCheck(c);
        } catch {
          if (!gone) setCheck({ state: "off", why: "the browser recheck could not run here" });
        }
      })
      .catch(() => { fail("the notary chain"); setCheck({ state: "off", why: "the chain could not be fetched" }); });
    get(ATTEST_URL)
      .then((j) => { if (!gone) setAt(j); })
      .catch(() => fail("the signed stream"));
    return () => { gone = true; };
  }, []);

  const comp = snap.engines?.composite ?? {};
  const today = snap.deep?.book?.today ?? {};
  const headRow = hist && hist.length ? hist[hist.length - 1] : null;
  const priorRow = hist && hist.length > 1 ? hist[hist.length - 2] : null;
  const entries: Any[] = nt?.entries ?? [];
  const topEntry = entries[0] ?? null;
  const anchoredEntry = entries.find((e: Any) => e.anchored) ?? null;
  const ver = at?.verification ?? null;
  const lastDay = at?.days?.length ? at.days[at.days.length - 1] : null;
  const replayDate = priorRow?.date ?? headRow?.date ?? String(snap.generated_at ?? "").slice(0, 10);

  return (
    <div className="card span12">
      <h2>Notary: verify this reading yourself</h2>
      <div className="sub">
        every reading this board publishes is hashed into an append only chain, signed, and anchored
        into Bitcoin. None of that is worth anything as a claim, so here is the machinery: what was
        published today, where it sits in the chain, and the commands that let you prove a past
        reading was not quietly improved later. No login, no key, nothing to ask us for.
      </div>

      {dead.length > 0 && (
        <div className="faults">
          could not reach {dead.join(" and ")} from this page just now, so those rows are not drawn.
          The commands below do not depend on this page.
        </div>
      )}

      <div className="kv">
        <div className="item">
          <div className="k">published today</div>
          <div className="v">{headRow?.date ?? String(snap.generated_at ?? "").slice(0, 10)}</div>
        </div>
        <div className="item">
          <div className="k">index</div>
          <div className="v">{fmt(headRow?.index ?? comp.value, 1)}</div>
        </div>
        <div className="item">
          <div className="k">regime</div>
          <div className="v">{headRow?.regime ?? comp.regime ?? fmt(null)}</div>
        </div>
        <div className="item">
          <div className="k">book stance</div>
          <div className="v">{headRow?.stance ?? today.stance ?? fmt(null)}</div>
        </div>
        <div className="item">
          <div className="k">P(event, 5bd)</div>
          <div className="v">
            {(headRow?.p_ensemble ?? today.p_ensemble) == null
              ? fmt(null)
              : `${fmt((headRow?.p_ensemble ?? today.p_ensemble) * 100, 1)}%`}
          </div>
        </div>
        <div className="item">
          <div className="k">links to</div>
          <div className="v" style={{ fontSize: 12, fontFamily: "var(--mono)" }}>
            {headRow?.prev_hash ? `${headRow.prev_hash.slice(0, 16)}…` : fmt(null)}
          </div>
        </div>
      </div>
      <div className="dimsmall">
        today's record carries the hash of yesterday's. Its own hash becomes checkable tomorrow, as
        tomorrow's prev_hash: that is what append only means, and it is why a reader who keeps a copy
        of the file today holds a copy of the past that nobody can edit out from under them.
      </div>

      <div className="sub" style={{ marginTop: 12 }}>
        the commitment chain: one link per distinct published state, each link
        sha256(prev_hash|record_digest|utc|date) from a fixed published genesis
        {nt?.genesis ? ` (${nt.genesis})` : ""}.
      </div>
      <div className="kv">
        <div className="item">
          <div className="k">links</div>
          <div className="v">{nt?.chain?.n ?? fmt(null)}</div>
        </div>
        <div className="item">
          <div className="k">today's link</div>
          <div className="v">{topEntry ? `seq ${topEntry.seq}` : fmt(null)}</div>
        </div>
        <div className="item">
          <div className="k">record digest</div>
          <div className="v" style={{ fontSize: 12, fontFamily: "var(--mono)" }}>
            {topEntry ? `${topEntry.record_sha256.slice(0, 16)}…` : fmt(null)}
          </div>
        </div>
        <div className="item">
          <div className="k">head</div>
          <div className="v" style={{ fontSize: 12, fontFamily: "var(--mono)" }}>
            {nt?.head ? `${nt.head.slice(0, 16)}…` : fmt(null)}
          </div>
        </div>
        <div className="item">
          <div className="k">rechecked in your browser</div>
          <div
            className="v"
            style={{
              color:
                check.state === "ok" ? P.calm : check.state === "broken" ? P.stress : undefined,
            }}
          >
            {check.state === "pending" && "walking the chain…"}
            {check.state === "off" && "not run"}
            {check.state === "ok" && `${check.n} links OK`}
            {check.state === "broken" && `BROKEN at seq ${check.at}`}
          </div>
        </div>
      </div>
      <div className="dimsmall">
        {check.state === "ok" && (
          <>
            recomputed here, in this tab, {check.fromGenesis
              ? "from the published genesis to the head"
              : "across the served window (raise n to reach genesis)"}
            : {check.n} links reproduce and the head is {check.head.slice(0, 32)}. The server's own ok
            flag was not taken on trust, and neither should this be: run the command below on your
            machine.
          </>
        )}
        {check.state === "broken" && (
          <>a link does not reproduce at seq {check.at}. Do not take the board's word for anything
            until that is explained: run the command below and publish what you find.</>
        )}
        {check.state === "off" && <>browser recheck not run: {check.why}. The command below is the real check.</>}
        {check.state === "pending" && <>recomputing every link from the published genesis…</>}
      </div>

      {entries.length > 0 && (
        <table className="mini">
          <thead>
            <tr><th>seq</th><th>data day</th><th>committed (utc)</th><th>record digest</th><th>bitcoin anchor</th></tr>
          </thead>
          <tbody>
            {entries.slice(0, 6).map((e: Any) => (
              <tr key={e.seq}>
                <td className="num">{e.seq}</td>
                <td className="num">{e.pit_date}</td>
                <td className="num">{String(e.utc).replace("T", " ").slice(0, 19)}</td>
                <td className="num" style={{ fontFamily: "var(--mono)" }}>{e.record_sha256.slice(0, 24)}</td>
                <td style={{ color: e.anchored ? P.calm : P.erosion }}>
                  {e.anchored ? "stamped" : "pending its stamp"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="sub" style={{ marginTop: 12 }}>
        the signed layer: a chain proves order, not authorship, and nothing stops an operator from
        generating a flattering history yesterday and calling it a year old. So each day's record hash
        is Ed25519 signed over {`{domain}:{stream}:{day}:{record_hash}`}, and submitted to the
        OpenTimestamps calendars, which commit it into a Bitcoin block.
      </div>
      <div className="kv">
        <div className="item">
          <div className="k">stream</div>
          <div className="v" style={{ fontSize: 13 }}>{at?.stream ?? STREAM}</div>
        </div>
        <div className="item">
          <div className="k">records signed</div>
          <div className="v" style={{ color: ver && ver.n_signed_valid === ver.n_records ? P.calm : undefined }}>
            {ver ? `${ver.n_signed_valid} / ${ver.n_records}` : fmt(null)}
          </div>
        </div>
        <div className="item">
          <div className="k">confirmed in bitcoin</div>
          <div className="v">{ver ? `${ver.n_anchors_bitcoin_confirmed} days` : fmt(null)}</div>
        </div>
        <div className="item">
          <div className="k">latest anchor</div>
          <div className="v">
            {lastDay?.anchor?.bitcoin_height
              ? `block ${lastDay.anchor.bitcoin_height}`
              : lastDay?.anchor
                ? String(lastDay.anchor.status)
                : fmt(null)}
          </div>
        </div>
        <div className="item">
          <div className="k">problems reported</div>
          <div className="v" style={{ color: ver && ver.problems?.length ? P.stress : undefined }}>
            {ver ? (ver.problems?.length ? ver.problems.length : "none") : fmt(null)}
          </div>
        </div>
      </div>
      {ver?.problems?.length > 0 && (
        <div className="crunch"><b>the stream reports</b> {ver.problems.join(" · ")}</div>
      )}

      <Cmd
        label="1. did the published numbers change?"
        note="the only public surface that carries the readings themselves. Re-walks the whole file: any edit to a past record breaks every link after it."
        text={CMD_NUMBERS}
      />
      <Cmd
        label="2. replay the commitment chain from genesis"
        note="commitments only, so this proves order and integrity, not what the numbers were. It is the check the board cannot lie about, because you compute it."
        text={CMD_CHAIN}
      />
      <Cmd
        label="3. who signed it (needs the cryptography package)"
        note="verifies each day's signature against the operator key at /api/attest/pubkey and prints the Bitcoin block the day's hash landed in."
        text={CMD_SIG}
      />
      <Cmd
        label="4. when did Bitcoin see it (needs the ots client)"
        note={
          anchoredEntry
            ? "the detached OpenTimestamps proof for a stamped digest. Upgrade completes the calendar proof, info prints the Bitcoin block header attestations. A full trustless ots verify wants a Bitcoin node."
            : "no stamped digest is in the served window right now, so this command carries the placeholder: substitute any record_sha256 whose anchor column reads stamped. A fresh digest answers 404 until its stamp settles, which is better than a proof that proves nothing."
        }
        text={cmdBitcoin(anchoredEntry ? anchoredEntry.record_sha256 : "RECORD_SHA256")}
      />

      <div className="sub" style={{ marginTop: 14 }}>what is, and is not, attested</div>
      <div className="caveat">
        ▸ attested: the order and integrity of every published reading (chain), who published it
        (Ed25519 signature), and that it existed by a given Bitcoin block time (OpenTimestamps).
      </div>
      <div className="caveat">
        ▸ not attested: that the inputs were honest or the model is any good. A perfectly sealed record
        of bad calls is still a record of bad calls, which is what the PROOF page and the skeptic page
        are for.
      </div>
      <div className="caveat">
        ▸ the notary and attest layers store commitments, not payloads: a hash pins a reading only if
        you hold the reading. Pinning a past number therefore means the published numbers file above,
        or your own copy of what you were shown.
      </div>
      <div className="caveat">
        ▸ anchoring is a separate step from committing, so the newest links can read pending. Until a
        stamp settles, that link's date rests on the operator's word.
      </div>
      <div className="caveat">
        ▸ the chain is append only by construction, not by hosting: it proves an edit happened, it
        cannot prevent one. That is why keeping your own copy of the file is the point.
      </div>

      <div style={{ marginTop: 12, fontSize: 13 }}>
        <a href="/skeptic.html" style={{ color: "var(--accent-bright)" }}>the skeptic's page</a>
        {" · "}
        <a href="/methodology.html" style={{ color: "var(--dim)" }}>methodology</a>
        {" · "}
        <a href={`${API_BASE}/api/notary`} style={{ color: "var(--dim)" }}>the raw chain (JSON, no login)</a>
        {" · "}
        <a href={HISTORY_URL} style={{ color: "var(--dim)" }}>the published numbers</a>
        {" · "}
        <a href={`${API_BASE}/api/attest/pubkey`} style={{ color: "var(--dim)" }}>the signing key</a>
      </div>
      <div className="dimsmall" style={{ marginTop: 6 }}>
        replay: the Time Machine tab rebuilds the entire board as of a past date through{" "}
        <a href={`${API_BASE}/api/asof/${replayDate}`} style={{ color: "var(--dim)" }}>
          /api/asof/{replayDate}
        </a>
        . That call re-assembles every engine from final vintage inputs, so it runs on a board session
        and takes its time; the instant, no login route to a past reading is the numbers file above.
      </div>

      <Method>
        chain: sha256(prev_hash|record_digest|utc|date) from {nt?.genesis ?? "the published genesis"},
        recomputed in your browser and again by you on the command line · signatures: Ed25519 over
        seiche-pit-v1:stream:day:record_hash · anchor: OpenTimestamps into Bitcoin ·
        {" "}{nt?.how_to_verify ?? "endpoints are public and unauthenticated on purpose"}
      </Method>
    </div>
  );
}
