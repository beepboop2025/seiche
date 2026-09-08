FROM python:3.12.12-slim-bookworm@sha256:2986c55feb36e6cae00fa1fefb454283e4b33f35e75ff8bdd123b134130be301 AS python
FROM postgres:18.6-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af
COPY --from=python /usr/local /usr/local
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git libgomp1 openssh-client openssl procps tini unzip \
    && rm -rf /var/lib/apt/lists/*
RUN curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    https://awscli.amazonaws.com/awscli-exe-linux-x86_64-2.36.35.zip -o /tmp/aws.zip \
    && echo 'd2dc4df7edbd93913606f27c2fef7dd7ed19e4ebf659251dbf83b759dd5e816c  /tmp/aws.zip' | sha256sum -c - \
    && cd /tmp && unzip -q aws.zip && ./aws/install && rm -rf /tmp/aws /tmp/aws.zip
RUN curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    https://github.com/railwayapp/cli/releases/download/v5.43.1/railway-v5.43.1-x86_64-unknown-linux-gnu.tar.gz -o /tmp/railway.tar.gz \
    && echo '88fb44b35b74ded67d383fe6f8b69161d8cc4ca5e3f9b8c6c338293f65f52ee9  /tmp/railway.tar.gz' | sha256sum -c - \
    && tar -xzf /tmp/railway.tar.gz -C /usr/local/bin railway \
    && mv /usr/local/bin/railway /usr/local/bin/railway-real && rm /tmp/railway.tar.gz
COPY requirements.lock /controller/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r /controller/requirements.lock
COPY . /controller
RUN mkdir /controller/native-bin && cp /controller/native_docker.py /controller/native-bin/docker \
    && chmod 0755 /controller/native-bin/docker /controller/trusted/ops/deploy/seiche-s3-object-lock.sh \
    && if test -f /controller/trusted/ops/railway/retry_read.py; then cp /controller/trusted/ops/railway/retry_read.py /usr/local/bin/railway; chmod 0755 /usr/local/bin/railway; fi \
    && chmod -R u=rwX,go=rX /controller \
    && cd /controller && python -c 'import verify; verify.modules()' \
    && /usr/bin/tini -s -- python -m unittest -v test_verify test_attest test_recurring
WORKDIR /controller
ENTRYPOINT ["/usr/bin/tini", "-s", "--"]
CMD ["timeout", "--signal=TERM", "--kill-after=30", "5400", "python", "-u", "/controller/verify.py"]
