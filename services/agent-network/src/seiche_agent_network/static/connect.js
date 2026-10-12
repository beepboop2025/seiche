'use strict';
const byId = id => document.getElementById(id);
let products = [], credentials = null, sharing = null;
let guideRequest = 0;
const sharingPolicy = 'task-signals-2026-10-12';

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function dateOnly(value) {
  if (typeof value !== 'string' || !value.trim()) return 'unknown';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'unknown' : date.toISOString().slice(0, 10);
}
function dateTime(value) {
  if (typeof value !== 'string' || !value.trim()) return 'unknown';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'unknown' : date.toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
}
function sourceLink(label, source, fallback) {
  const link = element('a', label);
  try {
    const url = new URL(source || fallback);
    if (url.protocol !== 'https:' || !['openrouter.ai', 'www.census.gov'].includes(url.hostname)) throw new Error();
    link.href = url.href;
  } catch { link.href = fallback; }
  link.target = '_blank'; link.rel = 'noopener noreferrer';
  return link;
}
async function request(path, {method = 'GET', body, token} = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  const headers = {'Accept': 'application/json'};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (token) headers.Authorization = 'Bearer ' + token;
  try {
    const response = await fetch('./' + path, {method, headers, body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store', redirect: 'error', credentials: 'omit', signal: controller.signal});
    if (!response.ok) {
      if (response.status === 401 || response.status === 403) throw new Error('This key cannot perform that action. Use this workspace’s owner key to manage sharing.');
      if (response.status === 429) throw new Error('This service is at capacity. Wait a moment before trying again.');
      if (response.status === 409 && path === 'v1/enrollment') throw new Error('That enrollment challenge was already used. Check whether your credential file was saved before creating another workspace.');
      if (response.status === 422) throw new Error('The service could not accept these choices. Reload the page to get the current options.');
      const error = new Error('The request did not complete (HTTP ' + response.status + '). Please check the service before trying again.');
      error.requestMayHaveCompleted = method !== 'GET' && response.status >= 500;
      throw error;
    }
    try { return await response.json(); }
    catch {
      const error = new Error('The service returned an unreadable response. Check the current state before repeating this action.');
      error.requestMayHaveCompleted = method !== 'GET';
      throw error;
    }
  } catch (error) {
    if (error.name === 'AbortError' || error instanceof TypeError) {
      const interrupted = new Error(method === 'GET' ? 'Could not reach the service in time. Please check your connection and try again.' : 'The connection was interrupted; the service may have received this request. Check your current settings before trying again.');
      interrupted.requestMayHaveCompleted = method !== 'GET';
      throw interrupted;
    }
    throw error;
  } finally { clearTimeout(timer); }
}
async function copy(value, statusId = 'status') {
  try { await navigator.clipboard.writeText(value); byId(statusId).textContent = 'Copied.'; }
  catch { byId(statusId).textContent = 'Select and copy the text directly; clipboard access is unavailable.'; }
}
function showProduct() {
  const product = products.find(p => p.id === byId('product').value);
  if (!product) return;
  byId('endpoint').textContent = product.url;
  byId('prompt').textContent = product.first_prompt;
}
byId('copy-endpoint').addEventListener('click', () => copy(byId('endpoint').textContent));
byId('copy-starting-prompt').addEventListener('click', () => copy(byId('starting-prompt').textContent, 'guide-status'));
byId('copy-agent').addEventListener('click', () => { if (credentials) copy(credentials.agent_token, 'join-status'); });
byId('copy-instruction').addEventListener('click', () => copy(byId('agent-instruction').textContent, 'sharing-status'));
byId('product').addEventListener('change', showProduct);
request('v1/integrations').then(value => {
  products = Array.isArray(value.products) ? value.products : [];
  if (!products.length) throw new Error('No product connections are currently available.');
  byId('product').replaceChildren();
  for (const product of products) {
    const option = element('option', product.name + ' · ' + product.use_case);
    option.value = product.id; byId('product').append(option);
  }
  showProduct();
}).catch(error => { byId('status').textContent = error.message; });

function sourceNote(card, source, fallback, rows) {
  const first = rows[0];
  const note = element('p', undefined, 'source-note');
  note.append(sourceLink(source.attribution || first?.attribution || 'Original source', source.source_url, fallback));
  const collected = source.knowledge_time || first?.knowledge_time;
  note.append(document.createTextNode(' · Collected ' + dateTime(collected)));
  card.append(note);
  const fresh = source.status === 'ok' && source.fresh === true;
  card.append(element('p', fresh ? 'Collection available. See the reference period above.' : 'Collection is stale or unavailable; do not assume these observations are current.', 'feed-state'));
}
async function loadEconomy() {
  const preview = byId('economy-preview');
  preview.setAttribute('aria-busy', 'true');
  preview.replaceChildren(element('p', 'Loading the latest available observations…'));
  byId('reload-economy').disabled = true;
  try {
    const value = await request('commerce/v1/public/economy');
    const rows = Array.isArray(value.observations) ? value.observations : [];
    const sources = Array.isArray(value.sources) ? value.sources : [];
    const models = rows.filter(row => row.source === 'openrouter_rankings' && row.kind === 'usage_aggregate').sort((a, b) => Number(a.rank) - Number(b.rank)).slice(0, 5);
    const retail = rows.filter(row => row.source === 'census_ecommerce' && row.kind === 'retail_activity').sort((a, b) => String(b.period_end).localeCompare(String(a.period_end)));
    preview.replaceChildren();
    const modelCard = element('div', undefined, 'preview-card'); modelCard.id = 'model-preview';
    modelCard.append(element('h3', 'Models in use'));
    if (models.length) {
      modelCard.append(element('p', 'OpenRouter token usage · ' + dateOnly(models[0].period_start) + ' to ' + dateOnly(models[0].period_end) + ' UTC', 'fine'));
      const table = element('table', undefined, 'rankings'); table.setAttribute('aria-label', 'Top five models by public OpenRouter token usage');
      const head = element('thead'), headRow = element('tr');
      for (const title of ['Model', 'Tokens']) { const th = element('th', title); th.scope = 'col'; headRow.append(th); }
      head.append(headRow); table.append(head); const body = element('tbody');
      for (const model of models) { const row = element('tr'); row.append(element('td', model.model), element('td', model.tokens_display || 'Not reported')); body.append(row); }
      table.append(body); modelCard.append(table);
      modelCard.append(element('p', 'Rounded public values; includes free usage and excludes private requests.', 'fine'));
    } else { modelCard.append(element('p', 'No model observations are available right now. The source status below may explain the gap.')); }
    sourceNote(modelCard, sources.find(source => source.source === 'openrouter_rankings') || {}, 'https://openrouter.ai/rankings', models);
    const retailCard = element('div', undefined, 'preview-card'); retailCard.id = 'retail-preview';
    retailCard.append(element('h3', 'Online share of U.S. retail'));
    if (retail.length) {
      const row = retail[0];
      const rawShare = row.share_pct;
      const numericShare = typeof rawShare === 'number' || (typeof rawShare === 'string' && /^\d+(?:\.\d+)?$/.test(rawShare));
      const share = numericShare ? Number(rawShare) : NaN;
      retailCard.append(element('p', 'U.S. retail · ' + (typeof row.period === 'string' && row.period ? row.period : 'period unknown'), 'fine'));
      retailCard.append(element('p', Number.isFinite(share) && share >= 0 && share <= 100 ? share.toFixed(1) + '%' : 'Not reported', 'metric'));
      retailCard.append(element('p', 'of retail sales were e-commerce', 'metric-label'));
      retailCard.append(element('p', 'Seasonally adjusted survey estimate. ' + (row.estimate_status ? 'Status: ' + row.estimate_status + '. ' : '') + 'Estimates can be revised.', 'fine'));
      retailCard.append(element('p', 'Reference period: ' + dateOnly(row.period_start) + ' to ' + dateOnly(row.period_end) + '.', 'fine'));
    } else { retailCard.append(element('p', 'No retail observations are available right now. Please check the source status.')); }
    sourceNote(retailCard, sources.find(source => source.source === 'census_ecommerce') || {}, 'https://www.census.gov/retail/index.html', retail);
    preview.append(modelCard, retailCard);
    byId('reload-economy').hidden = models.length > 0 && retail.length > 0;
  } catch (error) {
    const message = element('p', 'The preview is unavailable. ' + error.message); message.setAttribute('role', 'status');
    preview.replaceChildren(message); byId('reload-economy').hidden = false;
  } finally { preview.setAttribute('aria-busy', 'false'); byId('reload-economy').disabled = false; }
}
byId('reload-economy').addEventListener('click', loadEconomy);
loadEconomy();

function fillChoices(id, choices, selected) {
  if (!Array.isArray(choices) || !choices.length) throw new Error('Task choices are unavailable. You can still browse all product connections below.');
  const select = byId(id); select.replaceChildren();
  for (const choice of choices) { const option = element('option', choice.label); option.value = choice.id; select.append(option); }
  if (choices.some(choice => choice.id === selected)) select.value = selected;
  select.disabled = false;
}
request('v1/task-guide').then(value => {
  fillChoices('goal', value.goals, value.defaults?.goal);
  fillChoices('topic', value.topics, value.defaults?.topic);
  byId('find-task').disabled = false;
}).catch(error => { byId('guide-status').textContent = error.message; });
byId('task-guide').addEventListener('submit', async event => {
  event.preventDefault(); const sequence = ++guideRequest;
  byId('find-task').disabled = true; byId('guide-status').textContent = 'Finding a useful place to start…';
  try {
    const value = await request('v1/task-guide', {method: 'POST', body: {goal: byId('goal').value, topic: byId('topic').value}});
    if (sequence !== guideRequest) return;
    const suggested = Array.isArray(value.recommended_products) ? value.recommended_products : [];
    if (!suggested.length) throw new Error('No matching tools are available for these choices. Browse all product connections below.');
    byId('starting-prompt').textContent = value.starting_prompt;
    byId('coverage-note').textContent = value.coverage_note;
    byId('recommended-products').replaceChildren();
    for (const product of suggested) {
      const card = element('article', undefined, 'recommendation');
      card.append(element('h3', product.name), element('p', 'Start with ' + product.first_tool, 'fine'), element('p', product.first_prompt));
      const endpoint = element('pre', product.url); card.append(endpoint);
      const copyEndpoint = element('button', 'Copy connection'); copyEndpoint.type = 'button'; copyEndpoint.addEventListener('click', () => copy(product.url, 'guide-status'));
      const copyPrompt = element('button', 'Copy tool prompt'); copyPrompt.type = 'button'; copyPrompt.addEventListener('click', () => copy(product.first_prompt, 'guide-status'));
      card.append(copyEndpoint, copyPrompt); byId('recommended-products').append(card);
    }
    byId('guide-result').hidden = false;
    byId('guide-status').textContent = suggested.length + ' useful ' + (suggested.length === 1 ? 'connection' : 'connections') + ' to try. Your choices were not saved as a task report.';
  } catch (error) { byId('guide-status').textContent = error.message; byId('guide-result').hidden = true; }
  finally { byId('find-task').disabled = false; }
});

function ownerKey() {
  const key = byId('owner-key').value.trim();
  if (!key) throw new Error('Enter your workspace owner key, or create a workspace above.');
  return key;
}
function resetSharing() {
  sharing = null; byId('sharing-details').hidden = true; byId('withdraw-sharing').disabled = true;
  byId('share-notice').checked = false; byId('task-summary').replaceChildren();
  byId('sharing-status').textContent = 'Sharing status has not been checked. Creating a workspace leaves task sharing off.';
}
function showSharing(value) {
  sharing = value;
  byId('withdraw-sharing').disabled = value.role !== 'owner';
  byId('sharing-details').hidden = false;
  byId('sharing-status').textContent = value.enabled ? 'Optional task sharing is enabled for this workspace. Agents may report only within the current grant; no report has been sent by this page.' : 'Optional task sharing is off. Your free public tools remain available.';
}
function sharingBusy(busy) {
  for (const id of ['enable-sharing', 'check-sharing', 'refresh-summary']) byId(id).disabled = busy;
  byId('withdraw-sharing').disabled = busy || !sharing || sharing.role !== 'owner';
}
function count(value) { return Number.isSafeInteger(value) && value >= 0 ? value : 0; }
async function loadSummary() {
  const token = ownerKey();
  const value = await request('v1/participation/summary', {token});
  if (token !== byId('owner-key').value.trim()) return;
  const summary = byId('task-summary'); summary.replaceChildren(element('h3', 'Your task reports · last 30 days'));
  const counters = element('div', undefined, 'summary-counts');
  for (const [label, data] of [['Reported real activity', value.traffic_classes?.real], ['Reported synthetic tests', value.traffic_classes?.synthetic]]) {
    const metric = element('p'); metric.append(element('strong', String(count(data?.total_events))), document.createTextNode(label)); counters.append(metric);
  }
  summary.append(counters);
  if (!count(value.total_events)) summary.append(element('p', 'No task reports yet. Enabling sharing allows your agents to contribute; it does not create reports automatically.'));
  const goals = value.traffic_classes?.real?.goals;
  if (goals && typeof goals === 'object') {
    const entries = Object.entries(goals).filter(([, total]) => count(total)).sort((a, b) => b[1] - a[1]).slice(0, 6);
    if (entries.length) summary.append(element('p', 'Reported goals: ' + entries.map(([goal, total]) => goal.replaceAll('_', ' ') + ' (' + count(total) + ')').join(' · '), 'fine'));
  }
  summary.append(element('p', 'These are self-reported task events, not unique people, verified purchases or completed trades. The submitting agent declares whether an event is real or synthetic.', 'fine'));
}
byId('owner-key').addEventListener('input', resetSharing);
byId('clear-owner-key').addEventListener('click', () => { byId('owner-key').value = ''; resetSharing(); });
byId('check-sharing').addEventListener('click', async () => {
  sharingBusy(true);
  try {
    const token = ownerKey(), value = await request('v1/participation', {token});
    if (token !== byId('owner-key').value.trim()) return;
    showSharing(value); await loadSummary();
  } catch (error) { byId('sharing-status').textContent = error.message; }
  finally { sharingBusy(false); }
});
byId('participation-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!byId('share-notice').checked) { byId('sharing-status').textContent = 'Read and acknowledge the separate task-sharing notice to enable sharing.'; byId('share-notice').focus(); return; }
  sharingBusy(true);
  try {
    const token = ownerKey();
    const value = await request('v1/participation', {method: 'PATCH', token, body: {enabled: true, policy_version: sharingPolicy, data_notice_acknowledged: true}});
    if (token !== byId('owner-key').value.trim()) return;
    showSharing(value); await loadSummary();
  } catch (error) { byId('sharing-status').textContent = error.message; }
  finally { sharingBusy(false); }
});
byId('withdraw-sharing').addEventListener('click', async () => {
  sharingBusy(true);
  try {
    const token = ownerKey();
    const value = await request('v1/participation', {method: 'DELETE', token});
    if (token !== byId('owner-key').value.trim()) return;
    showSharing(value); byId('share-notice').checked = false;
    byId('sharing-status').textContent = 'Task sharing has been withdrawn and this workspace’s retained task reports erased. Your free tools remain available.';
    await loadSummary();
  } catch (error) { byId('sharing-status').textContent = error.message; }
  finally { sharingBusy(false); }
});
byId('refresh-summary').addEventListener('click', async () => {
  sharingBusy(true);
  try { await loadSummary(); }
  catch (error) { byId('sharing-status').textContent = error.message; }
  finally { sharingBusy(false); }
});
byId('enroll').addEventListener('submit', async event => {
  event.preventDefault(); byId('join').disabled = true; const status = byId('join-status');
  try {
    status.textContent = 'Preparing your workspace…';
    const value = await request('v1/enrollment/challenge');
    if (value.challenge?.difficulty_bits !== 16) throw new Error('Workspace preparation has changed. Reload this page before trying again.');
    const encoder = new TextEncoder(); let proof = null; const deadline = Date.now() + 45000;
    for (let nonce = 0; nonce < 4000000; nonce++) {
      const bytes = new Uint8Array(await crypto.subtle.digest('SHA-256', encoder.encode(value.challenge.id + ':' + nonce)));
      if (bytes[0] === 0 && bytes[1] === 0) { proof = nonce; break; }
      if (nonce % 512 === 0) { if (Date.now() > deadline) break; await new Promise(resolve => setTimeout(resolve, 0)); }
    }
    if (proof === null) throw new Error('Workspace preparation timed out. Please try again.');
    const form = new FormData(event.target);
    credentials = await request('v1/enrollment', {method: 'POST', body: {challenge: value.challenge, signature: value.signature, proof, organization: form.get('organization'), agent: form.get('agent'), policy_version: value.policy_version, data_notice_acknowledged: form.get('notice') === 'on'}});
    byId('credentials').hidden = false; byId('agent-key').value = credentials.agent_token;
    byId('owner-key').value = credentials.owner_token; resetSharing();
    status.textContent = 'Workspace created. Save your credential file before leaving. Optional task sharing is still off.';
  } catch (error) {
    status.textContent = error.requestMayHaveCompleted ? 'Enrollment may have completed, but the keys were not received. Check any saved credential file or contact the service operator before creating another workspace. This page will not repeat that enrollment.' : error.message;
    byId('join').disabled = Boolean(error.requestMayHaveCompleted);
  }
});
byId('download-keys').addEventListener('click', () => {
  if (!credentials) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(credentials, null, 2)], {type: 'application/json'}));
  const link = document.createElement('a'); link.href = url; link.download = 'liquilens-workspace-credentials.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
function clearKeys() {
  credentials = null; byId('agent-key').value = ''; byId('owner-key').value = ''; byId('credentials').hidden = true; resetSharing();
}
byId('clear-keys').addEventListener('click', () => { clearKeys(); byId('join-status').textContent = 'Keys cleared from this page. Use your saved credential file to reconnect. Clearing this page does not revoke credentials or change sharing settings.'; });
window.addEventListener('pagehide', clearKeys);
window.addEventListener('pageshow', event => { if (event.persisted) clearKeys(); });
