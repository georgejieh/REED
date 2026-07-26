# REED — Real-time Equity and Economic Digest

[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/downloads/)
[![Node.js](https://img.shields.io/badge/node-18%2B-green)](https://nodejs.org/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

**REED is a self-hosted market news agent that writes scheduled stock market briefs from public RSS feeds using the LLM of your choice.** Clone the repo, run the setup wizard, and you have a local webapp that produces a market brief on its own schedule and a terminal-style React dashboard to read every past brief in. It runs on your own machine, needs no financial data subscription, and works with any model you already pay for: Anthropic, OpenAI, OpenRouter, Ollama, or any OpenAI-compatible endpoint.

A hosted demo of the reader is at [georgejieh.dev/reed](https://georgejieh.dev/reed). The demo is a static build of the same dashboard, so it shows what REED produces without you having to run anything.

---

## Table of Contents

- [Why I Built It](#why-i-built-it)
- [How It Works](#how-it-works)
- [The Schedule](#the-schedule)
- [What REED Is Not](#what-reed-is-not)
- [The Dashboard](#the-dashboard)
- [Quick Start](#quick-start)
- [Bring Your Own Model](#bring-your-own-model)
- [Configuration](#configuration)
- [Deployment](#deployment)
- [What This Does Not Do](#what-this-does-not-do)
- [Project Structure](#project-structure)
- [License](#license)

## Why I Built It

I wanted a market brief that reads like a person wrote it, on a schedule I do not have to think about, without paying for a data terminal or handing my reading habits to an ad-funded feed.

Most AI news tools fail in one of two ways. Either they wrap a single vendor's model, so your reading depends on that vendor's pricing and availability, or they hand the model a search tool and let it go find the news itself. The second approach is the one I actually tried first, and it is why REED works the way it does now. I built a version with a web crawler and a scraper, and it did not hold up: scraping was slow, frequently blocked, and inconsistent enough that the briefs could not be trusted. As a result I moved the research step off the model entirely. The feeds are fetched and filtered before the model is ever called, and the model's only job is to write.

That split is the whole design. The runner owns the parts a language model is bad at, which is timestamps, schemas, deduplication, and knowing what is actually recent. The model owns the part it is good at, which is turning thirty headlines into readable prose. Meaning when something goes wrong, the failure is almost always in code I can inspect and fix, rather than in a model's judgment I can only re-prompt and hope about.

## How It Works

Every scheduled session runs the same six steps, synchronously, inside one trigger.

1. **RSS pre-flight.** Curated public feeds for that session are fetched concurrently, capped at 15 entries per outlet and 25 per session, and deduplicated by link. Each response is checked for an XML content type and capped at 5 MB, so an oversized or wrong-typed feed cannot take the process down.

2. **Time filter.** Entries are kept only if they fall inside the session's window, for example the last 12 hours. Entries dated more than 15 minutes in the future are dropped, because real feeds carry clock-skew and promo items. Entries with no usable timestamp are also dropped. The model has no web access and its training data predates the session, so it cannot judge whether an undated item belongs in today's brief, and an undated entry that reaches the prompt is indistinguishable from a current one.

3. **One LLM call.** The runner calls the provider exactly once with `tools=[]`, `max_turns=1`, and `json_mode=true`. The session prompt carries the filtered headlines, the time window, the topic, and a live market snapshot. There is no tool loop and no second turn.

4. **Coerce.** Any story whose `source_url` is not in the pre-fetched link set is dropped, which is what stops the model from inventing a plausible-looking URL. Sentiment is normalized to `bullish`, `bearish`, or `neutral`, and null or missing fields are filled with defaults.

5. **Validate.** The full Pydantic `Digest` schema is enforced before anything is written.

6. **Persist and display.** One JSON file per digest is written with an atomic rename, and a small `_index.json` is rebuilt on every write so a corrupted index recovers on the next run. The dashboard reads from there.

The market snapshot is fetched separately from Stooq and merged into the payload by the runner rather than the model, so the numbers in a brief are numbers REED looked up, not numbers the model recalled.

## The Schedule

Four briefs on a normal trading day, plus a weekend recap on Monday morning, for five session types in total. Times are US/Eastern.

| Session       | Time (ET) | Days    |
|---------------|-----------|---------|
| Pre-Market    | 08:00     | Mon-Fri |
| Early Market  | 09:45     | Mon-Fri |
| Midday        | 12:30     | Mon-Fri |
| Market Close  | 16:15     | Mon-Fri |
| Weekend Recap | 07:00     | Monday  |

The in-process scheduler (APScheduler) fires these automatically, and it skips days the NYSE is closed using the `exchange_calendars` XNYS calendar. On an always-on machine this is the only cron you need. The holiday check lives in a shared module used by both the scheduler and the HTTP trigger, so both firing paths behave the same way.

Sessions can be backfilled. The trigger endpoint accepts an `as_of` query parameter, which anchors both the RSS time filter and the digest's own timestamp to a past date.

## What REED Is Not

- **Not a multi-turn agent.** One call, no tools, no loop. The pre-flight is the research step.
- **Not a search-driven pipeline.** The RSS feeds are the news. There is no search API key, no crawler, and no scraper. An earlier version had them and they were removed.
- **Not a multi-provider runtime.** The wizard supports five provider classes so you can use what you already have, but one session uses one model.
- **Not a chatbot, watchlist, or prompt editor.** REED runs the schedule, writes the briefs, and renders them.
- **Not financial advice.** It summarizes public headlines. Nothing in a brief is a recommendation.

## The Dashboard

A single-page React and Vite app styled like a cross between an email client and a market terminal. Briefs are listed on the left, newest first and open by default. The full digest sits on the right: headline, executive summary, a market-snapshot strip, story cards with sentiment and ticker chips, and numbered sources. Arrow keys move between briefs. It is built for reading rather than clicking, so there are no settings to configure in the UI.

In development it runs at `http://localhost:5173` and proxies the backend at `http://localhost:8000`.

## Quick Start

Prerequisites: Python 3.12+, Node.js 18+, and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/georgejieh/REED
cd REED/backend
uv sync
cp .env.example .env      # add at least one provider key
python cli_setup.py       # detects your keys, picks a model, writes settings.yaml
uv run uvicorn app.main:app --port 8000
```

In a second terminal:

```bash
cd REED/dashboard
npm install
npm run dev
```

Open `http://localhost:5173`. The scheduler starts with the backend and fires the next session on time. To see a brief immediately rather than waiting for the schedule, set `REED_TRIGGER_TOKEN` in `.env` and post to the trigger endpoint:

```bash
curl -X POST -H "X-REED-Token: $REED_TRIGGER_TOKEN" \
  http://localhost:8000/api/trigger/pre_market
```

## Bring Your Own Model

The wizard detects which keys are present in your `.env` and only offers those providers. There is no default provider and no default model, because the right answer depends on what you already pay for.

- **Anthropic** and **OpenAI** through their first-party APIs.
- **OpenRouter**, one key for models across most major labs.
- **Ollama**, for local models on your own hardware, or Ollama Cloud.
- **Any OpenAI-compatible endpoint**, including Together, Groq, Fireworks, DeepInfra, Mistral, xAI, vLLM, and LM Studio. Set `base_url` and REED will use it.

The hosted demo runs `google/gemini-2.5-flash` through OpenRouter. A brief is one call against roughly 25 headlines, so a cheap model is usually the right call.

## Configuration

`backend/settings.yaml` is the operator config and holds the provider, model, enabled sessions, data directory, and scheduler behavior. The wizard writes it, and you can hand-edit it afterwards. Secrets never go in this file.

Environment variables carry secrets and deployment mode:

| Variable              | Purpose                                                          |
|-----------------------|------------------------------------------------------------------|
| `OPENAI_API_KEY`      | OpenAI provider                                                  |
| `ANTHROPIC_API_KEY`   | Anthropic provider                                               |
| `OPENROUTER_API_KEY`  | OpenRouter provider                                              |
| `OLLAMA_API_KEY`      | Ollama Cloud                                                     |
| `OLLAMA_HOST`         | Ollama endpoint, defaults to `http://localhost:11434`            |
| `REED_SETTINGS_PATH`  | path to `settings.yaml`, defaults to `./settings.yaml`           |
| `REED_TRIGGER_TOKEN`  | enables `POST /api/trigger/{session}`, compared in constant time |
| `REED_ENV`            | `prod` (default) or `dev`                                        |
| `REED_STORE`          | `local` (default) or `mirror`                                    |
| `HF_DATASET_REPO`     | dataset repo, used only when `REED_STORE=mirror`                 |
| `HF_TOKEN`            | write token for that dataset repo                                |

The trigger endpoint fails closed. Without `REED_TRIGGER_TOKEN` it returns 503, except when `REED_ENV=dev` on a non-hosted machine.

## Deployment

REED is local-first. Everything below is the same backend with different assumptions about whether the machine stays awake.

- **Local.** The default, and the one the project is designed around. Digests are JSON files on your disk.
- **Small VPS.** The same thing on an always-on box. The in-process scheduler owns the schedule.
- **Docker.** `docker compose up backend` runs the backend and mounts `settings.yaml` and the digest directory. The dashboard is run with `npm run dev`, or built and served as static files.
- **Sleeping host, such as a free Hugging Face Space.** The in-process scheduler cannot be trusted when the host sleeps between requests, so it is turned off and an external cron posts to the trigger endpoint instead. Because the filesystem is also ephemeral there, `REED_STORE=mirror` pushes every brief to a Hugging Face Dataset repo and rehydrates the full history on restart. This is how the hosted demo runs. See [`docs/HF_DEPLOYMENT.md`](docs/HF_DEPLOYMENT.md).

The dashboard has two build modes. `npm run build` expects the live backend API next to it. `npm run build:demo` reads a public dataset repo at build time and falls back to a baked sample when that is unreachable, which produces a static site that can be hosted on any CDN.

## What This Does Not Do

REED summarizes public RSS headlines with a language model, and that bounds what a brief can be trusted for. It does not verify claims against a second source, so a wrong headline produces a wrong summary. Coverage is whatever the configured feeds published in the window, which means a story no feed carried will not appear. The market snapshot comes from Stooq and is delayed, which is why every digest records the source and timestamp of the numbers it used.

When the model returns something unparseable, REED writes a digest marked `fallback_used` with the reason attached rather than failing silently or inventing content, so a brief that could not be produced is visible as such instead of looking like a slow news day.

## Project Structure

```
backend/               FastAPI app: providers, RSS pre-flight, runner, scheduler, read API
backend/cli_setup.py   Setup wizard, writes settings.yaml
dashboard/             React + Vite + TypeScript reader
data/digests/          Generated briefs (JSON, one per digest)
docs/                  Architecture reference and deployment runbook
.github/               Scheduled trigger workflow for the hosted demo
```

`docs/ARCHITECTURE.md` has the full system description.

## License

Apache-2.0. See [LICENSE](LICENSE).

## Contributing

Not accepting contributions yet.
