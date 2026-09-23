# ── Stage 1: Build frontend ───────────────────────────────────────────────────
FROM node:20-slim AS frontend-builder

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* frontend/yarn.lock* frontend/pnpm-lock.yaml* ./
ARG NPM_CONFIG_REGISTRY=https://registry.npmjs.org/
RUN --mount=type=cache,target=/root/.npm npm ci --registry="${NPM_CONFIG_REGISTRY}"

COPY frontend/ ./
RUN npm run build
RUN npm prune --omit=dev

# ── Stage 2: Build backend ────────────────────────────────────────────────────
FROM python:3.11-slim AS backend-builder

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/

COPY pyproject.toml uv.lock README.md ./
ARG UV_INDEX_URL=https://pypi.org/simple
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --index-url "${UV_INDEX_URL}"

# ── Stage 3: Runtime image ────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Function authoring checks fail closed if the host/container policy denies
# user/network namespaces; installing the launcher alone does not grant them.
RUN apt-get update && apt-get install -y --no-install-recommends bubblewrap git ripgrep && apt-get clean

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

# Deterministic Page compilation and isolated browser validation, not a Node
# Agent service. Generated source is only parsed here, never imported by Node.
COPY --from=frontend-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend-builder /frontend/node_modules ./frontend/node_modules
RUN node frontend/node_modules/playwright/cli.js install --with-deps chromium

COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
