FROM node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS frontend-build

WORKDIR /build/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund

COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/public ./public
COPY frontend/src ./src
RUN npm run build


FROM cgr.dev/chainguard/python:latest@sha256:1e64347fb2a3f1a57a8839fa05df8f0cb8c2c050ac85f3ee6b24d442ae282fd9 AS runtime-base


FROM cgr.dev/chainguard/python:latest-dev@sha256:85c70fe9ec4313b5c8b1adcb4f0e7fa45d171ae203d2c64c69c4ea3eb96fb7dc AS python-build

# Patch the signed CA package without advancing these Python 3.14 images to
# Python 3.15. apk verifies the existing repository keys and updates its real
# package database. Remove this step when both base pins include the fixed CA.
USER 0
COPY --from=runtime-base / /runtime-root/
RUN python - <<'PYCA'
import hashlib
import pathlib
import shutil
import subprocess

expected_hash = "5961c0a3c65459931c023e0075c09733f09aea4ca439cb85bc4a08bb4f3f568b"
def packages(root):
    records = (root / "usr/lib/apk/db/installed").read_text().split("\n\n")
    fields = [dict(line.split(":", 1) for line in record.splitlines() if ":" in line) for record in records]
    return {item["P"]: item["V"] for item in fields if "P" in item}

for root in (pathlib.Path("/"), pathlib.Path("/runtime-root")):
    before = packages(root)
    # The distroless root's ldconfig APK hook needs a shell during the update.
    # Borrow the pinned builder's BusyBox only for that hook, then remove it.
    shell = root / "usr/bin/sh" if root != pathlib.Path("/") else None
    if shell is not None:
        assert not shell.exists() and not shell.is_symlink(), "unexpected runtime shell"
        shutil.copy2("/usr/bin/busybox", shell)
    try:
        subprocess.run(["apk", "--root", str(root), "--no-cache", "add", "--upgrade", "ca-certificates-bundle=20260909-r3"], check=True)
    finally:
        if shell is not None:
            shell.unlink()
    if shell is not None:
        assert not shell.exists() and not shell.is_symlink(), "runtime shell retained"
    expected = before | {"ca-certificates-bundle": "20260909-r3"}
    assert packages(root) == expected, "CA update changed another package"
    bundle = root / "etc/ssl/certs/ca-certificates.crt"
    assert hashlib.sha256(bundle.read_bytes()).hexdigest() == expected_hash, "unexpected CA bundle"
    assert not (root / "var/lib/db/sbom/ca-certificates-bundle-20260909-r2.spdx.json").exists()
    assert (root / "var/lib/db/sbom/ca-certificates-bundle-20260909-r3.spdx.json").is_file()
PYCA
USER 65532:65532

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


FROM cgr.dev/chainguard/python:latest@sha256:1e64347fb2a3f1a57a8839fa05df8f0cb8c2c050ac85f3ee6b24d442ae282fd9 AS runtime

# Copy the fully apk-managed root, including its updated package inventory.
# COPY overlays files, so repeat apk's deletion of the superseded package SBOM.
COPY --from=python-build /runtime-root/ /
USER 0
RUN ["/usr/bin/python", "-c", "import pathlib; pathlib.Path('/var/lib/db/sbom/ca-certificates-bundle-20260909-r2.spdx.json').unlink()"]

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
      org.opencontainers.image.description="Money, FX, capital-market and metadata-only China macro evidence with source clocks and limits." \
      org.opencontainers.image.authors="Mrinal" \
      org.opencontainers.image.url="https://seiche.info" \
      org.opencontainers.image.documentation="https://github.com/beepboop2025/seiche/blob/main/docs/DISTRIBUTION.md" \
      org.opencontainers.image.source="${SOURCE}" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.created="${CREATED}" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later" \
      org.opencontainers.image.base.name="cgr.dev/chainguard/python:latest" \
      org.opencontainers.image.base.digest="sha256:1e64347fb2a3f1a57a8839fa05df8f0cb8c2c050ac85f3ee6b24d442ae282fd9"

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
