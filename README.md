# agent-os-kernel

企业 Agent 通用框架（AI OS 控制面）内核仓库。

- **狗粮 #1：** 聊天式洞察报告助理（结论页 + 同线程追问 + DAS MCP）
- **形态：** 模块化单体；八层控制面可简陋但要齐
- **协作：** 不强制 Playbook；用 `designs/collab/` 事件日志观测角色协作

## 文档入口

- [M0 宪章](designs/2026-10-21-enterprise-agent-framework-m0-charter.md)
- [协作看板](designs/collab/BOARD.md)
- [协作事件](designs/collab/events.jsonl)

## 里程碑

| 阶段 | 状态 |
| --- | --- |
| M0 宪章 | ✅ |
| M1 包边界 + 接口草图 | ⏳ |
| M2 可跑内核 + 报告 Skill | 🚫 另令 |
| M3 真数据烟测 | 🚫 |

## 硬规则（摘要）

- Session ≠ Run；内核禁洞察业务专有名词
- DAS 白名单只读；禁止 SQL 透传
- v0 不依赖 simulated-research
- 批准前不拉实现角色开工
