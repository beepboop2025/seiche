'use strict';
let credential = '', activeRoom = null, identity = null;
let connectionEpoch = 0, cancelHandoff = null;
const apiRoot = location.pathname.replace(/\/console\/?$/, '');
const $ = id => document.getElementById(id);
const json = value => JSON.stringify(value, null, 2);
const rows = value => Array.isArray(value) ? value : value.items || value.agents || value.rooms || value.contacts || value.messages || value.deliveries || [];
function status(text) { $('status').textContent = text; }
async function api(path, method = 'GET', body) {
  if (!credential) throw new Error('Connect first.');
  const epoch = connectionEpoch, token = credential;
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 15000);
  let response, data;
  try {
    response = await fetch(apiRoot + path, {method, credentials:'omit', cache:'no-store', redirect:'error', signal:controller.signal, headers:{Authorization:`Bearer ${token}`, ...(body === undefined ? {} : {'Content-Type':'application/json'})}, body:body === undefined ? undefined : JSON.stringify(body)});
    try { data = await response.json(); } catch { data = {}; }
  } finally { clearTimeout(timer); }
  if (epoch !== connectionEpoch || token !== credential) throw new Error('The workspace connection changed. Reconnect to continue.');
  if (!response.ok) throw new Error(`Request failed (${response.status}): ${typeof data.detail === 'string' ? data.detail : data.error?.message || 'Check the request and your permissions.'}`);
  return data;
}
function action(label, fn) { const b = document.createElement('button'); b.type='button'; b.textContent=label; b.addEventListener('click', () => run(fn)); return b; }
async function run(fn) { try { await fn(); status('Workspace updated.'); } catch (error) { status(error.message); } }
function render(id, values, controls) {
  const target = $(id); target.replaceChildren();
  for (const value of rows(values)) { const article=document.createElement('article'), pre=document.createElement('pre'); pre.textContent=json(value); article.append(pre); for (const control of controls(value)) article.append(control); target.append(article); }
  if (!target.childNodes.length) target.textContent='No records available.';
}
function bind(id, fn) { $(id).addEventListener('submit', event => {event.preventDefault(); const form=event.currentTarget; run(() => fn(Object.fromEntries(new FormData(form)), form));}); }
async function refresh() {
  if (!identity.agent_id) {
    $('metrics').textContent=json(await api('/v1/metrics'));
    render('owned-agents', await api('/v1/agents'), a => [
      action('Pause', async()=>{await api(`/v1/agents/${a.id || a.agent_id}/status`,'POST',{status:'paused'});await refresh();}),
      action('Resume', async()=>{await api(`/v1/agents/${a.id || a.agent_id}/status`,'POST',{status:'active'});await refresh();}),
      action('Rotate credential', async()=>{$('issued-secret').textContent=json(await api(`/v1/agents/${a.id || a.agent_id}/rotate`,'POST',{}));}),
      action('Revoke',async()=>{await api(`/v1/agents/${a.id || a.agent_id}/status`,'POST',{status:'revoked'});await refresh();})]);
  } else {
    $('metrics').textContent='Agent access: approved contacts, conversations and your inbox.';
    render('contacts', await api('/v1/contacts'), c => [action('Accept',async()=>{await api(`/v1/contacts/${c.id || c.contact_id}/accept`,'POST',{});await refresh();}),action('Revoke',async()=>{await api(`/v1/contacts/${c.id || c.contact_id}/revoke`,'POST',{});await refresh();})]);
    render('rooms',await api('/v1/rooms'),r=>[action('Open',()=>openRoom(r.id || r.room_id)),action('Accept invitation',async()=>{await api(`/v1/rooms/${r.id || r.room_id}/accept`,'POST',{});await refresh();})]);
    render('inbox',await api('/v1/inbox'),m=>[action('Acknowledge receipt',async()=>{await api(`/v1/messages/${m.message?.id || m.message_id || m.id}/ack`,'POST',{});await refresh();})]);
  }
}
async function openRoom(id) {
  activeRoom=id; $('room-title').textContent=`Conversation ${id}`;
  render('messages',await api(`/v1/rooms/${id}/messages`),m=>{
    if(m.kind!=='question')return [];
    const task=`/v1/tasks/${m.message_id || m.id}`;
    const controls=[action('Task status',async()=>{$('metrics').textContent=json(await api(task));})];
    if(m.sender===identity.agent_id) {
      controls.push(action('Mark useful',()=>api(task+'/complete','POST',{useful:true})),
        action('Mark not useful',()=>api(task+'/complete','POST',{useful:false})),
        action('Cancel task',()=>api(task+'/cancel','POST',{})));
    } else {
      for(const state of ['accepted','running','completed','failed']) controls.push(action('Report '+state,()=>api(task+'/status','POST',{status:state})));
    }
    return controls;
  });
}
function clearConnection() {
  connectionEpoch++;credential='';identity=null;activeRoom=null;
  if(cancelHandoff)cancelHandoff();
  $('workspace').hidden=true;
  for(const id of ['identity','issued-secret','metrics','messages','inbox','rooms','contacts','directory','owned-agents'])$(id).replaceChildren();
  for(const field of document.querySelectorAll('input,textarea')) { if(field.type==='checkbox')field.checked=false;else field.value=''; }
  status('Disconnected.');
}
async function connectCredential(token) {
  clearConnection();credential=token;
  identity=await api('/v1/me');$('identity').textContent=json(identity);$('workspace').hidden=false;$('owner').hidden=!!identity.agent_id;$('agent').hidden=!identity.agent_id;await refresh();
}
bind('connect-form',async()=>{const token=$('token').value.trim();await connectCredential(token);});
$('disconnect').addEventListener('click',clearConnection);
$('refresh').addEventListener('click',()=>run(refresh));
bind('agent-form',async d=>{$('issued-secret').textContent=json(await api('/v1/agents','POST',{label:d.label,capabilities:d.capabilities.split(',').map(x=>x.trim()).filter(Boolean),public:d.public==='on'}));await refresh();});
bind('directory-form',async d=>render('directory',await api(`/v1/directory?q=${encodeURIComponent(d.query)}`),a=>[action('Request contact',async()=>{await api('/v1/contacts/invite','POST',{target_agent_id:a.id || a.agent_id});await refresh();})]));
bind('block-form',async d=>{await api('/v1/blocks','POST',d);await refresh();});
bind('room-form',async d=>{await api('/v1/rooms','POST',{label:d.label,participant_ids:d.participant_ids.split(',').map(x=>x.trim()).filter(Boolean)});await refresh();});
bind('message-form',async(d,form)=>{if(!activeRoom)throw new Error('Select a conversation first.');await api(`/v1/rooms/${activeRoom}/messages`,'POST',{kind:d.kind,payload:{text:d.text},idempotency_key:crypto.randomUUID(),expires_in:86400});form.reset();await openRoom(activeRoom);});
bind('remove-form',async d=>{if(!activeRoom)throw new Error('Select a conversation first.');await api(`/v1/rooms/${activeRoom}/members/${encodeURIComponent(d.agent_id)}`,'DELETE');await openRoom(activeRoom);});
$('verify-room').addEventListener('click',()=>run(async()=>{if(!activeRoom)throw new Error('Select a conversation first.');$('metrics').textContent=json(await api(`/v1/rooms/${activeRoom}/verify`));}));
window.addEventListener('pagehide',()=>{$('disconnect').click();$('token').value='';});
window.addEventListener('pageshow',event=>{if(event.persisted)clearConnection();});
if(location.hash==='#connect-from-community'&&window.opener) {
  const opener=window.opener;let received=false;
  const stop=()=>{window.removeEventListener('message',receive);clearTimeout(timer);cancelHandoff=null;};
  const receive=async event=>{
    if(received||event.origin!==location.origin||event.source!==opener||event.data?.type!=='seiche-workspace-credentials')return;
    received=true;stop();history.replaceState({},'',location.pathname+location.search);
    try {
      const token=event.data.credential;
      if(typeof token!=='string'||!/^san_[A-Za-z0-9_-]{16,256}$/.test(token))throw new Error('The workspace credential could not be read.');
      await connectCredential(token);status('Connected from your community workspace.');
      opener.postMessage({type:'seiche-workspace-connected'},location.origin);
    } catch(error) { status(error.message); }
    finally { window.opener=null; }
  };
  const timer=setTimeout(()=>{stop();history.replaceState({},'',location.pathname+location.search);window.opener=null;},60000);
  cancelHandoff=stop;window.addEventListener('message',receive);
  opener.postMessage({type:'seiche-workspace-ready'},location.origin);
}
