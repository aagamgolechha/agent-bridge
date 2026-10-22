# Mock Payment Gateway (single deployable)

One FastAPI process serves **both** the API and the dashboard UI — no separate
frontend server, no CORS issues, no hardcoded `localhost` URLs.

- UI: `GET /` → `index.html` (+ `app.js`, `styles.css` served same-origin)
- API docs: `GET /docs`, `GET /openapi.json`
- Health: `GET /health` (service status + DB connectivity + active mock config)

All existing payment/subscription routes, SQLite persistence, latency/
throttling/error simulation, and Loki logging are preserved.

## Quick start (local, single server)

```powershell
cd mock_payment
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # optional; safe defaults work without it
python app.py            # serves UI + API on http://localhost:8001
```

Then open:

- UI → http://localhost:8001/
- Health → http://localhost:8001/health
- Docs → http://localhost:8001/docs

Single-server alternative (same thing `python app.py` does):

```powershell
uvicorn app:app --host 0.0.0.0 --port 8001
```

## Configuration (env vars)

Local dev loads `.env` via `python-dotenv`; in production the hosting
platform's env vars take precedence over `.env`.

| Variable | Default | Purpose |
|---|---|---|
| `PORT` / `HOST` | `8001` / `0.0.0.0` | `python app.py` binds `0.0.0.0:${PORT}` |
| `DATABASE_URL` | SQLite file at `MOCK_DATA_DIR/mock_payments.db` | Override e.g. `sqlite:////data/mock_payments.db` or Postgres URL |
| `MOCK_DATA_DIR` | `.` (folder next to `app.py`) | Where the default SQLite file lives |
| `MOCK_MIN_LATENCY_MS` / `MOCK_MAX_LATENCY_MS` | `50` / `300` | Artificial latency window (API routes only) |
| `MOCK_SYSTEM_ERROR_RATE` | `0` | % of API requests that return `500 SYSTEM_ERROR` |
| `MOCK_FORCE_SYSTEM_ERROR` | `false` | `true` = every API request returns `500 SYSTEM_ERROR` |
| `MOCK_DB_ERROR_RATE` | `0` | % of write ops raising simulated `500 DB_ERROR` |
| `MOCK_FORCE_DB_ERROR` | `false` | `true` = every write op returns `500 DB_ERROR` |
| `MOCK_PAYMENT_SUCCESS_RATE` / `MOCK_PAYMENT_DECLINE_RATE` / `MOCK_PAYMENT_PENDING_RATE` | `90` / `8` / `2` | Authorize-outcome mix |
| `MOCK_FORCE_OUTCOME` | _(empty)_ | `AUTHORIZED`\|`DECLINED`\|`PENDING`\|`FAILED` — deterministic authorize result |
| `MOCK_SEED` | _(empty)_ | e.g. `42` → deterministic latency (range midpoint) + seeded outcome RNG |
| `MOCK_THROTTLE_ENABLED` | `false` | Enable requests/minute cap |
| `MOCK_THROTTLE_REQUESTS_PER_MINUTE` | `60` | Sliding 1-minute window budget |
| `LOKI_ENABLED` | `false` (example) | `true` = ship logs to Loki; `false` = console only |
| `LOKI_URL` | `http://localhost:3100/.../push` | Loki push endpoint (only used when enabled) |
| `LOKI_SERVICE_NAME` / `LOKI_ENVIRONMENT` | `mock-payment` / `local` | Loki stream labels |

## Fault injection

All fault switches default to **off**, so normal behavior is unchanged.
Injected faults return structured errors — never secrets or full card
numbers (only `card_last4` and non-sensitive IDs are logged):

| Scenario | Env | Result |
|---|---|---|
| Random 500s | `MOCK_SYSTEM_ERROR_RATE=20` | ~20% of API calls → `500 {"error_code":"SYSTEM_ERROR"}` |
| Force 500s | `MOCK_FORCE_SYSTEM_ERROR=true` | all API calls → `500 SYSTEM_ERROR` |
| Random DB failures | `MOCK_DB_ERROR_RATE=50` | ~50% of authorize/capture/refund/sub-create/cancel → `500 DB_ERROR` |
| Force DB failures | `MOCK_FORCE_DB_ERROR=true` | all writes → `500 DB_ERROR` |
| Deterministic outcome | `MOCK_FORCE_OUTCOME=DECLINED` | every authorize → `DECLINED/CARD_DECLINED` (persisted, `200`) |
| Deterministic suite | `MOCK_SEED=42` + `MOCK_MIN_LATENCY_MS=100 MOCK_MAX_LATENCY_MS=100` | seeded RNG, fixed 100 ms latency |

Example (PowerShell):

```powershell
$env:MOCK_FORCE_OUTCOME="DECLINED"
$env:MOCK_SEED="42"
$env:MOCK_FORCE_SYSTEM_ERROR="false"
python app.py
```

## Observability / Loki

- Controlled by `LOKI_ENABLED` + `LOKI_URL`. Loki is **never assumed** —
  on hosts without Loki, set `LOKI_ENABLED=false` (or leave `LOKI_URL`
  unset) and the app logs to console only.
- If Loki init fails (package missing, host unreachable), startup prints a
  one-line warning to stderr and continues with console logging.
- `/health` reports `loki_enabled` so you can confirm which mode is active.

## Deployment

- Start command (any host): `uvicorn app:app --host 0.0.0.0 --port ${PORT:-8001}`
  (`Procfile` contains the same command; `python app.py` honors `PORT` too.)
- Listens on `0.0.0.0`; honors the host-provided `PORT`.
- SQLite: set `DATABASE_URL` (or `MOCK_DATA_DIR`) to a **persistent disk**
  path on the host (e.g. `sqlite:////data/mock_payments.db`), otherwise the
  DB file is ephemeral. For Postgres, set `DATABASE_URL=postgresql://...`
  (requires a Postgres driver package).
- Env: set the table above in the host dashboard; no `.env` file needed.
- Health check path: `/health` (returns `200` with `database: connected`
  when healthy, `degraded` when the DB is unreachable).

## Validation checklist

```powershell
# 1) Start one server
python app.py

# 2) Frontend from FastAPI root
Invoke-RestMethod http://localhost:8001/ | Out-String | Select-Object -First 1

# 3) Health / docs
Invoke-RestMethod http://localhost:8001/health | ConvertTo-Json
Invoke-WebRequest http://localhost:8001/docs -UseBasicParsing | Select-Object StatusCode
Invoke-WebRequest http://localhost:8001/openapi.json -UseBasicParsing | Select-Object StatusCode

# 4) Normal payment flow
$auth = Invoke-RestMethod -Method Post http://localhost:8001/payments/authorize `
  -ContentType 'application/json' `
  -Body '{"customer":{"name":"John Doe","email":"john@example.com"},"card":{"card_number":"4111111111111111","expiry":"12/28","cvv":"123"},"amount":99.99,"currency":"USD"}'
$auth.payment_id  # then /payments/capture, /payments/refund, /payments/{id}, /payments?limit=10

# 5) Fault injection (separate shell): MOCK_FORCE_SYSTEM_ERROR=true → expect 500 SYSTEM_ERROR
# 6) Loki disabled: LOKI_ENABLED=false → app starts, /health shows loki_enabled=false
```
