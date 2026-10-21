# Kernel chain hardening（过夜补强）

- **何时：** 2026-10-22 凌晨（Asia/Shanghai）
- **触发：** 用户指出 POC 在无 API key 时「技能偷偷拼报告、假装走模型」——底座链路未真正贯通
- **目标：** 空 key 也可走 **同一条** Session → Run → Orchestrator → Model Broker → Tool Gateway → HITL → Obs

## 改动

1. **新增 `orchestrator.py`**：每条用户消息创建 Run；循环 `decide` → 工具 / final，直到 completed / failed / awaiting_hitl。
2. **重写 `model_broker.py`**：`decide()` 永远进 broker。
   - 有 `MODEL_API_KEY` / `XAI_API_KEY` / `OPENAI_API_KEY` → OpenAI 兼容 chat（默认 x.ai 或 openai；模型 `grok-2-latest`）。
   - 无 key → **StubBackend**：返回 `{type:tool_calls,...}` 或 `{type:final,content}`；按意图选 DAS 白名单工具；final **只引用** tool_results 里的聚合与 evidence 指纹，禁止臆造数字。
3. **`skills/insight_report.py` 瘦身为清单**：system prompt / allowed_tools / hitl_kinds；**不再**直调 DAS。
4. **`main.py`**：`/sessions/{id}/messages` 只走 orchestrator。
5. **Health**：`model_mode: stub|live`，`chain: ok`（DAS 可达时）。
6. **UAT**：断言 stub 模式、报告 GMV 对齐 SQLite、`/runs/{id}` 含 `model_step` + ≥3 `tool_call`、追问省份对齐库内第一。

## 仍诚实标注

- 数据仍是自造 SQLite，非公司真 DAS。
- 无 key 时是 StubBackend（结构化决策），不是外部 LLM；但与 live **同编排路径**。
- `HITL_AUTO=1` 默认自动批准 outline；`HITL_AUTO=0` 会 pause，可用 `/runs/{id}/resume`。

## 非目标（未做）

- 真 Staging DAS、真模型必配、工作流引擎、微服务拆分。
