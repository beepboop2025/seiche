FROM python:3.12.12-slim-bookworm@sha256:2986c55feb36e6cae00fa1fefb454283e4b33f35e75ff8bdd123b134130be301
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git openssh-client python3 tini \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock /controller/requirements.lock
RUN python -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r /controller/requirements.lock
RUN curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
      https://github.com/railwayapp/cli/releases/download/v5.43.1/railway-v5.43.1-x86_64-unknown-linux-gnu.tar.gz \
      --output /tmp/railway.tar.gz \
    && echo '88fb44b35b74ded67d383fe6f8b69161d8cc4ca5e3f9b8c6c338293f65f52ee9  /tmp/railway.tar.gz' | sha256sum --check --strict \
    && test "$(tar -tzf /tmp/railway.tar.gz)" = railway \
    && tar -xzf /tmp/railway.tar.gz -C /usr/local/bin railway \
    && mv /usr/local/bin/railway /usr/local/bin/railway-real \
    && rm /tmp/railway.tar.gz
COPY . /controller/
RUN cp /controller/trusted/ops/railway/retry_read.py /usr/local/bin/railway \
    && chmod 0755 /usr/local/bin/railway \
    && chmod -R go-w /controller \
    && python /controller/test_monitor.py
WORKDIR /controller
ENTRYPOINT ["/usr/bin/tini", "-s", "--"]
CMD ["timeout", "--signal=TERM", "--kill-after=30", "1800", "python", "-u", "monitor.py"]
