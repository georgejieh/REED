# Deploying REED to a Hugging Face Space

REED runs on a free Hugging Face Space via the standard Docker Space SDK. The image is the same `backend/Dockerfile` that powers local Docker, with two HF-specific knobs:

1. The Space sleeps between requests, so the in-process scheduler is turned off and an external cron wakes the Space by POSTing to a token-protected trigger endpoint.
2. Local disk on a free Space is not durable, so storage is set to `mirror`. Every digest is written locally and pushed to a private HF Dataset repo. On boot the mirror rehydrates history from the Dataset repo so the dashboard shows every past brief.

This document covers the full setup. It assumes you have already run the wizard locally and have a working `backend/settings.yaml`.

## 1. Create the Space

Create a new Space on Hugging Face:

- Name: `reed` (or whatever you prefer).
- SDK: `docker`.
- Hardware: `cpu-basic` is enough; the agent does not run on the Space.
- Visibility: public so the dashboard can read from it without auth, or private if you will run the dashboard against an HF proxy.

Clone the Space's git repo locally and add REED as a remote or just initialize it from the REED source. The Dockerfile in this repo's `backend/` directory is the Space's build context. The Space expects a top-level `Dockerfile`, so the simplest path is to copy `backend/Dockerfile` to the Space root, or push this whole repository and tell the Space to look at `backend/` as the build context.

## 2. Configure environment variables

In the Space's Settings page, set the following environment variables. Do not commit a `.env` to the Space repo. The Space's secrets UI is the only place these values should live.

| Variable | Required | Purpose |
|----------|----------|---------|
| `OPENAI_API_KEY` | if OpenAI | provider key |
| `ANTHROPIC_API_KEY` | if Anthropic | provider key |
| `OPENROUTER_API_KEY` | if OpenRouter | provider key |
| `OLLAMA_API_KEY` / `OLLAMA_HOST` | if Ollama | provider config |
| `OPENAI_COMPATIBLE_API_KEY` / `OPENAI_COMPATIBLE_BASE_URL` | if openai_compatible | custom endpoint |
| `REED_TRIGGER_TOKEN` | yes | enables `POST /api/trigger/{session}` |
| `REED_STORE` | yes | set to `mirror` |
| `HF_DATASET_REPO` | yes when `REED_STORE=mirror` | e.g. `your-username/reed-digests` |
| `HF_TOKEN` | yes when `REED_STORE=mirror` | write token for the Dataset repo |
| `REED_SEARCH_PROVIDER` | optional | `ddgs` (default), `brave`, or `tavily` |
| `BRAVE_API_KEY` | if search=brave | search key |
| `TAVILY_API_KEY` | if search=tavily | search key |

REED detects which provider keys are present and lets only the matching providers through the API. The trigger endpoint is disabled until `REED_TRIGGER_TOKEN` is set, so an unset token is a safe default.

## 3. Ship `settings.yaml`

The wizard writes `backend/settings.yaml` based on the keys in your local `.env`. The same file is read on the Space if you place it in the Space repo at `backend/settings.yaml`. Keep these knobs in mind:

- `provider` and `model` must match the keys you set on the Space.
- `scheduler.enabled` must be `false` on a free Space. The Space sleeps between requests, so the in-process scheduler cannot be trusted to fire on time.
- `trigger.enabled` must be `true` so the external cron can wake the Space.
- `data_dir` defaults to `./data/digests`. On a free Space that path is ephemeral; the mirror store pushes every write to the Dataset repo.

Push the file to the Space repo:

```bash
git add backend/settings.yaml
git commit -m "Add REED operator settings"
git push
```

## 4. Configure the trigger endpoint

The trigger endpoint is `POST /api/trigger/{session}`. It accepts a header `X-Trigger-Token: <REED_TRIGGER_TOKEN>`. The Space's external cron needs a way to make this call, and the Space sleeps between requests, so the cron cannot be in-process. Two options:

- Run the cron on GitHub Actions, a tiny always-on machine, or a paid cron service.
- Run the cron on the HF Hub itself if you have access to HF Jobs (not covered here).

The trigger returns a 202 immediately and runs the session in the background. The Space stays awake for the duration of the session and goes back to sleep a few minutes after the response is written. A typical cron entry is:

```cron
# m h dom mon dow   command
0 12 * * 1-5         curl -fsS -X POST -H "X-Trigger-Token: $REED_TRIGGER_TOKEN" https://your-space.hf.space/api/trigger/pre_market
```

`fsS` fails the cron run on any non-2xx response, which is what you want; a silent failure here means a missed session.

## 5. Mirror to an HF Dataset

Create a private Dataset repo (e.g. `your-username/reed-digests`) and set `HF_DATASET_REPO` and `HF_TOKEN`. The Dataset repo holds one JSON file per digest and an `_index.json` manifest. Writes are non-blocking from the agent's perspective: if the mirror push fails, the digest is still saved locally and the next successful push retries. Reads happen at boot, so a fresh Space restart rehydrates every past brief from the Dataset repo.

The Dataset repo is private by default, which means the dashboard needs to read it with the same `HF_TOKEN` when `VITE_DATASET_BASE` points at the mirror URL. See the static demo build section in the README for the exact URL pattern.

## 6. Build the static dashboard

`VITE_DATASET_BASE` is consumed only by `npm run build:demo` in the `dashboard/` directory. Set it to the mirror's raw URL when you build the static dashboard:

```bash
VITE_DATASET_BASE='https://huggingface.co/datasets/your-username/reed-digests/resolve/main' \
  npm install && npm run build:demo
```

The output is `dashboard/dist/`. Host that anywhere: the Space itself, GitHub Pages, or a CDN. The demo build validates the URL: it must be `https://`, must not point at a loopback or RFC1918 address, and must match the `https://huggingface.co/datasets/<repo>/resolve/<branch>` shape. On any failure the build falls back to a baked sample and logs a single warning.

## 7. Verify

After the Space boots, the dashboard, and the cron are all in place:

1. `curl https://your-space.hf.space/api/health` returns `{"status":"ok","service":"reed"}`.
2. `curl -H "X-Trigger-Token: $REED_TRIGGER_TOKEN" -X POST https://your-space.hf.space/api/trigger/pre_market` returns 202 and produces a digest within a minute or two.
3. `curl https://your-space.hf.space/api/digests/latest` returns the new digest.
4. The Dataset repo's `main` branch now has a `2026-07-23-pre_market.json` file and an updated `_index.json`.
5. The static dashboard, rebuilt with `VITE_DATASET_BASE` pointing at the mirror, shows the new brief.

## 8. Failure modes

- The Space sleeps. A 503 response from the Space's edge to the cron means the Space is cold-starting. The cron should retry with exponential backoff for the first ten minutes after the scheduled time.
- The mirror push fails. The digest is still saved locally; the next push retries. If three pushes in a row fail, the cron should page the operator.
- The trigger token leaks. Rotate it on the Space's secrets page. The old token is rejected on the next request.

## What this chunk ships

This file, plus the existing `backend/Dockerfile` and `docker-compose.yml`. No code changes. The mirror store, the trigger endpoint, and the static demo build were each landed in their own chunk; this document ties them together.