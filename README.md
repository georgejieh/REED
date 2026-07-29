# REED

REED is a standalone, local-first market-news digest reader. It collects your
selected RSS feeds for defined U.S. market windows, asks your configured
provider for a bounded summary, validates the result, and publishes only
complete digests. Failed runs never replace the last good digest.

## Run REED locally

Install Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), and Node.js 18
or newer. From a fresh clone:

```powershell
cd dashboard
npm ci
npm run build
cd ..\backend
uv sync
uv run python -m app
```

Open `http://127.0.0.1:8000` in your browser.

The first-run wizard asks for:

- a provider and model;
- a provider credential when one is required;
- the market windows you want REED to cover; and
- the RSS sources you want it to read.

No environment file or terminal configuration is required for normal local
use. Ollama is available only through an explicit loopback HTTP endpoint.

REED binds to `127.0.0.1` by default. The initial page establishes a
short-lived, server-controlled browser session; changes require that session
and a CSRF value kept only in application memory. Provider credentials use the
operating system credential store. Runtime configuration and published data use
`backend/data/reed.db` by default.

## Docker Compose

If you prefer Docker:

```powershell
docker compose up --build
```

Then open `http://127.0.0.1:8000`. The published port is limited to the host
loopback interface. Mount `./data` on durable local storage before relying on
scheduled history.

## What REED does

- Reads configured RSS sources within market-window time bounds.
- Requires sufficient validated RSS evidence before generation.
- Can use bounded supplemental SearXNG search only after the RSS requirement
  is met.
- Runs scheduled market-window digests locally once setup is complete.
- Keeps historical published digests available for reading.
- Shows an honest failed-run state while preserving the prior good digest.

REED does not provide chat, trading execution, real-time market feeds, a
general-purpose agent loop, or a cloud credential-sync service.

## Local API

These read-only endpoints expose published data and safe runtime state:

- `GET /api/health`
- `GET /api/runtime/status`
- `GET /api/digests`
- `GET /api/digests/{id}`
- `GET /api/digests/latest`
- `GET /api/sessions`

The older `GET /api/runtime-status` path remains as a read-only compatibility
alias.

## Development verification

```powershell
cd backend
$env:PYTHONPATH=""
.\.venv\Scripts\python.exe -m pytest -q
cd ..\dashboard
npm run build
```

## License

Apache-2.0. See [LICENSE](LICENSE).
