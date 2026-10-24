import { Hono, type MiddlewareHandler } from 'hono';
import { paymentMiddleware } from '@x402/hono';
import { HTTPFacilitatorClient, x402ResourceServer, type FacilitatorClient } from '@x402/core/server';
import { ExactAvmScheme } from '@x402/avm/exact/server';
import type { ResourceServerExtension } from '@x402/core/types';
import { declareDiscoveryExtension, bazaarResourceServerExtension } from '@x402-avm/extensions/bazaar';
import { assemble, preview, SOURCES, type Dossier, type Fetcher } from './evidence.js';
import { configuration, guardedFacilitator, validPaymentHeader, selectNetwork, FACILITATOR, CHALLENGE_TAG, PRICE_ATOMIC, type Env } from './payment.js';
import { PAGE } from './page.js';
export type AppEnv = { Bindings: Env; Variables: { dossier: Dossier } };
export function createApp(options: { env: Env; facilitator?: FacilitatorClient; fetcher?: Fetcher; now?: () => Date }) {
  const app = new Hono<AppEnv>();
  let cached: { until: number; value: Dossier } | undefined;
  let pending: Promise<Dossier> | undefined;
  const clock = options.now ?? (() => new Date());
  async function getDossier() {
    const now = clock();
    if (cached && cached.until > now.getTime()) return cached.value;
    if (!pending) pending = assemble(options.fetcher, now).then(value => { cached = { until: now.getTime() + 30_000, value }; return value; }).finally(() => { pending = undefined; });
    return pending;
  }
  let config: ReturnType<typeof configuration> | undefined;
  try { config = configuration(options.env); } catch { /* Dormant until explicit valid operator config. */ }
  app.use('*', async (c, next) => {
    const securityHeaders = () => {
    c.header('Cache-Control', 'no-store'); c.header('X-Content-Type-Options', 'nosniff');
    c.header('Referrer-Policy', 'no-referrer');
    c.header('Access-Control-Allow-Origin', '*');
    c.header('Access-Control-Expose-Headers', 'PAYMENT-REQUIRED, PAYMENT-RESPONSE');
    c.header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'");
    };
    securityHeaders();
    if (c.req.method === 'OPTIONS') { c.header('Access-Control-Allow-Methods', 'GET, OPTIONS'); c.header('Access-Control-Allow-Headers', 'PAYMENT-SIGNATURE, Content-Type'); return c.body(null, 204); }
    if (c.req.method !== 'GET') return c.json({ error: 'method_not_allowed' }, 405);
    await next();
    const paymentResponse = c.res.headers.get('PAYMENT-RESPONSE');
    if (c.res.status === 402 && paymentResponse) {
      try {
        const receipt = JSON.parse(atob(paymentResponse));
        if (['settlement_outcome_unknown_do_not_retry', 'settlement_receipt_invalid'].includes(receipt.errorReason)) {
          c.res = c.json({ error: 'payment_outcome_uncertain', instruction: 'Do not repeat this payment. The facilitator may have settled it. Reconcile the original transaction and receiver balance before proceeding.' }, 503);
          c.header('PAYMENT-RESPONSE', paymentResponse);
        }
      } catch { /* An invalid SDK receipt remains a payment failure, never protected content. */ }
    }
    // SDK settlement errors replace the Response; reapply CORS/security after it returns.
    securityHeaders();
  });
  app.get('/', c => c.html(PAGE));
  app.get('/health', c => c.json({ service: 'Seiche Evidence Dossier', schema: 'seiche.dossier-health.v1', payment_configured: !!config, network: config?.networkName ?? null, facilitator: FACILITATOR, source_read_mode: 'public_only', settlement_verified: false, note: 'Configuration is not proof of Testnet/Mainnet settlement or challenge eligibility.' }));
  app.get('/v1/preview', async c => c.json({ ...preview(await getDossier()), payment: { configured: !!config, network: config?.networkName ?? null, asset: config?.asset ?? null, amount: PRICE_ATOMIC, currency: 'USDC', decimals: 6, price_usd: '0.01', endpoint: '/v1/dossier' } }));
  app.get('/openapi.json', c => c.json({ openapi: '3.1.0', info: { title: 'Seiche Evidence Dossier', version: '0.1.0', description: 'A source-dated, context-only evidence package assembled from public product APIs. Payment buys assembly; it does not confer trading authority.' },
    servers: config ? [{ url: config.origin }] : [], paths: {
      '/v1/preview': { get: { summary: 'Inspect source availability before payment', responses: { '200': { description: 'Coverage and price without protected dossier content' } } } },
      '/v1/dossier': { get: { summary: 'Purchase a public-evidence research dossier', 'x-x402': { version: 2, facilitator: FACILITATOR, tag: CHALLENGE_TAG, price: '0.01 USDC' }, responses: { '200': { description: 'Dossier after successful settlement; PAYMENT-RESPONSE contains the receipt' }, '402': { description: 'x402 v2 PAYMENT-REQUIRED challenge; no protected data released' }, '503': { description: 'Configuration, source coverage or facilitator unavailable; do not assume payment settled' } } } },
    }, 'x-sources': SOURCES.map(({ id, url, clock }) => ({ id, url, clock })),
  }));
  app.use('/v1/dossier', async (c, next) => {
    if (!config) return c.json({ error: 'payment_not_configured', instruction: 'Operator must configure a valid public receiver, network and HTTPS origin before accepting payments.' }, 503);
    if (new URL(c.req.url).origin !== config.origin || new URL(c.req.url).search) return c.json({ error: 'canonical_resource_required', resource: `${config.origin}/v1/dossier` }, 400);
    if (['PAYMENT-SIGNATURE', 'X-PAYMENT'].some(h => (c.req.header(h)?.length ?? 0) > 16_384)) return c.json({ error: 'payment_header_too_large' }, 400);
    const paymentHeaders = ['PAYMENT-SIGNATURE', 'X-PAYMENT'].map(h => c.req.header(h)).filter((v): v is string => v !== undefined);
    if (paymentHeaders.length > 1 || paymentHeaders.some(h => !validPaymentHeader(h))) return c.json({ error: 'payment_header_malformed' }, 400);
    const dossier = await getDossier();
    if (!dossier.chargeable) return c.json({ error: 'evidence_unavailable', preview: preview(dossier), payment_attempted: false }, 503);
    c.set('dossier', dossier); await next();
  });
  if (config) {
    const configured = config;
    let middleware: Promise<MiddlewareHandler> | undefined;
    const initialize = async () => {
    const client = options.facilitator ?? new HTTPFacilitatorClient({ url: FACILITATOR, timeoutMs: 15_000 });
    let supported; try { supported = await client.getSupported(); } catch { throw new Error('facilitator_unavailable'); }
    const config = { ...configured, network: selectNetwork(supported, configured.networkName) };
    if (!Array.isArray(supported.extensions) || !supported.extensions.includes('bazaar')) throw new Error('facilitator_bazaar_unsupported');
    const cachedClient: FacilitatorClient = { getSupported: async () => supported, verify: (p, r) => client.verify(p, r), settle: (p, r) => client.settle(p, r) };
    const server = new x402ResourceServer(guardedFacilitator(config, cachedClient))
      .register(config.network, new ExactAvmScheme())
      .registerExtension(bazaarResourceServerExtension as unknown as ResourceServerExtension);
    const discovery = declareDiscoveryExtension({ input: {}, inputSchema: { type: 'object', properties: {}, additionalProperties: false },
      output: { example: { schema: 'seiche.evidence-dossier.v1', status: 'PARTIAL', context_only: true, sections: [], integrity: { algorithm: 'SHA-256', authenticated: false } },
        schema: { type: 'object', properties: { schema: { const: 'seiche.evidence-dossier.v1' }, status: { enum: ['AVAILABLE', 'PARTIAL'] }, context_only: { const: true }, sections: { type: 'array' }, integrity: { type: 'object' } }, required: ['schema','status','context_only','sections','integrity'] } } });
    return paymentMiddleware({ 'GET /v1/dossier': { accepts: [{ scheme: 'exact', network: config.network, payTo: config.payTo,
      price: { amount: PRICE_ATOMIC, asset: config.asset, extra: { decimals: 6 } }, maxTimeoutSeconds: 60, extra: { tag: CHALLENGE_TAG } }],
      description: 'Seiche Evidence Dossier: source-dated funding, money-market and liquidity research, explicit missing/withheld evidence and SHA-256 fingerprints; context only. Read /v1/preview before paying.', mimeType: 'application/json', extensions: discovery,
    } }, server);
    };
    app.use('/v1/dossier', async (c, next) => {
      if (!middleware) middleware = initialize().catch(error => { middleware = undefined; throw error; });
      return (await middleware)(c, next);
    });
  }
  app.get('/v1/dossier', c => c.json(c.get('dossier')));
  // No stack traces, raw facilitator messages, signed payment headers or source response bodies in errors/logs.
  app.onError((_error, c) => c.json({ error: 'service_temporarily_unavailable', instruction: 'Do not automatically repeat a payment after an uncertain settlement. Inspect your wallet and facilitator receipt first.' }, 503));
  app.notFound(c => c.json({ error: 'not_found' }, 404));
  return app;
}
