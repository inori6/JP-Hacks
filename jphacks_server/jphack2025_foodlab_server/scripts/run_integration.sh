#!/usr/bin/env bash
set -euo pipefail

export API_BASE="${API_BASE:-https://lab.160.16.126.35.sslip.io}"
export RUN_INTEGRATION=1

pytest -m integration -q "$@"
