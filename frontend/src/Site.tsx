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
    <div className="research-page"><nav className="audience-paths" aria-label="Start with your work"><a href="#today"><span>Treasury and research</span><strong>Open the funding desk</strong><small>Review the published context, source clocks and availability.</small></a><a href="https://beepboop2025.github.io/financial-evidence-skills/media/"><span>Newsrooms and publications</span><strong>Check a funding claim</strong><small>Download a dated chart and reproduce its underlying calculation.</small></a><a href="https://beepboop2025.github.io/financial-evidence-skills/partners/dollar-funding.html"><span>Platforms and data teams</span><strong>Evaluate one useful workflow</strong><small>Inspect the API contract and a small research integration.</small></a></nav><section className="rw-follow" aria-labelledby="funding-questions-title">
      <div className="rw-section-title"><h2 id="funding-questions-title">Funding questions, with the evidence attached</h2><p>Learn what the rates and balances can tell you before interpreting the funding board.</p></div>
      <div className="rw-follow-grid">
        <article><h3><a href="/questions/why-sofr-above-iorb/">Why can SOFR trade above IORB?</a></h3><p>Compare a secured market rate with interest on reserve balances, using a dated worked example.</p></article>
        <article><h3><a href="/questions/sofr-iorb-spread/">How is the SOFR-IORB spread calculated?</a></h3><p>Check matching observation dates, percentage points and basis points before drawing a conclusion.</p></article>
        <article><h3><a href="/questions/are-bank-reserves-scarce/">Are bank reserves scarce?</a></h3><p>Read reserve balances alongside funding rates and facilities; one aggregate cannot establish scarcity.</p></article>
      </div>
      <p><a href="/questions/">All funding questions →</a> · <a href="/use-cases/money-market-research/">Money-market research guide</a></p>
      <p>Bring the same evidence into your workflow: <a href="https://agents.seiche.info/v1/source-data/?product=seiche">inspect dated funding histories</a> or <a href="https://beepboop2025.github.io/financial-evidence-skills/agents/low-latency.md">receive source-status updates</a>. Status updates describe source freshness and permissions; they are not live market quotes.</p>
    </section><section className="rw-follow" aria-labelledby="practitioner-review-title">
      <div className="rw-section-title"><h2 id="practitioner-review-title">Bring a funding question from your work</h2><p>For treasury, finance, risk and research teams.</p></div>
      <p>Share a recurring task, what makes it difficult and what a useful result would look like. We would welcome a 20-minute video call or an in-person discussion in Jaipur. General examples are enough; no confidential records are needed.</p>
      <p>If there is a fit, we can explore a small product evaluation and future collaboration.</p>
      <p><a href="mailto:mrinal@liquilens.in?subject=Seiche%20practitioner%20discussion">Discuss a research workflow with Mrinal</a> · <a href="/guide">Read the practical guide</a></p>
    </section></div>
    </>}<Suspense fallback={<main id="main" className="research-page"><p className="rw-loading">Opening the funding research workspace…</p></main>}><Terminal/></Suspense>{entry && <div className="research-page"><ConnectedUpgrades /></div>}</>;
}
