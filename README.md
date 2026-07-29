# REED

REED is a local-first market-news digest reader. It collects selected RSS
feeds for defined U.S. market windows, asks the configured provider for a
bounded summary, validates the result, and publishes only complete digests.
Failed runs never replace the last good digest.

REED has two delivery modes:

- a standalone browser application on one computer
- a public GitHub Pages reader backed by one private operator deployment on
  Hugging Face Spaces or Render

Provider credentials stay on the backend. They are never placed in the Pages
site, browser storage, published digests, or frontend build.

## Local browser application

Install Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), and Node.js 18
or newer. From a fresh clone:

```powershell
cd dashboard
npm install
npm run build
cd ..\backend
uv sync
uv run python -m app
```

Open `http://127.0.0.1:8000`. The browser wizard asks for the provider, model,
credential when required, market windows, and RSS sources. No environment file
or terminal configuration is required.

The local server binds to `127.0.0.1` by default and accepts only the exact
configured loopback Host and origin values. The initial page establishes a
short-lived, server-controlled session. Mutations require that session and a
CSRF value kept only in application memory. Provider credentials use the
operating system credential store. Runtime configuration and published data
use `backend/data/reed.db` by default.

Docker Compose is also available:

```powershell
docker compose up --build
```

The published port is limited to the host loopback interface. Mount `./data`
on durable local storage before relying on scheduled history.

## Public API

These reads contain published data and safe runtime state only:

- `GET /api/health`
- `GET /api/runtime/status`
- `GET /api/digests`
- `GET /api/digests/{id}`
- `GET /api/digests/latest`
- `GET /api/sessions`

The older `GET /api/runtime-status` path remains as a read-only compatibility
alias.

## GitHub Pages with a hosted backend

The Pages build is a public reader. Build it with the exact backend URL and
the repository base path:

```powershell
cd dashboard
$env:VITE_REED_API_BASE_URL="https://reed-backend.example"
$env:VITE_BASE_PATH="/REED/"
npm ci
npm run build
```

Publish `dashboard/dist` from the Pages branch or the repository's configured
Pages upload. REED does not use GitHub Actions as an application runtime.

Configure the backend with exact values:

```text
REED_RUNTIME_MODE=hosted
REED_ALLOWED_HOSTS=reed-backend.example
REED_HOSTED_BACKEND_ORIGIN=https://reed-backend.example
REED_HOSTED_ALLOWED_ORIGINS=https://georgejieh.github.io
REED_PROVIDER_ALLOWED_HOSTS=
REED_HOSTED_OPERATOR_SECRET=<deployment secret>
REED_DATABASE_PATH=/data/reed.db
```

`REED_HOSTED_ALLOWED_ORIGINS` is required in hosted mode and must contain at
least one exact origin. List only the exact production Pages origin and any
development origins actually used, separated by commas. For example, append
`http://localhost:5173` only while using that local development server.
Wildcards and empty hosted allowlists are rejected at startup. Public reads
support credential-aware CORS only for the configured exact origins.

OpenRouter uses the built-in approved HTTPS host `openrouter.ai`. Ollama is
limited to explicit loopback HTTP endpoints in local mode. A generic
OpenAI-compatible endpoint fails closed unless its exact hostname is listed in
the non-secret `REED_PROVIDER_ALLOWED_HOSTS` value, for example
`REED_PROVIDER_ALLOWED_HOSTS=api.compatible-provider.example`. Schemes, paths,
ports, credentials, queries, fragments, and wildcards do not belong in the
host allowlist.

The operator signs in at the backend origin, not the Pages origin. The backend
keeps the authentication secret server-side and issues a short-lived
`HttpOnly`, `Secure`, `SameSite=Strict` session with CSRF protection and login
rate limiting. The Pages client cannot invoke mutations or receive provider
credentials. Manual runs, wizard changes, source validation, and other
controls remain available from the backend-hosted interface.

### Render

`render.yaml` provisions one web service and one persistent disk. Set every
entry marked `sync: false` in the Render dashboard. Use exactly one
scheduler-enabled replica. Additional replicas must not enable the scheduler.
The SQLite database must remain on the mounted `/data` disk.

### Hugging Face Spaces

Use a Docker Space with the repository `Dockerfile`. Add the hosted settings
above as Space secrets or variables, set `PORT=7860`, and use the Space's exact
`.hf.space` hostname and HTTPS origin. Attach a read-write Storage Bucket at
`/data` and keep `REED_DATABASE_PATH=/data/reed.db`. Use one scheduler-enabled
Space replica. Without the attached bucket, configuration, validation history,
and published digests will be lost when the Space restarts.

Hosted provider credentials, including `REED_OPENROUTER_CREDENTIAL`, belong in
the backend platform secret store only. Do not add them to Pages variables,
source control, sample data, or demo builds.

## RSS catalog validation

Application startup does not contact catalog feeds by default. An authenticated
operator can run:

```text
POST /api/admin/rss-catalog/validate
```

REED safely fetches and parses each built-in catalog feed and persists only a
source identifier, validity, item count, catalog version, and timestamp. Set
`REED_VALIDATE_RSS_CATALOG_ON_STARTUP=true` only when deployment policy
explicitly requires validation during boot.

## Verification

```powershell
cd backend
$env:PYTHONPATH=""
.\.venv\Scripts\python.exe -m pytest -q
cd ..\dashboard
npm run build
```

## License

Apache-2.0. See [LICENSE](LICENSE).
