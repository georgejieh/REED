# REED Architecture

## 1. Project identity

REED (Real-time Equity and Economic Digest) is a self-hosted,
local-first market-news webapp. Clone the repo, run the setup
wizard, and you have a personal application that produces five
scheduled market briefs per US-trading day, with a terminal-style
dashboard to read them on. Bring your own LLM key. The same code
runs on a small VPS, on a Docker compose stack, or on a Hugging Face
Space; the local-first path is the primary use case, the hosted
variants are deployment modes.

The first round of REED attempted a multi-provider, multi-turn,
search-and-scrape-driven agent. That round was over-engineered for a
daily digest and was rewritten down to the current scope: one
provider, one model, one turn, one tool-free call, RSS-only news
discovery. Everything else is in the codebase for the operator's
CLI use or for future expansion, but is not on the cron path.

## 2. High-level architecture

```
reed/
  backend/
    app/
      main.py              FastAPI app, lifespan, mounts routers
      config.py            pydantic-settings from .env
      scheduler.py         APScheduler, ET-aware, NYSE holiday skip
      market_calendar.py   NYSE holiday check shared with trigger path
      providers/           LLM provider implementations
      news/                RSS pre-flight module
      market_data/         quote providers
      agents/              single-turn runner, tool bindings
      digests/             models, generator, store
      api/                 read API + trigger endpoint
      sessions/            per-session job definitions
    cli_setup.py           operator setup wizard
    pyproject.toml         uv-managed Python package
    Dockerfile             python:3.12-slim, uvicorn on port 7860
  dashboard/
    src/                   React + Vite + TypeScript app
    package.json
    vite.config.ts
    tsconfig.json
  data/
    digests/               runtime output (gitignored)
  scripts/                 operational helpers
  docker-compose.yml       local full-stack
  docs/
    ARCHITECTURE.md        this file
    HF_DEPLOYMENT.md       operator runbook for HF Space setup
  .env.example
  README.md
  LICENSE
```

## 3. Local-first deployment (default)

The default deployment is local-first. The operator runs the setup
wizard once, then `uv run uvicorn app.main:app` starts the backend
and `npm run dev` starts the dashboard. The in-process APScheduler
fires five weekday sessions plus one Monday-morning weekend recap.
Each session writes one JSON file per digest to `data/digests/` and
rebuilds `data/digests/_index.json`. The dashboard reads the backend
API and renders the briefs.

The cron story is "you do not have to do anything." APScheduler
fires at 08:00 ET weekdays; the dashboard refreshes with each new
brief. If the operator's machine is asleep, the session is missed
for that day. That is the documented behavior for the local path.
The trigger endpoint exists for operator-driven manual runs and for
the HF deployment.

## 4. Setup wizard

`backend/cli_setup.py` is the operator's first-run flow:

1. Reads `.env` and detects which provider keys are present.
2. Lists only the matching providers.
3. Asks the operator to pick a provider and model.
4. Prompts for any provider-specific fields.
5. Writes `backend/settings.yaml`.

The wizard accepts multiple keys at once (e.g. OpenAI plus OpenRouter).
The chosen provider is the cron path; the others remain available for
operator-driven CLI experimentation. Re-running the wizard overwrites
`settings.yaml` cleanly. If no provider keys are present the wizard
exits with a message pointing at `.env.example`.

## 5. HF deployment (optional, hosted variant)

`docs/HF_DEPLOYMENT.md` is the full operator runbook for the
hosted variant. The HF Space sleeps between requests, so the
in-process scheduler is disabled and an external cron (GitHub
Actions) drives the trigger endpoint. Storage is set to `mirror`:
every digest is pushed to a public HF Dataset repo so the static
reader at `georgejieh.dev/reed` (a separate portfolio site, not part
of this repo) can render every past brief without hitting the
Space.

The HF deployment is the author's demo. The primary use case for
REED is local-first. If you fork and adapt, you do not need any of
the HF-specific plumbing.

## 6. Provider abstraction

The provider layer ships five implementations: OpenAI, Anthropic,
OpenRouter, Ollama, and a generic OpenAI-compatible client. The cron
path uses OpenRouter with `google/gemini-2.5-flash` in production.
The other providers exist for the operator's CLI experimentation,
not for the cron.

The provider interface exposes `generate(system, user, *, tools,
tool_choice, max_turns, json_mode, model)` and capability flags. The
cron path passes `tools=[]`, `max_turns=1`, and `json_mode=true` on
every call. Two-pass plans and tool-calling fallbacks from the
original architecture are not implemented; they were cut during the
simplification.

## 7. Session run shape

A session runs as a single LLM call with no tools exposed to the
model:

1. Market data is pre-fetched before the model sees anything,
   populating `market_snapshot` and `market_snapshot_meta` so the
   model cannot invent numbers. `MarketSnapshotMeta.values_raw`
   carries the per-symbol values the dashboard reads.
2. A curated RSS pre-flight fetches and deduplicates public headlines
   for the session, filtered by the session's `time_window` (e.g.
   "last 12 hours"). Entries with unparseable timestamps are dropped.
   Entries dated more than 15 minutes in the future relative to the
   trigger time are dropped (clock skew, timezone bugs, promo items).
3. The runner calls `provider.generate` once with the session system
   prompt and a user prompt containing the headlines, time window, and
   topic. `tools=[]` and `max_turns=1`.
4. The response is parsed as JSON. On parse failure the runner retries
   once with a corrective system prompt; if that still fails, a
   balanced-JSON extractor is tried as a last resort.
5. If no parseable JSON is recovered, the runner synthesizes a fallback
   digest with `fallback_used=True` so the trigger does not 500 and
   the dataset repo still gets a record.
6. The runner coerces null/missing fields in the parsed JSON
   (stories whose `source_url` is not in the pre-fetched link set are
   dropped, sentiment is normalized to one of the three valid
   literals), then merges runner-owned fields (`id`, `session`,
   `as_of`, `market_snapshot`, `market_snapshot_meta`, `generation`)
   with agent-owned fields and validates the full Pydantic schema
   before writing.

The bounded `scrape_url` tool is still exposed via
`bind_scrape_tool()` for operator-driven CLI use, but it is not
wired into the session agent loop. Returning `[]` from
`get_agent_tools()` enforces that at runtime.

## 8. Digest data shape

The digest is the public contract between backend, dashboard, and
any external consumer:

| Field                 | Owner | Meaning                                     |
|-----------------------|-------|---------------------------------------------|
| `id`                  | runner | session identifier, e.g. `2026-07-21-pre_market` |
| `session`             | runner | session name                                |
| `as_of`               | runner | generation timestamp                        |
| `headline`            | agent | session headline                            |
| `executive_summary`   | agent | short narrative summary                      |
| `market_snapshot`     | runner | human-readable market summary               |
| `market_snapshot_meta`| runner | provenance + raw values                     |
| `stories`             | agent | array of ticker / headline / summary / sentiment / source |
| `themes`              | agent | short list of themes                        |
| `watch_next_session`  | agent | events to watch before the next session     |
| `sources`             | agent | numbered list of cited sources              |
| `generation`          | runner | provider, model, turns, tool calls, duration, fallback_used |

`market_snapshot` is what the dashboard renders as a tile strip.
`market_snapshot_meta` carries the raw numbers, the source, and the
fetch timestamp so readers can verify where the data came from.

## 9. Storage

`JsonFileStore` writes one JSON file per digest to `data_dir` and
rebuilds `_index.json` on each write. The default deployment uses
local disk and the disk is durable. The HF deployment uses
`DatasetMirrorStore`, which wraps `JsonFileStore` and also pushes
each digest to an HF Dataset repo. A failed push never fails a
digest; the next successful push retries. On Space boot the local
disk is rebuilt from the dataset repo so no past brief is lost.

The local-first path does not need the dataset repo. It is a
deployment-mode knob.

## 10. Read API

The backend exposes a small read API:

| Method | Path                          | Description                  |
|--------|-------------------------------|------------------------------|
| GET    | `/api/health`                 | service health               |
| GET    | `/api/digests`                | list all digest IDs, newest first |
| GET    | `/api/digests/{id}`           | single digest by ID          |
| POST   | `/api/trigger/{session}`      | run a session, requires `X-REED-Token` |

The read API is for the local dashboard and for operator curl probes.
The HF static demo reads the public dataset repo instead and does
not hit the API at runtime.

The trigger endpoint accepts the `X-REED-Token` header when
`REED_TRIGGER_TOKEN` is set, fail-closed in production when unset,
and accepts an optional `?as_of=ISO8601` query parameter for
backfilling past dates.

## 11. Cron schedule

Canonical session times are defined in
`backend/app/scheduler.py SCHEDULE` (US/Eastern).

| Job name        | Time (ET) | Days    |
|-----------------|-----------|---------|
| weekend_recap   | 07:00 Mon | Monday  |
| pre_market      | 08:00     | Mon-Fri |
| early_market    | 09:45     | Mon-Fri |
| midday          | 12:30     | Mon-Fri |
| close           | 16:15     | Mon-Fri |

The scheduler skips US market holidays. The `is_us_market_holiday`
check lives in `app/market_calendar.py` and is shared between the
in-process scheduler and the HF trigger endpoint, so both firing
paths use the same calendar.

## 12. Why this scope

The first round of REED had a multi-turn agent loop, a search tool,
and a scrape tool. The search layer cost money (Brave, Firecrawl)
and added latency; the multi-turn loop produced inconsistent JSON;
the scrape tool was unused because the RSS pre-flight already had
the day's stories. Removing all three cut cold-trigger time from
60-120s to 10-30s and dropped the cost from ~$0.05 per brief to
~$0.005.

The bounded `scrape_url` tool is still in the codebase for the
operator's CLI use. The provider abstraction is still multi-provider
for the same reason. Everything else is on the cron path or
removed.

## 13. Configuration

| File                    | Purpose                                              |
|-------------------------|------------------------------------------------------|
| `backend/.env`          | provider keys and deployment flags                   |
| `backend/settings.yaml` | wizard-written: provider, model, sessions, market data |

Recognized environment variables:

| Variable                | Purpose                                              |
|-------------------------|------------------------------------------------------|
| `OPENAI_API_KEY`        | OpenAI provider                                      |
| `ANTHROPIC_API_KEY`     | Anthropic provider                                   |
| `OPENROUTER_API_KEY`    | OpenRouter provider (used in production)             |
| `OLLAMA_HOST`           | Ollama local                                         |
| `REED_TRIGGER_TOKEN`    | enables `POST /api/trigger/<session>` (HF deployment) |
| `REED_STORE`            | `local` (default) or `mirror` for HF Dataset        |
| `HF_DATASET_REPO`       | dataset repo, used only when `REED_STORE=mirror`     |
| `HF_TOKEN`              | write token for the dataset repo                     |
| `REED_SKIP_HOLIDAYS`    | `1` (default) skips NYSE holidays                    |
| `REED_ENV`              | `prod` (default) or `dev`                           |

## 14. Deployment targets

- **Local-first (default).** The setup wizard, then `uv run uvicorn`
  and `npm run dev`. The in-process scheduler fires the five sessions
  plus the Monday recap automatically. The dashboard refreshes with
  each new brief.
- **Docker compose.** `docker compose up backend dashboard` runs the
  full stack together.
- **Small VPS.** The same containers on any always-on box.
- **Hugging Face Space.** The HF-compatible image with the
  in-process scheduler turned off and an external cron driving the
  trigger endpoint. The author's demo uses this. See
  `docs/HF_DEPLOYMENT.md`.

The static demo at `georgejieh.dev/reed` is a separate portfolio
site, deployed to GitHub Pages, and reads the public HF Dataset repo
directly. It is the author's personal demo of the project, not a
deployment variant of REED itself.
