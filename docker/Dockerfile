# syntax=docker/dockerfile:1.7
# Production image (task 9, from the task 42 spike). Targets: builder, runtime (default), test.
# Versions and digests read on 2026-11-04 (Python image digest: 2026-11-05, Python 3.14); never `latest`.
ARG PY_IMAGE=python:3.14-slim-trixie@sha256:af8a5aa14780025dbfbcfbc81bd8a91ebd87711dd5b0f48ed71090949dd7cb1d

FROM ${PY_IMAGE} AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /bin/
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake git && rm -rf /var/lib/apt/lists/*
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0 \
    CMAKE_ARGS="-DGGML_NATIVE=OFF -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON -DGGML_BMI2=ON -DGGML_AVX512=OFF"
WORKDIR /app
COPY pyproject.toml uv.lock README* CLAUDE.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
# the project is installed in editable mode and /app is read-only for `rushes`: compile it here
RUN /app/.venv/bin/python -m compileall -q --invalidation-mode unchecked-hash /app/src

# Same virtualenv plus the dev dependencies (pytest...), built here where the toolchain is.
FROM builder AS builder-test
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen
RUN /app/.venv/bin/python -m compileall -q --invalidation-mode unchecked-hash /app/src

# What both final images share. The venv and RUSHES_COMMIT come after, so that a new commit
# only invalidates the layers below.
FROM ${PY_IMAGE} AS base
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgomp1 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=denoland/deno:bin-2.9.8 /deno /usr/local/bin/deno
RUN groupadd --gid 1000 rushes \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin rushes \
    && install -d -o rushes -g rushes /data
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" OMP_NUM_THREADS=4 PYTHONUNBUFFERED=1

FROM base AS runtime
COPY --from=builder /app /app
COPY scripts/ scripts/
COPY docker/entrypoint.sh docker/entrypoint.sh
ARG RUSHES_COMMIT=""
ENV RUSHES_COMMIT=${RUSHES_COMMIT}
USER rushes
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["rushes", "--help"]

# pytest only ever runs here, never in the production image.
FROM base AS test
COPY --from=builder-test /app /app
COPY scripts/ scripts/
COPY docker/entrypoint.sh docker/entrypoint.sh
COPY tests/ tests/
COPY config/ config/
COPY docs/ docs/
ENV PYTEST_ADDOPTS="-p no:cacheprovider"
ARG RUSHES_COMMIT=""
ENV RUSHES_COMMIT=${RUSHES_COMMIT}
USER rushes
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["rushes", "--help"]
