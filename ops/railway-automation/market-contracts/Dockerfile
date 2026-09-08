FROM postgres:17-bookworm AS postgres
FROM python:3.12-slim-bookworm
COPY --from=postgres /usr/lib/postgresql/17 /usr/lib/postgresql/17
COPY --from=postgres /usr/share/postgresql /usr/share/postgresql
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git build-essential libffi-dev libssl-dev libpq5 libllvm14 libicu72 \
    libldap-2.5-0 libxml2 libxslt1.1 libpam0g libedit2 liblz4-1 libzstd1 \
    tini sudo util-linux procps passwd openssh-client systemd shellcheck python3 \
    && rm -rf /var/lib/apt/lists/* && useradd --system --create-home postgres \
    && useradd --uid 10001 --create-home runner \
    && echo "runner ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/runner \
    && chmod 0440 /etc/sudoers.d/runner
COPY --from=postgres /usr/lib/x86_64-linux-gnu/libpq.so.5* /usr/lib/x86_64-linux-gnu/
ENV PATH="/usr/lib/postgresql/17/bin:${PATH}"
ENV OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 NUMEXPR_NUM_THREADS=2
RUN curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    https://github.com/caddyserver/caddy/releases/download/v2.11.4/caddy_2.11.4_linux_amd64.tar.gz -o /tmp/caddy.tar.gz \
    && echo '8220d1f013b6f27510247b2360c9e0ca9f018feebd82515f07635318b34ff9777ccc8fd0b6e6f2486ce3a33fe389fbb7db12d05baa474f4587509fb4f5ebf1c9  /tmp/caddy.tar.gz' | sha512sum -c - \
    && tar -xzf /tmp/caddy.tar.gz -C /usr/local/bin caddy && rm /tmp/caddy.tar.gz
COPY . /app
WORKDIR /app
ARG RAILWAY_GIT_COMMIT_SHA
RUN printf '%s\n' "$RAILWAY_GIT_COMMIT_SHA" | grep -Eq '^[0-9a-f]{40}$' \
    && git init && git remote add origin https://github.com/beepboop2025/seiche.git \
    && git fetch --no-tags origin "$RAILWAY_GIT_COMMIT_SHA" main \
    && git reset --hard "$RAILWAY_GIT_COMMIT_SHA" && git clean -ffdx \
    && git merge-base --is-ancestor refs/remotes/origin/main HEAD \
    && printf 'RAILWAY_MARKET_SOURCE_ADMITTED source=%s base=%s\n' \
       "$RAILWAY_GIT_COMMIT_SHA" "$(git rev-parse refs/remotes/origin/main)"
RUN python -m pip install --no-cache-dir -e './backend[dev,collectors,postgres]' PyYAML==6.0.3 \
    && python -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r ops/requirements-social-cards.txt
RUN /usr/bin/tini -s -- timeout --signal=TERM --kill-after=30 1500 bash ops/railway-automation/market-contracts/run.sh
ENTRYPOINT ["/usr/bin/tini", "-s", "--"]
CMD ["python", "-c", "import os; print('RAILWAY_MARKET_CONTRACTS_PASS source='+os.environ.get('RAILWAY_GIT_COMMIT_SHA','unavailable')+' deployment='+os.environ.get('RAILWAY_DEPLOYMENT_ID','unavailable'),flush=True)"]
