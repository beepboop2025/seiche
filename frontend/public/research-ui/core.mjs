/** Public presentation primitives. No model scores, inferred freshness or private state. */
export const BANK_STATES = { observed: 'Accepted', stale: 'Stale', historical: 'Historical', unavailable: 'Unavailable' };
export const SECTORS = { sfb: 'Small finance bank', ucb: 'Co-operative bank', bank: 'Commercial bank' };
export const MARKET_NAMES = { UST: 'US Treasuries', IG: 'Investment-grade credit', HY: 'High-yield credit', EQUITY: 'Equities', ETF: 'Exchange-traded funds', FX: 'Foreign exchange', CN: 'China', CRYPTO: 'Crypto', BSTOCK: 'Tokenized stocks' };
const MARKET_STATES = new Set(['NORMAL', 'SURPLUS', 'STRAINED', 'DEFICIENT', 'PARTIAL', 'UNAVAILABLE', 'ACCRUING']);
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const boundedText = (value, limit = 5000) => typeof value === 'string' && value.length <= limit;
export const finite = value => typeof value === 'number' && Number.isFinite(value) ? value : null;
const percentile = value => finite(value) !== null && value >= 0 && value <= 1 ? value : null;
export function dateOnly(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const date = new Date(value + 'T00:00:00Z');
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0,10) === value ? value : null;
}
export function dateText(value) {
  const date = dateOnly(value);
  return date ? new Intl.DateTimeFormat('en-GB', {day:'numeric',month:'short',year:'numeric',timeZone:'UTC'}).format(new Date(date+'T00:00:00Z')) : 'Date unavailable';
}
export function sourceUrl(value) {
  try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password ? url.href : null; }
  catch { return null; }
}
export function bankCoverage(payload) {
  if (payload?.schema !== 'liquilens.bank-specialisation.v1' || payload.score_authority !== false || !dateOnly(payload.as_of) || !Array.isArray(payload.rows) || payload.rows.length > 1000) throw new Error('Banking coverage could not be verified.');
  const seen = new Set();
  const rows = payload.rows.map(row => {
    if (!record(row) || !/^[a-z0-9-]{1,100}$/.test(row.slug) || seen.has(row.slug) || !boundedText(row.name,200) || !Object.hasOwn(SECTORS,row.sector) || !Object.hasOwn(BANK_STATES,row.status) || (row.period_end !== null && !dateOnly(row.period_end))) throw new Error('A banking coverage record could not be verified.');
    seen.add(row.slug);
    return {slug:row.slug,name:row.name,sector:row.sector,status:row.status,period:row.period_end,ageDays:Number.isInteger(row.age_days) && row.age_days>=0 ? row.age_days:null};
  }).sort((a,b) => (a.status !== 'observed') - (b.status !== 'observed') || a.name.localeCompare(b.name));
  return {asOf:payload.as_of,rows,counts:Object.fromEntries(Object.keys(BANK_STATES).map(key=>[key,rows.filter(row=>row.status===key).length]))};
}
export function marketBoard(payload) {
  if (payload?.schema !== 'undertow.board.v1' || !record(payload.segments) || Object.keys(payload.segments).length > 30) throw new Error('The market board could not be verified.');
  const rows = Object.entries(payload.segments).map(([key,row]) => {
    if (!/^[A-Z0-9_-]{1,20}$/.test(key) || !record(row) || !MARKET_STATES.has(row.tier) || !Number.isInteger(row.n_measures) || row.n_measures<0 || !Number.isInteger(row.n_qualifying) || row.n_qualifying<0 || row.n_qualifying>row.n_measures || !Array.isArray(row.measures) || row.measures.length>500) throw new Error('A market coverage record could not be verified.');
    const measures = row.measures.map(value => {
      if (!record(value) || !boundedText(value.measure,500) || !boundedText(value.limits || '',12000)) throw new Error('A market observation could not be verified.');
      const notes = ['note','caveat','stress_pctl_withheld_note'].map(key=>value[key]).filter(value=>boundedText(value,12000));
      return {name:value.measure,date:dateOnly(value.asof),observations:Number.isInteger(value.obs)&&value.obs>=0?value.obs:null,percentile:value.stress_pctl_withheld===true?null:percentile(value.stress_pctl),limits:value.limits || '',notes};
    });
    const dates = measures.map(row=>row.date).filter(Boolean).sort();
    const withheld = boundedText(row.score_withheld_reason,12000) ? row.score_withheld_reason : '';
    return {key,name:MARKET_NAMES[key]||key,status:row.tier,total:row.n_measures,qualifying:row.n_qualifying,score:withheld||['PARTIAL','UNAVAILABLE','ACCRUING'].includes(row.tier)?null:percentile(row.score),withheld,measures,oldest:dates[0]||null,newest:dates.at(-1)||null};
  });
  return {rows,asOf:dateOnly(payload.asof),published:boundedText(payload.provenance?.generated_at,80)?payload.provenance.generated_at:null,readings:Number.isInteger(payload.n_readings)?payload.n_readings:null,oldest:dateOnly(payload.provenance?.freshness?.upstream_inputs?.oldest_measure_asof),newest:dateOnly(payload.provenance?.freshness?.upstream_inputs?.newest_measure_asof)};
}
export function filterRows(rows, {query='',status='all',sector='all'}={}) {
  const terms=query.toLocaleLowerCase('en-US').trim().split(/\s+/).filter(Boolean);
  return rows.filter(row=>(status==='all'||row.status===status)&&(sector==='all'||row.sector===sector)&&terms.every(term=>`${row.name} ${row.slug||row.key||''} ${row.sector||''}`.toLocaleLowerCase('en-US').includes(term)));
}
export function csv(rows) {
  return rows.map(row=>row.map(value=>{
    let text=value===null||value===undefined?'':String(value);
    if (typeof value!=='number' && /^[\s\u0000-\u001f]*[=+@-]/.test(text)) text="'"+text;
    return '"'+text.replaceAll('"','""')+'"';
  }).join(',')).join('\r\n')+'\r\n';
}
export function download(name, body, type='text/csv;charset=utf-8') {
  const url=URL.createObjectURL(new Blob([body],{type}));
  const link=document.createElement('a');link.href=url;link.download=name;link.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export async function readJSON(url, {signal,limit=2*1024*1024}={}) {
  const response=await fetch(url,{signal,credentials:'omit',headers:{Accept:'application/json'}});
  if (!response.ok) throw new Error(response.status===429?'The source rate limit was reached. Try again later.':`The source returned HTTP ${response.status}.`);
  const reader=response.body?.getReader();if(!reader)throw new Error('The response could not be read.');
  const chunks=[];let length=0;
  try {
    while(true){const part=await reader.read();if(part.done)break;length+=part.value.byteLength;if(length>limit)throw new Error('The response exceeds the display limit.');chunks.push(part.value);}
  } catch(error) {await reader.cancel().catch(()=>{});throw error;} finally {reader.releaseLock();}
  const bytes=new Uint8Array(length);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
  return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
}
export function element(tag,text,className) {
  const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;
}
export function statusBadge(label,state) {const badge=element('span',label,'rw-status');badge.dataset.state=state;return badge;}
export function link(label,url,className) {const node=element('a',label,className);node.href=url;return node;}
export function sourceDetails(title,lines) {const item=element('details',undefined,'rw-detail');item.append(element('summary',title));const list=element('ul');for(const line of lines)list.append(element('li',line));item.append(list);return item;}
export function visibleRefresh(refresh, interval=300000) {
  let disposed=false,last=0,busy=false;
  const run=async()=>{if(disposed||busy||document.hidden||Date.now()-last<interval)return;busy=true;last=Date.now();try{await refresh();}finally{busy=false;}};
  const timer=setInterval(run,interval);document.addEventListener('visibilitychange',run);void run();
  return ()=>{disposed=true;clearInterval(timer);document.removeEventListener('visibilitychange',run);};
}
