# 狗粮 #1 更正：洞察报告助理（架构补丁）

相对上一页（C 研发只读）**作废作主验**；内核八层不变，场景进 Skill。

## 狗粮定义

- **交互**：聊天为主（会话里要报告、追问、改口径、确认段落）。
- **能力**：背后接公司社媒/洞察数据（现成 `dataset-analysis-service` MCP + 可参考 `insight-skills` 剧本），产出**分析报告**（可简陋）。
- **非目标**：任意 SQL；自动发帖/改生产数据；把 9 个洞察 Skill 抬进内核；Agentic CI / Legacy Migration。

## 边界（不变 + 落点）

| 层 | 本狗粮里谁干活 |
| --- | --- |
| 内核八层 | 照旧：只认 Run/工具/模型/策略/HITL/事件，不出现「人群/痛点/知识卡」域名 |
| Skill | 报告大纲、章节剧本、何时 HITL、如何引用 DAS 结果、聊天话术 |
| 人 | 口径是否成立、段落是否过关、是否对外交付；预算与数据权限签字 |

`insight-skills`＝**Skill 素材/参考实现**，经 Tool Gateway 调 DAS；`simulated-research`＝另一领域，v0 **不进**本狗粮依赖。

## Run / HITL：聊天下要不要补状态？

**顶层状态机不扩。** 仍用：

`created → planning? → acting ⇄ awaiting_hitl → completed|failed|cancelled`

要补的是约定，不是新大状态：

1. **Session ≠ Run**：聊天线程（session）可多轮；**一次「出报告/续写/按反馈改一章」＝一次 Run**（或 resume 同一 Run）。禁止把 Orchestrator 做成「永久聊天机器人进程」。
2. **HITL `kind`（payload 字段）**：`user_input`（澄清口径）/ `outline_confirm` / `segment_confirm` / `final_confirm`。长报告＝多次 `awaiting_hitl`，人批一段再 `acting` 下一段。
3. **普通闲聊气泡**：若无需策略暂停，可留在 `acting` 内「模型直接回会话」；一旦要改报告结构或落地交付，必须走 HITL kind。
4. **可选极瘦**：`runs.parent_run_id` 或 session 上挂 `active_run_id`——仅当要串「大纲 Run → 正文 Run」；v0 用单 Run 多段 HITL 即可，**先别上工作流引擎**。

结论：框架状态机**够用**；补的是 HITL payload 与 Session/Run 文档约定。

## Tool Gateway ↔ DAS MCP

已知工具面：`list` / `describe` / `query_stats` / `search` / `sample`；受控 DSL，禁任意 SQL。

| 要注意 | 做法 |
| --- | --- |
| 污染 | Gateway 只做 MCP 客户端 + 允许名单；**禁止**在内核解析洞察业务 DSL 语义 |
| 纵深 | 适配器再挡一层：只转发白名单 tool；拒绝 `sql`/未知方法；行数/超时默认上限 |
| 身份 | Session/Run 的租户与调用方身份透传给 DAS（怎么透传跟现网 DAS 契约走），框架不自造租户模型 |
| Context | 存「查询指纹 + 摘要/引用指针」，**不**把 sample 海量行塞进长期记忆 |
| Obs | span 打 `tool=query_stats` 等与 fingerprint；避免日志里落原始 UG C 内容 |
| Policy | 默认只读；「导出/外发报告」必须 `final_confirm` HITL |
| 与 insight-skills | v0 可先一个「报告 Skill」内联精简剧本；成熟后再把 9 Skill 当可组装步骤，仍在 Skill 层 |

## 相对四候选的新排序

- **狗粮 #1：洞察报告助理（聊天 + DAS）** — 主验 Chat Session 约定、分段 HITL、Tool Gateway 只读 MCP、Context 引用。
- 原 C/B/A：降为非本阶段；D 仍禁止驱动内核。

## 给主控

若庆颃确认本补丁：宪章里写死 Session≠Run、HITL kinds、DAS 白名单只读；再拉品味看报告交互，**批准前不拉嘉树**。
