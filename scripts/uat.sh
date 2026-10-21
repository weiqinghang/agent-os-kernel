#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
LOG="$ROOT/data/uat-last.log"
mkdir -p data
exec > >(tee "$LOG") 2>&1

BASE_K="${BASE_K:-http://127.0.0.1:8787}"
BASE_D="${BASE_D:-http://127.0.0.1:8788}"

echo "=== UAT $(date '+%Y-%m-%d %H:%M:%S %Z') ==="

curl -sf "$BASE_D/health" | grep -q ok
echo "PASS das health"

H=$(curl -sf "$BASE_K/health")
echo "$H" | grep -q '"status":"ok"'
echo "$H" | grep -q '"das_reachable":true'
echo "$H" | grep -q '"model_mode":"stub"'
echo "$H" | grep -q '"chain":"ok"'
echo "PASS kernel health (stub + chain ok)"
echo "health=$H"

# Expected GMV from SQLite (same DB fake-das uses)
GMV=$(uv run python -c "
import sqlite3
c=sqlite3.connect('services/fake_das/data/sales.db')
print(c.execute('SELECT ROUND(SUM(gmv),2) FROM sales').fetchone()[0])
")
TOP_PROV=$(uv run python -c "
import sqlite3
c=sqlite3.connect('services/fake_das/data/sales.db')
print(c.execute('SELECT province FROM sales GROUP BY province ORDER BY SUM(gmv) DESC LIMIT 1').fetchone()[0])
")
echo "expect GMV=$GMV top_province=$TOP_PROV"

curl -sf -X POST "$BASE_D/mcp" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_datasets","arguments":{}}}' \
  | grep -q ecommerce
echo "PASS list_datasets"

code=$(curl -s -o /tmp/uat-sql.out -w '%{http_code}' -X POST "$BASE_D/mcp" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"query_stats","arguments":{"sql":"select 1"}}}')
test "$code" != "200"
echo "PASS reject SQL (http=$code)"

SID=$(curl -sf -X POST "$BASE_K/sessions" -H 'Content-Type: application/json' -d '{}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
echo "session=$SID"

RESP=$(curl -sf -X POST "$BASE_K/sessions/$SID/messages" \
  -H 'Content-Type: application/json' \
  -d '{"text":"请基于电商销量数据写一份简短洞察报告"}')
echo "$RESP" | grep -EEq '发现|证据|evidence'
echo "$RESP" | grep -EEq 'GMV|类目|渠道|退货'
echo "$RESP" | grep -F "$GMV" >/dev/null
echo "PASS e2e report (GMV matches SQLite)"

RUN_ID=$(echo "$RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin)["run_id"])')
echo "run_id=$RUN_ID"
EVENTS=$(curl -sf "$BASE_K/runs/$RUN_ID")
echo "$EVENTS" | python3 -c '
import sys, json
d=json.load(sys.stdin)
kinds=[e["kind"] for e in d.get("events") or []]
print("event_kinds=", kinds)
assert "model_step" in kinds, kinds
assert "tool_call" in kinds, kinds
n_tool=sum(1 for k in kinds if k=="tool_call")
assert n_tool >= 3, f"need >=3 tool_calls, got {n_tool}"
print(f"PASS run events model_step+tool_call (n_tool={n_tool})")
'

RESP2=$(curl -sf -X POST "$BASE_K/sessions/$SID/messages" \
  -H 'Content-Type: application/json' \
  -d '{"text":"哪个省份 GMV 最高？"}')
echo "$RESP2" | grep -F "$TOP_PROV" >/dev/null
echo "PASS follow-up top province=$TOP_PROV"

echo "UAT PASS"
exit 0
