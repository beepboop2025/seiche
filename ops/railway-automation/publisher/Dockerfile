FROM node:20-bookworm-slim AS node
FROM python:3.12-slim-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && apt-get update && apt-get install -y --no-install-recommends \
       git openssh-client curl rsync ca-certificates tini util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home builder \
    && npm install --global wrangler@4.125.0 \
    && python -m pip install --no-cache-dir PyYAML==6.0.3
COPY requirements-social-cards.txt /controller/requirements-social-cards.txt
RUN python -m pip install --no-cache-dir --only-binary=:all: --require-hashes \
    -r /controller/requirements-social-cards.txt
COPY publish.py publish-static.yml gate-sha256.json github-known-hosts controller-source.json /controller/
WORKDIR /controller
ENTRYPOINT ["/usr/bin/tini", "-s", "--"]
CMD ["timeout", "--signal=TERM", "--kill-after=30", "1200", "python", "publish.py"]
