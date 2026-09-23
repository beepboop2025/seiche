import { lazy, Suspense, useEffect, useState } from "react";
import ProductHome, { FamilyNav } from "./ProductHome";
import { terminalTabFromHash } from "./productRoutes";
import "./product-home.css";

const Terminal = lazy(() => import("./App"));

export default function Site() {
  const [terminal, setTerminal] = useState(() => terminalTabFromHash(window.location.hash) !== null);
  useEffect(() => {
    const navigate = () => setTerminal(terminalTabFromHash(window.location.hash) !== null);
    window.addEventListener("hashchange", navigate);
    return () => window.removeEventListener("hashchange", navigate);
  }, []);
  useEffect(() => {
    document.documentElement.dataset.surface = terminal ? "desk" : "product";
    window.scrollTo(0, 0);
  }, [terminal]);
  return terminal
    ? <><FamilyNav desk /><Suspense fallback={<main className="desk-loading"><p>Opening the funding desk…</p></main>}><Terminal /></Suspense></>
    : <ProductHome />;
}
