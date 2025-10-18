# Mobile API Gateway

FastAPI service that classifies food photos with OpenAI Vision, evaluates freshness, and stores results in Postgres. Existing web flow (`POST /recognize/fresh`) remains intact while a new `/api/v1` gateway enables mobile clients to reuse the same upload → analyze → persist pipeline.

## Endpoints

| Purpose | Path | Notes |
| --- | --- | --- |
| Health | `GET /health` | Legacy simple check |
| Web flow | `POST /recognize/fresh` | Form upload, same as before |
| Mobile upload | `POST /api/v1/uploads` | Multipart `file`, returns `upload_id` |
| Mobile analyze | `POST /api/v1/analyze` | JSON `{upload_id, storage}` or multipart; returns analysis JSON or 202 job |
| Job polling | `GET /api/v1/jobs/{job_id}` | Use when `/analyze` returns 202 |
| Persist result | `POST /api/v1/items` | Saves recognition/freshness to Postgres |
| Mobile health | `GET /api/v1/healthz` | Includes model name |

### Authentication

- Header: `X-Api-Key: <key>`
- Allowed keys: comma-separated list in `API_KEYS` env var.
- If `API_KEYS` is empty we only accept requests from `127.0.0.1` / `::1` (for local Caddy).

### Upload constraints

- Only `image/*` content-type accepted.
- Size limit: `MAX_UPLOAD_MB` (default **15 MB**). Exceeding returns 413 with JSON error.
- Successful uploads are stored under `STORAGE_DIR` and available via `upload_id`.
- Basic rate limit: `RATE_LIMIT_PER_MINUTE` requests per minute per API key + client IP.

### Example requests

```bash
# Upload image
curl -X POST "$API_BASE/api/v1/uploads" \
  -H "X-Api-Key: $API_KEY" \
  -F "file=@tests/assets/apple.jpg"

# Analyze (synchronous if <= timeout)
curl -X POST "$API_BASE/api/v1/analyze" \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: $API_KEY" \
  -d '{"upload_id":"<id>","storage":"cool"}'

# Persist result once user confirms
curl -X POST "$API_BASE/api/v1/items" \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: $API_KEY" \
  -d '{"upload_id":"<id>","storage":"cool"}'
```

### Pipeline

```
Mobile/Web App → POST /api/v1/uploads → POST /api/v1/analyze (timeout→jobs)
        │                                              ↓
        └──────────── POST /api/v1/items ─────────→ Postgres (app.food_items / fresh_checks)
```

`X-Request-ID` is echoed in responses for traceability; upstream proxies should forward existing IDs.

## Environment & deployment

See `.env.example` for required variables and defaults. Additional deployment notes, including new dependencies and routes, are documented in `docs/DEPLOY_NOTES.md`.

## Local smoke test

- Environment variable: `LAB_API_KEY` (set locally; never commit real keys).
- Health check: `GET /api/v1/healthz` (returns 200 when the key is accepted).
- Pre-flight curl:

  ```powershell
  curl.exe -sS -i `
    -H "X-Api-Key: $env:LAB_API_KEY" `
    "https://lab.160.16.126.35.sslip.io/api/v1/healthz"
  ```

- End-to-end smoke script (includes upload → analyze → job poll → optional item creation):

  ```powershell
  powershell -NoProfile -ExecutionPolicy Bypass `
    -File .\scripts\smoke.ps1 `
    -BaseUrl "https://lab.160.16.126.35.sslip.io" `
    -Image  (Resolve-Path ".\Sample Pictures\meat.jpg").Path `
    -CreateItem
  ```

## Contract tests

- Path: `tests/integration/test_predict_flow.py`
- Enable with `RUN_INTEGRATION=1` and set `API_BASE` / `API_KEY`.
- Scripts:
  1. Bash – `scripts/run_integration.sh`
  2. PowerShell – `scripts/run_integration.ps1`

The contract test uploads `tests/assets/apple.jpg`, runs the `/api/v1` flow against the live environment, and asserts response structure (including async job fallback).
