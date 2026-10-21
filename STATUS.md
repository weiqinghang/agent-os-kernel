# Overnight POC STATUS

- **Updated:** 2026-10-22 00:51 Asia/Shanghai (CST, UTC+8)
- **Result:** UAT PASS · services RUNNING
- **SoT Issue:** https://github.com/weiqinghang/agent-os-kernel/issues/3

## Processes

| Service | Port | PID file | Listening PID (ss) |
| --- | --- | --- | --- |
| fake-das | 127.0.0.1:8788 | data/fake-das.pid → 156206 | uvicorn 156216 |
| agent-os-kernel | 127.0.0.1:8787 | data/kernel.pid → 156207 | uvicorn 156215 |

Health:

```text
GET http://127.0.0.1:8788/health → {"status":"ok","rows":10000,"service":"fake-das"}
GET http://127.0.0.1:8787/health → {"status":"ok","service":"agent-os-kernel","das_reachable":true,"hitl_auto":true,"llm":false}
```

## Data

- SQLite: `services/fake_das/data/sales.db` — **10000** ecommerce rows
- Kernel DB: `data/kernel.db` (sessions / runs / run_events)
- UAT log: `data/uat-last.log`

## How to demo (morning)

```bash
cd /workspace/agent-os-kernel
# if somehow down:
./scripts/start.sh

curl -s http://127.0.0.1:8788/health
curl -s http://127.0.0.1:8787/health

# Chat UI
open http://127.0.0.1:8787/
# 试：「请基于电商销量数据写一份简短洞察报告」
# 追问：「哪个省份 GMV 最高？」

./scripts/uat.sh
```

Stub LLM mode (no MODEL_API_KEY / XAI_API_KEY / OPENAI_API_KEY): report still **calls DAS** and cites real aggregates with `evidence:` fingerprints.

## Plan §11 checklist

- [x] fake-das + kernel listening 8788/8787
- [x] SQLite ≈10k ecommerce rows
- [x] Chat UI can request report + follow-up
- [x] Report contains DAS evidence anchors
- [x] uat.sh PASS
- [x] Plan file present and matches implementation

## Stop / restart

```bash
./scripts/stop.sh
./scripts/start.sh
```
