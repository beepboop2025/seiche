import { useEffect, useRef } from "react";
import { mountEditorial } from "./family-editorial.js";
import "./family-editorial.css";

export default function EditorialPreview() {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (root.current) return mountEditorial(root.current, { initialProduct: "seiche" });
  }, []);
  return <section className="product-section product-editorial" aria-labelledby="editorial-heading"><div className="product-section__intro"><h2 id="editorial-heading">From the desks.</h2><p>Read the research across Seiche, Undertow, LiquiLens and MyQuant. Each published edition keeps its date and source.</p></div><div ref={root}><p><a href="https://myquantdoesntspeakenglish.com/">Read the public editorial archive</a> or <a href="/articles/">open Seiche research</a>.</p></div></section>;
}
