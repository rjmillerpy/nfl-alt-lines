# syntax=docker/dockerfile:1
# Two stages: the builder installs the locked dependencies with uv; the runtime image gets only the
# virtualenv and the source, runs as a non-root user, and has no uv or build toolchain.
# Secrets are passed as environment variables at run time (see docker-compose.yml), never baked in.

FROM ghcr.io/astral-sh/uv:0.13.3 AS uv

FROM python:3.14-slim-trixie AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first, so a source-only change reuses this layer.
# Each "open-core: private-only" comment and the line after it are dropped from the public mirror
# (docs/open-core-setup.md).
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --all-packages --no-install-workspace
COPY nfl_alt_lines ./nfl_alt_lines
# Editable installs: the packages stay under /app, so ROOT is /app and data/ is /app/data.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --all-packages

FROM python:3.14-slim-trixie
# LightGBM needs the OpenMP runtime.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
# uid 1000 matches the usual first user on the host, so a mounted data/ stays writable on both sides.
RUN useradd --uid 1000 --create-home app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/nfl_alt_lines /app/nfl_alt_lines
RUN mkdir /app/data && chown app:app /app/data
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
USER app
VOLUME /app/data
ENTRYPOINT ["nfl-alt-lines"]
CMD ["--help"]
