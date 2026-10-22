# agent-os-kernel

企业 Agent 通用框架（AI OS 控制面）内核仓库。

- **狗粮 #1：** 聊天式洞察报告助理（结论页 + 同线程追问 + DAS MCP）
- **形态：** 模块化单体；八层控制面可简陋但要齐
- **协作：** 不强制 Playbook；用 `designs/collab/` 事件日志观测角色协作

## 链路（必须同路径）

```text
Session → Run → Orchestrator → Model Broker → Tool Gateway → HITL → Obs
                              ↘ stub | live ↗
```

- **有 key**（`MODEL_API_KEY` / `XAI_API_KEY` / `OPENAI_API_KEY`）：OpenAI 兼容 chat；默认 base 在有 `XAI_API_KEY` 时为 `https://api.x.ai/v1`，否则 openai；默认模型 `grok-2-latest`（可用 `MODEL_NAME` / `MODEL_BASE_URL` 覆盖）。
- **无 key**：`StubBackend` 仍返回结构化 `tool_calls` / `final`；数字只来自 DAS 工具结果 + evidence 指纹。**不是**「技能偷偷拼假报告」。
- Skill（`insight_report`）只是清单（prompt / 白名单工具 / HITL kinds），**禁止**绕过 orchestrator 直调 DAS。

## 过夜 POC（电商销量洞察）

| 服务 | 端口 | 说明 |
| --- | --- | --- |
| fake-das | `127.0.0.1:8788` | SQLite ~1 万行电商销量；MCP 五工具白名单 |
| agent-kernel | `127.0.0.1:8787` | 完整编排链 + 聊天 UI |

### 启动 / 验收

```bash
cd /workspace/agent-os-kernel
./scripts/start.sh

curl -s http://127.0.0.1:8787/health
# 期望含 "model_mode":"stub","chain":"ok","das_reachable":true

# 浏览器：http://127.0.0.1:8787/
./scripts/uat.sh
./scripts/stop.sh
```

默认 `HITL_AUTO=1`。设 `HITL_AUTO=0` 时 outline 会 pause，用 `POST /runs/{id}/resume`。

详见 [过夜计划](designs/2026-10-22-overnight-ecommerce-insight-poc.md)、[链路补强](designs/2026-10-22-kernel-chain-hardening.md)、`STATUS.md`。

## 文档入口

- [M0 宪章](designs/2026-10-21-enterprise-agent-framework-m0-charter.md)
- [协作看板](designs/collab/BOARD.md)
- [协作事件](designs/collab/events.jsonl)

## 里程碑

| 阶段 | 状态 |
| --- | --- |
| M0 宪章 | ✅ |
| Overnight POC（假 DAS + 报告） | ✅ 箱内可跑 |
| Kernel chain hardening（同路径 stub/live） | ✅ |
| M1 包边界 + 接口草图 | ⏳ |
| M2 可跑内核 + 报告 Skill | 🚫 另令（嘉树） |
| M3 真数据烟测 | 🚫 |

## 硬规则（摘要）

- Session ≠ Run；内核禁洞察业务专有名词进包名
- DAS 白名单只读；禁止 SQL 透传
- 空 key 也必须走 Model Broker（StubBackend），禁止 skill 旁路
- v0 不依赖 simulated-research
