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
curl -sf "$BASE_K/health" | grep -q ok
echo "PASS kernel health"

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
echo "PASS e2e report"

RESP2=$(curl -sf -X POST "$BASE_K/sessions/$SID/messages" \
  -H 'Content-Type: application/json' \
  -d '{"text":"哪个省份 GMV 最高？"}')
echo "$RESP2" | grep -EEq '省|GMV'
echo "PASS follow-up"

echo "UAT PASS"
exit 0
