FROM node:20-bookworm-slim AS node
FROM node:22-bookworm-slim AS publisher_node
FROM python:3.12-slim-bookworm
COPY --from=publisher_node /usr/local /opt/node22
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && apt-get update && apt-get install -y --no-install-recommends \
       git openssh-client curl rsync ca-certificates tini util-linux procps build-essential libffi-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home builder \
    && PATH="/opt/node22/bin:$PATH" /opt/node22/bin/npm install --prefix /opt/publisher wrangler@4.125.0 \
    && python -m pip install --no-cache-dir PyYAML==6.0.3
COPY requirements-social-cards.txt /controller/requirements-social-cards.txt
RUN python -m pip install --no-cache-dir --only-binary=:all: --require-hashes \
    -r /controller/requirements-social-cards.txt
RUN curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    https://github.com/caddyserver/caddy/releases/download/v2.11.4/caddy_2.11.4_linux_amd64.tar.gz -o /tmp/caddy.tar.gz \
    && echo '8220d1f013b6f27510247b2360c9e0ca9f018feebd82515f07635318b34ff9777ccc8fd0b6e6f2486ce3a33fe389fbb7db12d05baa474f4587509fb4f5ebf1c9  /tmp/caddy.tar.gz' | sha512sum -c - \
    && tar -xzf /tmp/caddy.tar.gz -C /usr/local/bin caddy && rm /tmp/caddy.tar.gz
COPY Dockerfile test_publish.py publish.py publish.yml gate-sha256.json github-known-hosts controller-source.json /controller/
WORKDIR /controller
RUN /opt/node22/bin/node /opt/publisher/node_modules/wrangler/bin/wrangler.js --version \
    && node -e "if(process.versions.node.split('.')[0] !== '20') process.exit(1)"
RUN /usr/bin/tini -s -- python -m unittest test_publish -v
ENTRYPOINT ["/usr/bin/tini", "-s", "--"]
CMD ["timeout", "--signal=TERM", "--kill-after=30", "5400", "python", "publish.py"]
