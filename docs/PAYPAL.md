# Undertow PayPal edge rail

Status: **DORMANT**. `https://api.seiche.info/undertow/paypal/*` intentionally
returns a small JSON `503 Service Unavailable` response with `Retry-After`.
The Seiche edge does not proxy this route to loopback port 8798, and production
must not assume a PayPal webhook process is listening there.

This explicit response is the safe inactive state. It keeps monitoring and
integrators from confusing a connection-refused proxy `502` with an enabled
card rail.

## Mandatory readiness preflight

Replacing the dormant responder with a reverse proxy requires a recorded PASS
for every item below in the same release candidate:

1. A supervised webhook service is installed, enabled and bound only to the
   intended loopback port, with a separate authenticated readiness endpoint.
2. PayPal webhook signature verification, event-type allow-listing, timestamp
   bounds and secret rotation are configured and fail closed.
3. Event IDs are durably deduplicated before entitlement or account mutation;
   delivery replay is tested and processing is idempotent.
4. Sandbox capture, failure, duplicate and refund flows pass end to end without
   logging credentials or full payment instruments.
5. Alerting, an audit trail and an operator reconciliation/refund runbook exist.
6. The candidate Caddyfile validates, the loopback readiness probe passes, and
   an external smoke confirms the exact webhook contract through the edge.

A listening port alone is not readiness. Until all six checks pass, the bounded
`503` responder and its `Retry-After` header must remain in place.
