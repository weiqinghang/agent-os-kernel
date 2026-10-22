# Overnight POC STATUS

- **Updated:** 2026-10-22 08:28 Asia/Shanghai (CST, UTC+8)
- **Result:** UAT PASS（晨间复测）· services RUNNING · demo ready
- **SoT Issue:** https://github.com/weiqinghang/agent-os-kernel/issues/3
- **PR:** https://github.com/weiqinghang/agent-os-kernel/pull/4

## Chain

`Session → Run → Orchestrator → Model Broker(stub|live) → Tool Gateway → HITL → Obs`

- Skill `insight_report` = manifest only (no direct DAS)
- No API key → `model_mode: stub`；有 key → `live`；**同编排路径**

## Processes

| Service | Port | launcher PID | listening uvicorn |
| --- | --- | --- | --- |
| fake-das | 127.0.0.1:8788 | 172969 | 172978 |
| agent-os-kernel | 127.0.0.1:8787 | 172970 | 172979 |

Health:

```text
GET http://127.0.0.1:8787/health → {"status":"ok","service":"agent-os-kernel","das_reachable":true,"model_mode":"stub","chain":"ok","hitl_auto":true}
```

## Data

- SQLite: `services/fake_das/data/sales.db` — **10000** rows；总 GMV **11838497.57**；省份第一 **江苏**
- Kernel DB: `data/kernel.db`
- UAT log: `data/uat-last.log` → **UAT PASS**
- Sample run: `run_3cbdbe50347f` (08:24 UAT) events include model_step + 6× tool_call

## Chat UI · Agent 链路

每条助理回复下方有可折叠 **Agent 链路** 时间线：发完消息后前端 `GET /runs/{run_id}`，按序展示 `model_step` / `tool_call`（含工具名）/ `hitl` / `state`（中文标签）。HITL 确认/拒绝按钮未改。本地改动，未推仓。

## Demo

```bash
cd /workspace/agent-os-kernel
./scripts/start.sh   # if down
# UI http://127.0.0.1:8787/
./scripts/uat.sh
```
