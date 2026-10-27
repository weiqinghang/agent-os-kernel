#!/usr/bin/env bash
# Skill#2 checks. Pytest covers the stub path (no live DAS required).
# If the kernel is already up, also exercise the return-risk session over HTTP.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Live titles keep product wording. This gate only requires 退货风险简报 and
# 近7天 in the same paragraph or the first screen. Both of these pass:
#   # 退货风险简报｜近7天
#   退货风险简报（近7天）
TITLE_OK_PY=$(cat <<'PY'
def brief_title_ok(text: str) -> bool:
    """Accept a brief whose title segment or first screen names the window.

    Parentheses （近7天） and the full-width bar ｜近7天 are both fine.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    chunks = [text[:1200]]
    chunks.extend(part.strip() for part in text.split("\n\n") if part.strip())
    chunks.extend(line.strip() for line in text.splitlines() if "退货风险简报" in line)
    return any("退货风险简报" in chunk and "近7天" in chunk for chunk in chunks)
PY
)

echo "=== UAT skill#2 pytest $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
if command -v uv >/dev/null 2>&1; then
  uv run pytest tests/test_return_risk_brief.py tests/test_ui_p0_prime.py -o addopts=-q
else
  python3 -m pytest tests/test_return_risk_brief.py tests/test_ui_p0_prime.py -o addopts=-q
fi

echo "=== UAT skill#2 title forms ==="
UAT_TITLE_OK_PY="$TITLE_OK_PY" python3 - <<'PY'
import os

exec(os.environ["UAT_TITLE_OK_PY"])
samples = (
    "# 退货风险简报｜近7天",
    "退货风险简报（近7天）",
)
for sample in samples:
    assert brief_title_ok(sample), sample
far = "# 退货风险简报\n\n" + ("正文。\n" * 400) + "\n\n窗口是近7天"
assert not brief_title_ok("# 退货风险简报")
assert not brief_title_ok(far)
print("PASS title forms")
PY

BASE_K="${BASE_K:-http://127.0.0.1:8787}"
if ! curl -sf "$BASE_K/health" >/dev/null; then
  echo "NOTE kernel not on $BASE_K; stub pytest is the skill#2 gate on this box"
  echo "Reseed fake DAS to ~1e5 (same id, no new MCP): uv run python scripts/seed_ecommerce.py"
  echo "UAT skill#2 PASS (pytest)"
  exit 0
fi

echo "=== UAT skill#2 live $BASE_K ==="
SID=$(curl -sf -X POST "$BASE_K/sessions" -H 'Content-Type: application/json' \
  -d '{"agent_id":"return_risk.brief"}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
META=$(curl -sf "$BASE_K/sessions/$SID")
echo "$META" | python3 -c '
import json,sys
body=json.load(sys.stdin)
assert body.get("agent_id")=="return_risk.brief", body.get("agent_id")
tw=(body.get("bindings") or {}).get("time_window") or {}
assert tw.get("label")=="近7天", tw
assert (body.get("bindings") or {}).get("dataset")=="ecommerce_sales_v2"
assert (body.get("bindings") or {}).get("skill")=="return_risk_brief"
print("PASS session defaults")
'
code=$(curl -s -o /tmp/uat-skill2-immutable.out -w '%{http_code}' -X PATCH "$BASE_K/sessions/$SID/context" \
  -H 'Content-Type: application/json' \
  -d '{"bindings":{"agent_id":"insight.report"}}')
test "$code" = "409"
echo "PASS agent_id immutable"

UAT_TITLE_OK_PY="$TITLE_OK_PY" python3 - "$BASE_K" "$SID" <<'PY'
import json, os, sys, time, urllib.request

exec(os.environ["UAT_TITLE_OK_PY"])
base, sid = sys.argv[1], sys.argv[2]
req = urllib.request.Request(
    base + "/sessions/" + sid + "/messages",
    data=json.dumps({"text": "近7天退货风险"}).encode(),
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(req, timeout=30) as resp:
    accepted = json.load(resp)
run_id = accepted["run_id"]
deadline = time.time() + 180
while time.time() < deadline:
    with urllib.request.urlopen(base + "/runs/" + run_id, timeout=10) as resp:
        snap = json.load(resp)
    if snap.get("state") in ("completed", "failed", "cancelled", "awaiting_hitl"):
        break
    time.sleep(0.2)
else:
    raise SystemExit("turn did not finish")
assert snap["state"] == "completed", snap.get("result")
text = snap.get("result") or ""
assert brief_title_ok(text), text[:500]
assert "建议先看" in text
assert "类目集中" not in text
assert "推迟" not in text
kinds = [event.get("kind") for event in snap.get("events") or []]
assert not any(str(kind).startswith("phase_") for kind in kinds)
print("PASS live risk brief")
PY

echo "UAT skill#2 PASS"
