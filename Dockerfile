FROM node:22-alpine@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2 AS dashboard

WORKDIR /dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build

FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest@sha256:606e70c71c852d03f611b1e56a195d08648507018a7057fab82c4974c4eae105 /uv /uvx /usr/local/bin/
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
