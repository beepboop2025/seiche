import { createApp } from './app.js';
import type { Env } from './payment.js';
let lastKey = ''; let app: ReturnType<typeof createApp> | undefined;
export default {
  fetch(request: Request, env: Env) {
    const key = JSON.stringify([env.SEICHE_ALGORAND_NETWORK, env.SEICHE_ALGORAND_PAY_TO, env.SEICHE_PUBLIC_ORIGIN]);
    if (!app || key !== lastKey) { app = createApp({ env }); lastKey = key; }
    return app.fetch(request, env);
  },
};
