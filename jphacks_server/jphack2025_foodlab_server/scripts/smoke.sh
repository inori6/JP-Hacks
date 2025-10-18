#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: smoke.sh [options]
  --base URL        Base URL of the API (default env BASE or https://lab.160.16.126.35.sslip.io)
  --image PATH      Image to upload (default env IMG or ./Sample Pictures/meat.jpg)
  --storage NAME    Storage value for upload/analyze (default env STORAGE or cool)
  --no-item         Skip creating an item
  --keep-logs       Reserved flag (logs always kept)
  -h, --help        Show this help

Requires LAB_API_KEY in the environment.
USAGE
}

BASE="${BASE:-https://lab.160.16.126.35.sslip.io}"
IMG="${IMG:-./Sample Pictures/meat.jpg}"
STORAGE="${STORAGE:-cool}"
CREATE_ITEM=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base)
      BASE="$2"; shift 2;;
    --image)
      IMG="$2"; shift 2;;
    --storage)
      STORAGE="$2"; shift 2;;
    --no-item)
      CREATE_ITEM=0; shift;;
    --keep-logs)
      shift;;
    -h|--help)
      usage; exit 0;;
    *)
      echo "Unknown option: $1" >&2
      usage; exit 2;;
  esac
done

if [[ -z "${LAB_API_KEY:-}" ]]; then
  echo "LAB_API_KEY not set" >&2
  exit 2
fi

if [[ "${STORAGE,,}" == "local" ]]; then
  STORAGE="cool"
fi

TS="$(date +%Y%m%d-%H%M%S)"
REP="reports/smoke-$TS"
mkdir -p "$REP"
SUMMARY="$REP/SUMMARY.txt"

summary() {
  printf '%s\n' "$*" | tee -a "$SUMMARY" >/dev/null
}

fail() {
  local stage="$1" msg="$2"
  summary "FAIL(${stage}): ${msg}"
  exit 1
}

health_url="$BASE/api/v1/healthz"

code=$(curl -sS -o "$REP/A_health_unauthorized.txt" -w "%{http_code}" "$health_url" || true)
summary "HEALTH(no-key)=$code"
[[ "$code" == "401" ]] || fail "health-no-key" "expected 401 got $code"

code=$(curl -sS -H "X-Api-Key: $LAB_API_KEY" -o "$REP/B_health_ok.txt" -w "%{http_code}" "$health_url")
summary "HEALTH(with-key)=$code"
[[ "$code" == "200" ]] || fail "health-with-key" "expected 200 got $code"

if [[ ! -f "$IMG" ]]; then
  IMG="$REP/tmp.png"
  python3 - <<'PY'
import base64, sys
b = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
with open(sys.argv[1], 'wb') as fh:
    fh.write(base64.b64decode(b))
PY
fi

code=$(curl -sS -X POST "$BASE/api/v1/uploads" \
  -H "X-Api-Key: $LAB_API_KEY" \
  -F "file=@${IMG}" -F "storage=${STORAGE}" \
  -o "$REP/C_upload.json" -w "%{http_code}")
[[ "$code" == "200" || "$code" == "201" ]] || fail "upload" "unexpected status $code"
upload_id=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['upload_id'])" "$REP/C_upload.json")
[[ -n "$upload_id" ]] || fail "upload" "upload_id missing"
summary "UPLOAD: upload_id=$upload_id; storage=$STORAGE"

code=$(curl -sS -X POST "$BASE/api/v1/analyze" \
  -H "X-Api-Key: $LAB_API_KEY" -H "Content-Type: application/json" \
  --data "{\"upload_id\":\"$upload_id\",\"storage\":\"$STORAGE\"}" \
  -o "$REP/D_analyze.json" -w "%{http_code}")
[[ "$code" == "200" || "$code" == "202" ]] || fail "analyze" "unexpected status $code"
job_id=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('job_id',''))" "$REP/D_analyze.json")
job_path=""
if [[ -n "$job_id" ]]; then
  summary "ANALYZE: job_id=$job_id"
else
  summary "ANALYZE: sync result"
  job_path="$REP/E_job_00.json"
  cp "$REP/D_analyze.json" "$job_path"
fi

if [[ -n "$job_id" ]]; then
  deadline=$(( $(date +%s) + 120 ))
  poll=0
  while :; do
    poll=$((poll+1))
    job_path="$REP/E_job_$(printf '%02d' "$poll").json"
    curl -sS -H "X-Api-Key: $LAB_API_KEY" "$BASE/api/v1/jobs/$job_id" -o "$job_path"
    status=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['status'])" "$job_path")
    if [[ "$status" == "done" ]]; then
      break
    fi
    if (( $(date +%s) > deadline )); then
      fail "job" "timeout waiting for done"
    fi
    sleep 2
  done
fi

read_field() {
  local field="$1"
  python3 -c "import json,sys;data=json.load(open(sys.argv[1]));base=data.get('result', data);print(base.get('$field',''))" "$job_path"
}

name=$(read_field name)
classid=$(read_field class_id)
expiry=$(read_field expiry)
foodid=$(read_field food_id)
summary "JOB: status=done; name=$name; class=$classid; expiry=$expiry; food_id=$foodid"

if [[ "$CREATE_ITEM" -eq 1 ]]; then
  code=$(curl -sS -X POST "$BASE/api/v1/items" \
    -H "X-Api-Key: $LAB_API_KEY" -H "Content-Type: application/json" \
    --data "{\"upload_id\":\"$upload_id\",\"storage\":\"$STORAGE\"}" \
    -o "$REP/F_item.json" -w "%{http_code}")
  [[ "$code" == "200" || "$code" == "201" ]] || fail "item" "unexpected status $code"
  itemid=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['id'])" "$REP/F_item.json")
  summary "ITEM: created id=$itemid"
fi

summary "ALL DONE"
