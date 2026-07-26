# REED

Real-time Equity and Economic Digest.

REED is a self-hosted, local-first market-news agent. Clone the repo,
run the setup wizard, and you have a personal webapp that produces
five scheduled market briefs per US-trading day, with a terminal-style
React dashboard to read them on. Bring your own LLM key. REED runs
on your machine, on a small VPS, or on a free Hugging Face Space if
you want a hosted demo.

## Why I built it

I wanted a market brief that reads like something a person would write,
on a schedule I do not have to think about, without paying for a data
terminal or handing my reading habits to an ad-funded feed. Most AI
news tools either wrap a single vendor's model or lean on an RSS pile
that goes stale the moment a feed breaks. REED is the version I
actually wanted: an agent that goes and finds the day's stories
itself, runs on whatever model I feel like pointing it at, and keeps
every past brief so I can scroll back.

## What REED is

- A Python backend (FastAPI) that owns the LLM call, the RSS pre-flight,
  the scheduler, and a small read API.
- A React + Vite dashboard that reads the backend's API and renders
  every past brief in a terminal-style layout.
- A setup wizard that detects which provider keys you have and writes
  `settings.yaml` for you.
- A local cron (APScheduler) that fires five weekday sessions plus one
  Monday-morning weekend recap. On a small box, this is the only cron
  you need.
- An optional HF Space deployment with an external GitHub Actions cron
  and a static reader on GitHub Pages. The author uses this variant
  to demo REED on `georgejieh.dev/reed`. For your own deployment,
  the local-first path is the primary use case.

## What REED is not

- Not a multi-provider agent runtime. The wizard supports five
  provider classes for flexibility, but the production path uses
  OpenRouter with one model.
- Not a multi-turn agent. The session runs as a single LLM call with
  zero tools exposed. The pre-flight is the research.
- Not a search-driven news pipeline. The RSS feeds are the news.
  No Brave, no Firecrawl, no news-search API key.
- Not a chatbot, not a prompt editor, not a watchlist service. REED
  runs the schedule, writes the briefs, and renders them. That is it.

## How it works

Each scheduled session runs the same shape, synchronously inside the
trigger:

1. **RSS pre-flight.** Curated public feeds for the session's time
   window are fetched and deduplicated. Entries without a usable
   timestamp are dropped. Entries dated more than 15 minutes in the
   future relative to the trigger time are dropped.
2. **Single LLM call.** The runner calls `provider.generate` once
   with the session system prompt and a user prompt containing the
   headlines, time window, and topic. `tools=[]`, `max_turns=1`,
   `json_mode=true`.
3. **Coerce.** Stories whose `source_url` is not in the pre-fetched
   link set are dropped. Stories with non-string or empty headlines
   are coerced; sentiment is normalized to one of `bullish`/`bearish`/
   `neutral`.
4. **Validate.** The full Pydantic `Digest` schema is enforced.
5. **Persist.** One JSON file per digest plus a rebuilt `_index.json`
   is written to the local `data_dir` (or pushed to an HF Dataset repo
   in `mirror` mode).
6. **Display.** The dashboard reads the latest briefs from the local
   API (or from the dataset repo in static-reader mode) and renders
   them.

The LLM writes the prose. The runner owns the schema, the timestamps,
the session metadata, and the market snapshot. The split keeps the
error-prone parts off the model.

## Schedule

Five sessions, ET, weekdays except `weekend_recap` which is Monday
only. The local scheduler skips US market holidays automatically.

| Session       | Time (ET) | Days    |
|---------------|-----------|---------|
| Pre-Market    | 08:00     | Mon-Fri |
| Early Market  | 09:45     | Mon-Fri |
| Midday        | 12:30     | Mon-Fri |
| Market Close  | 16:15     | Mon-Fri |
| Weekend Recap | 07:00     | Monday  |

On a small box, the in-process APScheduler fires these automatically
and the dashboard refreshes with each new brief. No external cron
needed.

On a Hugging Face Space, the Space sleeps between requests. The
HF deployment uses an external cron (GitHub Actions) that POSTs to
the Space's token-protected trigger endpoint on the same schedule.
See `docs/HF_DEPLOYMENT.md`.

## The dashboard

The local dashboard is a single-page React + Vite app styled like a
cross between an email client and a market terminal. A list of briefs
on the left, newest first and open by default, and the full digest on
the right: headline, summary, a market-snapshot strip, story cards
with sentiment and ticker chips, and numbered sources. Built for
reading, not clicking. Keyboard navigation, no settings to fiddle
with, every past brief one arrow-key away.

The local dashboard runs at `http://localhost:5173` and proxies the
backend at `http://localhost:8000`. The HF-hosted static demo at
`georgejieh.dev/reed` reads the public HF Dataset repo instead.

## Architecture

REED is one repository with a clean split:

- **Backend.** FastAPI. Owns the provider abstraction, the RSS
  pre-flight, the single-turn runner, the scheduler, and a small
  read API.
- **Dashboard.** React with Vite and TypeScript. Reads the backend's
  API in dev; reads a baked sample or public dataset repo in static
  demo mode.
- **Setup wizard.** `backend/cli_setup.py` detects provider keys and
  writes `backend/settings.yaml`.

Digests are stored as JSON. Local and self-hosted deployments keep
them on disk. On ephemeral hosts, REED mirrors every brief to a
durable store and rehydrates its full history on restart, so no past
brief is lost.

## Bring your own model

The wizard supports five provider classes. Pick whichever you already
pay for:

- **Anthropic** and **OpenAI** with first-party APIs.
- **OpenRouter** with one key and hundreds of models across every
  major lab. The production path uses `google/gemini-2.5-flash` via
  OpenRouter.
- **Ollama** for local models on your own machine, or Ollama Cloud.
- **Any OpenAI-compatible endpoint**: Together, Groq, Fireworks,
  DeepInfra, Google Gemini, Mistral, xAI, Perplexity, vLLM, LM Studio,
  and the rest. If it speaks the OpenAI API, REED can use it.

There is no default provider and no default model. The wizard
detects which keys you have and lets you pick.

## Configuration

`backend/settings.yaml` is the operator-written config: provider,
model, sessions, market data settings. The wizard writes it on first
run. Environment variables carry secrets and deployment-mode flags.

Recognized environment variables:

| Variable                | Purpose                                              |
|-------------------------|------------------------------------------------------|
| `OPENAI_API_KEY`        | OpenAI provider                                      |
| `ANTHROPIC_API_KEY`     | Anthropic provider                                   |
| `OPENROUTER_API_KEY`    | OpenRouter provider                                  |
| `OLLAMA_HOST`           | Ollama local; or `OLLAMA_API_KEY` for cloud         |
| `REED_STORE`            | `local` (default) or `mirror` for HF Dataset        |
| `HF_DATASET_REPO`       | dataset repo, used only when `REED_STORE=mirror`     |
| `HF_TOKEN`              | write token for the dataset repo                     |
| `REED_TRIGGER_TOKEN`    | enables `POST /api/trigger/{session>` (HF deployment)|

The wizard writes `settings.yaml` based on what keys you have. You can
also hand-write the file. See `cli_setup.py` for the full schema.

## Quick start

```bash
git clone https://github.com/georgejieh/REED
cd REED/backend
uv sync
cp .env.example .env  # add at least one provider key
python cli_setup.py    # picks provider, model, writes settings.yaml
uv run uvicorn app.main:app --port 8000
```

In a second terminal:

```bash
cd REED/dashboard
npm install
npm run dev
```

Open `http://localhost:5173` to see the dashboard. The backend
scheduler fires at 08:00 ET weekdays. The dashboard shows each new
brief as it lands.

For the demo build that reads from a baked sample (no backend
needed):

```bash
cd REED/dashboard
npm run build:demo
```

The `dist/` output is a static site. Host it anywhere. The author's
demo at `georgejieh.dev/reed` is this build, reading from the public HF
Dataset repo.

## Deployment

The backend is built to run anywhere:

- **Local.** The default. `uv run uvicorn app.main:app` and
  `npm run dev` and you are done.
- **Small VPS.** The same containers on any always-on box. The
  scheduler runs in-process. One box is enough.
- **Docker compose.** `docker compose up backend dashboard` runs the
  full stack together.
- **Hugging Face Space.** An HF-compatible image with the in-process
  scheduler turned off and an external cron driving the trigger
  endpoint. The author's demo uses this. See `docs/HF_DEPLOYMENT.md`
  for the full operator runbook.

The dashboard has two build modes. `npm run build` reads the live API
and is meant to be served next to the backend. `npm run build:demo`
reads a public HF Dataset repo at build time, falls back to a baked
sample when the dataset is unreachable, and is meant to be hosted
statically on any CDN.

## Architecture reference

See `docs/ARCHITECTURE.md` for the full system description.

## License

Apache-2.0.

## Contributing

Not accepting contributions yet.
