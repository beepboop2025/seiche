import {element as el} from './core.mjs';

const NS='http://www.w3.org/2000/svg';
const node=(tag,attributes={},text)=>{const n=document.createElementNS(NS,tag);for(const [key,value] of Object.entries(attributes))n.setAttribute(key,String(value));if(text)n.textContent=text;return n;};
if(!customElements.get('research-flow'))customElements.define('research-flow',class extends HTMLElement{
  connectedCallback(){
    if(this.childNodes.length)return;
    const product=this.getAttribute('product')||document.documentElement.dataset.product||'liquilens';
    const label={liquilens:'LiquiLens',seiche:'Seiche',undertow:'Undertow'}[product]||'Research';
    const svg=node('svg',{viewBox:'0 0 640 180',role:'img','aria-label':'Illustration connecting institutions, funding and market liquidity.'});
    for(let channel=0;channel<3;channel++)for(let index=0;index<7;index++){
      const start=28+channel*58+(index-3)*4,middle=89+(index-3)*4,end=28+((channel+1)%3)*58+(index-3)*4;
      const path=`M0 ${start} C130 ${start},180 ${middle},320 ${middle} S500 ${end},640 ${end}`;
      svg.append(node('path',{d:path,class:'rw-flow-strand'}));
      const current=node('path',{d:path,class:'rw-flow-current',pathLength:100});current.style.animationDelay=`${(index+channel*3)*-.71}s`;svg.append(current);
    }
    svg.append(node('rect',{x:245,y:57,width:150,height:65,rx:32,class:'rw-flow-lens'}),node('text',{x:320,y:86,'text-anchor':'middle',class:'rw-flow-name'},label),node('text',{x:320,y:104,'text-anchor':'middle',class:'rw-flow-caption'},'Connected research'));
    const controls=el('div',undefined,'rw-flow-controls'),caption=el('span','Institutions · Funding · Markets'),pause=el('button','Pause motion');pause.type='button';pause.setAttribute('aria-pressed','false');
    const media=matchMedia('(prefers-reduced-motion: reduce)');let paused=false;
    const update=()=>{this.dataset.paused=String(paused||media.matches);pause.disabled=media.matches;pause.textContent=media.matches?'Reduced motion':paused?'Play motion':'Pause motion';pause.setAttribute('aria-pressed',String(paused||media.matches));};
    pause.addEventListener('click',()=>{paused=!paused;update();});media.addEventListener('change',update);this.cleanup=()=>media.removeEventListener('change',update);update();
    controls.append(caption,pause);this.append(svg,controls);
  }
  disconnectedCallback(){this.cleanup?.();this.replaceChildren();}
});
