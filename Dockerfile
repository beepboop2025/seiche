FROM node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS frontend-build

WORKDIR /build/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund

COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/public ./public
COPY frontend/src ./src
RUN npm run build


FROM cgr.dev/chainguard/python:latest-dev@sha256:cd42e3e78f19faffe161fccf60af83503ee3851dd12efdae7d2488148e2fcd49 AS python-build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/home/nonroot/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

WORKDIR /home/nonroot/build

RUN python -m venv "${VIRTUAL_ENV}"
COPY --chown=65532:65532 backend/pyproject.toml backend/README.md ./backend/
COPY --chown=65532:65532 backend/seiche ./backend/seiche
# pip is a build tool, not a runtime dependency. Removing it also removes its
# vendored package-manager code and advisory surface from the final image.
RUN python -m pip install ./backend \
    && python -m pip uninstall --yes pip \
    && mkdir -p /home/nonroot/runtime/app/backend/data


FROM cgr.dev/chainguard/python:latest@sha256:53757bfb153c99eb7005963b7e4ea3a8ba488badceab8487d3ba982ad54f2047 AS runtime

# The pinned distroless base includes CPython's package bootstrap module even
# though it includes no pip executable. Remove that bootstrap as root during
# the image build; the final process user remains the unprivileged UID below.
USER 0
RUN ["/usr/bin/python", "-c", "import ensurepip, pathlib, shutil; shutil.rmtree(pathlib.Path(ensurepip.__file__).parent)"]

ARG VERSION=development
ARG REVISION=unknown
ARG CREATED=1970-01-01T00:00:00Z
ARG SOURCE=https://github.com/beepboop2025/seiche

LABEL org.opencontainers.image.title="Seiche" \
      org.opencontainers.image.description="Money, FX and capital-market evidence with source clocks, canonical citations and explicit limits." \
      org.opencontainers.image.authors="Mrinal" \
      org.opencontainers.image.url="https://seiche.info" \
      org.opencontainers.image.documentation="https://github.com/beepboop2025/seiche/blob/main/docs/DISTRIBUTION.md" \
      org.opencontainers.image.source="${SOURCE}" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.created="${CREATED}" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later" \
      org.opencontainers.image.base.name="cgr.dev/chainguard/python:latest" \
      org.opencontainers.image.base.digest="sha256:53757bfb153c99eb7005963b7e4ea3a8ba488badceab8487d3ba982ad54f2047"

ENV PATH="/home/nonroot/venv/bin:${PATH}" \
    PYTHONPATH=/app/backend \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=python-build /home/nonroot/venv /home/nonroot/venv
COPY --from=python-build --chown=65532:65532 /home/nonroot/runtime/app/backend/data ./backend/data
COPY --chown=65532:65532 backend/seiche ./backend/seiche
COPY --chown=65532:65532 --from=frontend-build /build/frontend/dist ./frontend/dist
COPY --chown=65532:65532 LICENSE CITATION.cff codemeta.json ./

USER 65532:65532

VOLUME ["/app/backend/data"]
EXPOSE 8787
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=4 \
  CMD ["/home/nonroot/venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/api', timeout=4).read(1)"]

ENTRYPOINT ["/home/nonroot/venv/bin/python"]
CMD ["-m", "uvicorn", "seiche.api:app", "--host", "0.0.0.0", "--port", "8787"]
