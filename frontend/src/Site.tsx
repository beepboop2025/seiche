import { lazy, Suspense, useEffect, useState } from "react";
import ResearchHeader from "./ResearchHeader";
import { ConnectedUpgrades, useSeicheVersion } from "./ProductHome";
import "./product-home.css";
import "./audience-experience.css";
import { FundingNow } from "./desk/RatesDesk";
import PublishedRecord from "./PublishedRecord";
const Terminal=lazy(()=>import("./App"));
const COLAB_OFR="https://colab.research.google.com/github/beepboop2025/seiche/blob/26e0e053570934d2b0fc779428872b953c2f75a7/notebooks/seiche_direct_ofr_research.ipynb";
type Shipped={label:string;title:string;text:string;links:readonly {href:string;action:string}[]};
/** Recent changes, each linked to where it can be inspected. No counts are written here by hand. */
const SHIPPED:readonly Shipped[]=[
  {label:"Funding desk",title:"Official rates, curves and spreads",text:"The desk opens on dated observations: ECB government spot curves, the €STR distribution and its compounded averages, and Bank of Canada CORRA with its policy, bill and sovereign references. Compare curves across dates and spreads over a window you choose.",links:[{href:"#today",action:"Open the funding desk"}]},
  {label:"Evidence pages",title:"Methodology and audits in one interface",text:"Methodology, Skeptic, Ampleness and Referee now share the research interface used across LiquiLens and Undertow, on desktop and on phones.",links:[{href:"/methodology",action:"Read the methodology"},{href:"/skeptic",action:"Open the Skeptic pack"}]},
  {label:"Source rights",title:"What is served, and what is withheld",text:"Parts of the funding board are public. Readings that depend on sources not yet qualified are withheld, and legacy forecasts, stress scores, backtests and replay stay unavailable pending source qualification. Funding Pop is the one exception, shown with its research-only label.",links:[{href:"/guide",action:"How to read Seiche"}]},
  {label:"Agents",title:"MCP results that present their evidence",text:"Each MCP result with structured content now adds a plain-text block that presents its readings the way every family surface does, built only from the result itself.",links:[{href:"/developers",action:"API and MCP documentation"},{href:"https://beepboop2025.github.io/financial-evidence-skills/tools/",action:"Set up OpenBB, Excel or Power BI"}]},
  {label:"Notebooks",title:"Reproduce the research yourself",text:"Open the historical OFR money-market notebook directly in Colab, or rerun the ECB reference-rate holiday check from the repository.",links:[{href:COLAB_OFR,action:"Open the OFR notebook in Colab"},{href:"https://github.com/beepboop2025/seiche/blob/main/notebooks/seiche_ecb_reference_holiday.ipynb",action:"ECB holiday check"}]},
  {label:"Free and open",title:"Public research, open source",text:"Seiche needs no account or API key. The code, the methods and the full record of changes are public on GitHub.",links:[{href:"https://github.com/beepboop2025/seiche/blob/main/CHANGELOG.md",action:"Read the changelog"},{href:"https://github.com/beepboop2025/seiche",action:"Browse the code"}]},
];
/** The release line reads the live API; it renders nothing until a release is actually reported. */
function EntryFacts(){
  const version=useSeicheVersion();
  return <ul className="audience-facts" aria-label="About this research">
    {version && <li><span>Live release</span><strong>Seiche {version}</strong></li>}
    <li><span>Every reading</span><strong>Keeps its source and observation date</strong></li>
    <li><span>Missing evidence</span><strong>Stays visible, never filled in</strong></li>
    <li><span>Open source</span><strong><a href="https://github.com/beepboop2025/seiche">Read the code on GitHub</a></strong></li>
  </ul>;
}
function RecentlyShipped(){
  return <section className="audience-new" aria-labelledby="recently-shipped-title">
    <div className="rw-section-title"><h2 id="recently-shipped-title">New on Seiche</h2><p>Recent changes, each with a link to inspect it.</p></div>
    <div className="audience-new-grid">{SHIPPED.map((item)=><article key={item.title}><p className="audience-new-label">{item.label}</p><h3>{item.title}</h3><p>{item.text}</p><p className="audience-new-links">{item.links.map((link)=><a key={link.href} href={link.href}>{link.action}</a>)}</p></article>)}</div>
  </section>;
}
/** One persistent frame for the entry, every named tool and unavailable states. */
export default function Site(){
  const [entry, setEntry] = useState(() => !window.location.hash);
  useEffect(() => { const update = () => setEntry(!window.location.hash); window.addEventListener("hashchange", update); return () => window.removeEventListener("hashchange", update); }, []);
  useEffect(()=>{document.documentElement.classList.add("research-interface");document.documentElement.dataset.product="seiche";document.documentElement.dataset.surface="desk";},[]);
  return <><ResearchHeader/>{entry && <>
    <section className="research-page audience-hero" aria-labelledby="company-entry-heading">
      <div><p className="audience-kicker">Seiche / funding and money markets</p><h1 id="company-entry-heading">Follow funding.<br/>See the evidence.</h1><p className="audience-lede">Bring published rates, balances and source dates into your next treasury or research review. Explore the funding picture, then inspect the observations behind it.</p><div className="audience-actions"><a href="https://beepboop2025.github.io/financial-evidence-skills/funding/">Open the funding review</a><a href="https://beepboop2025.github.io/financial-evidence-skills/connect/?task=funding">Connect API or MCP</a></div><p className="audience-access">Public research. No account or API key.<br/>System funding context; each source retains its own observation date.</p></div>
      <div className="audience-preview"><FundingNow compact /></div>
    </section>
    <div className="research-page"><EntryFacts/><nav className="audience-paths" aria-label="Start with your work"><a href="#today"><span>Treasury and research</span><strong>Open the funding desk</strong><small>Review the published context, source clocks and availability.</small></a><a href="https://beepboop2025.github.io/financial-evidence-skills/media/"><span>Newsrooms and publications</span><strong>Check a funding claim</strong><small>Download a dated chart and reproduce its underlying calculation.</small></a><a href="https://beepboop2025.github.io/financial-evidence-skills/partners/dollar-funding.html"><span>Platforms and data teams</span><strong>Evaluate one useful workflow</strong><small>Inspect the API contract and a small research integration.</small></a></nav><RecentlyShipped/><section className="rw-follow" aria-labelledby="funding-questions-title">
      <div className="rw-section-title"><h2 id="funding-questions-title">Funding questions, with the evidence attached</h2><p>Learn what the rates and balances can tell you before interpreting the funding board.</p></div>
      <div className="rw-follow-grid">
        <article><h3><a href="/questions/why-sofr-above-iorb/">Why can SOFR trade above IORB?</a></h3><p>Compare a secured market rate with interest on reserve balances, using a dated worked example.</p></article>
        <article><h3><a href="/questions/sofr-iorb-spread/">How is the SOFR-IORB spread calculated?</a></h3><p>Check matching observation dates, percentage points and basis points before drawing a conclusion.</p></article>
        <article><h3><a href="/questions/are-bank-reserves-scarce/">Are bank reserves scarce?</a></h3><p>Read reserve balances alongside funding rates and facilities; one aggregate cannot establish scarcity.</p></article>
      </div>
      <p><a href="/questions/">All funding questions →</a> · <a href="/use-cases/money-market-research/">Money-market research guide</a></p>
      <p>Bring the same evidence into your workflow: <a href="https://agents.seiche.info/v1/source-data/?product=seiche">inspect dated funding histories</a> or <a href="https://beepboop2025.github.io/financial-evidence-skills/agents/low-latency.md">receive source-status updates</a>. Status updates describe source freshness and permissions; they are not live market quotes.</p>
    </section><PublishedRecord/><section className="rw-follow audience-contact" aria-labelledby="practitioner-review-title">
      <div className="rw-section-title"><h2 id="practitioner-review-title">Bring a funding question from your work</h2><p>For treasury, finance, risk and research teams.</p></div>
      <p>Share a recurring task, what makes it difficult and what a useful result would look like. We would welcome a 20-minute video call or an in-person discussion in Jaipur. General examples are enough; no confidential records are needed.</p>
      <p>If there is a fit, we can explore a small product evaluation and future collaboration.</p>
      <div className="audience-actions"><a href="mailto:mrinal@liquilens.in?subject=Seiche%20practitioner%20discussion">Discuss a research workflow with Mrinal</a><a href="/use-cases/money-market-research/">Read the money-market research guide</a></div>
    </section></div>
    </>}<Suspense fallback={<main id="main" className="research-page"><p className="rw-loading">Opening the funding research workspace…</p></main>}><Terminal/></Suspense>{entry && <div className="research-page"><ConnectedUpgrades /></div>}</>;
}
