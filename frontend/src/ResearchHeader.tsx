import { useEffect, useRef, useState } from "react";
import { terminalTabFromHash, tabLabel, type TerminalTab } from "./productRoutes";
const primary: TerminalTab[] = ["TODAY", "MONEY MARKETS", "WORKBENCH", "CORPUS", "RESEARCH"];
const groups: {title:string; tabs:TerminalTab[]}[] = [
  {title:"Funding and markets",tabs:["BOARD","GLOBAL","FX×MATERIALS","OIL×FUNDING","SCARCITY","SUPPLY","MARKET","CALENDAR","POSITIONING"]},
  {title:"Research and history",tabs:["DISPATCHES","FORECAST","PHYSICS","HELM","RESONANCE","TIME MACHINE"]},
  {title:"Evidence and settings",tabs:["PROOF","REFEREE","SYSTEM","ACCOUNT"]},
];
export default function ResearchHeader() {
  const [tab,setTab]=useState<TerminalTab>(()=>terminalTabFromHash(location.hash)??"TODAY");
  const [institution,setInstitution]=useState<string|null>(()=>{const values=new URLSearchParams(location.search).getAll("institution");return values.length===1&&values[0].length<=96&&/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(values[0])?values[0]:null;});
  const contextLink=(href:string)=>{if(!institution)return href;const url=new URL(href,location.href);url.searchParams.set("institution",institution);return url.href;};
  const clearContext=()=>{const url=new URL(location.href);url.searchParams.delete("institution");history.replaceState(null,"",url);setInstitution(null);};
  const menu=useRef<HTMLDetailsElement>(null);
  useEffect(()=>{const navigate=()=>{const next=terminalTabFromHash(location.hash);if(next||!location.hash)setTab(next??"TODAY");if(menu.current)menu.current.open=false;};window.addEventListener("hashchange",navigate);return()=>window.removeEventListener("hashchange",navigate);},[]);
  const tool=(target:TerminalTab)=><a key={target} href={`#${encodeURIComponent(target.toLowerCase())}`} aria-current={target===tab?"page":undefined}>{tabLabel(target)}</a>;
  return <><a className="research-skip" href="#main" onClick={event=>{event.preventDefault();const main=document.getElementById("main");main?.setAttribute("tabindex","-1");main?.focus();main?.scrollIntoView();}}>Skip to content</a><header className="research-header"><div className="research-mast">
    <a className="research-identity" href={contextLink("/")}><svg viewBox="0 0 36 36" aria-hidden="true"><circle cx="18" cy="18" r="14"/><path d="M7 17c5-8 9 8 14 0s7-5 8-2M7 23c5-8 9 8 14 0s7-5 8-2"/></svg><span><strong>Seiche</strong><small>Funding intelligence</small></span></a>
    <nav className="research-network" aria-label="Products"><a href={contextLink("https://liquilens.in/")}>LiquiLens</a><a href={contextLink("/")} aria-current="page">Seiche</a><a href={contextLink("https://liquilens-undertow.com/")}>Undertow</a></nav><a className="research-api" href="/developers">API &amp; agents</a></div>
    <nav className="workspace-tabs research-desk-tabs" aria-label="Seiche sections">{primary.map(tool)}<details className="workspace-tools" ref={menu} onKeyDown={event=>{if(event.key==="Escape"&&menu.current){menu.current.open=false;menu.current.querySelector("summary")?.focus();}}}><summary>{primary.includes(tab)?"All tools":tabLabel(tab)}</summary><div className="workspace-tools__menu">{groups.map(group=><section key={group.title}><h2>{group.title}</h2>{group.tabs.map(tool)}{group.title==="Evidence and settings"&&<><a href={contextLink("/markets/")}>World markets</a><a href={contextLink("/articles/")}>Published research</a><a href={contextLink("/guide")}>How to read the evidence</a></>}</section>)}</div></details></nav>
    {institution&&<div className="research-context"><span>Institution review</span><a href={contextLink("https://liquilens.in/")}>Return to institution evidence →</a><button type="button" aria-label="Clear institution review context" onClick={clearContext}>Clear</button></div>}
  </header></>;
}
