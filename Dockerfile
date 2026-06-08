# syntax=docker/dockerfile:1.7
# ===========================================================================
# Dash - Self-learning Data Agent (multi-stage build)
# ===========================================================================
#
# Stages:
#   1. frontend-builder  (oven/bun:1)      - builds SvelteKit SPA via Bun
#   2. python-deps       (uv:python3.12)   - installs Python requirements
#   3. runtime           (extends python-deps) - final image
#
# Why this layout:
#   - Frontend src edits invalidate ONLY stage 1
#   - requirements.txt edits invalidate ONLY stage 2
#   - Backend code edits invalidate ONLY the trailing COPY in stage 3
#
# PPTX rendering is native python-pptx (dash/pptx_renderer) — no Node sidecar.
# ===========================================================================

# ---------------------------------------------------------------------------
# Stage 1: frontend-builder  (Bun replaces npm/Node — faster + less memory)
# ---------------------------------------------------------------------------
FROM oven/bun:1 AS frontend-builder
WORKDIR /build/frontend

# Defensive — Bun handles memory better than Node, but vite/rollup still
# spawn workers that respect this env on Node-compat code paths.
ENV NODE_OPTIONS=--max-old-space-size=4096

# Copy manifests + configs FIRST so dep install caches independently of src
COPY frontend/package.json frontend/bun.lockb* frontend/package-lock.json* ./
COPY frontend/svelte.config.js frontend/vite.config.* frontend/tsconfig.json ./
COPY frontend/tailwind.config.* frontend/postcss.config.* ./

# bun install — frozen lockfile if available, otherwise fresh resolve.
# Cache mount = persistent across builds, no impact on image size.
RUN --mount=type=cache,target=/root/.bun/install/cache \
    if [ -f bun.lockb ]; then \
      bun install --frozen-lockfile; \
    else \
      bun install; \
    fi

# Now copy source — only this layer busts on .svelte/.ts/.css edits
COPY frontend/static ./static
COPY frontend/src ./src
COPY frontend/scripts ./scripts

# Build the SPA
RUN bun run build

# ---------------------------------------------------------------------------
# Stage 2: python-deps  (uv handles pip resolution + install in <30s)
# ---------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-trixie-slim AS python-deps

# Build-only headers + runtime libs we need in BOTH stages.
# We keep this list minimal here; runtime adds OCR / office tools.
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates gnupg build-essential \
      libffi-dev libssl-dev libxml2-dev libxslt-dev \
      libjpeg-dev libpng-dev zlib1g-dev libfreetype6-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./

# uv pip sync with cache mount = warm rebuilds in <5s
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=cache,target=/root/.cache/uv \
    uv pip sync requirements.txt --system

# ---------------------------------------------------------------------------
# Stage 3: runtime  (final image)
# ---------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-trixie-slim AS runtime

ARG TARGETARCH
ARG DOCKERIZE_VERSION=v0.11.0

# Runtime apt deps (OCR / PDF rendering — pdftoppm + tesseract for upload/vision).
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates gnupg \
      poppler-utils tesseract-ocr \
      libjpeg62-turbo libpng16-16 zlib1g libfreetype6 \
      libxml2 libxslt1.1 libffi8 \
    && curl -sSfL "https://github.com/jwilder/dockerize/releases/download/${DOCKERIZE_VERSION}/dockerize-linux-${TARGETARCH}-${DOCKERIZE_VERSION}.tar.gz" \
       | tar -xz -C /usr/local/bin \
    && rm -rf /var/lib/apt/lists/*

# Pull Python site-packages from the deps stage (no rebuild).
COPY --from=python-deps /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=python-deps /usr/local/bin /usr/local/bin

WORKDIR /app

# Frontend build artifacts (SPA) — cached unless frontend/src changes.
COPY --from=frontend-builder /build/frontend/build /app/frontend/build
COPY --from=frontend-builder /build/frontend/static /app/frontend/static

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ---------------------------------------------------------------------------
# Backend code (LAST — backend-only edits only invalidate this layer)
# ---------------------------------------------------------------------------
COPY . /app

# Ensure scripts/ and db/migrations/ are present at /app (drift gate + admin tooling).
# COPY . above already covers these, but explicit COPY guards against future .dockerignore drift.
COPY scripts /app/scripts
COPY db /app/db

# ---------------------------------------------------------------------------
# Build provenance — surfaced via /api/admin/image/info
# ---------------------------------------------------------------------------
ARG BUILD_COMMIT=unknown
ARG BUILD_TIME=
ARG APP_VERSION=dev
ENV BUILD_COMMIT=$BUILD_COMMIT \
    BUILD_TIME=$BUILD_TIME \
    APP_VERSION=$APP_VERSION

# ---------------------------------------------------------------------------
# Non-root user
# ---------------------------------------------------------------------------
RUN groupadd -r dash && useradd -r -g dash -d /app -s /sbin/nologin dash \
    && chown -R dash:dash /app
USER dash

RUN chmod +x /app/scripts/entrypoint.sh 2>/dev/null || true
ENTRYPOINT ["/app/scripts/entrypoint.sh"]

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=30s \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000
# gunicorn (not bare uvicorn) so post_fork hook can stamp WORKER_RANK per worker.
CMD ["gunicorn", "-c", "scripts/gunicorn_conf.py", "app.main:app"]
