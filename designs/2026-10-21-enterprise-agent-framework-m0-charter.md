# 企业 Agent 通用框架 · M0 宪章

- 状态：已立项（庆颃确认 2026-10-21）
- 狗粮 #1：洞察报告助理（聊天 + DAS MCP）
- 依据：矩白架构意见与补丁、可意产品剧本、主控收口
- 相关稿：
  - `/workspace/designs/2026-10-21-enterprise-agent-framework-v0.md`
  - `/workspace/designs/2026-10-21-enterprise-agent-dogfood-insight-report.md`

---

## 1. 一句话

做一套**企业 Agent 运行与治理内核**（五脏俱全、每块可简陋）；第一根垂直接力是**聊天式洞察报告助理**，用公司已有的社媒海量数据（`dataset-analysis-service` MCP）产出「结论页 + 同线程追问」。

## 2. 是 / 不是

| 是 | 不是 |
| --- | --- |
| Orchestrator / Runtime / Context / Tool Gateway / Model Broker / Policy / Observability / HITL | Agentic CI 产品 |
| 模块化单体；Skill 热插 | Legacy Migration Agent |
| Session 上的多轮聊天 + Run 级执行 | 任意 SQL / 完整 BI 仪表盘 |
| 受控查数 + 证据锚点的分析报告 | 定时报告工厂、自动外发 |
| 洞察域名只在 Skill | 把 9 个 insight-skills 抬进内核 |
| | 虚拟调研（simulated-research）进 v0 依赖 |
| | 多角色编排表演驱动内核状态机 |

## 3. 产品 MVP（狗粮 #1 验收）

**用户：** 市场/品牌经理、洞察分析师、偶发的业务负责人。

**交付物（可意钉死）：**

```text
一次提问
  → 一条结论页（3～5 个发现 + 证据锚点）
  → 同线程可追问
```

报告是**会话产物**，不是静态 PDF 工厂。首答必须可扫、可追问。

**刻意少做：** 虚拟访谈/人设；定时报告与仪表盘；无题闲聊；自动外发；多角色编排表演。

## 4. 架构硬边界

### 4.1 三栏归属

| 进**框架内核** | 进 **Skill** | **留给人** |
| --- | --- | --- |
| 八层控制面；Run 状态机；工具白名单客户端；策略开关；HITL 暂停/回执；事件与粗成本 | 报告大纲与章节剧本；何时 HITL；如何引用 DAS；聊天话术；洞察语义 | 口径是否成立；段落/终稿是否过关；是否对外交付；密钥与合规；组织权限 |

**硬规则：** 内核不得出现「人群 / 痛点 / 知识卡 / MR / 四角色」等业务专有名词与表。出现即污染，打回 Skill。

### 4.2 Run 状态机（不扩展）

```text
created → planning? → acting ⇄ awaiting_hitl → completed | failed | cancelled
```

### 4.3 Session ≠ Run

- **Session**：聊天线程，可多轮。
- **Run**：一次「出报告 / 续写 / 按反馈改一章」的执行（或 resume 同一 Run）。
- 禁止把 Orchestrator 做成「永久聊天机器人进程」。

### 4.4 HITL payload `kind`

| kind | 用途 |
| --- | --- |
| `user_input` | 澄清口径/选题 |
| `outline_confirm` | 大纲确认 |
| `segment_confirm` | 段落/章节确认 |
| `final_confirm` | 终稿 / 外发前必经 |

v0：**单 Run 多段 HITL**；不上工作流引擎。每次 `awaiting_hitl` 必带 `resume_token` + payload。

### 4.5 Tool Gateway ↔ DAS

- 工具白名单：`list_datasets` / `describe_*` / `query_stats` / `search_records` / `sample_records`（名以现网契约为准）。
- **禁止** SQL 透传与未知方法；行数/超时默认上限。
- 身份透传跟现网 DAS / 用户中心契约走；框架不自造租户中台。
- Context：查询指纹 + 摘要/引用指针；**不**把 sample 海量行写入长期记忆。
- Obs：工具 span + fingerprint；**不**在日志落原始 UGC。
- Policy：默认只读；外发/导出报告必须 `final_confirm`。

### 4.6 与现有资产关系

| 资产 | 关系 |
| --- | --- |
| `dataset-analysis-service` | v0 **必须**对接的数据 MCP |
| `insight-skills` | Skill 层参考/可组装素材；v0 可先内联精简「报告 Skill」 |
| `simulated-research` | **v0 不依赖**；可作为后续 Skill |

## 5. v0 技术形态

- **部署：** 一个可部署单体 + 一种库（Postgres 优先；开发可用 SQLite）。
- **队列：** 可无；HITL 用 DB 状态 + 回执。
- **模型：** Model Broker 配置 1～2 提供商 + 简单 fallback。
- **持久化最小：** `runs` + `run_events`（append-only）；session 挂 `active_run_id` 可选。
- **验收句：** 换洞察 Skill 或换数据集，**不必改 Runtime**。

## 6. 里程碑

| 阶段 | 产出 | 谁 |
| --- | --- | --- |
| **M0（本宪章）** | 边界冻结 | 主控 + 矩白 + 可意；庆颃批准 |
| **M1** | 包边界 + Run/HITL/DAS 适配接口草图 | 矩白；照言审「是否又堆平台」 |
| **M2** | v0 可跑内核 + 报告 Skill 最小闭环（假或真 DAS） | 嘉树（**本宪章批准后才拉**） |
| **M3** | 真数据烟测：提问→结论页→追问；trace 可查 | 全员；产品验收按可意剧本 |

## 7. 决策记录

| 日期 | 决策 |
| --- | --- |
| 2026-10-21 | 否决第一刀为 C/B/A/D |
| 2026-10-21 | 狗粮 #1＝洞察报告助理（E）；产品形态＝结论页+同线程追问 |
| 2026-10-21 | 批准前不拉陈嘉树实现 |
| 2026-10-21 | 讨论以主控点对点收口；宪章冻结前不必拉群 |

## 8. 开放问题（不挡 M1）

1. 第一版聊天壳：复用现有 Agent App 宿主，还是本框架自带最小 Web/API？
2. 首个真数据集与鉴权：Staging DAS（`t-dataset-analysis.tarsocial.com`）烟测账号谁备？
3. 结论页介质：纯 Markdown 气泡 / 简单 HTML 卡片 / 仅结构化 JSON 由宿主渲染？

---

**批准签字（逻辑）：** 庆颃 `confirm_charter` → 本宪章生效 → 可开 M1；实现须另令启动嘉树。
