const FEED = 'https://myquantdoesntspeakenglish.com/feed.json';
const HOSTS = new Set(['liquilens.in','api.liquilens.in','seiche.info','api.seiche.info','liquilens-undertow.com','myquantdoesntspeakenglish.com','palimpsest.info','www.palimpsest.info','narcoscope.com','www.narcoscope.com']);
const LABELS = {all:'All desks',liquilens:'LiquiLens',seiche:'Seiche','liquilens-undertow':'Undertow',myquant:'MyQuant',other:'Other desks'};
const safeUrl = value => {try{const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password&&HOSTS.has(u.hostname)?u.href:null;}catch{return null;}};
const string = value => typeof value === 'string' ? value : '';

export function editorialItems(feed) {
  if (feed?.version !== 'https://jsonfeed.org/version/1.1' || feed?._mqdnse?.schema !== 'mqdnse.web-feed.v1' || feed._mqdnse.authority !== 'PUBLIC_EDITORIAL_ARCHIVE' || !Array.isArray(feed.items)) throw new Error('The editorial archive could not be verified.');
  const seen = new Set();
  const headlines = new Set();
  return feed.items.flatMap(row => {
    const source = row?._mqdnse, evidence = source?.evidence;
    if (!row || source?.schema !== 'mqdnse.web-feed-item.v1' || !Object.hasOwn(LABELS,source.product) || ['all','other'].includes(source.product) || !['INTERPRETED','MYQUANT_ANALYSIS'].includes(source.lane) || evidence?.publicationStatus !== 'PUBLISHED' || source.sourceRecordId !== row.id || seen.has(row.id) || !string(row.title).trim() || !safeUrl(row.url) || new URL(row.url).hostname !== 'myquantdoesntspeakenglish.com' || !Number.isFinite(Date.parse(row.date_published)) || !string(evidence.limitation).trim() || row.summary !== source.copy?.inEnglish) return [];
    if (source.lane === 'INTERPRETED' && (row.external_url !== source.sourceUrl || !safeUrl(source.sourceUrl))) return [];
    seen.add(row.id);
    return [{id:row.id, product:source.product, title:row.title, summary:string(row.summary), date:row.date_published, url:row.url, original:source.lane==='INTERPRETED'?source.sourceUrl:null, limitation:evidence.limitation, lane:source.lane}];
  }).sort((a,b)=>Date.parse(b.date)-Date.parse(a.date)).filter(item => {
    const key = item.product + ":" + item.title.toLocaleLowerCase("en-US").replace(/\s+/g," ").trim();
    if (headlines.has(key)) return false;
    headlines.add(key); return true;
  });
}

function el(tag, className, text) {const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;}
function link(text,url) {const a=el('a','',text);a.href=url;a.target='_blank';a.rel='noopener noreferrer';return a;}
function dateText(value) {return new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'}).format(new Date(value));}
async function readFeed(signal) {
  const response=await fetch(FEED,{signal,credentials:'omit',redirect:'error',headers:{Accept:'application/json'}});
  if(!response.ok)throw new Error('The archive is unavailable right now.');
  const reader=response.body?.getReader();if(!reader)throw new Error('This browser cannot read the archive.');
  const chunks=[];let size=0;
  try{while(true){const {value,done}=await reader.read();if(done)break;size+=value.byteLength;if(size>2*1024*1024)throw new Error('The archive exceeds the preview limit.');chunks.push(value);}}catch(error){await reader.cancel().catch(()=>{});throw error;}
  const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.byteLength;}
  return editorialItems(JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes)));
}

/** Mount an isolated, accessible family editorial browser; returns full cleanup for React or static pages. */
export function mountEditorial(root,{initialProduct='all'}={}) {
  if(!root)return ()=>{};
  let selected=Object.hasOwn(LABELS,initialProduct)?initialProduct:'all',items=null,controller=null,disposed=false,loading=false,observer=null;
  const tabs=el('div','editorial-tabs');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','Editorial desks');
  const status=el('p','editorial-status','Published articles and plain-English editions. Select a desk to explore.');status.setAttribute('role','status');
  const panel=el('div','editorial-panel');panel.setAttribute('role','tabpanel');panel.tabIndex=0;
  const grid=el('div','editorial-grid');panel.append(grid);
  const footer=el('p','editorial-credit');footer.append('Excerpts from the public MyQuant archive. Publication dates describe the article, not every underlying observation. ',link('Visit the full archive','https://myquantdoesntspeakenglish.com/'));
  const token='editorial-'+Math.random().toString(36).slice(2,10);panel.id=token+'-panel';
  const buttons=Object.entries(LABELS).map(([key,label],index)=>{
    const button=el('button','',label);button.type='button';button.setAttribute('role','tab');button.id=token+'-'+key;button.setAttribute('aria-controls',panel.id);
    button.addEventListener('click',()=>{selected=key;render();void load();});
    button.addEventListener('keydown',event=>{let target;if(event.key==='ArrowRight')target=(index+1)%buttons.length;else if(event.key==='ArrowLeft')target=(index+buttons.length-1)%buttons.length;else if(event.key==='Home')target=0;else if(event.key==='End')target=buttons.length-1;else return;event.preventDefault();buttons[target].focus();buttons[target].click();});
    tabs.append(button);return button;
  });
  function render(){
    buttons.forEach((button,i)=>{const active=Object.keys(LABELS)[i]===selected;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;});panel.setAttribute('aria-labelledby',token+'-'+selected);grid.replaceChildren();
    if(selected==='other'){
      status.textContent='More research from the family. These links open the original publication.';
      for(const [name,description,url] of [['Palimpsest','Information control, network measurements and China evidence.','https://palimpsest.info/news/'],['NarcoScope','Official-source research on drug markets and illicit economies.','https://narcoscope.com/'],['The intelligence desk','Short findings with source records, open questions and evidence boundaries.','https://liquilens.in/desk/']]){const article=el('article','editorial-card');article.append(el('p','editorial-meta','Research publication'),el('h3','',name),el('p','editorial-summary',description),link('Visit publication',url));grid.append(article);}return;
    }
    if(!items){status.textContent=loading?'Loading the public editorial archive…':'Select a desk or scroll here to load published articles.';return;}
    const chosen=items.filter(item=>selected==='all'||item.product===selected).slice(0,3);
    status.textContent=chosen.length?'Published editions · '+LABELS[selected]+'. Source dates remain attached.':'No published edition is available for this desk in the archive.';
    for(const item of chosen){
      const article=el('article','editorial-card'),meta=el('p','editorial-meta');const time=el('time','',dateText(item.date));time.dateTime=item.date;meta.append(LABELS[item.product]+' · ',time);
      const heading=el('h3');heading.append(link(item.title,item.url));
      const summary=el('p','editorial-summary',item.summary.length>300?item.summary.slice(0,297).replace(/\s+\S*$/,'')+'…':item.summary);
      const edition=el('p','editorial-edition',item.lane==='INTERPRETED'?'Plain-English edition · MyQuant':'Original analysis · MyQuant');
      const links=el('div','editorial-links');links.append(link('Read article',item.url));if(item.original)links.append(link('Original desk',item.original));
      const limits=el('details','editorial-limits');limits.append(el('summary','','Evidence limits'),el('p','',item.limitation));
      article.append(meta,heading,summary,edition,links,limits);grid.append(article);
    }
  }
  async function load(){
    if(disposed||loading||items||selected==='other')return;
    loading=true;controller=new AbortController();const timeout=setTimeout(()=>controller?.abort(),12000);render();
    try{const result=await readFeed(controller.signal);if(disposed)return;items=result;render();}
    catch{if(!disposed){status.textContent='The editorial preview is unavailable. Open the archive to read the published editions.';const retry=el('button','editorial-retry','Try again');retry.type='button';retry.addEventListener('click',()=>void load());grid.replaceChildren(retry);}}
    finally{clearTimeout(timeout);loading=false;}
  }
  root.replaceChildren(tabs,status,panel,footer);render();
  if('IntersectionObserver' in window){observer=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting)){observer.disconnect();void load();}},{rootMargin:'180px'});observer.observe(root);}else void load();
  return ()=>{disposed=true;observer?.disconnect();controller?.abort();root.replaceChildren();};
}
