import { lazy, Suspense, useEffect } from "react";
import ResearchHeader from "./ResearchHeader";
import "./product-home.css";
const Terminal=lazy(()=>import("./App"));
/** One persistent frame for the entry, every named tool and unavailable states. */
export default function Site(){
  useEffect(()=>{document.documentElement.classList.add("research-interface");document.documentElement.dataset.product="seiche";document.documentElement.dataset.surface="desk";},[]);
  return <><ResearchHeader/><Suspense fallback={<main id="main" className="research-page"><p className="rw-loading">Opening the funding research workspace…</p></main>}><Terminal/></Suspense></>;
}
