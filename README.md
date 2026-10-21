# agent-os-kernel

企业 Agent 通用框架（AI OS 控制面）内核仓库。

- **狗粮 #1：** 聊天式洞察报告助理（结论页 + 同线程追问 + DAS MCP）
- **形态：** 模块化单体；八层控制面可简陋但要齐
- **协作：** 不强制 Playbook；用 `designs/collab/` 事件日志观测角色协作

## 过夜 POC（电商销量洞察）

箱内可演示路径（假 DAS + 内核 + 聊天 UI）：

| 服务 | 端口 | 说明 |
| --- | --- | --- |
| fake-das | `127.0.0.1:8788` | SQLite ~1 万行电商销量；MCP 五工具白名单 |
| agent-kernel | `127.0.0.1:8787` | Session/Run/Tool Gateway + insight_report + UI |

### 明早启动

```bash
cd /workspace/agent-os-kernel
./scripts/start.sh

curl -s http://127.0.0.1:8788/health
curl -s http://127.0.0.1:8787/health

# 浏览器打开聊天窗
# http://127.0.0.1:8787/

./scripts/uat.sh   # 重跑验收
./scripts/stop.sh  # 停止
```

首次会自动 `seed` 约 10000 行。无 LLM key 时走 stub（仍真实调用 DAS，数字带 evidence 指纹）。默认 `HITL_AUTO=1`。

详见 [过夜计划](designs/2026-10-22-overnight-ecommerce-insight-poc.md) 与 `STATUS.md`。

## 文档入口

- [M0 宪章](designs/2026-10-21-enterprise-agent-framework-m0-charter.md)
- [协作看板](designs/collab/BOARD.md)
- [协作事件](designs/collab/events.jsonl)

## 里程碑

| 阶段 | 状态 |
| --- | --- |
| M0 宪章 | ✅ |
| M1 包边界 + 接口草图 | ⏳ |
| Overnight POC（假 DAS + 报告） | ✅ 箱内可跑 |
| M2 可跑内核 + 报告 Skill | 🚫 另令（嘉树） |
| M3 真数据烟测 | 🚫 |

## 硬规则（摘要）

- Session ≠ Run；内核禁洞察业务专有名词进包名
- DAS 白名单只读；禁止 SQL 透传
- v0 不依赖 simulated-research
