# Deploy Notes

## New environment variables
- `DATABASE_URL` (optional) or `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD`
- `STORAGE_DIR` – filesystem directory for persisted uploads (default `./data/uploads`). Ensure write permissions inside container.
- `MAX_UPLOAD_MB` – upload size limit (default 15).
- `RATE_LIMIT_PER_MINUTE` – per API key/IP rate limit (default 30).
- `REQUEST_TIMEOUT_SECONDS` – sync analysis timeout before background jobs (default 15).
- `API_KEYS` – comma separated API keys for `X-Api-Key` header. Leave empty to allow only localhost/Caddy.
- `CORS_ORIGINS` – allowed origins list. If unset and `DEBUG=0`, CORS is disabled.
- `UPLOAD_URL_BASE` – optional public base URL for upload download links.

## API surface changes
- Mobile REST gateway mounted under `/api/v1` with endpoints:
  - `POST /api/v1/uploads`
  - `POST /api/v1/analyze`
  - `GET /api/v1/jobs/{job_id}`
  - `POST /api/v1/items`
  - `GET /api/v1/healthz`
- All endpoints require `X-Api-Key` unless running on localhost with empty `API_KEYS`.
- Error payloads now use `{ "error": { "code", "message" } }` format for `/api` routes.

## Dependencies
- Added runtime packages: `requests`, `tenacity` (for integration helpers) alongside existing FastAPI/OpenAI stack.
- Ensure Docker image installs requirements from `requirements.txt`.

## Request tracing
- New `RequestIDMiddleware` attaches/propagates `X-Request-ID` headers for consistent logging. Upstream proxy should pass through existing IDs.

## Contract tests
- Remote contract tests live in `tests/integration/test_predict_flow.py`.
- Enable with `RUN_INTEGRATION=1` and provide `API_KEY`/`API_BASE` env vars.
- Helper scripts: `scripts/run_integration.sh` and `scripts/run_integration.ps1`.

