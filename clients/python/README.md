# Seiche public REST example for Python

`world_markets.py` uses only the Python standard library and calls the
anonymous `https://api.seiche.info/api/v2/world-markets` contract. It returns
the service response unchanged; the optional receipt projection merely makes
the server's separate clocks, citation, scope, and local safety limits easy to
inspect.

```sh
python3 clients/python/world_markets.py
```

The example has a 15-second timeout, a 2 MB response ceiling, and zero
automatic retries. HTTP 503 remains an unavailable observation, not an empty
or calm result. `generated_at`, `clocks.evaluation_at`, and the domain/source
as-of values have different meanings; do not replace them with the local wall
clock. The response is bounded research context, not exhaustive market data or
investment advice. No API key is used.
