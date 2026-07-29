FROM node:22-alpine AS dashboard

WORKDIR /dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build

FROM python:3.12-slim

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev
RUN useradd --create-home --uid 1000 reed \
    && mkdir -p /data \
    && chown reed:reed /data
COPY --chown=reed:reed backend/app ./app
COPY --chown=reed:reed --from=dashboard /dashboard/dist ./dashboard

ENV PYTHONUNBUFFERED=1
ENV REED_DASHBOARD_PATH=/app/dashboard
ENV REED_DATABASE_PATH=/data/reed.db
EXPOSE 8000
EXPOSE 7860

USER reed
CMD ["sh", "-c", ".venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
