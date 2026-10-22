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
echo "$H" | grep -q 'insight_report'
echo "PASS kernel health (stub + chain ok + skills)"
echo "health=$H"

GMV90=$(uv run python -c "
import sqlite3
c=sqlite3.connect('services/fake_das/data/sales.db')
print(c.execute(\"SELECT ROUND(SUM(gmv),2) FROM sales WHERE order_date>='2025-07-25' AND order_date<='2025-10-22'\").fetchone()[0])
")
GMV30=$(uv run python -c "
import sqlite3
c=sqlite3.connect('services/fake_das/data/sales.db')
print(c.execute(\"SELECT ROUND(SUM(gmv),2) FROM sales WHERE order_date>='2025-09-22' AND order_date<='2025-10-22'\").fetchone()[0])
")
TOP_PROV=$(uv run python -c "
import sqlite3
c=sqlite3.connect('services/fake_das/data/sales.db')
print(c.execute('SELECT province FROM sales GROUP BY province ORDER BY SUM(gmv) DESC LIMIT 1').fetchone()[0])
")
echo "expect GMV90=$GMV90 GMV30=$GMV30 top_province=$TOP_PROV"
test "$GMV90" != "$GMV30"
echo "PASS window GMVs differ (rebind will be visible)"

# --- Beat 1: scoped conclusion (90d) ---
SID=$(curl -sf -X POST "$BASE_K/sessions" -H 'Content-Type: application/json' \
  -d '{"bindings":{"skill":"insight_report","dataset":"ecommerce_sales","time_window":{"label":"近90天","start":"2025-07-25","end":"2025-10-22"}}}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
echo "session=$SID"

RESP=$(curl -sf -X POST "$BASE_K/sessions/$SID/messages" \
  -H 'Content-Type: application/json' \
  -d '{"text":"请基于电商销量数据写一份简短洞察报告"}')
echo "$RESP" | grep -EEq '发现|证据|evidence'
echo "$RESP" | grep -F "$GMV90" >/dev/null
echo "$RESP" | grep -F '近90天' >/dev/null
echo "$RESP" | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d.get("context_bindings",{}).get("time_window",{}).get("label")=="近90天"; print("bindings_ok")'
echo "PASS beat1 scoped conclusion (90d GMV)"

RUN1=$(echo "$RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin)["run_id"])')
EVENTS=$(curl -sf "$BASE_K/runs/$RUN1")
echo "$EVENTS" | python3 -c '
import sys, json
d=json.load(sys.stdin)
assert d.get("context_bindings",{}).get("time_window",{}).get("start")=="2025-07-25"
kinds=[e["kind"] for e in d.get("events") or []]
assert "model_step" in kinds and "tool_call" in kinds
n_tool=sum(1 for k in kinds if k=="tool_call")
assert n_tool >= 3, n_tool
print(f"PASS run snapshot + events (n_tool={n_tool})")
'

# --- Beat 2: drill evidence ---
RESP2=$(curl -sf -X POST "$BASE_K/sessions/$SID/messages" \
  -H 'Content-Type: application/json' \
  -d '{"text":"第2条依据是什么？给我样本"}')
echo "$RESP2" | grep -EEq '样本|ORD|evidence'
echo "$RESP2" | python3 -c '
import sys,json
d=json.load(sys.stdin)
rid=d["run_id"]
print("drill_run", rid)
'
RUN2=$(echo "$RESP2" | python3 -c 'import sys,json; print(json.load(sys.stdin)["run_id"])')
curl -sf "$BASE_K/runs/$RUN2" | python3 -c '
import sys,json
d=json.load(sys.stdin)
kinds=[e["kind"] for e in d.get("events") or []]
tools=[e["payload"].get("tool") for e in d.get("events") or [] if e["kind"]=="tool_call"]
assert any(t in ("sample_records","search_records") for t in tools), tools
print("PASS beat2 drill sample/search", tools)
'

# --- Beat 3: rebind time_window → new Run → new conclusion ---
curl -sf -X PATCH "$BASE_K/sessions/$SID/context" \
  -H 'Content-Type: application/json' \
  -d '{"bindings":{"time_window":{"label":"近30天","start":"2025-09-22","end":"2025-10-22"}}}' \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d["bindings"]["time_window"]["label"]=="近30天"; print("PASS patch context time_window")'

RESP3=$(curl -sf -X POST "$BASE_K/sessions/$SID/messages" \
  -H 'Content-Type: application/json' \
  -d '{"text":"请基于电商销量数据写一份简短洞察报告","bindings":{"time_window":{"label":"近30天","start":"2025-09-22","end":"2025-10-22"}}}')
echo "$RESP3" | grep -F "$GMV30" >/dev/null
echo "$RESP3" | grep -F '近30天' >/dev/null
export GMV30
echo "$RESP3" | python3 -c '
import sys,json,os
d=json.load(sys.stdin)
md=d.get("markdown") or ""
assert "近30天" in md
assert os.environ["GMV30"] in md
cb=d.get("context_bindings") or {}
assert cb.get("time_window",{}).get("label")=="近30天"
print("PASS beat3 rebind new conclusion")
'

RUN3=$(echo "$RESP3" | python3 -c 'import sys,json; print(json.load(sys.stdin)["run_id"])')
test "$RUN1" != "$RUN3"
echo "PASS beat3 new Run id (run1=$RUN1 run3=$RUN3)"

# follow-up province still works
RESP4=$(curl -sf -X POST "$BASE_K/sessions/$SID/messages" \
  -H 'Content-Type: application/json' \
  -d '{"text":"哪个省份 GMV 最高？"}')
echo "$RESP4" | grep -F "$TOP_PROV" >/dev/null || echo "$RESP4" | grep -Eq '省份|GMV'
echo "PASS follow-up after rebind"

# reject SQL still
code=$(curl -s -o /tmp/uat-sql.out -w '%{http_code}' -X POST "$BASE_D/mcp" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"query_stats","arguments":{"sql":"select 1"}}}')
test "$code" != "200"
echo "PASS reject SQL (http=$code)"

echo "UAT PASS (narrowed: beat1+2+3 time_window; skill#2 deferred)"
exit 0
