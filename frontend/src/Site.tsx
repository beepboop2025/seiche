import { lazy, Suspense, useEffect, useState } from "react";
import ResearchHeader from "./ResearchHeader";
import "./product-home.css";
const Terminal=lazy(()=>import("./App"));
/** One persistent frame for the entry, every named tool and unavailable states. */
export default function Site(){
  const [entry, setEntry] = useState(() => !window.location.hash);
  useEffect(() => { const update = () => setEntry(!window.location.hash); window.addEventListener("hashchange", update); return () => window.removeEventListener("hashchange", update); }, []);
  useEffect(()=>{document.documentElement.classList.add("research-interface");document.documentElement.dataset.product="seiche";document.documentElement.dataset.surface="desk";},[]);
  return <><ResearchHeader/>{entry && <section className="research-page research-company" aria-labelledby="company-entry-heading"><div><p className="research-company__eyebrow">Seiche / the funding view</p><h1 id="company-entry-heading">Follow funding across your exposure.</h1><p>A deep USD funding desk and a broader source-dated market atlas for treasury teams, risk reviewers and AI agents working across borders.</p><p className="research-company__connection">Connect the review: <a href="https://liquilens.in/">LiquiLens institution evidence</a> → <strong>Seiche funding conditions</strong> → <a href="https://liquilens-undertow.com/">Undertow market liquidity</a>.</p><p className="research-company__legal">Three products from LIQUILENS PRIVATE LIMITED, a registered company in India. International access; market-specific coverage. <a href="https://liquilens.in/investors/">Company &amp; investors ↗</a></p></div></section>}<Suspense fallback={<main id="main" className="research-page"><p className="rw-loading">Opening the funding research workspace…</p></main>}><Terminal/></Suspense></>;
}
