# ── Stage 1: Build frontend ───────────────────────────────────────────────────
FROM node:20-slim AS frontend-builder

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* frontend/yarn.lock* frontend/pnpm-lock.yaml* ./
ARG NPM_CONFIG_REGISTRY=https://registry.npmjs.org/
RUN --mount=type=cache,target=/root/.npm npm ci --registry="${NPM_CONFIG_REGISTRY}"

COPY frontend/ ./
RUN npm run build

# ── Stage 2: Build backend ────────────────────────────────────────────────────
FROM python:3.11-slim AS backend-builder

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/

COPY pyproject.toml uv.lock README.md ./
ARG LITELLM_WHEEL_URL=https://files.pythonhosted.org/packages/1c/38/e6a4abb062e039d18d59538cc4e6fc370c2c10cd2bff4a2e546acb69dcb9/litellm-1.85.0-py3-none-any.whl
ADD --checksum=sha256:2bb449153610691faffd76f5b94a8c29e4b66fc5394156ebf54fd4fe92759b1a \
    "${LITELLM_WHEEL_URL}" /tmp/wheels/litellm-1.85.0-py3-none-any.whl
ARG UV_INDEX_URL=https://pypi.org/simple
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --index-url "${UV_INDEX_URL}" --find-links /tmp/wheels

# ── Stage 3: Runtime image ────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Copy Python env from builder
COPY --from=backend-builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Copy application code (includes app/builtin_skills/)
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini .
COPY tools/ ./tools/

# Copy frontend build output
COPY --from=frontend-builder /frontend/dist ./frontend/dist

COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
