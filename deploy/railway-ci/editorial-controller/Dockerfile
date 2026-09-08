FROM python:3.12.12-slim-bookworm@sha256:2986c55feb36e6cae00fa1fefb454283e4b33f35e75ff8bdd123b134130be301
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates git procps tini \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock /controller/requirements.lock
RUN python -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r /controller/requirements.lock
COPY . /controller/
RUN chmod -R go-w /controller && tini -s -- python /controller/test_editorial.py
WORKDIR /controller
ENTRYPOINT ["/usr/bin/tini", "-s", "--"]
CMD ["timeout", "--signal=TERM", "--kill-after=30", "4500", "python", "-u", "editorial.py"]
