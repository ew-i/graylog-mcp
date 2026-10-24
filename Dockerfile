ARG UV_VERSION=0.13.5

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv
FROM python:3.15-alpine@sha256:d44b1254cb92329418d164b22b20d86bbd3869d48e80e09e476531a409a728bb AS base

FROM base AS builder

ENV GCC_VERSION=15.2.0-r5
ENV MUSL_DEV_VERSION=1.2.6-r3
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

SHELL ["/bin/ash", "-eo", "pipefail", "-c"]

COPY --from=uv /uv /bin/

RUN apk add --no-cache \
        gcc="${GCC_VERSION}" \
        musl-dev="${MUSL_DEV_VERSION}"

WORKDIR /app
COPY pyproject.toml uv.lock ./
# Dependencies first so source edits don't invalidate this layer.
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM base

RUN adduser -u 1000 -G root -D mcp

# Same path as the builder: the venv's interpreter symlinks and scripts are absolute.
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}"

USER mcp

ENTRYPOINT ["graylog-mcp"]
