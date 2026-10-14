"use strict";
const $ = (id) => document.getElementById(id);
const base = new URL(document.baseURI);
let coverage, offset = 0;
const count = (n) => new Intl.NumberFormat("en").format(n || 0);
const stamp = (n) => n ? new Date(n * 1000).toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC") : "Not observed";
const node = (tag, value, cls) => { const e = document.createElement(tag); if (value !== undefined) e.textContent = value; if (cls) e.className = cls; return e; };
async function read(path, options = {}) { const response = await fetch(new URL(path, base), {cache:"no-store", credentials:"omit", redirect:"error", ...options}); if (!response.ok) throw new Error(`Service returned ${response.status}`); return response.json(); }
function link(text, href) { const a = node("a", text); a.href = href; a.rel = "noreferrer noopener"; a.target = "_blank"; return a; }
function empty(tbody, text, columns = 4) { tbody.replaceChildren(); const td = node("td", text, "empty"); td.colSpan = columns; const tr = node("tr"); tr.append(td); tbody.append(tr); }
async function loadCoverage() {
  try { coverage = await read("v1/coverage"); $("identities").textContent = count(coverage.retained_entities.registered_agent); $("services").textContent = count(coverage.retained_entities.paid_service);
    $("events-count").textContent = count(Object.values(coverage.observed_events).reduce((a,b)=>a+b,0)); $("updated").textContent = "Collection report · " + stamp(coverage.as_of); $("download").disabled=false;
    $("source-cards").replaceChildren(); for (const source of coverage.sources) { const card=node("article",undefined,"source"), top=node("div",undefined,"source-top"); top.append(node("h3",source.name),node("span",source.status.replaceAll("_"," "),"badge "+source.status)); card.append(top,node("p",source.scope),node("p","Captured: "+stamp(source.captured_at),"stamp")); if(source.error) card.append(node("p",source.error.replaceAll("_"," "),"stamp")); card.append(link("Source documentation",source.documentation_url)); $("source-cards").append(card); }
  } catch(e) { $("updated").textContent="Collection status unavailable. No current coverage claim can be made."; $("source-cards").replaceChildren(node("p",e.message)); }
}
async function loadEntities() {
  const params = new URLSearchParams({limit:"25",offset:String(offset)}); if($("kind").value)params.set("kind",$("kind").value);
  try {const data=await read("v1/entities?"+params); const body=$("entity-rows"); body.replaceChildren(); if(!data.items.length)empty(body,"No records in this selection yet.");
    for(const r of data.items){const tr=node("tr"); tr.append(node("td",r.name),node("td",r.kind==="registered_agent"?"Registered identity":"Paid service"),node("td",stamp(r.last_seen))); const proof=node("td"); proof.append(link("Collection receipt",new URL("v1/receipts/"+encodeURIComponent(r.receipt),base)));tr.append(proof);body.append(tr);}
    $("page-status").textContent=`${data.items.length ? offset+1 : 0}–${offset+data.items.length} of ${count(data.total_retained)} retained`;$("previous").disabled=offset===0;$("next").disabled=offset+25>=data.total_retained;
  } catch(e){empty($("entity-rows"),e.message);}
}
async function loadEvents(){try{const data=await read("v1/protocol-events?limit=20");const body=$("event-rows");body.replaceChildren();if(!data.items.length)empty(body,"No eligible protocol events observed in the captured interval yet.");for(const r of data.items){const tr=node("tr");tr.append(node("td",r.kind.replace(/([a-z])([A-Z])/g,"$1 $2")),node("td",stamp(r.occurred_at)),node("td",count(r.block_number)));const td=node("td");td.append(link(r.transaction_hash.slice(0,12)+"…",r.explorer_url));tr.append(td);body.append(tr);}}catch(e){empty($("event-rows"),e.message);}}
$("kind").addEventListener("change",()=>{offset=0;loadEntities();});$("previous").addEventListener("click",()=>{offset=Math.max(0,offset-25);loadEntities();});$("next").addEventListener("click",()=>{offset+=25;loadEntities();});
$("download").addEventListener("click",()=>{if(!coverage)return;const url=URL.createObjectURL(new Blob([JSON.stringify(coverage,null,2)],{type:"application/json"}));const a=node("a");a.href=url;a.download="agent-activity-coverage.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
$("pilot-form").addEventListener("submit",async e=>{e.preventDefault();const form=e.currentTarget, button=form.querySelector("button"), value=Object.fromEntries(new FormData(form));value.notice_accepted=form.elements.notice_accepted.checked;button.disabled=true;$("pilot-status").textContent="Submitting…";try{const result=await read("v1/pilot-requests",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(value)});$("pilot-status").textContent="Request recorded for source review. Reference: "+result.request_id+". Agent collection remains off until separately enrolled.";form.reset();}catch(error){$("pilot-status").textContent=error.message;}finally{button.disabled=false;}});
$("private-form").addEventListener("submit",async e=>{e.preventDefault();const token=$("credential").value;$("private-report").hidden=false;$("private-report").textContent="Loading…";try{const data=await read("v1/me",{headers:{Authorization:"Bearer "+token}});$("private-report").textContent=JSON.stringify(data,null,2);}catch(error){$("private-report").textContent=error.message;}});
$("clear-private").addEventListener("click",()=>{$("credential").value="";$("private-report").textContent="";$("private-report").hidden=true;});
loadCoverage();loadEntities();loadEvents();
