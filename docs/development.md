# Development & Deployment

## Local Setup

```bash
# 1. Create and activate a virtualenv
python -m venv .venv
.venv\Scripts\Activate.ps1          # PowerShell (Windows)
source .venv/bin/activate           # macOS / Linux

# 2. Install dependencies (dev deps are kept separate so the Vercel bundle stays lean)
pip install -r requirements.txt -r requirements-dev.txt
npm ci                              # ESLint tooling only; the frontend has no build step

# 3. Configure the environment
cp .env.example .env                # then fill in the values

# 4. Run
uvicorn main:app --reload --port 8000
```

The frontend is served by the same app — open `http://localhost:8000`. Note that
the in-app camera needs a secure context: `navigator.mediaDevices` is undefined
on plain http, with `localhost` the one exception.

## Environment Variables

Set these in `.env` locally, or in the Vercel project environment in production.
`tests/test_env_parity.py` asserts that every `os.getenv()` key in the source
appears in `.env.example` and that no documented key has gone unread, so add a
line there whenever you introduce a new variable.

### Required

| Variable | Purpose |
| :--- | :--- |
| `GROQ_API_KEY` | Groq Cloud API key (LLM intent extraction) |
| `SARVAM_API_KEY` | Sarvam AI subscription key (speech-to-text) |
| `FIREBASE_SERVICE_ACCOUNT` | Firebase Admin credentials as a single-line JSON string. Required on Vercel; locally `auth.py` falls back to the service-account `.json` file in the repo root |
| `FIREBASE_API_KEY`, `FIREBASE_AUTH_DOMAIN`, `FIREBASE_PROJECT_ID`, `FIREBASE_STORAGE_BUCKET`, `FIREBASE_MESSAGING_SENDER_ID`, `FIREBASE_APP_ID`, `FIREBASE_MEASUREMENT_ID` | Client-side Firebase config, served to the browser via `GET /config`. `FIREBASE_STORAGE_BUCKET` is **also** used server-side by the Admin SDK as the bucket for bill PDFs, so bill generation fails without it |
| `PAY_LINK_SECRET` | Signing secret for UPI payment link tokens. The payment endpoints return 503 without it. Use different secrets locally and in production |
| `BILL_TOKEN_SECRET` | Signing secret for bill PDF download tokens. Bills still generate without it, but tokens fall back to random values, so a bill rebuilt after its 30-day expiry gets a new URL and any link already shared with a customer stays dead. Changing it has the same effect for bills generated before the change. Use different secrets locally and in production |

### Optional

| Variable | Purpose |
| :--- | :--- |
| `ALLOWED_ORIGINS` | Comma-separated extra CORS origins. Localhost is always allowed, and the deployed frontend is same-origin, so this is usually empty |
| `DEBUG_LOGS` | Set to `1` to *print* transcripts and parsed intents to stdout. This is PII — keep it off in production. Note this is only about the function logs: transcripts and intents are stored in `users/{uid}/voice_logs` regardless, for the support console, with a 30-day TTL |

## Running the Checks

```bash
pytest                              # full suite (682 tests, no network, no live data)
pytest --cov=. --cov-report=term-missing
ruff check .                        # Python lint
ruff format --check tests/          # test formatting (only tests/ is format-gated)
npm run lint                        # ESLint over the frontend modules
```

Install the Git hooks once per machine so the same checks run before a commit
and a push:

```bash
pip install pre-commit
pre-commit install --install-hooks -t pre-commit -t pre-push
```

Commit-stage hooks are the fast ones (lint, format, JSON validity, secret
detection); the test suite runs at push time instead.

## Automated Quality Checks

Every push and pull request runs a CI pipeline before anything can reach production. `main` is protected — all checks must pass before a merge is allowed.

* **Test Suite:** 682 automated tests covering the ledger math, rate limiting, image sanitization, token verification, and every API route. They run against an in-memory database double with all external services stubbed, so no test spends an API quota or touches live shop data. Run on Python 3.12 (the realistic Vercel runtime) and 3.14.
* **Route Coverage:** `vercel.json` lists every API path by hand, and auth is enforced inside each route handler rather than centrally. Two tests catch a new endpoint that was added without a deploy route (which would 404 only in production) or without an auth check (which would expose another shop's data).
* **Config Drift:** Every environment variable the code reads must be documented in `.env.example`, and the deployment configs must parse — a malformed `vercel.json` otherwise breaks the deploy with no earlier warning.
* **Secret Scanning:** Full git history is scanned for leaked credentials on every run, with an explicit check that the Firebase Admin key and `.env` are never committed.
* **CodeQL Static Analysis:** GitHub's semantic code scanner runs the `security-extended` query suite over both the Python backend and the frontend JavaScript on every push and pull request, publishing findings to the repository's Security tab. It also re-scans weekly on a schedule, so a vulnerability class discovered *after* the last commit still gets reported against existing code. The maintainability queries are deliberately left off — Ruff and ESLint already cover style, and folding it in would bury real security findings in noise.
* **Linting:** Ruff on the Python backend and ESLint on the frontend modules.

The same checks run locally as Git hooks, so problems surface before a push rather than after.

Browser behaviour, live Firebase rules, and real speech recognition are still verified by hand — CI covers the logic, not the experience.

## Deployment

The app deploys to Vercel: `main.py` as a `@vercel/python` serverless function,
and the frontend files as static assets. `.vercelignore` keeps dev tooling,
tests, and documentation out of the bundle.

Adding a new API **path** requires an explicit `src`/`dest` mapping in
`vercel.json` — the patterns are method-agnostic, so a new *method* on an
existing path needs no change. A test enforces this.

Firebase-side setup, all one-time:

```bash
# Deny-all client access to Firestore
npx firebase-tools deploy --only firestore:rules

# Bill retention (see docs/bill-retention.md for what these do)
gcloud firestore fields ttls update expires_at --collection-group=bills --enable-ttl
gcloud storage buckets update gs://<FIREBASE_STORAGE_BUCKET> --lifecycle-file=storage.lifecycle.json
```
