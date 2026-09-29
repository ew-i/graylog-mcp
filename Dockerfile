ARG UV_VERSION=0.12.20

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv
FROM python:3.14-alpine@sha256:9e9fde4d32eedce0b661d9ab91e826b62dddf28e928c230ec55f1866cac66b01

ENV GCC_VERSION=15.2.0-r5
ENV MUSL_DEV_VERSION=1.2.6-r2

USER root

SHELL ["/bin/ash", "-eo", "pipefail", "-c"]

COPY --from=uv /uv /uvx /bin/

RUN apk add --no-cache \
        gcc="${GCC_VERSION}" \
        musl-dev="${MUSL_DEV_VERSION}"

RUN adduser -u 1000 -G root -D mcp

WORKDIR /app
COPY --chown=mcp:0 pyproject.toml uv.lock ./
COPY --chown=mcp:0 src ./src

RUN uv sync --locked --no-dev --no-cache \
  && chown -R mcp:0 /app/.venv \
  && chmod -R g=u /app/.venv

USER mcp

ENTRYPOINT ["uv", "run", "--no-sync", "--no-dev", "graylog-mcp"]
