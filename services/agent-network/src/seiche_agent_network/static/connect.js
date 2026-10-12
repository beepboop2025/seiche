'use strict';
const byId = id => document.getElementById(id);
let products = [], credentials = null;
function showProduct() {
  const product = products.find(p => p.id === byId('product').value);
  if (!product) return;
  byId('endpoint').textContent = product.url;
  byId('prompt').textContent = product.first_prompt;
}
async function copy(value) {
  try { await navigator.clipboard.writeText(value); byId('status').textContent = 'Copied.'; }
  catch { byId('status').textContent = 'Select and copy the text directly; clipboard access is unavailable.'; }
}
byId('copy-endpoint').addEventListener('click', () => copy(byId('endpoint').textContent));
byId('copy-agent').addEventListener('click', () => copy(credentials.agent_token));
byId('product').addEventListener('change', showProduct);
async function request(path, body) {
  const response = await fetch('./' + path, {method: body ? 'POST' : 'GET', headers: {'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : undefined, cache: 'no-store', redirect: 'error'});
  if (!response.ok) throw new Error(response.status === 429 ? 'Workspace enrollment is at capacity. Anonymous product tools remain available.' : 'The request did not complete. Check the service before trying again.');
  return response.json();
}
request('v1/integrations').then(value => {
  products = value.products; byId('product').replaceChildren();
  for (const product of products) { const option = document.createElement('option'); option.value = product.id; option.textContent = product.name + ' · ' + product.use_case; byId('product').append(option); }
  showProduct();
}).catch(error => { byId('status').textContent = error.message; });
byId('enroll').addEventListener('submit', async event => {
  event.preventDefault(); byId('join').disabled = true; const status = byId('join-status');
  try {
    status.textContent = 'Preparing your workspace…';
    const value = await request('v1/enrollment/challenge');
    const encoder = new TextEncoder(); let proof = null;
    for (let nonce = 0; nonce < 4000000; nonce++) {
      const bytes = new Uint8Array(await crypto.subtle.digest('SHA-256', encoder.encode(value.challenge.id + ':' + nonce)));
      if (bytes[0] === 0 && bytes[1] === 0) { proof = nonce; break; }
      if (nonce % 512 === 0) await new Promise(resolve => setTimeout(resolve, 0));
    }
    if (proof === null) throw new Error('Workspace preparation timed out. Please try again.');
    const form = new FormData(event.target);
    credentials = await request('v1/enrollment', {challenge: value.challenge, signature: value.signature, proof, organization: form.get('organization'), agent: form.get('agent'), policy_version: value.policy_version, data_notice_acknowledged: form.get('notice') === 'on'});
    byId('credentials').hidden = false; byId('agent-key').value = credentials.agent_token;
    status.textContent = 'Workspace created. Save your credential file before leaving.';
  } catch (error) { status.textContent = error.message; byId('join').disabled = false; }
});
byId('download-keys').addEventListener('click', () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(credentials, null, 2)], {type: 'application/json'}));
  const link = document.createElement('a'); link.href = url; link.download = 'liquilens-workspace-credentials.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
