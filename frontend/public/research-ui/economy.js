import {readJSON,element as el,link,dateOnly,dateText,finite,visibleRefresh} from './core.mjs';

const API='https://api.seiche.info';
const SERIES=[['SOFR','Overnight funding'],['DGS2','US Treasury · 2 years'],['DGS10','US Treasury · 10 years'],['INR','Indian rupee / US dollar'],['WRESBAL','Federal Reserve balances']];
const NS='http://www.w3.org/2000/svg';
const svgNode=(tag,attributes,text)=>{const node=document.createElementNS(NS,tag);for(const [key,value] of Object.entries(attributes))node.setAttribute(key,String(value));if(text!==undefined)node.textContent=text;return node;};
const number=(value,unit)=>new Intl.NumberFormat('en-GB',{maximumFractionDigits:unit==='$M'?0:3}).format(value);
export function seriesModel(payload,entry){
  const p=payload?.provenance;
  if(!entry?.available||entry.csv_restricted||!entry.json||p?.mnemonic!==entry.mnemonic||p.unit!==entry.unit||!dateOnly(p.asof)||!Array.isArray(payload.points)||payload.points.length>2000)throw new Error('The series metadata could not be verified.');
  let previous='';
  const points=payload.points.map(point=>{if(!Array.isArray(point)||point.length!==2||!dateOnly(point[0])||point[0]<=previous||finite(point[1])===null||point[0]>p.asof)throw new Error('The observation history could not be verified.');previous=point[0];return {date:point[0],value:point[1]};});
  if(!points.length||points.at(-1).date!==p.asof)throw new Error('The latest observation is absent from the history.');
  return {key:p.mnemonic,label:entry.label,unit:p.unit,points,asOf:p.asof,received:p.fetched_at,state:p.staleness,cadence:entry.cadence,lag:entry.native_lag,source:p.source,remote:p.remote_id};
}
function plot(model,{small=false,onPoint}={}){
  const width=small?220:640,height=small?54:210,left=small?2:52,right=small?218:626,top=small?5:18,bottom=small?48:170;
  const points=model.points,values=points.map(p=>p.value),low=Math.min(...values),high=Math.max(...values),padding=Math.max((high-low)*.12,Math.abs(high)*.005,.005),minimum=low-padding,maximum=high+padding;
  const start=Date.parse(points[0].date),end=Date.parse(points.at(-1).date);
  const xy=points.map((p,i)=>[left+(end===start?i/Math.max(1,points.length-1):(Date.parse(p.date)-start)/(end-start))*(right-left),bottom-(p.value-minimum)/(maximum-minimum)*(bottom-top)]);
  const svg=svgNode('svg',{viewBox:`0 0 ${width} ${height}`,role:'img','aria-label':`${model.label}, ${model.unit}, ${dateText(points[0].date)} to ${dateText(model.asOf)}${small?'':'. Use left and right arrow keys to inspect observations.'}`});
  if(!small){
    for(const fraction of [0,.5,1]){const y=bottom-fraction*(bottom-top);svg.append(svgNode('line',{x1:left,x2:right,y1:y,y2:y,stroke:'var(--research-line)','stroke-dasharray':'3 5'}));svg.append(svgNode('text',{x:left-10,y:y+4,'text-anchor':'end',fill:'var(--research-muted)','font-size':10},number(minimum+fraction*(maximum-minimum),model.unit)));}
    svg.append(svgNode('text',{x:left,y:197,fill:'var(--research-muted)','font-size':11},dateText(points[0].date)),svgNode('text',{x:right,y:197,fill:'var(--research-muted)','font-size':11,'text-anchor':'end'},dateText(model.asOf)));
  }
  const d=xy.map(([x,y],i)=>`${i?'L':'M'}${x.toFixed(2)} ${y.toFixed(2)}`).join(' ');
  svg.append(svgNode('path',{d:`${d} L${right} ${bottom} L${left} ${bottom} Z`,fill:'var(--research-accent)',opacity:small?.06:.055}));
  svg.append(svgNode('path',{d,fill:'none',stroke:'var(--research-accent)','stroke-width':small?1.5:2,'stroke-linejoin':'round','stroke-linecap':'round',pathLength:1,'data-draw':''}));
  const dot=svgNode('circle',{cx:xy.at(-1)[0],cy:xy.at(-1)[1],r:small?2:3,fill:'var(--research-accent)'});svg.append(dot);
  if(!small){
    let selected=points.length-1;const guide=svgNode('line',{x1:xy.at(-1)[0],x2:xy.at(-1)[0],y1:top,y2:bottom,stroke:'var(--research-accent)',opacity:.25,'stroke-dasharray':'3 4'});svg.append(guide);
    const select=index=>{selected=Math.max(0,Math.min(points.length-1,index));dot.setAttribute('cx',xy[selected][0]);dot.setAttribute('cy',xy[selected][1]);guide.setAttribute('x1',xy[selected][0]);guide.setAttribute('x2',xy[selected][0]);onPoint?.(points[selected]);};
    svg.setAttribute('tabindex','0');svg.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();select(event.key==='Home'?0:event.key==='End'?points.length-1:selected+(event.key==='ArrowRight'?1:-1));}});
    svg.addEventListener('pointermove',event=>{const box=svg.getBoundingClientRect(),x=(event.clientX-box.left)/box.width*width;select(xy.reduce((best,p,i)=>Math.abs(p[0]-x)<Math.abs(xy[best][0]-x)?i:best,0));});svg.addEventListener('pointerleave',()=>select(points.length-1));
  }
  return svg;
}
export function mountEconomy(target){
  let selected='SOFR',models=new Map(),controller=null,disposed=false;
  const chart=el('section',undefined,'rw-chart-frame'),rail=el('section',undefined,'rw-chart-frame'),notice=el('p','Loading source-dated economic observations…','rw-caption');target.replaceChildren(chart,rail);chart.append(el('h2','The economic backdrop'),notice);rail.append(el('p','Official funding, currency and reserve series.','rw-caption'));
  function render(){
    const model=models.get(selected);if(!model)return;chart.replaceChildren();
    const heading=el('h2',SERIES.find(([key])=>key===selected)[1]),tabs=el('div',undefined,'rw-chart-tabs');tabs.setAttribute('role','group');tabs.setAttribute('aria-label','Economic series');
    for(const [key,label]of SERIES){const button=el('button',key==='WRESBAL'?'Reserves':key);button.type='button';button.disabled=!models.has(key);button.title=label;button.setAttribute('aria-pressed',String(key===selected));button.addEventListener('click',()=>{selected=key;render();});tabs.append(button);}
    const reading=el('div',undefined,'rw-chart-number'),value=el('strong'),date=el('span');reading.append(value,date);
    const update=point=>{value.textContent=`${number(point.value,model.unit)} ${model.unit}`;date.textContent=`Observed ${dateText(point.date)}`;};update(model.points.at(-1));
    const figure=el('figure',undefined,'rw-chart');figure.append(plot(model,{onPoint:update}));
    const metadata=el('p',`${model.label}. ${model.cadence}. ${model.lag}. Source state: ${model.state||'not supplied'}.`,'rw-caption');
    const source=/^[A-Za-z0-9_]+$/.test(model.remote||'')&&model.source==='fred'?`https://fred.stlouisfed.org/series/${model.remote}`:`${API}/api/series/index.json`;
    const actions=el('div',undefined,'rw-actions');actions.append(link('Source and definition',source),link('Download source CSV',`${API}/api/series/${model.key}.csv`));chart.append(heading,tabs,reading,figure,metadata,actions,notice);
    rail.replaceChildren(el('h2','Across the economy'),el('p','Each series keeps its own reporting date and unit.','rw-caption'));
    const grid=el('div',undefined,'rw-mini-series');for(const [key,label]of SERIES.filter(([key])=>key!==selected)){const item=el('button',undefined,'rw-mini-chart');item.type='button';item.addEventListener('click',()=>{selected=key;render();});const row=models.get(key);item.disabled=!row;item.append(el('span',label));if(row){item.append(el('strong',`${number(row.points.at(-1).value,row.unit)} ${row.unit}`),el('small',dateText(row.asOf)));const figure=el('div',undefined,'rw-chart');figure.append(plot(row,{small:true}));item.append(figure);}else item.append(el('small','Source unavailable'));grid.append(item);}rail.append(grid);
  }
  async function refresh(){
    if(controller)return;controller=new AbortController();const signal=controller.signal,timer=setTimeout(()=>controller?.abort(),20000);
    try{
      const catalog=await readJSON(`${API}/api/series/index.json`,{signal});if(catalog.schema!=='seiche.series-index.v1'||!Array.isArray(catalog.series))throw new Error('Series catalog unavailable.');
      let failures=0;
      // Only explicit, redistributable catalog members are requested.
      for(let i=0;i<SERIES.length;i+=2){await Promise.all(SERIES.slice(i,i+2).map(async([key])=>{try{const entry=catalog.series.find(row=>row.mnemonic===key);if(!entry?.available||entry.csv_restricted||entry.json!==`/api/series/${key}`)throw new Error('Unavailable');const row=seriesModel(await readJSON(`${API}${entry.json}?n=120`,{signal}),entry);if(!disposed)models.set(key,row);}catch{failures++;}}));}
      if(disposed)return;if(!models.has(selected))selected=models.keys().next().value||'SOFR';render();notice.textContent=failures?`${failures} series could not be refreshed. Any retained observations keep their original dates.`:`Checked ${new Date().toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',timeZone:'UTC'})} UTC. Hover or use arrow keys to inspect the chart.`;if(failures)notice.setAttribute('role','status');else notice.removeAttribute('role');
    }catch{if(!disposed){notice.textContent='Economic data could not be refreshed. Existing observations retain their original dates.';notice.setAttribute('role','status');}}
    finally{clearTimeout(timer);controller=null;}
  }
  const stop=visibleRefresh(refresh);return()=>{disposed=true;stop();controller?.abort();};
}
if(typeof customElements!=='undefined'&&!customElements.get('economic-context'))customElements.define('economic-context',class extends HTMLElement{connectedCallback(){this.classList.add('rw-economic-strip');this.stop=mountEconomy(this);}disconnectedCallback(){this.stop?.();}});
