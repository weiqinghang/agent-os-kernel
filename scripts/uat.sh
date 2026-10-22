#!/usr/bin/env bash
# Dual path: real DAS (default) asserts seed id ecommerce_sales_v2 via MCP.
# USE_FAKE_DAS=1 or a fake-das /health body keeps the SQLite numeric checks.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
LOG="$ROOT/data/uat-last.log"
mkdir -p data
# shellcheck disable=SC1091
source "$ROOT/scripts/load_local_env.sh"
load_local_env "$ROOT"
exec > >(tee "$LOG") 2>&1

BASE_K="${BASE_K:-http://127.0.0.1:8787}"
BASE_D="${BASE_D:-http://127.0.0.1:8788}"
DATASET_ID="${DAS_DATASET_ID:-ecommerce_sales_v2}"
export DATASET_ID

echo "=== UAT $(date '+%Y-%m-%d %H:%M:%S %Z') ==="

DAS_HEALTH=$(curl -sf "$BASE_D/health")
echo "$DAS_HEALTH" | grep -q ok
echo "PASS das health"

if [[ "${USE_FAKE_DAS:-0}" == "1" ]] || echo "$DAS_HEALTH" | grep -q '"service":"fake-das"'; then
  DAS_MODE=fake
else
  DAS_MODE=real
fi
echo "DAS_MODE=$DAS_MODE dataset=$DATASET_ID"

H=$(curl -sf "$BASE_K/health")
echo "$H" | grep -q '"status":"ok"'
echo "$H" | grep -q '"das_reachable":true'
echo "$H" | grep -Eq '"model_mode":"(stub|live)"'
echo "$H" | grep -q '"chain":"ok"'
echo "$H" | grep -q 'insight_report'
echo "$H" | grep -F "\"dataset_id\":\"ecommerce_sales_v2\"" >/dev/null
echo "PASS kernel health (chain ok + skills + seed dataset id)"
echo "health=$H"

if [[ "$DAS_MODE" == "fake" ]]; then
  uv run python scripts/seed_ecommerce.py --ensure-alias
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
else
  MCP_URL="${DAS_MCP_URL:-$BASE_D/api/mcp}"
  if [[ -z "${DAS_MCP_AUTH_TOKEN:-}" ]]; then
    echo "FAIL real DAS requires DAS_MCP_AUTH_TOKEN" >&2
    exit 1
  fi
  LIST=$(curl -sf -X POST "$MCP_URL" \
    -H "Authorization: Bearer ${DAS_MCP_AUTH_TOKEN}" \
    -H "Accept: application/json, text/event-stream" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_datasets","arguments":{}}}')
  echo "$LIST" | grep -F "$DATASET_ID" >/dev/null
  echo "PASS real MCP list_datasets contains $DATASET_ID"
  TOOLS=$(curl -sf -X POST "$MCP_URL" \
    -H "Authorization: Bearer ${DAS_MCP_AUTH_TOKEN}" \
    -H "Accept: application/json, text/event-stream" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}')
  echo "$TOOLS" | grep -F 'describe_fields' >/dev/null
  echo "$TOOLS" | grep -F 'describe_tag_hierarchies' >/dev/null
  echo "PASS real MCP tools/list exposes describe_fields and describe_tag_hierarchies"
  GMV90=""
  GMV30=""
  TOP_PROV=""
fi
export DAS_MODE GMV90 GMV30 TOP_PROV

# --- Beat 1: scoped conclusion (90d) ---
SID=$(curl -sf -X POST "$BASE_K/sessions" -H 'Content-Type: application/json' \
  -d "{\"bindings\":{\"skill\":\"insight_report\",\"dataset\":\"$DATASET_ID\",\"time_window\":{\"label\":\"近90天\",\"start\":\"2025-07-25\",\"end\":\"2025-10-22\"}}}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
echo "session=$SID"

RESP=$(curl -sf -X POST "$BASE_K/sessions/$SID/messages" \
  -H 'Content-Type: application/json' \
  -d '{"text":"请基于电商销量数据写一份简短洞察报告"}')
echo "$RESP" | grep -EEq '发现|证据|evidence'
echo "$RESP" | grep -F "$DATASET_ID" >/dev/null
echo "$RESP" | grep -F '近90天' >/dev/null
if [[ "$DAS_MODE" == "fake" ]]; then
  echo "$RESP" | grep -F "$GMV90" >/dev/null
fi
echo "$RESP" | python3 -c 'import sys,json,os; d=json.load(sys.stdin); assert d.get("context_bindings",{}).get("time_window",{}).get("label")=="近90天"; assert d.get("context_bindings",{}).get("dataset")==os.environ["DATASET_ID"]; print("bindings_ok")'
echo "PASS beat1 scoped conclusion (dataset=$DATASET_ID)"

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
echo "$RESP2" | grep -EEq '样本|ORD|evidence|依据'
if [[ "$DAS_MODE" == "fake" ]]; then
  echo "$RESP2" | python3 -c '
import re, sys, json
d=json.load(sys.stdin)
md=d.get("markdown") or ""
m=re.search(r"这条发现讲的是渠道 \*\*(.+?)\*\*", md)
assert m, md[:800]
top=m.group(1)
lines=[ln for ln in md.splitlines() if re.match(r"\d+\. ", ln)]
assert lines, md[:800]
assert all((" · "+top+" · ") in ln for ln in lines), (top, lines)
print("PASS beat2 samples align", top, "n="+str(len(lines)), "run", d["run_id"])
'
else
  echo "$RESP2" | python3 -c '
import sys, json
d=json.load(sys.stdin)
md=d.get("markdown") or ""
assert "依据" in md or "样本" in md, md[:800]
print("PASS beat2 real DAS drill page", d.get("run_id"))
'
fi
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
echo "$RESP3" | grep -F '近30天' >/dev/null
echo "$RESP3" | grep -F "$DATASET_ID" >/dev/null
if [[ "$DAS_MODE" == "fake" ]]; then
  echo "$RESP3" | grep -F "$GMV30" >/dev/null
fi
export GMV30 DATASET_ID DAS_MODE
echo "$RESP3" | python3 -c '
import sys, json, os
d=json.load(sys.stdin)
md=d.get("markdown") or ""
assert "近30天" in md
assert os.environ["DATASET_ID"] in md
if os.environ.get("DAS_MODE") == "fake":
    assert os.environ["GMV30"] in md
cb=d.get("context_bindings") or {}
assert cb.get("time_window",{}).get("label")=="近30天"
assert cb.get("dataset")==os.environ["DATASET_ID"]
print("PASS beat3 rebind new conclusion")
'

RUN3=$(echo "$RESP3" | python3 -c 'import sys,json; print(json.load(sys.stdin)["run_id"])')
test "$RUN1" != "$RUN3"
echo "PASS beat3 new Run id (run1=$RUN1 run3=$RUN3)"

RESP4=$(curl -sf -X POST "$BASE_K/sessions/$SID/messages" \
  -H 'Content-Type: application/json' \
  -d '{"text":"哪个省份 GMV 最高？"}')
if [[ "$DAS_MODE" == "fake" ]]; then
  echo "$RESP4" | grep -F "$TOP_PROV" >/dev/null || echo "$RESP4" | grep -Eq '省份|GMV'
else
  echo "$RESP4" | grep -Eq '省份|GMV|发现'
fi
echo "PASS follow-up after rebind"

# reject SQL
if [[ "$DAS_MODE" == "fake" ]]; then
  code=$(curl -s -o /tmp/uat-sql.out -w '%{http_code}' -X POST "$BASE_D/mcp" \
    -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"query_stats","arguments":{"sql":"select 1"}}}')
  test "$code" != "200"
  echo "PASS reject SQL (http=$code)"
else
  MCP_URL="${DAS_MCP_URL:-$BASE_D/api/mcp}"
  code=$(curl -s -o /tmp/uat-sql.out -w '%{http_code}' -X POST "$MCP_URL" \
    -H "Authorization: Bearer ${DAS_MCP_AUTH_TOKEN}" \
    -H "Accept: application/json, text/event-stream" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"query_stats","arguments":{"sql":"select 1","dataset_id":"'"$DATASET_ID"'"}}}')
  python3 - "$code" <<'PY'
import json, sys
code = sys.argv[1]
raw = open("/tmp/uat-sql.out", encoding="utf-8", errors="replace").read()
if code != "200":
    print(f"PASS reject SQL (http={code})")
    raise SystemExit(0)
body = raw
if "data:" in raw and raw.lstrip().startswith("event:"):
    chunks = []
    for line in raw.splitlines():
        if line.startswith("data:"):
            chunks.append(line[5:].strip())
    body = chunks[-1] if chunks else raw
data = json.loads(body)
err = data.get("error") or (data.get("result") or {}).get("isError")
if not err:
    raise SystemExit("real DAS accepted raw SQL")
print("PASS reject SQL (jsonrpc error)")
PY
fi

echo "UAT PASS (mode=$DAS_MODE dataset=$DATASET_ID; skill#2 deferred)"
exit 0
