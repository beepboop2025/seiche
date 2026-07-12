# Debugging a live Seiche box

Two Bloomberg-OSS tools are installed in the app venv on the box
(`/home/seiche/app/backend/.venv`) for poking at the 24/7 services without
restarting them. Run both as root.

## The service is wedged / slow — where is it stuck?

[PyStack](https://github.com/bloomberg/pystack) prints the Python (and native)
stack of a *running* process without stopping it:

```sh
PID=$(systemctl show -p MainPID --value seiche-api)
/home/seiche/app/backend/.venv/bin/pystack remote "$PID"
```

Add `--native` if the hang looks like it's inside numpy/pandas/httpx C code.
Works on any Python process on the box (collectors, cron one-shots) — just
point it at the PID. If a process died and left a core file:
`pystack core <corefile>`.

## Memory is creeping — what's holding it?

[Memray](https://github.com/bloomberg/memray) attaches to a live process and
tracks allocations:

```sh
PID=$(systemctl show -p MainPID --value seiche-api)
/home/seiche/app/backend/.venv/bin/memray attach "$PID" -o /tmp/seiche-live.bin
# ...let it observe a few refresh cycles, Ctrl-C, then:
/home/seiche/app/backend/.venv/bin/memray flamegraph /tmp/seiche-live.bin -o /tmp/seiche-live.html
```

## The same tools gate CI

- `pytest --memray` enforces the `limit_memory("256 MB")` leak canary on the
  engine tests (fattest test peaks ~65MiB — only an order-of-magnitude
  regression trips it).
- `--pystack-threshold=300` makes any test that hangs >5 minutes print its
  full stack instead of timing out silently — the deploy gate can never again
  eat a hang without saying where.

Both flags run in `.github/workflows/publish.yml` and in the box deploy gate
(`update.sh`); plain local `pytest` runs without them are unaffected.
