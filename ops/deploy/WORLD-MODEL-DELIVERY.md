# Private signed world-model delivery

Seiche exposes one opt-in machine-to-machine relay for the signed Lab v2
delivery envelope:

```text
offline Lab runner
  -> /var/lib/liquilens-world-model/export/us-usd-funding-core-v2.json
  -> authenticated Seiche byte relay
  -> Railway LiquiLens consumer
  -> Ed25519 verification before acceptance
```

The HTTPS source is exactly:

```text
https://api.seiche.info/api/internal/v1/world-model/us-usd-funding-core-v2
```

This route is absent from Seiche's public discovery document and OpenAPI. It
accepts only `GET` with `Authorization: Bearer <token>`, does not redirect, and
returns `application/json` with `Cache-Control: no-store, no-transform`. The
response body is streamed unchanged from the already-signed file.

Seiche does not parse, train, evaluate, sign, or trust this artifact. The
bearer token is only a retrieval ACL. LiquiLens independently verifies the
publisher's Ed25519 signature and policy envelope before using it.

## Filesystem boundary

Lab owns the dedicated producer/export boundary:

```text
/var/lib/liquilens-world-model                         0710 producer:readers
/var/lib/liquilens-world-model/archive                 0750 producer:producer
/var/lib/liquilens-world-model/export                  2750 producer:readers
/var/lib/liquilens-world-model/export/us-usd-funding-core-v2.json
                                                        0440 producer:readers
```

Here `producer` is `liquilens-world-model` and `readers` is
`liquilens-world-model-readers`. The Lab installer atomically materializes only
the verified signed envelope in `export/`. Its archive, inputs, fits, scenarios,
keys, and staging files remain under the private producer group.

The Seiche provisioning helper adds only the `seiche` API identity to the
reader group. It does not change Lab ownership or grant archive traversal.

## Opt-in provisioning

The route is disabled unless both path and bearer-token environment values are
present. Generate a 32-byte token into an operator-controlled root-only file;
never put the token on a command line or in deployment logs:

```sh
umask 077
openssl rand -hex 32 > /root/seiche-world-model-relay.token
chmod 0600 /root/seiche-world-model-relay.token
SEICHE_WORLD_MODEL_DELIVERY_TOKEN_FILE=/root/seiche-world-model-relay.token \
  bash /home/seiche/app/ops/deploy/install-world-model-delivery-relay.sh
systemctl restart seiche-api.service
```

The helper first requires the Lab reader group and exact signed export to
exist, verifies read access as `seiche`, then atomically installs
`/etc/seiche/world-model-delivery.env` as `root:seiche` mode `0640`. It never
prints the token. `install-market-platform.sh` validates the file without
sourcing it and adds it to the API service through an optional systemd
`EnvironmentFile`; an absent file remains disabled.

The environment contract is:

```text
SEICHE_WORLD_MODEL_DELIVERY_PATH=/var/lib/liquilens-world-model/export/us-usd-funding-core-v2.json
SEICHE_WORLD_MODEL_DELIVERY_BEARER_TOKEN=<64 lowercase hex characters>
SEICHE_WORLD_MODEL_DELIVERY_MAX_BYTES=2097152
```

The default response ceiling is 2 MiB. Operators may lower it, but the relay
rejects any value above 5 MiB because the Railway consumer has the same 5 MiB
signed-envelope ceiling. A missing, non-regular, symlinked, empty, changing, or
oversized file is never served.

Configure Railway only after the Lab publisher key and relay are provisioned:

```text
LIQUILENS_WORLD_MODEL_DELIVERY_ENABLED=1
LIQUILENS_WORLD_MODEL_DELIVERY_URL=https://api.seiche.info/api/internal/v1/world-model/us-usd-funding-core-v2
LIQUILENS_WORLD_MODEL_DELIVERY_BEARER_TOKEN=<same retrieval token>
```

The LiquiLens public key configuration remains separate and authoritative.

## Failure behavior and rotation

- Disabled or malformed configuration returns `404`.
- Missing or unsafe delivery state returns `503` after successful auth.
- Missing, malformed, or incorrect bearer credentials return `401` with the
  same generic response; credentials and paths are never logged by the route.
- Every route response is `no-store, no-transform`; the edge does not enable
  CORS for this private source.
- LiquiLens must retain its last verified state on any non-200, stale,
  oversized, malformed, or signature-invalid response.

Token rotation requires coordinated replacement of the root-only token file,
rerunning the helper, updating the Railway secret, and restarting Seiche.
There is intentionally no unauthenticated external smoke probe for this route.
