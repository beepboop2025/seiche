import { isValidAddress, ALGORAND_ZERO_ADDRESS_STRING } from '@algorandfoundation/algokit-utils/common';
import { HTTPFacilitatorClient, type FacilitatorClient } from '@x402/core/server';
import { SettleError } from '@x402/core/types';
import type { Network, PaymentPayload, PaymentRequirements, SupportedResponse } from '@x402/core/types';
import { canonical, record } from './evidence.js';
export const FACILITATOR = 'https://facilitator.goplausible.xyz';
export const PRICE_ATOMIC = '10000';
export const CHALLENGE_TAG = 'x402-global-challenge';
// Current CAIP2 and legacy GoPlausible aliases; select only what the facilitator advertises.
export const NETWORKS = {
  mainnet: { network: 'algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=' as Network, asset: '31566704' },
  testnet: { network: 'algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=' as Network, asset: '10458941' },
};
export interface Env { SEICHE_ALGORAND_NETWORK?: string; SEICHE_ALGORAND_PAY_TO?: string; SEICHE_PUBLIC_ORIGIN?: string }
export function configuration(env: Env) {
  const networkName = env.SEICHE_ALGORAND_NETWORK;
  const payTo = env.SEICHE_ALGORAND_PAY_TO ?? '';
  const origin = env.SEICHE_PUBLIC_ORIGIN ?? '';
  if (networkName !== 'testnet' && networkName !== 'mainnet') throw new Error('network_not_configured');
  if (!isValidAddress(payTo) || payTo === ALGORAND_ZERO_ADDRESS_STRING) throw new Error('receiving_address_invalid');
  let parsed: URL; try { parsed = new URL(origin); } catch { throw new Error('public_origin_invalid'); }
  if (parsed.protocol !== 'https:' || parsed.origin !== origin || parsed.username || parsed.password) throw new Error('public_origin_invalid');
  return { ...NETWORKS[networkName], networkName: networkName as "testnet" | "mainnet", payTo, origin, amount: PRICE_ATOMIC };
}
export type Config = ReturnType<typeof configuration>;
export function selectNetwork(supported: SupportedResponse, target: 'mainnet' | 'testnet'): Network {
  const legacy = NETWORKS[target].network;
  const canonicalId = legacy.slice(0, 'algorand:'.length + 32) as Network;
  const found = [canonicalId, legacy].find(network => supported.kinds.some(k => k.x402Version === 2 && k.scheme === 'exact' && k.network === network));
  if (!found) throw new Error('facilitator_network_unsupported');
  return found;
}
function matches(payment: PaymentPayload, requirements: PaymentRequirements, config: Config): boolean {
  return payment.x402Version === 2 && canonical(payment.accepted) === canonical(requirements)
    && requirements.network === config.network && requirements.asset === config.asset
    && requirements.amount === PRICE_ATOMIC && requirements.payTo === config.payTo
    && requirements.extra.tag === CHALLENGE_TAG
    && (!payment.resource || payment.resource.url === `${config.origin}/v1/dossier`);
}
/** Pin facilitator/network/resource and validate receipt shape; never sign or custody funds. */
export function guardedFacilitator(config: Config, client: FacilitatorClient = new HTTPFacilitatorClient({ url: FACILITATOR, timeoutMs: 15_000 })): FacilitatorClient {
  return {
    async getSupported() {
      let out; try { out = await client.getSupported(); } catch { throw new Error("facilitator_unavailable"); }
      if (!out.kinds.some(k => k.x402Version === 2 && k.scheme === 'exact' && k.network === config.network)) throw new Error('facilitator_network_unsupported');
      if (!out.extensions.includes('bazaar')) throw new Error('facilitator_bazaar_unsupported');
      return out;
    },
    async verify(payment, requirements) {
      if (!matches(payment, requirements, config)) return { isValid: false, invalidReason: 'payment_requirements_mismatch' };
      let out; try { out = await client.verify(payment, requirements); } catch { return { isValid: false, invalidReason: "facilitator_verification_unavailable" }; }
      if (out.isValid && out.payer && !isValidAddress(out.payer)) return { isValid: false, invalidReason: 'facilitator_payer_invalid' };
      return out.isValid ? { isValid: true, payer: out.payer } : { isValid: false, invalidReason: "payment_not_verified" };
    },
    async settle(payment, requirements) {
      const failure = { success: false, errorReason: 'settlement_receipt_invalid', transaction: '', network: config.network };
      if (!matches(payment, requirements, config)) return failure;
      let out;
      try { out = await client.settle(payment, requirements); }
      catch (error) {
        if (error instanceof SettleError) out = { success: false, transaction: error.transaction, network: error.network, payer: error.payer };
        else return { ...failure, errorReason: 'settlement_outcome_unknown_do_not_retry' };
      }
      if (!out.success) return {
        ...failure, errorReason: 'settlement_outcome_unknown_do_not_retry',
        transaction: out.network === config.network && /^[A-Z2-7]{52}$/.test(out.transaction) ? out.transaction : '',
        ...(out.payer && isValidAddress(out.payer) ? { payer: out.payer } : {}),
      };
      // The facilitator may have settled despite an invalid/timeout response. Never retry automatically.
      if (out.network !== config.network || !/^[A-Z2-7]{52}$/.test(out.transaction)
        || (out.payer !== undefined && !isValidAddress(out.payer))) return failure;
      return { success: true, transaction: out.transaction, network: out.network, payer: out.payer };
    },
  };
}

/** Reject malformed envelopes before SDK parsers can log decoded header fragments. */
export function validPaymentHeader(header: string): boolean {
  if (!header || header.length > 16_384 || !/^[A-Za-z0-9+/]+={0,2}$/.test(header)) return false;
  try {
    const payment: unknown = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Uint8Array.from(atob(header), c => c.charCodeAt(0))));
    if (!record(payment) || payment.x402Version !== 2 || !record(payment.accepted) || !record(payment.payload)) return false;
    const a=payment.accepted, p=payment.payload;
    if (!['scheme','network','asset','amount','payTo'].every(k=>typeof a[k]==='string') || !Number.isInteger(a.maxTimeoutSeconds) || !record(a.extra)) return false;
    if (!Array.isArray(p.paymentGroup) || p.paymentGroup.length<1 || p.paymentGroup.length>16 || !p.paymentGroup.every(v=>typeof v==='string') || !Number.isInteger(p.paymentIndex)) return false;
    if (payment.resource !== undefined && (!record(payment.resource) || typeof payment.resource.url !== 'string')) return false;
    if (payment.extensions !== undefined && !record(payment.extensions)) return false;
    return true;
  } catch { return false; }
}
