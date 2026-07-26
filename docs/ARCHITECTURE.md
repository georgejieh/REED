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
      news/                RSS pre-flight and bounded article scraping
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

The wizard detects which keys are present in `.env` and only offers the matching providers. There is no default provider and no default model. The provider interface exposes `generate(system, user, *, tools, tool_choice, max_turns, json_mode, model)` and capability flags for tool support and JSON mode. The current runner does not implement the documented two-pass plan for local models; only the cron path runs in production and it uses a single OpenRouter-backed Gemini call.

## 4. Session run shape

A session runs as a single LLM call with no tools exposed to the model:

1. Market data is pre-fetched before the model sees anything, populating `market_snapshot` and `market_snapshot_meta` so the model cannot invent numbers. `MarketSnapshotMeta.values_raw` carries the per-symbol values the dashboard reads.
2. A curated RSS pre-flight fetches and deduplicates public headlines for the session, filtered by the session's `time_window` (e.g. "last 12 hours"). Entries with unparseable timestamps are dropped; entries with future-dated timestamps (> now + 15 min) are dropped.
3. The runner calls `provider.generate` once with the session system prompt and a user prompt containing the headlines, time window, and topic. `tools=[]` and `max_turns=1`.
4. The response is parsed as JSON. On parse failure the runner retries once with a corrective system prompt; if that still fails, a balanced-JSON extractor is tried as a last resort.
5. If no parseable JSON is recovered, the runner synthesizes a fallback digest with `fallback_used=True` so the trigger does not 500 and the dataset repo still gets a record.
6. The runner merges runner-owned fields (`id`, `session`, `as_of`, `market_snapshot`, `market_snapshot_meta`, `generation`) with agent-owned fields and validates the full Pydantic schema before writing.

The bounded `scrape_url` tool is still exposed via `bind_scrape_tool()` for operator-driven CLI use, but it is not wired into the session agent loop. Returning `[]` from `get_agent_tools()` enforces this at runtime.

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
| POST | `/api/trigger/{session}` | run a session now; requires `X-REED-Token` |

Read endpoints need no authentication. The trigger endpoint exists so an external cron can wake a sleeping Space by posting to it. It is disabled unless `REED_TRIGGER_TOKEN` is set.

## 8. Cron schedule

Canonical session times are defined in `backend/app/scheduler.py` as `SCHEDULE` (US/Eastern). `.github/workflows/reed-trigger.yml` mirrors them in UTC for HF Spaces and must be updated whenever `scheduler.py` changes. `backend/settings.yaml` does not contain session times.

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
| `.env` / `backend/.env` | provider keys, trigger token, and optional storage flags |
| `backend/settings.yaml` | wizard-written: provider, model, sessions, tool budget, data dir |

Recognized environment variables:

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | OpenAI provider |
| `ANTHROPIC_API_KEY` | Anthropic provider |
| `OPENROUTER_API_KEY` | OpenRouter provider |
| `OLLAMA_HOST` / `OLLAMA_API_KEY` | Ollama local or cloud |
| `REED_TRIGGER_TOKEN` | enables `POST /api/trigger/{session}` |
| `REED_STORE` | `local` or `mirror` |
| `HF_DATASET_REPO` / `HF_TOKEN` | Dataset mirror credentials |

## 11. CLI wizard behavior

The wizard detects which provider keys are present, lists only the matching providers, asks the operator to pick a provider and model, prompts for any provider-specific fields, and writes `backend/settings.yaml`. News feeds are configured in code by session and do not require a search-provider selection. Re-running the wizard overwrites the file cleanly. If no provider keys are present the wizard exits with a message pointing at `.env.example`.
