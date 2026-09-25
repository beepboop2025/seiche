import { useEffect, useState } from "react";
import { terminalTabFromHash, type TerminalTab } from "./productRoutes";
import WorkspaceNavigation from "./WorkspaceNavigation";
export default function ResearchHeader() {
  const [tab,setTab]=useState<TerminalTab>(()=>terminalTabFromHash(location.hash)??"TODAY");
  const [institution,setInstitution]=useState<string|null>(()=>{const values=new URLSearchParams(location.search).getAll("institution");return values.length===1&&values[0].length<=96&&/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(values[0])?values[0]:null;});
  const contextLink=(href:string)=>{if(!institution)return href;const url=new URL(href,location.href);url.searchParams.set("institution",institution);return url.href;};
  const clearContext=()=>{const url=new URL(location.href);url.searchParams.delete("institution");history.replaceState(null,"",url);setInstitution(null);};
  useEffect(()=>{const navigate=()=>{const next=terminalTabFromHash(location.hash);if(next||!location.hash)setTab(next??"TODAY");};window.addEventListener("hashchange",navigate);return()=>window.removeEventListener("hashchange",navigate);},[]);
  return <><a className="research-skip" href="#main" onClick={event=>{event.preventDefault();const main=document.getElementById("main");main?.setAttribute("tabindex","-1");main?.focus();main?.scrollIntoView();}}>Skip to content</a><header className="research-header"><div className="research-mast">
    <a className="research-identity" href={contextLink("/")}><svg viewBox="0 0 36 36" aria-hidden="true"><circle cx="18" cy="18" r="14"/><path d="M7 17c5-8 9 8 14 0s7-5 8-2M7 23c5-8 9 8 14 0s7-5 8-2"/></svg><span><strong>Seiche</strong><small>Funding intelligence</small></span></a>
    <nav className="research-network" aria-label="Products"><a href={contextLink("https://liquilens.in/")}>LiquiLens</a><a href={contextLink("/")} aria-current="page">Seiche</a><a href={contextLink("https://liquilens-undertow.com/")}>Undertow</a></nav><a className="research-api" href="/developers">API &amp; agents</a></div>
    <WorkspaceNavigation tab={tab} goTab={target=>{location.hash=encodeURIComponent(target.toLowerCase());}}
      contextLink={contextLink} openCommands={()=>document.dispatchEvent(new Event("seiche:open-commands"))}
      openHelp={()=>document.dispatchEvent(new Event("seiche:open-help"))}/>
    {institution&&<div className="research-context"><span>Institution review</span><a href={contextLink("https://liquilens.in/")}>Return to institution evidence →</a><button type="button" aria-label="Clear institution review context" onClick={clearContext}>Clear</button></div>}
  </header></>;
}
