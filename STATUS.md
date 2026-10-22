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

## Chat UI · P0′

终端默认**没有** Agent 链路。发令后同一条呼吸进度条原地更新白名单文案（处理中 / 思考中 / 正在查询数据 / 正在整理结论 / 等确认）。工具名、`mode`、run/session id、原始 JSON、CoT 不上屏。结论正文默认也不出现 `dataset=`、`evidence:` 指纹、`Tool Gateway`；每条发现给「查看依据」。页眉「调试」默认关，链路只重复白名单短句，打开后结论原文（含指纹）可对。开场是业务空态加可点示例。时间窗仍只有 chip；已有结论时改窗是一行复述加确认，确认不会把输入框占位符当成用户消息。第 N 条依据按该条发现的渠道/类目/省份抽样本。

## Demo

```bash
cd /workspace/agent-os-kernel
./scripts/start.sh   # if down
# UI http://127.0.0.1:8787/
./scripts/uat.sh
```
