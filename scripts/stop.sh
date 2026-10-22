#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
for name in fake-das kernel; do
  pidfile="data/${name}.pid"
  if [[ -f "$pidfile" ]]; then
    pid=$(cat "$pidfile" || true)
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "[stop] $name pid=$pid"
      kill "$pid" 2>/dev/null || true
      sleep 0.3
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pidfile"
  fi
done
# also free ports if orphaned
for port in 8787 8788; do
  pids=$(ss -lptn "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' || true)
  for p in $pids; do
    echo "[stop] freeing :$port pid=$p"
    kill "$p" 2>/dev/null || true
  done
done
echo "[stop] done"
