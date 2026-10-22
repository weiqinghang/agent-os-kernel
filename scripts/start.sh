#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p data services/fake_das/data

export HITL_AUTO="${HITL_AUTO:-1}"
export FAKE_DAS_URL="${FAKE_DAS_URL:-http://127.0.0.1:8788}"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

if [[ ! -f services/fake_das/data/sales.db ]]; then
  echo "[start] seeding ecommerce DB..."
  uv run python scripts/seed_ecommerce.py
fi

./scripts/stop.sh 2>/dev/null || true

echo "[start] fake-das :8788"
nohup uv run uvicorn services.fake_das.app:app --host 127.0.0.1 --port 8788 --workers 1 \
  > data/fake-das.log 2>&1 &
echo $! > data/fake-das.pid

echo "[start] kernel :8787"
nohup uv run uvicorn agent_os_kernel.main:app --host 127.0.0.1 --port 8787 --workers 1 \
  > data/kernel.log 2>&1 &
echo $! > data/kernel.pid

# wait health
for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8788/health >/dev/null && curl -sf http://127.0.0.1:8787/health >/dev/null; then
    echo "[start] both healthy"
    echo "DAS_PID=$(cat data/fake-das.pid) KERNEL_PID=$(cat data/kernel.pid)"
    exit 0
  fi
  sleep 0.5
done
echo "[start] health timeout" >&2
tail -n 40 data/fake-das.log data/kernel.log || true
exit 1
