# Deploying REED to a Hugging Face Space

This document covers the hosted-variant deployment. The default
deployment is local-first; see `README.md` for that path. The HF
deployment exists so the author can demo REED on
`georgejieh.dev/reed` and so the same backend code can be hosted
without a machine of your own. If you only want to run REED for
yourself, skip this and use the local path.

Three HF-specific knobs:

1. The Space sleeps between requests, so the in-process scheduler is
   turned off. An external cron (GitHub Actions) wakes the Space by
   POSTing to a token-protected trigger endpoint.
2. Local disk on a free Space is not durable, so storage is set to
   `mirror`. Every digest is written locally and pushed to a public
   HF Dataset repo. On boot the mirror rehydrates history from the
   Dataset repo so the static reader shows every past brief.
3. Trigger auth is fail-closed: an empty or missing
   `REED_TRIGGER_TOKEN` returns 503 in production. A leaked token
   is rejected via constant-time compare.

This document assumes you have already run the wizard locally and
have a working `backend/settings.yaml`.

## 1. Create the Space

Create a new Space on Hugging Face:

- Name: `reed` (or whatever you prefer).
- SDK: `docker`.
- Hardware: `cpu-basic` is enough; the agent runs only one LLM
  call per session.
- Visibility: public.

Clone the Space's git repo locally. The Dockerfile in this repo's
`backend/` directory is the build context. The Space expects a
top-level `Dockerfile`, so the simplest path is to copy
`backend/Dockerfile` to the Space root. The Space repo contains the
same `app/`, `settings.yaml`, `pyproject.toml`, etc. as `backend/`
here.

## 2. Configure environment variables

In the Space's Settings page, set the following secrets. The Space's
secrets UI is the only place these values should live.

| Variable              | Required | Purpose                                          |
|-----------------------|----------|--------------------------------------------------|
| `OPENROUTER_API_KEY`  | yes (production) | the LLM provider key                     |
| `REED_TRIGGER_TOKEN`  | yes      | enables `POST /api/trigger/<session>`           |
| `REED_STORE`          | yes      | set to `mirror`                                  |
| `HF_DATASET_REPO`     | yes      | e.g. `your-username/reed-digests`                |
| `HF_TOKEN`            | yes      | write token for the Dataset repo                 |
| `REED_ENV`            | no       | `prod` (default) or `dev`                        |

Holiday skipping is controlled by `scheduler.skip_holidays` in
`settings.yaml`, not by an environment variable.

The Space no longer requires a news-search provider or scrape tool
on the cron path. RSS is the only news source. `OPENAI_API_KEY` and
`ANTHROPIC_API_KEY` are supported via the provider abstraction but
not used in production. `OLLAMA_HOST` is for operator-driven CLI use.

## 3. Ship `settings.yaml`

The wizard writes `backend/settings.yaml` based on the keys in
your local `.env`. The same file is read on the Space if you place
it in the Space repo at the right path. Keep these knobs in mind:

- `provider` and `model` must match the keys you set on the Space.
  Production is OpenRouter with `google/gemini-2.5-flash`.
- `scheduler.enabled` must be `false` on a free Space. The Space
  sleeps between requests, so the in-process scheduler cannot be
  trusted to fire on time.
- `trigger.enabled` is a documentation flag only. The trigger endpoint
  is gated by the `REED_TRIGGER_TOKEN` secret, so setting the token is
  what actually opens the route for the external cron.
- `data_dir` defaults to `./data/digests`. On a free Space that path
  is ephemeral; the mirror store pushes every write to the Dataset
  repo.

## 4. Configure the cron

The trigger endpoint is `POST /api/trigger/{session}`. It accepts a
header `X-REED-Token: <REED_TRIGGER_TOKEN>`.

The Space sleeps between requests, so the cron cannot be in-process.
Use GitHub Actions. The repo at `github.com/georgejieh/REED` ships
`.github/workflows/reed-trigger.yml` with five schedules plus one
Monday-only schedule. The workflow uses `-fsS` so any non-2xx
response fails the cron run rather than silently succeeding on a
stub brief. The workflow also rejects stub responses
(`fallback_used=true` or a `[STUB]` headline) with a non-zero exit
so a failure does not look green.

The workflow fires on this schedule (UTC):

| Job name        | Time (ET) | Cron (UTC)        |
|-----------------|-----------|-------------------|
| pre_market      | 08:00     | `0 12 * * 1-5`    |
| early_market    | 09:45     | `45 13 * * 1-5`   |
| midday          | 12:30     | `30 16 * * 1-5`   |
| close           | 16:15     | `15 20 * * 1-5`   |
| weekend_recap   | 07:00 Mon | `0 11 * * 1`      |

The cron fires the Space, the Space runs the session synchronously
inside the request, the response carries the new digest id, and the
Space goes back to sleep. Cold-start is 30-60 seconds; total
trigger-to-brief latency is 40-90 seconds.

## 5. Mirror to an HF Dataset

Create a Dataset repo (e.g. `your-username/reed-digests`). Set
`HF_DATASET_REPO` and `HF_TOKEN`. The Dataset repo holds one JSON
file per digest and an `_index.json` manifest. Writes are
non-blocking from the agent's perspective: if the mirror push
fails, the digest is still saved locally and the next successful
push retries. Reads happen at boot, so a fresh Space restart
rehydrates every past brief from the Dataset repo.

The Dataset repo can be private or public. The author's static
demo at `georgejieh.dev/reed` is a separate portfolio site that
reads the public HF Dataset repo.

## 6. The author's demo stack

The author's hosted demo uses three repos working together:

- `github.com/georgejieh/REED` (this repo, canonical source).
- `huggingface.co/spaces/ColdAshSage/reed` (Space clone pinned to
  the production commit).
- `huggingface.co/datasets/ColdAshSage/reed-digests` (briefs
  archive).
- `github.com/georgejieh/georgejieh-portfolio` (the static reader
  site deployed to GitHub Pages at `georgejieh.dev/reed`).

You do not need this stack. It is the author's personal
configuration for showing REED on a public portfolio page. For
your own deployment, the local-first path is the primary use case.

## 7. Verify

After the Space boots and the cron is in place:

1. `curl https://your-space.hf.space/api/health` returns
   `{"status":"ok","service":"reed"}`.
2. `curl -X POST -H "X-REED-Token: $REED_TRIGGER_TOKEN"    https://your-space.hf.space/api/trigger/pre_market` returns 200
   with a digest id and headline. The session runs synchronously
   inside the request; on success the brief is already in the
   dataset repo when the curl returns.
3. `curl https://huggingface.co/datasets/your-username/reed-digests/resolve/main/_index.json`
   lists the new digest id.

## 8. Failure modes

- **Space sleeps.** A 503 response from the Space's edge to the
  cron means the Space is cold-starting. The cron retries with
  `--retry 3 --retry-delay 20 --retry-all-errors` for the first 60
  seconds after the scheduled time.
- **Mirror push fails.** The digest is still saved locally; the
  next push retries. If three pushes in a row fail, the cron
  should page the operator.
- **Trigger token leaks.** Rotate it on the Space's secrets page.
  The old token is rejected on the next request.
- **NYSE holiday.** The trigger endpoint returns 200 with a stub
  payload (`{"skipped": true}`) and the cron treats it as success.
  The dataset repo is not updated on a holiday skip.
- **Empty headlines for backfill.** When backfilling past dates
  via `?as_of=`, the RSS feeds may not have in-window content. The
  trigger still returns 200; the brief will have 0 real stories
  rather than fabricated ones.

## 9. Operator secrets (no longer needed)

The Space no longer requires these. They may exist in old `secrets`
configs from earlier deployments. Remove them on next Space
restart.

- `BRAVE_API_KEY`
- `TAVILY_API_KEY`
- `REED_SEARCH_PROVIDER`
- `FIRECRAWL_API_KEY`

None of these are read by REED any more; the search and scrape code
that used them has been removed. The cron path uses RSS only. No
news-search provider, no scrape tool, no agent loop.
