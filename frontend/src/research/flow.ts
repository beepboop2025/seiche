import {element as el, svgNode as node} from "./core";

class ResearchFlow extends HTMLElement {
  private cleanup?: () => void;

  connectedCallback() {
    if (this.childNodes.length) return;
    const product = this.getAttribute("product") || document.documentElement.dataset.product || "seiche";
    const labels: Record<string, string> = {liquilens: "LiquiLens", seiche: "Seiche", undertow: "Undertow"};
    const svg = node("svg", {viewBox: "0 0 640 180", role: "img", "aria-label": "Illustration connecting institutions, funding and market liquidity."});
    for (let channel = 0; channel < 3; channel++) for (let index = 0; index < 7; index++) {
      const start = 28 + channel * 58 + (index - 3) * 4, middle = 89 + (index - 3) * 4;
      const end = 28 + ((channel + 1) % 3) * 58 + (index - 3) * 4;
      const path = `M0 ${start} C130 ${start},180 ${middle},320 ${middle} S500 ${end},640 ${end}`;
      svg.append(node("path", {d: path, class: "rw-flow-strand"}));
      const current = node("path", {d: path, class: "rw-flow-current", pathLength: 100});
      current.style.animationDelay = `${(index + channel * 3) * -.71}s`;
      svg.append(current);
    }
    svg.append(node("rect", {x: 245, y: 57, width: 150, height: 65, rx: 32, class: "rw-flow-lens"}),
      node("text", {x: 320, y: 86, "text-anchor": "middle", class: "rw-flow-name"}, labels[product] || "Research"),
      node("text", {x: 320, y: 104, "text-anchor": "middle", class: "rw-flow-caption"}, "Connected research"));
    const controls = el("div", undefined, "rw-flow-controls"), pause = el("button", "Pause motion");
    pause.type = "button";
    const media = matchMedia("(prefers-reduced-motion: reduce)");
    let paused = false;
    const update = () => {
      this.dataset.paused = String(paused || media.matches);
      pause.disabled = media.matches;
      pause.textContent = media.matches ? "Reduced motion" : paused ? "Play motion" : "Pause motion";
      pause.setAttribute("aria-pressed", String(paused || media.matches));
    };
    pause.addEventListener("click", () => {paused = !paused; update();});
    media.addEventListener("change", update);
    this.cleanup = () => media.removeEventListener("change", update);
    update();
    controls.append(el("span", "Institutions · Funding · Markets"), pause);
    this.append(svg, controls);
  }

  disconnectedCallback() {this.cleanup?.(); this.replaceChildren();}
}

if (!customElements.get("research-flow")) customElements.define("research-flow", ResearchFlow);
