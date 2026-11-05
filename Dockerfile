# ---- Stage 1: build the wheel from source ----
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /src
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv build --wheel --out-dir /dist

# ---- Stage 2: runtime image ----
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    HOST=0.0.0.0 \
    PORT=8000

# Install dependencies first so this layer is cached when only app code changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

COPY --from=builder /dist/*.whl /tmp/
RUN uv pip install /tmp/*.whl --no-deps && rm /tmp/*.whl

EXPOSE 8000

# OPENAI_API_KEY is provided at runtime (docker run --env-file / -e).
CMD ["resume-scanner"]
