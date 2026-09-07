FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends bash curl tini \
    && rm -rf /var/lib/apt/lists/*
COPY . /app
WORKDIR /app
ENTRYPOINT ["/usr/bin/tini", "-s", "--"]
CMD ["timeout", "--signal=TERM", "--kill-after=15", "540", "bash", "ops/railway-automation/run.sh"]
