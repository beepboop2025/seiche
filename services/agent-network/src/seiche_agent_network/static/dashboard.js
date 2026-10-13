"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const base = location.pathname.replace(/\/dashboard\/?$/, "");
  const names = {seiche: "Seiche", liquilens: "LiquiLens", undertow: "Undertow", palimpsest: "Palimpsest", noisefloor: "NoiseFloor", narcoscope: "NarcoScope", wake: "Wake", riptide: "Riptide", "myquant-editorial": "MyQuant editorial", "myquant-app": "MyQuant app", "trade-safety": "Trade Safety", "financial-evidence": "Financial Evidence", "seiche-agent-network": "Agent Network", other: "Other"};
  const trafficNames = {operator_reported_production: "Reported production", paper: "Paper workflows", shadow: "Shadow workflows", backtest: "Backtests", synthetic: "Synthetic checks", internal: "Internal traffic", unknown: "Unknown classification"};
  const states = new Set(["fresh", "stale", "unknown", "restricted"]);
  const fmt = new Intl.NumberFormat("en", {maximumFractionDigits: 1});
  let regions;
  try { regions = new Intl.DisplayNames(["en"], {type: "region"}); } catch (_) { regions = null; }
  const session = {token: "", usage: null, freshness: null, demand: null, history: null, ecosystem: null, generation: 0, controller: null, loading: false, timer: null};
  const safe = (value, fallback = "Unavailable") => typeof value === "string" && value.length ? value.slice(0, 300) : fallback;
  const number = (value) => typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
  const count = (value) => number(value) === null ? "—" : fmt.format(value);
  const array = (value) => Array.isArray(value) ? value.slice(0, 2000) : [];
  const productName = (value) => names[value] || safe(value);
  const titleCase = (value) => value.charAt(0).toUpperCase() + value.slice(1);
  const node = (tag, text, className) => {
    const value = document.createElement(tag);
    if (text !== undefined && text !== null) value.textContent = String(text);
    if (className) value.className = className;
    return value;
  };

  function interval(value) {
    if (!value || number(value.lower_bound_ms) === null) return "Unavailable";
    const lower = value.lower_bound_ms, upper = number(value.upper_bound_ms);
    if (upper === null) return "> " + fmt.format(lower / 1000) + " s";
    if (upper < lower) return "Unavailable";
    const divisor = lower >= 1000 ? 1000 : 1;
    return fmt.format(lower / divisor) + "–" + fmt.format(upper / divisor) + (divisor === 1000 ? " s" : " ms");
  }

  function timestamp(value) {
    if (typeof value !== "string") return "Unavailable";
    const date = new Date(value);
    return Number.isNaN(date.valueOf()) ? "Unavailable" : date.toISOString().replace("T", " ").replace(/\.\d{3}Z$/, " UTC");
  }

  function age(value) {
    if (number(value) === null) return "Age unknown";
    if (value < 60) return fmt.format(value) + " seconds";
    if (value < 3600) return fmt.format(value / 60) + " minutes";
    if (value < 86400) return fmt.format(value / 3600) + " hours";
    return fmt.format(value / 86400) + " days";
  }

  function status(text, error = false) {
    byId("connection-status").textContent = text;
    byId("connection-status").className = "status-message" + (error ? " error" : "");
  }

  function empty(parent, text) {
    parent.replaceChildren(node("p", text, "empty-message"));
  }

  function badge(value, label) {
    const state = states.has(value) || value === "unavailable" ? value : "unknown";
    return node("span", label || titleCase(state), "badge " + state);
  }

  function metric(label, value, note, compact = false) {
    const card = node("article", null, "metric");
    card.append(node("p", label, "metric-label"), node("p", value, "metric-value" + (compact ? " compact" : "")), node("p", note, "metric-note"));
    return card;
  }

  function renderOverview(report) {
    const totals = report.totals || report.counts || {}, activity = report.activity || {};
    byId("metrics").replaceChildren(
      metric("Observed exchanges", count(totals.requests), count(totals.anonymous_requests) + " anonymous exchanges"),
      metric("Successful tool results", count(totals.tool_successes), count(totals.tool_outcome_unknown) + " unknown / non-tool outcomes"),
      metric("Tool errors", count(totals.tool_errors), count(totals.http_errors) + " HTTP errors, counted separately"),
      metric("p95 response interval", interval(report.latency?.p95), "Histogram bucket · not an exact percentile", true),
      metric("Observed organisation IDs", count(activity.observed_org_ids), "Identities with traffic · not verified customers"),
      metric("Observed agent IDs", count(activity.observed_agent_ids), "Delegated identities · not people or installs")
    );
    byId("report-window").textContent = "Global report · " + count(report.retention_days) + " UTC days · from " + safe(report.cutoff_day);
    byId("report-scope").textContent = "Central ingress exchanges only";
    const complete = report.collection_complete === true ? "Collection is marked complete for its stated scope. " : "Collection completeness is unverified. ";
    const diagnostics = report.measurement_diagnostics;
    const dropped = number(diagnostics?.dropped_capacity) !== null && number(diagnostics?.dropped_closing) !== null ? diagnostics.dropped_capacity + diagnostics.dropped_closing : null;
    const collection = diagnostics?.scope === "current_process_only" ? " Collection in this process: " + count(diagnostics.committed) + " committed, " + count(diagnostics.pending) + " pending, " + count(diagnostics.late_committed) + " committed after the response deadline, " + count(dropped) + " dropped, " + count(diagnostics.permanent_errors) + " write errors. These diagnostics reset on restart; late writes are not necessarily lost." : "";
    byId("coverage-notice").textContent = complete + safe(report.coverage, "Direct product endpoints, requests rejected before forwarding and downtime are outside these counts.") + collection;

    const days = array(report.days).filter((row) => typeof row?.day === "string" && /^\d{4}-\d{2}-\d{2}$/.test(row.day)).sort((a, b) => a.day.localeCompare(b.day)).slice(-30);
    const maximum = Math.max(1, ...days.map((row) => number(row.requests) || 0));
    if (!days.length) empty(byId("daily-activity"), "No daily observations are available in this reporting window.");
    else byId("daily-activity").replaceChildren(...days.map((row) => {
      const cell = node("article", null, "daily-cell"), date = node("time", row.day), bar = node("progress");
      date.dateTime = row.day;
      bar.max = maximum; bar.value = number(row.requests) || 0;
      bar.setAttribute("aria-label", row.day + ": " + count(row.requests) + " observed exchanges");
      cell.append(date, node("strong", count(row.requests)), bar);
      return cell;
    }));
    renderTraffic(report);
  }

  function ranked(parent, rows, total, labeler) {
    if (!rows.length) { empty(parent, "No observations for this view."); return; }
    const max = Math.max(1, ...rows.map((row) => number(row.requests) || 0));
    parent.replaceChildren(...rows.map((row) => {
      const line = node("div", null, "ranked-row"), label = node("span"), value = node("span", count(row.requests), "value"), bar = node("progress");
      const [primary, secondary] = labeler(row);
      label.append(node("span", primary));
      if (secondary) label.append(node("span", secondary, "secondary"));
      value.append(node("span", total > 0 ? fmt.format((number(row.requests) || 0) / total * 100) + "%" : "—", "percentage"));
      bar.max = max; bar.value = number(row.requests) || 0;
      bar.setAttribute("aria-label", primary + ": " + count(row.requests) + " exchanges");
      line.append(label, value, bar);
      return line;
    }));
  }

  function renderTraffic(report) {
    const values = report.traffic_classes || {};
    const rows = Object.keys(trafficNames).map((key) => ({country: key, requests: number(values[key]) || 0})).sort((a, b) => b.requests - a.requests);
    ranked(byId("traffic"), rows, rows.reduce((total, row) => total + row.requests, 0), (row) => [trafficNames[row.country], ""]);
    const returning = report.activity?.weekly_returning_orgs || {};
    const denominator = number(returning.eligible_denominator), numerator = number(returning.numerator);
    const box = byId("returning-activity");
    box.replaceChildren(node("strong", "Weekly returning organisation IDs"));
    if (denominator && numerator !== null) box.append(node("p", count(numerator) + " of " + count(denominator) + " prior active organisation IDs appeared in both adjacent seven-day windows."));
    else box.append(node("p", "Not enough prior-window observations to calculate a returning share."));
    box.append(node("p", "This is observed repeat traffic, not installation-cohort retention. " + (returning.continuous_collection_verified === true ? "Continuous collection is marked verified." : "Continuous collection has not been verified.")));
  }

  function renderFilters() {
    const select = byId("product-filter"), selected = select.value;
    const available = new Set([...array(session.usage?.products), ...array(session.freshness?.products), ...array(session.demand?.rows)].map((row) => row?.product).filter((value) => typeof value === "string" && value.length < 80));
    select.replaceChildren(node("option", "All products")); select.firstChild.value = "all";
    [...available].sort().forEach((value) => { const option = node("option", productName(value)); option.value = value; select.append(option); });
    select.value = available.has(selected) ? selected : "all";
  }

  function productRows() {
    const selected = byId("product-filter").value;
    return array(session.usage?.products).filter((row) => selected === "all" || row.product === selected);
  }

  function renderCountries() {
    const selected = byId("product-filter").value;
    const reportRows = selected === "all" ? array(session.usage?.countries) : Object.entries(productRows()[0]?.countries || {}).map(([country, requests]) => ({country, requests}));
    const values = new Map();
    reportRows.forEach((row) => {
      const country = typeof row?.country === "string" && /^[A-Z]{2}$/.test(row.country) ? row.country : "unknown";
      values.set(country, (values.get(country) || 0) + (number(row.requests) || 0));
    });
    if (!values.has("unknown")) values.set("unknown", 0);
    const rows = [...values].map(([country, requests]) => ({country, requests})).sort((a, b) => b.requests - a.requests);
    const total = rows.reduce((sum, row) => sum + row.requests, 0);
    byId("country-count").textContent = count(rows.filter((row) => row.country !== "unknown" && row.requests > 0).length) + " observed country codes";
    ranked(byId("countries"), rows, total, (row) => {
      if (row.country === "unknown") return ["Unknown network origin", ""];
      let label = row.country;
      try { label = regions?.of(row.country) || row.country; } catch (_) { /* Keep the bounded country code. */ }
      return [label, row.country];
    });
  }

  function tableCell(text, className) { return node("td", text, className); }
  function emptyTable(parent, message) {
    const row = node("tr"), cell = tableCell(message, "empty-message"); cell.colSpan = 5; row.append(cell); parent.replaceChildren(row);
  }

  function renderProducts() {
    const rows = productRows();
    if (!rows.length) { emptyTable(byId("product-rows"), "No product exchanges in this view."); return; }
    byId("product-rows").replaceChildren(...rows.map((value) => {
      const row = node("tr");
      row.append(tableCell(productName(value.product)), tableCell(count(value.requests), "table-number"), tableCell(count(value.http_errors), value.http_errors ? "error-count" : "table-number"), tableCell(count(value.tool_errors), value.tool_errors ? "error-count" : "table-number"), tableCell(interval(value.latency?.p95), "table-number"));
      return row;
    }));
  }

  function renderTools() {
    const selected = byId("product-filter").value, query = byId("tool-search").value.toLowerCase();
    const rows = array(session.usage?.tools).filter((row) => (selected === "all" || row.product === selected) && safe(row.tool, "").toLowerCase().includes(query)).sort((a, b) => (number(b.requests) || 0) - (number(a.requests) || 0));
    if (!rows.length) emptyTable(byId("tool-rows"), "No matching tool observations.");
    else byId("tool-rows").replaceChildren(...rows.slice(0, 500).map((value) => {
      const row = node("tr"), tool = tableCell(safe(value.tool)); tool.append(node("span", productName(value.product), "tool-product"));
      row.append(tool, tableCell(count(value.requests), "table-number"), tableCell(count(value.tool_errors), value.tool_errors ? "error-count" : "table-number"), tableCell(count(value.http_errors), value.http_errors ? "error-count" : "table-number"), tableCell(interval(value.latency?.p95), "table-number"));
      return row;
    }));
    byId("tool-count").textContent = "Showing " + count(Math.min(rows.length, 500)) + " of " + count(rows.length) + " matching tool / product groups.";
  }

  function renderDemand() {
    const report = session.demand;
    if (!report || report._unavailable) {
      emptyTable(byId("demand-rows"), "Data demand could not be loaded. Refresh to retry.");
      byId("demand-summary").textContent = "No demand total is available for this snapshot.";
      byId("demand-count").textContent = "Unavailable does not mean zero requests.";
      return;
    }
    const totals = report.totals || {}, selected = byId("product-filter").value;
    const rows = array(report.rows).filter((row) => selected === "all" || row.product === selected);
    byId("demand-summary").textContent = count(totals.data_requests) + " data requests · " + count(totals.discovery_requests) + " discovery requests · " + count(totals.unknown_requests) + " unclassified requests. Across all products in the selected traffic view. " + count(totals.authenticated_agent_requests) + " requests carry an agent credential; " + count(totals.owner_requests) + " carry a workspace-owner credential.";
    if (!rows.length) emptyTable(byId("demand-rows"), "No data-demand observations match this view.");
    else byId("demand-rows").replaceChildren(...rows.map((value) => {
      const row = node("tr"), label = tableCell(safe(value.label, safe(value.dataset_id)));
      label.append(node("span", productName(value.product) + " · " + safe(value.basis) + " · " + safe(value.operation).replaceAll("_", " "), "tool-product"));
      const errors = tableCell(count(value.http_errors) + " HTTP / " + count(value.tool_errors) + " tool", "table-number");
      row.append(label, tableCell(count(value.requests), "table-number"), tableCell(count(value.authenticated_agent_requests), "table-number"), tableCell(count(value.anonymous_requests), "table-number"), errors);
      return row;
    }));
    byId("demand-count").textContent = "Showing " + count(rows.length) + " groups from the server's top " + count(report.limit) + ". " + (report.truncated ? "More groups exist; the totals include them. " : "") + "Observed since " + safe(report.cutoff_day) + " UTC. Family classification does not establish which individual records were used.";
  }

  function sourceCard(value) {
    const card = node("article", null, "source-card"), top = node("div", null, "source-top");
    top.append(node("h3", productName(value.product)), badge(value.freshness));
    card.append(top, node("p", "MCP transport: " + safe(value.transport, "Unknown"), "source-transport"));
    if (value.coverage && number(value.coverage.inspected_rows) !== null) {
      const counts = node("div", null, "source-counts");
      for (const state of ["fresh", "stale", "unknown", "restricted"]) {
        const item = node("span"); item.append(node("strong", count(value.coverage[state])), node("span", titleCase(state))); counts.append(item);
      }
      card.append(counts, node("p", count(value.coverage.inspected_rows) + " source rows inspected. This is the assessed scope, not the whole product."));
    } else card.append(node("p", value.freshness === "restricted" ? "The response preserves a rights or context-only restriction." : "No qualified source-row coverage is available from this probe."));
    const critical = value.critical_subset;
    if (critical && typeof critical === "object") {
      const box = node("div", null, "critical-subset");
      box.append(node("strong", "Critical funding inputs"), badge(critical.freshness));
      const clocks = array(critical.clocks);
      clocks.forEach((clock) => box.append(node("p", safe(clock.source_id) + " · " + safe(clock.freshness, "unknown") + " · " + timestamp(clock.observed_at))));
      if (array(critical.missing_source_ids).length) box.append(node("p", "Missing critical source clocks: " + array(critical.missing_source_ids).map((id) => safe(id)).join(", ")));
      box.append(node("p", "Input clock check only; it does not authorise an order."));
      card.append(box);
    }
    const reasons = array(value.reasons).filter((reason) => typeof reason === "string");
    const sourceReasons = value.source_reason_counts && typeof value.source_reason_counts === "object" ? Object.entries(value.source_reason_counts) : [];
    if (reasons.length || sourceReasons.length) {
      const detail = node("details"); detail.append(node("summary", "Assessment details"));
      reasons.forEach((reason) => detail.append(node("p", safe(reason).replaceAll("_", " "))));
      sourceReasons.slice(0, 20).forEach(([reason, amount]) => detail.append(node("p", count(amount) + " · " + safe(reason).replaceAll("_", " "))));
      if (number(value.source_age_seconds) !== null) detail.append(node("p", "Oldest inspected observation age: " + age(value.source_age_seconds) + ". Historical or retired series can contribute; this is not transport latency."));
      card.append(detail);
    }
    return card;
  }

  function renderFreshness() {
    const report = session.freshness, selected = byId("product-filter").value;
    const statusNode = byId("monitor-status"), summary = byId("freshness-summary"), cards = byId("freshness-products");
    if (!report || report._unavailable) {
      statusNode.textContent = "Unavailable"; statusNode.className = "badge unavailable";
      summary.textContent = "The freshness report could not be loaded. Product traffic can still be inspected; no source-readiness claim is available.";
      cards.replaceChildren(); return;
    }
    const unavailable = {not_configured: "The scheduled source monitor is not configured for this service.", missing_report: "The configured source monitor report is missing.", invalid_report: "The source monitor report could not be validated."};
    if (unavailable[report.status]) {
      statusNode.textContent = report.status === "not_configured" ? "Not configured" : "Unavailable"; statusNode.className = "badge unknown";
      summary.textContent = unavailable[report.status] + " Unknown is not fresh."; cards.replaceChildren(); return;
    }
    const current = report.monitor_current === true;
    statusNode.textContent = current ? "Current at last fetch" : "Monitor not current";
    statusNode.className = "badge " + (current ? "fresh" : "stale");
    summary.textContent = "Probe evaluated " + timestamp(report.evaluated_at) + ". " + (current ? "The monitor timestamp was current at the last fetch; each product’s source assessment remains separate." : "The monitor timestamp is old or unverified. Treat the displayed assessments as a previous snapshot.");
    const rows = array(report.products).filter((row) => selected === "all" || row.product === selected);
    if (rows.length) cards.replaceChildren(...rows.map(sourceCard));
    else empty(cards, "No source monitor assessment exists for this product.");
  }

  function renderEcosystem() {
    const report = session.ecosystem;
    const source = byId("ecosystem-source").value;
    const search = byId("ecosystem-search").value.trim().toLowerCase();
    const cards = byId("ecosystem-sources"), body = byId("ecosystem-rows");
    cards.replaceChildren(); body.replaceChildren();
    if (!report || report._unavailable || !["ok", "stale_report"].includes(report.status)) {
      byId("ecosystem-summary").textContent = "The scheduled ecosystem report is unavailable. No external activity counts are inferred.";
      byId("ecosystem-count").textContent = ""; return;
    }
    array(report.sources).forEach((item) => {
      const card = node("article", null, "source-card"), heading = node("div", null, "source-top");
      heading.append(node("h3", safe(item.label)), badge(item.fresh === true ? "fresh" : item.status === "not_admitted" ? "restricted" : "unknown"));
      card.append(heading, node("p", safe(item.measurement)), node("p", "Last collected: " + timestamp(item.knowledge_time)), node("p", safe(item.limits), "small-note"));
      cards.append(card);
    });
    const meta = array(report.sources).find((item) => item.source === source);
    byId("ecosystem-summary").textContent = (report.report_current ? "" : "Previous report; collection status is stale. ") + safe(meta?.measurement) + ". " + safe(meta?.rights) + ".";
    const captured = Array.isArray(report.observations) ? report.observations.slice(0, 5000) : [];
    const rows = captured.filter((row) => row.source === source && (!search || [row.item, row.model, row.dimension, ...(Array.isArray(row.tags) ? row.tags : [])].join(" ").toLowerCase().includes(search)));
    rows.slice(0, 100).forEach((row) => {
      const tr = node("tr"), name = tableCell(safe(row.item || row.model || row.product));
      if (row.dimension) name.append(node("span", safe(row.dimension).replaceAll("_", " "), "secondary"));
      let measure = "Unavailable", window = safe(row.window, "Observed listing; no usage count");
      if (row.kind === "dataset_popularity") measure = count(row.downloads_30d) + " downloads · " + count(row.likes) + " likes";
      if (row.kind === "ai_crawler_distribution") { measure = safe(row.value) + "% · confidence " + count(row.confidence_level) + "/5"; window = timestamp(row.period_start) + " — " + timestamp(row.period_end); }
      if (row.kind === "usage_aggregate") { measure = safe(row.tokens_display) + " tokens"; window = timestamp(row.period_start) + " — " + timestamp(row.period_end); }
      if (row.kind === "mcp_server_listing") measure = safe(row.listing_status) + " · " + safe(row.version);
      if (row.kind === "tool_dispatch_window") measure = count(row.calls) + " calls · " + count(row.errors) + " errors";
      tr.append(name, tableCell(measure), tableCell(window)); body.append(tr);
    });
    if (!rows.length) { const tr = node("tr"), td = tableCell(meta?.status === "not_admitted" ? (source === "cloudflare_radar" ? "Cloudflare requires a reviewed commercial grant and a Radar Read token." : "This source is not enabled in the scheduled collector.") : "No observations match this source and filter.", "empty-message"); td.colSpan = 3; tr.append(td); body.append(tr); }
    byId("ecosystem-count").textContent = "Showing " + count(Math.min(rows.length, 100)) + " of " + count(rows.length) + " captured observations for this filter. Source samples may overlap. Full captured rows are available from the authenticated agent-activity API.";
  }

  function renderDemandHistory() {
    const report = session.history, body = byId("demand-history-rows"), selected = byId("product-filter").value;
    if (!report || report._unavailable) { emptyTable(body, "Daily data-demand history is unavailable."); return; }
    const rows = array(report.rows).filter((row) => selected === "all" || row.product === selected).sort((a, b) => safe(b.day).localeCompare(safe(a.day)));
    byId("demand-history-summary").textContent = "Closed UTC days from " + safe(report.cutoff_day) + ". Synthetic, internal and discovery requests excluded. " + (report.truncated ? "The report is truncated. " : "") + "Showing " + count(Math.min(rows.length, 100)) + " of " + count(rows.length) + " dataset-day observations.";
    if (!rows.length) { emptyTable(body, "No completed-day dataset requests have been observed in this window."); return; }
    body.replaceChildren(...rows.slice(0, 100).map((row) => {
      const tr = node("tr"), label = tableCell(safe(row.dataset_id));
      label.append(node("span", productName(row.product), "secondary"));
      tr.append(tableCell(safe(row.day)), label, tableCell(count(row.requests)), tableCell(count(row.registered_agents)), tableCell(count(row.returning_registered_agents)));
      return tr;
    }));
  }

  function renderSelected() { renderCountries(); renderProducts(); renderTools(); renderDemand(); renderDemandHistory(); renderFreshness(); renderEcosystem(); }

  function clearSession(message = "Disconnected. The credential and loaded reports have been cleared.", error = false) {
    session.generation += 1; session.controller?.abort(); session.controller = null;
    session.token = ""; session.usage = null; session.freshness = null; session.demand = null; session.history = null; session.ecosystem = null; session.loading = false;
    clearInterval(session.timer); session.timer = null;
    byId("auto-refresh").checked = false; byId("report-token").value = "";
    byId("connect-form").hidden = false; byId("connection-actions").hidden = true;
    byId("report-view").hidden = true; byId("disconnected-view").hidden = false;
    byId("connection-tag").textContent = "Disconnected"; byId("connection-tag").className = "connection-tag";
    byId("connect-button").disabled = false; byId("refresh-button").disabled = false; byId("demand-traffic").disabled = false;
    for (const id of ["metrics", "daily-activity", "countries", "traffic", "returning-activity", "product-rows", "tool-rows", "demand-rows", "freshness-products"]) byId(id).replaceChildren();
    for (const id of ["coverage-notice", "report-window", "report-scope", "freshness-summary", "updated-label", "tool-count", "demand-summary", "demand-count", "country-count"]) byId(id).textContent = "";
    byId("product-filter").replaceChildren(node("option", "All products")); byId("product-filter").firstChild.value = "all";
    byId("tool-search").value = ""; byId("demand-traffic").value = "non_test";
    for (const id of ["ecosystem-sources", "ecosystem-rows", "ecosystem-summary", "ecosystem-count"]) byId(id).replaceChildren();
    byId("demand-history-rows").replaceChildren(); byId("demand-history-summary").textContent = "";
    byId("ecosystem-search").value = "";
    status(message, error);
  }

  async function getReport(path, signal) {
    const response = await fetch(base + path, {method: "GET", headers: {Authorization: "Bearer " + session.token}, credentials: "omit", cache: "no-store", redirect: "error", signal});
    if (!response.ok) { const failure = new Error("Report unavailable"); failure.status = response.status; throw failure; }
    const text = await response.text();
    if (text.length > 4194304) throw new Error("Report exceeds dashboard limit");
    return JSON.parse(text);
  }

  async function refresh() {
    if (!session.token || session.loading) return;
    const generation = session.generation;
    session.loading = true; const controller = new AbortController(); session.controller = controller;
    const signal = controller.signal;
    const deadline = setTimeout(() => controller.abort(), 20000);
    byId("connect-button").disabled = true; byId("refresh-button").disabled = true; byId("demand-traffic").disabled = true;
    status("Loading private operational reports…");
    try {
      const [usage, freshness, demand, ecosystem, history] = await Promise.allSettled([getReport("/v1/operator/usage", signal), getReport("/v1/operator/freshness", signal), getReport("/v1/operator/dataset-usage?traffic=" + encodeURIComponent(byId("demand-traffic").value) + "&limit=200", signal), getReport("/v1/operator/agent-activity", signal), getReport("/v1/operator/demand-history", signal)]);
      if (generation !== session.generation) return;
      if (usage.status !== "fulfilled") {
        const code = usage.reason?.status;
        if (code === 401 || code === 403) { clearSession("The report credential was not accepted. Use the platform operator report credential, not an organisation or agent token.", true); return; }
        const explanation = code === 404 ? "Operator reporting is not configured at this service path." : "The usage report is unavailable or timed out. Refresh to retry.";
        if (session.usage) { status(explanation + " The previous snapshot remains displayed.", true); byId("updated-label").textContent = "Refresh failed · previous snapshot"; }
        else clearSession(explanation, true);
        return;
      }
      if (!usage.value || usage.value.scope !== "operator_global") { clearSession("The response was not a global operator report. No dashboard data was displayed.", true); return; }
      session.usage = usage.value;
      session.freshness = freshness.status === "fulfilled" ? freshness.value : {_unavailable: true};
      session.demand = demand.status === "fulfilled" && demand.value?.scope === "operator_global" ? demand.value : {_unavailable: true};
      session.ecosystem = ecosystem.status === "fulfilled" && ecosystem.value?.scope === "operator_global" ? ecosystem.value : {_unavailable: true};
      session.history = history.status === "fulfilled" && history.value?.scope === "operator_global" ? history.value : {_unavailable: true};
      byId("connect-form").hidden = true; byId("connection-actions").hidden = false;
      byId("report-token").value = "";
      byId("report-view").hidden = false; byId("disconnected-view").hidden = true;
      byId("connection-tag").textContent = "Connected"; byId("connection-tag").className = "connection-tag connected";
      byId("updated-label").textContent = "Fetched " + timestamp(new Date().toISOString());
      renderOverview(session.usage); renderFilters(); renderSelected();
      const incomplete = freshness.status !== "fulfilled" || session.demand._unavailable || session.history._unavailable || session.ecosystem._unavailable;
      status(incomplete ? "Usage loaded. One or more additional reports are unavailable; see their sections below." : "Reports loaded. Coverage and source limitations are shown below.", incomplete);
    } catch (_) {
      if (generation === session.generation) status("The report could not be displayed. Refresh to retry; no readiness conclusion was made.", true);
    } finally {
      clearTimeout(deadline);
      if (generation === session.generation) { session.loading = false; byId("connect-button").disabled = false; byId("refresh-button").disabled = false; byId("demand-traffic").disabled = false; }
    }
  }

  byId("connect-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const token = byId("report-token").value.trim();
    if (token.length < 32 || token.length > 512) { status("Enter a valid operator report credential of at least 32 characters.", true); return; }
    clearSession(""); session.token = token;
    byId("connect-form").hidden = true; byId("connection-actions").hidden = false;
    byId("updated-label").textContent = "Connecting to private reports…";
    byId("connection-tag").textContent = "Connecting";
    refresh();
  });
  byId("refresh-button").addEventListener("click", refresh);
  byId("disconnect-button").addEventListener("click", () => { clearSession(); byId("report-token").focus(); });
  byId("product-filter").addEventListener("change", renderSelected);
  byId("tool-search").addEventListener("input", renderTools);
  byId("demand-traffic").addEventListener("change", refresh);
  byId("ecosystem-source").addEventListener("change", renderEcosystem);
  byId("ecosystem-search").addEventListener("input", renderEcosystem);
  byId("auto-refresh").addEventListener("change", () => {
    clearInterval(session.timer); session.timer = null;
    if (byId("auto-refresh").checked && session.token) session.timer = setInterval(refresh, 60000);
  });
  window.addEventListener("pagehide", () => clearSession());
})();
