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

- **有 key**（`MODEL_API_KEY` / `XAI_API_KEY` / `OPENAI_API_KEY`）：OpenAI 兼容 `chat/completions`，请求带 `tools`，工具结果走 `tool` 角色；`tool_calls` 解析成原来的 `calls`。没有原生 `tool_calls` 时仍解析正文里的 JSON。默认 base 在有 `XAI_API_KEY` 时为 `https://api.x.ai/v1`，否则 openai；默认模型 `grok-2-latest`（可用 `MODEL_NAME` / `MODEL_BASE_URL` 覆盖）。
- **无 key**：`StubBackend` 仍返回结构化 `tool_calls` / `final`；数字只来自 DAS 工具结果 + evidence 指纹。**不是**「技能偷偷拼假报告」。
- Skill（`insight_report`）只是清单（prompt / 白名单工具 / HITL kinds），**禁止**绕过 orchestrator 直调 DAS。

## 过夜 POC（电商销量洞察）

| 服务 | 端口 | 说明 |
| --- | --- | --- |
| dataset-analysis-service | `127.0.0.1:8788` | **默认**。本地 SQLite，`APP_ENV=local`，uvicorn `--workers 2`，MCP 在 `/api/mcp` |
| agent-kernel | `127.0.0.1:8787` | 完整编排链 + 聊天 UI。Tool Gateway 带 Bearer 打 `DAS_MCP_URL` |
| fake-das | `127.0.0.1:8788` | 仅 `USE_FAKE_DAS=1`。过夜假服务，`/mcp` 无鉴权 |

### 启动 / 验收

```bash
cd /workspace/agent-os-kernel
# 真 DAS：/workspace/dataset-analysis-service + gitignored data/das-local.env
# .env 里的 MODEL_* / XAI_* / OPENAI_* 会自动带上，不必再 export
./scripts/start.sh

curl -s http://127.0.0.1:8787/health
# 期望含 "chain":"ok","das_reachable":true,"dataset_id":"ecommerce_sales_v2"

# 浏览器：http://127.0.0.1:8787/
./scripts/uat.sh
./scripts/stop.sh

# 只有显式开关才回到过夜假 DAS
USE_FAKE_DAS=1 ./scripts/start.sh
```

密钥只放 gitignored 的 `data/das-local.env` 和 `.env`。运维细节见 [DAS-LOCAL-ON-BOX.md](designs/collab/DAS-LOCAL-ON-BOX.md)。

默认 `HITL_AUTO=1`。设 `HITL_AUTO=0` 时 outline 会 pause，用 `POST /runs/{id}/resume`。

### UI P0′ 行为测试

```bash
npm install
uv run pytest
```

产品行为在 `tests/features/ui_p0_prime.feature`（进度、脱敏、时间窗确认，以及结论正文脱敏、业务空态、确认留下用户气泡、依据按钮不冒号、调试入口默认隐藏、第 N 条依据对齐）。覆盖率门是 `ui/chat` 行覆盖 ≥ 90%（c8，pytest 收尾执行）。仓库里没有 `.playbook/docs/project/product-development-xp-contract.md`，按 agent-os-btdd 最小规则。

详见 [过夜计划](designs/2026-10-22-overnight-ecommerce-insight-poc.md)、[链路补强](designs/2026-10-22-kernel-chain-hardening.md)、`STATUS.md`。

## 文档入口

- [M0 宪章](designs/2026-10-21-enterprise-agent-framework-m0-charter.md)
- [协作看板](designs/collab/BOARD.md)
- [协作事件](designs/collab/events.jsonl)

## 里程碑

| 阶段 | 状态 |
| --- | --- |
| M0 宪章 | ✅ |
| Overnight POC（假 DAS + 报告） | ✅ 箱内可跑；默认启动已切真 DAS，假服务仅 `USE_FAKE_DAS=1` |
| Kernel chain hardening（同路径 stub/live） | ✅ |
| M1 包边界 + 接口草图 | ⏳ |
| M2 可跑内核 + 报告 Skill | 🚫 另令（嘉树） |
| M3 真数据烟测 | 🚫 |

## 硬规则（摘要）

- Session ≠ Run；内核禁洞察业务专有名词进包名
- DAS 白名单只读；禁止 SQL 透传
- 空 key 也必须走 Model Broker（StubBackend），禁止 skill 旁路
- v0 不依赖 simulated-research
