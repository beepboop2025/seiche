import {mountFundingAnalytics} from './fundingAnalytics';

export const mountEconomy = mountFundingAnalytics;
class EconomicContext extends HTMLElement {
  private stop?: () => void;
  private data?: unknown;
  get snapshot() {return this.data;}
  set snapshot(value: unknown) {this.data = value; this.dispatchEvent(new Event("funding-snapshot"));}
  connectedCallback() {this.stop = mountFundingAnalytics(this, () => this.data);}
  disconnectedCallback() {this.stop?.();}
}
if (!customElements.get('economic-context')) customElements.define('economic-context', EconomicContext);
