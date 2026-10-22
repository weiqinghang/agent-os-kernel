#!/usr/bin/env bash
# Default: real dataset-analysis-service on :8788 (APP_ENV=local, workers 2) + kernel :8787.
# Overnight fake DAS only when USE_FAKE_DAS=1.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p data
# shellcheck disable=SC1091
source "$ROOT/scripts/load_local_env.sh"
load_local_env "$ROOT"

export HITL_AUTO="${HITL_AUTO:-1}"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export DAS_DATASET_ID="${DAS_DATASET_ID:-ecommerce_sales_v2}"

./scripts/stop.sh 2>/dev/null || true

if [[ "${USE_FAKE_DAS:-0}" == "1" ]]; then
  mkdir -p services/fake_das/data
  export FAKE_DAS_URL="${FAKE_DAS_URL:-http://127.0.0.1:8788}"
  unset DAS_MCP_URL || true
  if [[ ! -f services/fake_das/data/sales.db ]]; then
    echo "[start] seeding ecommerce DB..."
    uv run python scripts/seed_ecommerce.py
  else
    uv run python scripts/seed_ecommerce.py --ensure-alias
  fi
  echo "[start] fake-das :8788 (USE_FAKE_DAS=1)"
  nohup uv run uvicorn services.fake_das.app:app --host 127.0.0.1 --port 8788 --workers 1 \
    > data/fake-das.log 2>&1 &
  echo $! > data/fake-das.pid
else
  DAS_ROOT="${DAS_ROOT:-/workspace/dataset-analysis-service}"
  if [[ ! -d "$DAS_ROOT" ]]; then
    echo "[start] real DAS tree missing: $DAS_ROOT" >&2
    echo "[start] copy dataset-analysis-service there, or set USE_FAKE_DAS=1 for the overnight fake." >&2
    exit 1
  fi
  if [[ ! -x "$DAS_ROOT/.venv/bin/python" ]]; then
    echo "[start] DAS venv missing: $DAS_ROOT/.venv" >&2
    exit 1
  fi
  if [[ ! -f "$DAS_ROOT/.env" ]]; then
    echo "[start] DAS env missing: $DAS_ROOT/.env (gitignored; APP_ENV=local)" >&2
    exit 1
  fi
  export DAS_MCP_URL="${DAS_MCP_URL:-http://127.0.0.1:8788/api/mcp}"
  export DAS_HEALTH_URL="${DAS_HEALTH_URL:-http://127.0.0.1:8788/health}"
  export FAKE_DAS_URL="${FAKE_DAS_URL:-http://127.0.0.1:8788}"
  if [[ -z "${DAS_MCP_AUTH_TOKEN:-}" ]]; then
    echo "[start] DAS_MCP_AUTH_TOKEN missing (put it in data/das-local.env, do not commit)" >&2
    exit 1
  fi
  echo "[start] real DAS :8788 workers=2 ($DAS_ROOT)"
  (
    cd "$DAS_ROOT"
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
    export APP_ENV="${APP_ENV:-local}"
    export APP_RUNTIME_ENV_FILE="${APP_RUNTIME_ENV_FILE:-$DAS_ROOT/.env}"
    nohup .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8788 --workers 2 \
      > "$ROOT/data/das.log" 2>&1 &
    echo $! > "$ROOT/data/das.pid"
  )
fi

echo "[start] kernel :8787"
nohup uv run uvicorn agent_os_kernel.main:app --host 127.0.0.1 --port 8787 --workers 1 \
  > data/kernel.log 2>&1 &
echo $! > data/kernel.pid

for _ in $(seq 1 60); do
  if curl -sf http://127.0.0.1:8788/health >/dev/null && curl -sf http://127.0.0.1:8787/health >/dev/null; then
    echo "[start] both healthy"
    if [[ "${USE_FAKE_DAS:-0}" == "1" ]]; then
      echo "FAKE_DAS_PID=$(cat data/fake-das.pid) KERNEL_PID=$(cat data/kernel.pid)"
    else
      echo "DAS_PID=$(cat data/das.pid) KERNEL_PID=$(cat data/kernel.pid)"
    fi
    exit 0
  fi
  sleep 0.5
done
echo "[start] health timeout" >&2
tail -n 40 data/das.log data/fake-das.log data/kernel.log 2>/dev/null || true
exit 1
