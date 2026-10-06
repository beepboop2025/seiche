/* A separate, user-requested reading. Retained examples are never overwritten. */
(function (root) {
  "use strict";
  const finite = v => typeof v === "number" && Number.isFinite(v);
  function day(v) {
    if (typeof v !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(v)) throw Error("Source date is missing or invalid.");
    const date = new Date(v + "T00:00:00Z");
    if (!Number.isFinite(+date) || date.toISOString().slice(0, 10) !== v || v > new Date().toISOString().slice(0, 10)) throw Error("Source date is invalid or in the future.");
    return v;
  }
  function money(d) {
    if (!d || d.schema !== "seiche.money-market-desk.v1" || d.ok !== true || !Array.isArray(d.sections)) throw Error("A valid USD funding desk is unavailable.");
    const all = d.sections.flatMap(s => Array.isArray(s.metrics) ? s.metrics : []);
    const ids = ["policy.sofr", "policy.iorb", "policy.sofr_minus_iorb", "liquidity.reserves", "liquidity.srf"];
    const ms = ids.map(id => { const found = all.filter(m => m.id === id); if (found.length !== 1) throw Error("Required source evidence is missing or ambiguous."); return found[0]; });
    ms.forEach((m, i) => { if (m.status !== "available" || !finite(m.value) || m.unit !== ["%", "%", "bp", "$B", "$B"][i]) throw Error("Required evidence is unavailable or has changed units."); day(m.asof); });
    if (new Set(ms.slice(0, 3).map(m => m.asof)).size !== 1 || Math.abs(100 * (ms[0].value - ms[1].value) - ms[2].value) > 1e-7) throw Error("Rate observations do not form a valid same-date spread.");
    return "Latest published observations; source dates may precede today.\n" + ms.map(m => m.id.replace("policy.", "").replace("liquidity.", "").replaceAll("_", " ") + ": " + m.value + " " + m.unit + " · " + m.asof + " · publisher freshness: " + (m.freshness || "unknown")).join("\n") + "\nA matched rate spread is not a bank-failure signal. Weekly reserves have their own date.";
  }
  function banks(d) {
    const c = d && d.current_disclosures;
    if (!c || !Array.isArray(c.rows) || c.rows.length > 1000) throw Error("Current accepted filing coverage is unavailable.");
    const rows = c.rows.filter(r => r.sector === "sfb");
    if (!rows.length) throw Error("No small finance bank filing evidence was returned.");
    return "Latest accepted filing records; this is not a same-day credit-risk assessment.\n" + rows.map(r => {
      const m = r.metrics && r.metrics.gnpa_pct;
      if (r.status !== "observed" || !m || m.status !== "observed" || !finite(m.value) || m.value < 0 || m.value > 100 || m.unit !== "percent") return String(r.name || "Unnamed bank") + ": unavailable or not currently observed";
      return String(r.name) + ": GNPA " + m.value.toFixed(2) + "% · period " + day(r.period_end);
    }).join("\n") + "\nCoverage is a selected set of accepted filings. Different period ends cannot be ranked as one quarter.";
  }
  function exit(d, size) {
    if (![100000, 1000000].includes(size) || !root.UTExitResearch) throw Error("The reviewed exit-size reader is unavailable.");
    const snap = root.UTExitResearch.capture(d, size, size, {ok:true,carried:false}, new Date().toISOString());
    if (!snap.source_asof || !snap.source_generated_at) throw Error("Source clocks are unavailable.");
    day(snap.source_asof.slice(0, 10)); day(snap.source_generated_at.slice(0, 10));
    return "Latest published BTC sell estimates for $" + size.toLocaleString("en-US") + ". Generated " + snap.source_generated_at + ".\n" + snap.venues.map(v => {
      if (!v.observed_at || v.availability !== "published_estimate") return v.venue + ": " + (v.availability === "published_estimate" ? "source clock unavailable" : v.availability);
      day(v.observed_at.slice(0, 10));
      return v.venue + ": " + v.sell_cost_bp.toFixed(3) + " bp · $" + v.estimated_slippage_usd.toFixed(2) + " · " + v.observed_at + " · " + v.quote_currency;
    }).join("\n") + "\nThese are source-dated estimates, not executable quotes. This reader does not establish freshness, all-in fees or venue safety.";
  }
  async function fetchBounded(url) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(url, {signal:controller.signal,credentials:"omit",headers:{Accept:"application/json"}});
      if (!response.ok || !/application\/json/i.test(response.headers.get("content-type") || "")) throw Error("The source did not return a public JSON reading.");
      const reader = response.body.getReader(); const chunks = []; let bytes = 0;
      for (;;) { const {done,value} = await reader.read(); if (done) break; bytes += value.byteLength; if (bytes > 3 * 1024 * 1024) { await reader.cancel(); throw Error("The response exceeds this reader’s size limit."); } chunks.push(value); }
      const buffer = new Uint8Array(bytes); let offset = 0; chunks.forEach(c => {buffer.set(c,offset); offset += c.length;});
      return JSON.parse(new TextDecoder().decode(buffer));
    } finally { clearTimeout(timeout); }
  }
  root.QuestionEvidence = Object.freeze({money,banks,exit,day});
  if (!root.document) return;
  const button = document.getElementById("load-current"), output = document.getElementById("current-result");
  if (!button || !output) return;
  button.hidden = false;
  button.addEventListener("click", async () => {
    button.disabled = true; output.textContent = "Loading published evidence…";
    try {
      const product = document.body.dataset.product;
      if (product === "seiche") output.textContent = money(await fetchBounded("https://api.seiche.info/api/money-markets"));
      else if (product === "undertow") output.textContent = exit(await fetchBounded("/api/crypto_desk.json"), Number(document.body.dataset.size));
      else output.textContent = banks(await fetchBounded("https://api.liquilens.in/api/failure-radar/board"));
    } catch (_) { output.textContent = "Newer evidence could not be verified. The retained example above remains dated and unchanged. Open the research board for coverage and source status."; }
    finally { button.disabled = false; }
  });
})(typeof window === "undefined" ? globalThis : window);
