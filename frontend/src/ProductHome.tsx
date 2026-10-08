import "./family-upgrades.css";
import { useEffect, useState } from "react";
import FundingPreview from "./FundingPreview";
import EditorialPreview from "./EditorialPreview";

const channels = [
  { title: "Cash supply", description: "Follow reserves, central-bank facilities and the funding calendar.", route: "#board", action: "Inspect the funding board" },
  { title: "Money markets", description: "Compare funding observations across markets, with their own dates and definitions.", route: "#money%20markets", action: "Explore money markets" },
  { title: "Market context", description: "Trace connections to currencies and markets, then open the observations behind them.", route: "#workbench", action: "Open the market workbench" },
] as const;

export function FamilyNav({ desk = false }: { desk?: boolean }) {
  return <header className={`product-nav${desk ? " product-nav--desk" : ""}`}>
    <nav className="product-nav__inner" aria-label="Product navigation">
      <a className="product-brand" href={desk ? "#" : "/"}>Seiche<span className="product-brand__mark" aria-hidden="true">≈</span></a>
      <div className="product-nav__links">
        <a href="https://liquilens.in/">LiquiLens</a><a href="https://liquilens-undertow.com/">Undertow</a>
        <a href="/developers">For developers</a><a className="product-nav__action" href="#today">Open the desk</a>
      </div>
    </nav>
  </header>;
}

function FundingFlow() {
  const [selected, setSelected] = useState(0);
  const [paused, setPaused] = useState(false);
  const [reduced, setReduced] = useState(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return <figure className={`funding-flow${paused || reduced ? " funding-flow--paused" : ""}`}>
    <div className="funding-flow__drawing">
      <svg viewBox="0 0 1000 400" role="img" aria-labelledby="funding-flow-title funding-flow-desc">
        <title id="funding-flow-title">Connected funding channels</title>
        <desc id="funding-flow-desc">An illustrative network connects cash supply, money markets and wider market context. This is not live market data.</desc>
        <defs>
          <linearGradient id="flow-light" x1="0" x2="1"><stop offset="0" stopColor="#8aafff" stopOpacity=".02"/><stop offset=".5" stopColor="#8aafff" stopOpacity=".7"/><stop offset="1" stopColor="#8aafff" stopOpacity=".02"/></linearGradient>
        </defs>
        {[0, 1, 2].map((channel) => <g key={channel} className={selected === channel ? "flow-channel flow-channel--selected" : "flow-channel"}>
          {Array.from({ length: 9 }, (_, index) => {
            const start = 70 + channel * 125 + (index - 4) * 5;
            const middle = 190 + (index - 4) * 5;
            const end = 70 + ((channel + 1) % 3) * 125 + (index - 4) * 5;
            const path = `M 10 ${start} C 220 ${start}, 290 ${middle}, 500 ${middle} S 780 ${end}, 990 ${end}`;
            return <g key={index}><path d={path} className="flow-strand"/><path d={path} className="flow-current" style={{ animationDelay: `${(index + channel * 3) * -.53}s` }}/></g>;
          })}
        </g>)}
        <rect x="406" y="133" width="188" height="112" rx="56" className="flow-lens"/>
        <text x="500" y="184" textAnchor="middle" className="flow-name">Seiche</text>
        <text x="500" y="207" textAnchor="middle" className="flow-subtitle">Funding context</text>
      </svg>
    </div>
    <div className="funding-flow__controls" role="group" aria-label="Explore funding channels">
      {channels.map((channel, index) => <button key={channel.title} aria-pressed={selected === index} onClick={() => setSelected(index)}>{channel.title}</button>)}
    </div>
    <figcaption><span>Illustration of connected funding markets.</span><button className="flow-pause" onClick={() => setPaused(!paused)} disabled={reduced} aria-pressed={paused || reduced}>{reduced ? "Reduced motion" : paused ? "Play motion" : "Pause motion"}</button></figcaption>
  </figure>;
}

export default function ProductHome() {
  return <div className="product-home">
    <a className="product-skip" href="#product-content">Skip to content</a>
    <FamilyNav />
    <main id="product-content">
      <section className="product-hero">
        <h1>Follow the flow<br/>of funding.</h1>
        <p>Follow funding conditions across money markets, with a deep USD desk and a broader source-dated market atlas. Built for treasury teams, risk reviewers and AI agents working across borders.</p>
        <div className="product-actions"><a className="product-button" href="#today">Open the desk</a><a className="product-link" href="/developers">Connect an agent <span aria-hidden="true">↗</span></a></div>
        <FundingFlow />
      </section>
      <FundingPreview />
      {/* CONNECTED-UPGRADES:START */}
<section className="family-upgrades" aria-labelledby="connected-upgrades-heading" data-upgrade-revision="9d5d4c9cd904dd25aa21ee59370ccbd8310ff6d2ecc7b98faeb2ed1cad3e290f"><div className="family-upgrades-intro"><h2 id="connected-upgrades-heading">A connected review, from evidence to follow-up</h2><p>Review the institution, check funding conditions, inspect market liquidity, then retain the evidence for the next review.</p><a href="https://liquilens.in/updates/">What changed and what each tool can do</a></div><div className="family-upgrades-grid"><article><h3>Keep missing evidence in the review</h3><p>Inspect source-dated changes, overdue fields and visibility gaps across tracked banks and lenders. Open the live review queue and its original disclosures.</p><small>Available for research</small><a href="https://liquilens.in/banking/monitoring/">Open institution monitoring</a></article><article><h3>Make the review repeatable</h3><p>Connect the Research Desk, schedule bounded research and retain source records with operating diagnostics and recovery guidance.</p><small>Available for research</small><a href="https://liquilens.in/agents/infrastructure/">Set up recurring research</a></article><article><h3>Inspect changes before interpreting them</h3><p>Use NoiseFloor 0.3.1 to examine caller-supplied market series and headlines. Preserve measure identity, source restrictions and gaps.</p><small>Available for caller-supplied evidence</small><a href="https://liquilens.in/agents/infrastructure/#noisefloor">Connect NoiseFloor</a></article><article><h3>Rehearse the controls around an action</h3><p>Explore the paper workbench: inspect refusals, duplicate protection, STOP and reconciliation before considering an execution workflow.</p><small>Paper rehearsal; execution disabled</small><a href="https://beepboop2025.github.io/financial-evidence-skills/agents/execution.html">Explore the paper workbench</a></article></div><p className="family-upgrades-boundary">Each source keeps its dates, coverage and permissions. These tools support human review; they do not produce a combined risk score or authorize credit or trading.</p></section>
{/* CONNECTED-UPGRADES:END */}
      <EditorialPreview />
      <section className="product-section product-capabilities" aria-labelledby="capabilities-heading">
        <div className="product-section__intro"><h2 id="capabilities-heading">See where<br/>pressure begins.</h2><p>Start with a question. Move from the funding picture to the observations that explain it.</p></div>
        <div className="product-task-list">{channels.map((channel) => <article key={channel.title}><h3>{channel.title}</h3><p>{channel.description}</p><a href={channel.route}>{channel.action}<span aria-hidden="true">↗</span></a></article>)}</div>
      </section>
      <section className="product-evidence-band">
        <div className="product-section"><h2>The context.<br/>And the evidence.</h2><div><p className="product-large-copy">Every market has its own clock. Keep the observation date, source and gaps in view as you work.</p><p>Use the desk for the current picture, the atlas for individual series, and the published record to revisit what was known. Coverage and availability remain visible.</p><div className="product-inline-links"><a href="#corpus">Explore the market atlas</a><a href="/articles/">Read the research</a><a href="/guide">How to read Seiche</a></div></div></div>
      </section>
      <section className="product-section product-agent-section">
        <div><h2>Works where<br/>you work.</h2><p>Bring funding context into an existing review or agent workflow through Seiche’s API and MCP.</p><a className="product-button" href="/developers">Connect an agent</a></div>
        <div className="product-agent-diagram" role="img" aria-label="Seiche evidence flows to a reviewer, an AI agent, or an existing workflow"><div className="agent-source">Seiche<span>Funding evidence</span></div><div className="agent-outputs"><span>Risk review</span><span>AI agents</span><span>Your workflow</span></div></div>
      </section>
      <section className="product-section product-family" aria-labelledby="family-heading">
        <h2 id="family-heading">One exposure.<br/>Three connected questions.</h2><div className="product-family__products"><a href="https://liquilens.in/"><h3>LiquiLens</h3><p>What is changing at the bank or lender? Inspect its public disclosures and institution-risk evidence.</p><span>Explore LiquiLens ↗</span></a><div><h3>Seiche</h3><p>What funding environment surrounds it? Follow system funding and money-market pressure.</p><span>You are here</span></div><a href="https://liquilens-undertow.com/"><h3>Undertow</h3><p>What does liquidity look like at your size? Inspect market depth and stated-size exit evidence.</p><span>Explore Undertow ↗</span></a></div>
        <p>Three products from one registered company, connected through browser, API and MCP workflows. Source clocks and market coverage stay separate. <a href="https://liquilens.in/start/">Build a connected review ↗</a></p>
      </section>
    </main>
    <footer className="product-footer"><p>Seiche provides research context. It does not determine an institution’s regulatory compliance or promise a market outcome.</p><nav aria-label="Supporting information"><a href="/developers">API and MCP</a><a href="/support">Support</a><a href="/methodology">Methodology</a><a href="https://github.com/beepboop2025/seiche">Open source</a><a href="/privacy">Privacy</a><a href="https://liquilens.in/investors/">Company &amp; investors</a></nav><span>Seiche · LIQUILENS PRIVATE LIMITED · registered company in India</span></footer>
  </div>;
}
