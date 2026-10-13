FROM node:22.11-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS app
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY backend/scripts/start.sh ./start.sh
COPY backend/artifacts ./artifacts
RUN uv sync --frozen --no-dev
COPY --from=web /web/dist ./static
ENV PATH=/app/.venv/bin:$PATH \
    STATIC_DIR=/app/static \
    ARTIFACTS_DIR=/app/artifacts
RUN useradd --create-home app && chown -R app /app
USER app
EXPOSE 8000
CMD ["./start.sh"]
