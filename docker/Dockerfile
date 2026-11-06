# syntax=docker/dockerfile:1.7
# v0: feasibility spike image (task 42). Task 9 finalises it.
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

FROM ${PY_IMAGE} AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgomp1 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=denoland/deno:bin-2.9.8 /deno /usr/local/bin/deno
WORKDIR /app
COPY --from=builder /app /app
COPY scripts/ scripts/
ENV PATH="/app/.venv/bin:$PATH" OMP_NUM_THREADS=4 PYTHONUNBUFFERED=1
