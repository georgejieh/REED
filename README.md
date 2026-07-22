# REED

Real-time Equity and Economic Digest.

## What it does

REED is a scheduled agent runtime that generates structured market digests. It runs a fixed cron schedule of research sessions, each one searching the web, scraping relevant articles, and producing a digest with a market snapshot, headline stories, sentiment per story, and a watchlist for the next session. The output is served through a read API and rendered by the bundled dashboard.

## Quick start

1. Clone the repo and change into it.
2. `cd backend && cp .env.example .env` and add keys for the providers you want to use.
3. `uv sync`
4. Run the setup wizard: `python cli_setup.py` (run from inside `backend/`). By default it reads `.env` from the repo root and detects which providers you have keys for.
5. Start the backend: `docker compose up backend` (or `uv run uvicorn app.main:app --port 8000`)
6. In a second terminal, start the dashboard:
   ```bash
   cd dashboard
   npm install
   npm run dev
   ```
   The dashboard proxies `/api` to `http://localhost:8000`.

## Configuration

The wizard writes `backend/settings.yaml` based on the keys it finds in `.env`. You can also place `.env` at the repo root. Recognized provider keys:

| Provider | Env key |
|----------|---------|
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |
| OpenRouter | `OPENROUTER_API_KEY` |
| Ollama | `OLLAMA_HOST` (local) + `OLLAMA_API_KEY` (cloud) |
| OpenAI-compatible | any `base_url` + `api_key` you configure in settings |

Optional search backends: `ddgs` (default, keyless), `BRAVE_API_KEY`, `TAVILY_API_KEY`.
Optional storage mode: `REED_STORE=mirror` plus `HF_DATASET_REPO` and `HF_TOKEN` for HF Spaces.

## Deployment targets

- **Local Docker**: `docker compose up backend` runs the backend on port 8000.
- **Hugging Face Space**: the backend Dockerfile exposes 7860 and reads config from env. Use an external cron to POST to `/api/trigger/{session}` when the Space sleeps.
- **VPS / self-hosted**: run the backend container anywhere and serve the dashboard as a static build.

## Architecture

See `docs/ARCHITECTURE.md` for the full system description.

## License

Apache-2.0. See `LICENSE`.

## Contributing

Not accepting contributions yet.
