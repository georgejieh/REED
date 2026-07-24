# REED

Real-time Equity and Economic Digest.

REED is a self-hosted AI agent that researches US market news on a fixed
schedule and publishes structured briefs to a terminal-style dashboard.
It runs a small research loop several times a trading day. Before the
open, through the session, and after the close. It searches the web for
what actually moved, reads the sources, and writes a concise digest with
sentiment, tickers, and citations. You bring your own LLM key. REED stays
out of the way.

## Why I built it

I wanted a market brief that reads like something a person would write, on
a schedule I don't have to think about, without paying for a data terminal
or handing my reading habits to an ad-funded feed. Most AI news tools
either wrap a single vendor's model or lean on an RSS pile that goes stale
the moment a feed breaks. REED is the version I actually wanted: an agent
that goes and finds the day's stories itself, runs on whatever model I
feel like pointing it at, and keeps every past brief so I can scroll
back.

## How it works

Each scheduled session runs the same shape:

1. **Seed.** A small search seeds the agent with a few anchor stories for
   the session's time window.
2. **Research.** The agent uses a web-search tool and a page-scraper tool
   to find and read the articles that matter, deciding for itself what
   is relevant rather than following a fixed keyword list.
3. **Ground.** Live index, Treasury, and volatility numbers are fetched
   from a market-data source before the model runs, so the snapshot
   figures come from real data, not the model's memory.
4. **Write.** The agent returns a structured JSON digest. Headline,
   executive summary, per-story sentiment and tickers, themes, and
   numbered source citations.
5. **Publish.** The digest is stored and served to the dashboard, with
   the newest brief open by default.

The report text is written by the model you choose. The market numbers
are not. They are fetched and merged in separately, which keeps the most
error-prone part of a market brief off the model's plate.

## Bring your own model

REED is provider-agnostic by design. Point it at whatever you already pay
for:

- **Anthropic** and **OpenAI** with first-party APIs.
- **OpenRouter** with one key and hundreds of models across every major
  lab.
- **Ollama** for local models on your own machine, or Ollama Cloud.
- **Any OpenAI-compatible endpoint**: Together, Groq, Fireworks, DeepInfra,
  Google Gemini, Mistral, xAI, Perplexity, vLLM, LM Studio, and the rest.
  If it speaks the OpenAI API, REED can use it.

There is no default provider and no default model. A first-run setup
wizard detects which keys you have and lets you pick.

## Web search, your choice

News discovery is pluggable the same way models are:

- **DuckDuckGo** (default). Keyless, no signup.
- **Brave Search** and **Tavily**. Free tiers, more reliable for
  unattended, always-on deployments.

## Schedule

REED runs five sessions, in US/Eastern, and skips US market holidays:

| Session       | Time (ET) | Days    |
|---------------|-----------|---------|
| Pre-Market    | 08:00     | Mon-Fri |
| Early Market  | 09:45     | Mon-Fri |
| Midday        | 12:30     | Mon-Fri |
| Market Close  | 16:15     | Mon-Fri |
| Weekend Recap | 07:00     | Monday  |

## The dashboard

The reader is a single-page app styled like a cross between an email
client and a market terminal. A list of briefs on the left, newest first
and open by default, and the full digest on the right: headline, summary,
a market-snapshot strip, story cards with sentiment and ticker chips, and
numbered sources. Built for reading, not clicking. Keyboard navigation,
no settings to fiddle with, every past brief one arrow-key away.

## Architecture

REED is one repository with a clean split:

- **Backend.** FastAPI. Owns the provider abstraction, the search and
  scrape pipeline, the agent loop, the scheduler, and a small read API.
- **Dashboard.** React with Vite and TypeScript. Reads the backend's
  API and holds no credentials.

Digests are stored as JSON. Local and self-hosted deployments keep them
on disk. On ephemeral hosts, REED mirrors every brief to a durable store
and rehydrates its full history on restart, so no past brief is lost.

## Configuration

The wizard writes `backend/settings.yaml` based on the keys it finds in
`.env`. You can also place `.env` at the repo root.

Recognized provider keys:

| Provider            | Env var                                       |
|---------------------|-----------------------------------------------|
| OpenAI              | `OPENAI_API_KEY`                              |
| Anthropic           | `ANTHROPIC_API_KEY`                           |
| OpenRouter          | `OPENROUTER_API_KEY`                          |
| Ollama              | `OLLAMA_HOST` (local) plus `OLLAMA_API_KEY` for cloud |
| OpenAI-compatible   | any `base_url` plus `api_key` you configure in settings |

Optional search backends: `ddgs` (default, keyless), `BRAVE_API_KEY`,
`TAVILY_API_KEY`. Optional storage mode: `REED_STORE=mirror` plus
`HF_DATASET_REPO` and `HF_TOKEN` for Hugging Face Spaces.

### Search providers

Set `search.provider` to one of `ddgs`, `brave`, or `tavily` in
`backend/settings.yaml`:

- `ddgs`: DuckDuckGo, keyless.
- `brave`: Brave Search API, requires `BRAVE_API_KEY`.
- `tavily`: Tavily API, requires `TAVILY_API_KEY`.

Sessions are defined in `backend/app/sessions/`. Built-in sessions:

- `pre_market`, before the US equity open.
- `early_market`, the first hour of trading.
- `midday`, the mid-session recap.
- `close`, the end-of-day summary.
- `weekend_recap`, a Saturday wrap of the week.

## Quick start

1. Clone the repo and change into it.
2. `cd backend && cp .env.example .env` and add keys for the providers
   you want to use.
3. `uv sync`.
4. Run the setup wizard: `python cli_setup.py` (run from inside
   `backend/`). By default it reads `.env` from the repo root and
   detects which providers you have keys for.
5. Start the backend: `docker compose up backend`, or run it directly
   with `uv run uvicorn app.main:app --port 8000`.
6. In a second terminal, start the dashboard:
   ```bash
   cd dashboard
   npm install
   npm run dev
   ```
   The dashboard proxies `/api` to `http://localhost:8000`.

## Deployment

The backend is built to run anywhere and assumes no specific host:

- **Local.** One `docker compose up` runs the backend and dashboard
  together.
- **VPS.** The same container on a small always-on box, with the
  scheduler running in-process. See `scripts/deploy_vps.sh` for an
  idempotent deploy helper.
- **Hugging Face Spaces.** An HF-compatible image, woken on schedule by
  an external cron that calls a token-protected trigger endpoint. See
  `docs/HF_DEPLOYMENT.md` for the full setup.

The dashboard has two build modes. `npm run build` reads the live API
and is meant to be served next to the backend. `npm run build:demo`
reads a public HF Dataset repo at build time, falls back to a baked
sample when the dataset is unreachable, and is meant to be hosted
statically on any CDN. See `docs/HF_DEPLOYMENT.md` for the URL
pattern.

## Roadmap

The first release targets the full pipeline described above: the five
provider classes, pluggable search, the agentic research loop, grounded
market snapshots, the scheduler with holiday handling, the read API, the
dashboard, and local plus Hugging Face deployment. Paid data feeds,
real-time (non-delayed) quotes, and any watchlist or personalization
features are deliberately out of scope for the first version.

## Architecture reference

See `docs/ARCHITECTURE.md` for the full system description.

## License

Apache-2.0. See `LICENSE`.

## Contributing

Not accepting contributions yet.