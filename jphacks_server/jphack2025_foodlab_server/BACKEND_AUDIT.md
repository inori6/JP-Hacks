# Backend Audit

## FastAPI Application (main.py)
- `FastAPI` instance named `app` with title "AI Food Recognizer (OpenAI)".
- Routes:
  - `GET /health`: returns `{ "ok": true, "model": MODEL }`.
  - `POST /recognize/fresh`: multipart form with `image` and optional `storage` (default `cool`). Implements upload → analysis → persistence pipeline.
- Key helpers reused within the route:
  - `sha1_of_bytes`: SHA1 hash helper for deduplication.
  - `call_llm_classify`: Vision model call (OpenAI SDK) returning `{label, confidence}`.
  - `map_to_class_id`: Maps raw vision label to internal class code (`CLASS_MAP`).
  - `call_llm_freshness`: LLM call producing `{ripeness, hours_left, deadline, note}` and sanitizing values.
  - `_get_note`: Extracts optional `note` from stored JSON payloads.

## Upload, Analysis, Persistence Flow
1. Read uploaded file bytes and compute SHA1.
2. Lookup `FoodItem` cache by `image_sha1`; call `call_llm_classify` if missing and insert a new `FoodItem` row.
3. Call `call_llm_freshness` with class + storage; normalize deadline and insert `FreshCheck` row.
4. Commit transaction and respond with merged recognition + freshness data.

## Database Layer (SQLAlchemy models)
- PostgreSQL connection via `postgresql+psycopg2` using env vars (`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`).
- `FoodItem` (schema `app.food_items`): `id`, `image_sha1`, `guess_label`, `class_id`, `confidence`, `created_at`.
- `FreshCheck` (schema `app.fresh_checks`): `id`, `food_id`, `storage`, `ripeness`, `hours_left`, `deadline`, `raw_json`, `created_at`.

## Dependencies (requirements.txt)
- Web stack: `fastapi`, `uvicorn[standard]`, `python-multipart`.
- OpenAI client: `openai`, `httpx`, `httpcore`, `anyio`.
- Config: `python-dotenv`.
- Image processing: `pillow`.
- Database: `SQLAlchemy`, `psycopg2-binary`.

## Supporting Files
- `deco.py`: local decorator demo (not used by web app).
- `test/http_test.py`: raw socket HTTP example (unrelated to API runtime).
- `.gitignore`: ignores `.env`, `.pyc`, caches, IDE metadata, `test/` directory.
- `config`: provisioning helper that writes `/srv/app/.env` with DB + OpenAI creds when executed.

## Docker / Entrypoint Status
- No Dockerfile or compose definitions present.
- Entrypoint assumed to launch `uvicorn main:app` (not declared in repo).
- No task queue / background worker defined.

