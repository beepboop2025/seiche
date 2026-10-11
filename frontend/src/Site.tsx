import { lazy, Suspense, useEffect, useState } from "react";
import ResearchHeader from "./ResearchHeader";
import { ConnectedUpgrades } from "./ProductHome";
import "./product-home.css";
import "./audience-experience.css";
import { fundingPreview } from "./funding-preview";
const Terminal=lazy(()=>import("./App"));
/** One persistent frame for the entry, every named tool and unavailable states. */
export default function Site(){
  const [entry, setEntry] = useState(() => !window.location.hash);
  useEffect(() => { const update = () => setEntry(!window.location.hash); window.addEventListener("hashchange", update); return () => window.removeEventListener("hashchange", update); }, []);
  useEffect(()=>{document.documentElement.classList.add("research-interface");document.documentElement.dataset.product="seiche";document.documentElement.dataset.surface="desk";},[]);
  return <><ResearchHeader/>{entry && <>
    <section className="research-page audience-hero" aria-labelledby="company-entry-heading">
      <div><p className="audience-kicker">Seiche / funding and money markets</p><h1 id="company-entry-heading">Follow funding.<br/>See the evidence.</h1><p className="audience-lede">Bring published rates, balances and source dates into your next treasury or research review. Explore the funding picture, then inspect the observations behind it.</p><div className="audience-actions"><a href="https://beepboop2025.github.io/financial-evidence-skills/funding/">Open the funding review</a><a href="https://beepboop2025.github.io/financial-evidence-skills/connect/?task=funding">Connect API or MCP</a></div><p className="audience-access">Public research. No account or API key.<br/>System funding context; each source retains its own observation date.</p></div>
      <figure className="audience-preview"><div className="audience-preview-top"><span>Inside the research</span><span>Dated funding example</span></div><h2>A funding question.<br/>A comparison you can check.</h2><img src={fundingPreview} alt="SOFR, IORB and the SOFR 99th percentile on 8 October 2026" width="900" height="520"/><figcaption>Historical example: 8 October 2026.<br/>Inspect the calculation, chart data and original publisher sources.</figcaption><a href="https://beepboop2025.github.io/financial-evidence-skills/media/briefs/2026-10-11-dollar-funding/">Read the dated briefing</a></figure>
    </section>
    <div className="research-page"><nav className="audience-paths" aria-label="Start with your work"><a href="#today"><span>Treasury and research</span><strong>Open the funding desk</strong><small>Review the published context, source clocks and availability.</small></a><a href="https://beepboop2025.github.io/financial-evidence-skills/media/"><span>Newsrooms and publications</span><strong>Check a funding claim</strong><small>Download a dated chart and reproduce its underlying calculation.</small></a><a href="https://beepboop2025.github.io/financial-evidence-skills/partners/dollar-funding.html"><span>Platforms and data teams</span><strong>Evaluate one useful workflow</strong><small>Inspect the API contract and a small research integration.</small></a></nav></div>
    </>}<Suspense fallback={<main id="main" className="research-page"><p className="rw-loading">Opening the funding research workspace…</p></main>}><Terminal/></Suspense>{entry && <div className="research-page"><ConnectedUpgrades /></div>}</>;
}
