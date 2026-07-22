# REED Architecture

## 1. Project identity

REED (Real-time Equity and Economic Digest) is a scheduled agent runtime with a single responsibility: run a fixed daily schedule of web-research sessions and publish each result as a structured digest. There is no chat interface, no prompt editor, and no per-run configuration. An operator sets provider keys once, runs a setup wizard, and the system produces digests on its own. External consumers read the published digests through a small, stable API.

## 2. High-level architecture

The repository is a monorepo: backend, dashboard, sample data, docs, and deployment files live together. The backend owns the agent runtime, scheduler, read API, and persistence. The dashboard is a React + Vite static frontend that consumes only the read API. A public consumer can call the same read endpoints or, for a fully static demo, read a published dataset mirror directly.

```
reed/
  backend/
    app/
      main.py              FastAPI app, lifespan, mounts routers
      config.py            pydantic-settings from .env
      scheduler.py         APScheduler AsyncIOScheduler, ET-aware
      providers/           LLM provider implementations
      news/                search + scrape pipeline
      market_data/         quote providers
      agents/                tool-calling loop
      digests/               models, generator, store
      api/                   read API routes
      sessions/              per-session job definitions
    cli_setup.py           operator setup wizard
    pyproject.toml         uv-managed Python package
    Dockerfile             python:3.12-slim, uvicorn on port 7860
  dashboard/
    src/                   React + TypeScript + Vite app
    package.json
    vite.config.ts
    tsconfig.json
  data/
    digests/               runtime output (gitignored)
    samples/               frozen sample digest
  scripts/                 operational helpers
  docs/
    ARCHITECTURE.md        this file
  .env.example
  docker-compose.yml
  README.md
  LICENSE
```

## 3. Provider abstraction

REED supports five LLM provider classes: Anthropic, OpenAI, OpenRouter, Ollama, and a generic OpenAI-compatible client. The generic client covers any service that speaks the OpenAI API shape: Together, Groq, Fireworks, DeepInfra, Google Gemini, Mistral, Cohere, xAI, Perplexity, vLLM, llama.cpp server, LM Studio, llamafile, and others. The operator supplies `base_url`, `api_key`, and `model`; nothing else is provider-specific.

The wizard detects which keys are present in `.env` and only offers the matching providers. There is no default provider and no default model. The provider interface exposes `generate(system, user, *, tools, tool_choice, max_turns, json_mode, model)` and capability flags for tool support and JSON mode, so the runner can fall back to a two-pass plan for local models that lack tool calling.

## 4. Agent loop

A session runs as a short agent loop:

1. Market data is pre-fetched before the agent sees anything, populating `market_snapshot` and `market_snapshot_meta` so the model cannot invent numbers.
2. A seed search returns a small starting set of URLs biased toward scrapable outlets.
3. The agent begins with the session's system prompt and a user prompt containing the seed URLs, time window, and topic.
4. Each turn the model may call `search_news(query)`, call `scrape_url(url)`, or emit a final answer. The runner enforces the configured rate limit and retries on rate-limit exceptions.
5. The final answer is parsed into the digest schema. On parse failure the error is sent back and retried up to two times.
6. A hard cap of six turns total applies. If the cap is reached without a parseable answer, the runner force-validates the best partial into a minimal digest so the dashboard never fails completely.
7. The runner merges agent-owned fields with runner-owned fields, validates the full Pydantic schema, and writes the digest to the store.

For Ollama models without tool support, the runner uses a deterministic two-pass fallback: ask for a JSON plan of searches and URLs, execute the tools, then ask again with `json_mode=True` for the final digest.

## 5. Digest data shape

The digest is the public contract between backend, dashboard, and any external consumer:

| Field | Owner | Meaning |
|-------|-------|---------|
| `id` | runner | session identifier, e.g. `2026-07-21-pre_market` |
| `session` | runner | session name |
| `as_of` | runner | generation timestamp |
| `headline` | agent | session headline |
| `executive_summary` | agent | short narrative summary |
| `market_snapshot` | runner | human-readable market summary |
| `market_snapshot_meta` | runner | provenance + raw values |
| `stories` | agent | array of ticker / headline / summary / sentiment / source |
| `themes` | agent | short list of themes |
| `watch_next_session` | agent | events to watch before the next session |
| `sources` | agent | numbered list of cited sources |
| `generation` | runner | provider, model, turns, tool calls, duration |

`market_snapshot` is what the dashboard renders as a four-tile strip. `market_snapshot_meta` carries the raw numbers, the source, and the fetch timestamp so readers can verify where the data came from.

## 6. Storage

The shipped product uses local JSON files. The `DigestStore` protocol exposes `write`, `get`, `list`, and `latest`, so the storage backend can be swapped without changing the API or agent code.

`JsonFileStore` writes one file per digest to `data/digests/YYYY-MM-DD-<job>.json` and rebuilds `data/digests/_index.json` on each write. On local or VPS deployments the disk is durable, so history browsing works without extra machinery.

For operators who deploy to a free Hugging Face Space, an optional `DatasetMirrorStore` wraps `JsonFileStore` and also pushes each digest to an HF Dataset repo. A failed push never fails a digest, and a failed pull on Space boot never blocks the app from starting. Local users never see or depend on the mirror; it is a deployment-mode knob, not a product feature.

## 7. Read API

The backend exposes a small read API:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | service health |
| GET | `/api/digests` | list all digest IDs, newest first |
| GET | `/api/digests/latest` | latest digest; optional `?session=` filter |
| GET | `/api/digests/{id}` | single digest by ID |
| GET | `/api/digests/{id}/snapshot` | snapshot at generation time |
| POST | `/api/trigger/{session}` | run a session now; requires `X-Trigger-Token` |

Read endpoints need no authentication. The trigger endpoint exists so an external cron can wake a sleeping Space by posting to it. It is disabled unless `REED_TRIGGER_TOKEN` is set.

## 8. Cron schedule

All session times are stored as US/Eastern in `settings.yaml` and converted to UTC for the scheduler.

| Job name | Time (ET) | Days |
|----------|-----------|------|
| weekend_recap | 07:00 Mon | Monday only |
| pre_market | 08:00 | Mon-Fri |
| early_market | 09:45 | Mon-Fri |
| midday | 12:30 | Mon-Fri |
| close | 16:15 | Mon-Fri |

The scheduler skips US market holidays: New Year's Day, MLK Day, Presidents Day, Good Friday, Memorial Day, Juneteenth, Independence Day, Labor Day, Thanksgiving, and Christmas. `REED_SKIP_HOLIDAYS=0` disables the skip. On HF Spaces the in-process scheduler can be turned off so an external cron drives the sessions instead.

## 9. Deployment targets

Three deployment targets are supported:

- **Local Docker**: `docker compose up backend` runs the backend on port 8000. The dashboard runs via `npm run dev` on port 5173 with a proxy to the backend.
- **Hugging Face Space**: the Dockerfile exposes 7860 and reads configuration from environment variables. An external cron is required because free Spaces sleep between requests; the cron POSTs to `/api/trigger/{session}` to wake the Space and run a session.
- **VPS / self-hosted**: run the backend container on any small box. APScheduler fires in-process, and the dashboard builds into `dist/` for static hosting.

## 10. Configuration

| File | Purpose |
|------|---------|
| `.env` / `backend/.env` | provider keys, trigger token, optional search/storage flags |
| `backend/settings.yaml` | wizard-written: provider, model, sessions, search backend, data dir |

Recognized environment variables:

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | OpenAI provider |
| `ANTHROPIC_API_KEY` | Anthropic provider |
| `OPENROUTER_API_KEY` | OpenRouter provider |
| `OLLAMA_HOST` / `OLLAMA_API_KEY` | Ollama local or cloud |
| `BRAVE_API_KEY` | Brave Search |
| `TAVILY_API_KEY` | Tavily Search |
| `REED_TRIGGER_TOKEN` | enables `POST /api/trigger/{session}` |
| `REED_STORE` | `local` or `mirror` |
| `REED_SEARCH_PROVIDER` | `ddgs`, `brave`, `tavily` |

## 11. CLI wizard behavior

`python backend/cli_setup.py` detects which provider keys are present, lists only the matching providers, asks the operator to pick a provider and model, prompts for any provider-specific fields, and writes `backend/settings.yaml`. Re-running the wizard overwrites the file cleanly. If no provider keys are present the wizard exits with a message pointing at `.env.example`.
