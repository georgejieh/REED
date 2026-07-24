FROM python:3.12-slim

WORKDIR /app

# uv for fast, deterministic installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

COPY pyproject.toml uv.lock ./
COPY settings.yaml ./
RUN uv sync --frozen --no-dev

COPY app ./app

ENV PYTHONUNBUFFERED=1
EXPOSE 7860

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
