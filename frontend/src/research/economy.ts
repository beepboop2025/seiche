import {mountFundingAnalytics} from './fundingAnalytics';

export const mountEconomy = mountFundingAnalytics;
class EconomicContext extends HTMLElement {
  private stop?: () => void;
  connectedCallback() {this.stop = mountFundingAnalytics(this);}
  disconnectedCallback() {this.stop?.();}
}
if (!customElements.get('economic-context')) customElements.define('economic-context', EconomicContext);
