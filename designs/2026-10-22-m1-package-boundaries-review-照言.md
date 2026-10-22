# M1 包边界审稿 · 程照言

- 状态：审毕（有条件通过）
- 对象：`/workspace/designs/2026-10-22-m1-package-boundaries.md`
- 对照：M0 宪章 + 现码 `/workspace/agent-os-kernel`（已核对 orchestrator / model_broker / hitl / insight_report / tool_gateway / policy）
- 审层：偏架构与「少做」；产品只点狗粮压力；不改仓实现

---

## 0. 一句话结论

**方向对，没有又堆平台。建议庆颃批准本页方向；冻结前请矩白收三条补丁，嘉树只按收窄后的 P0 开工。**

不是否决。不是无条件盖章。

---

## 1. 现码核对（矩白指控成立）

| 指控 | 现码 | 宪章 |
| --- | --- | --- |
| 硬 `import insight_report` | `orchestrator.py` L8–12 `DEFAULT_SKILL = insight_report.SKILL` | 换 Skill 不必改 Runtime → 违规苗头 |
| 内核电商 HITL 文案 | `orchestrator` 返回「近90天电商销量洞察：类目/渠道/省份/退货」 | §4.1 内核不得出现业务专名 |
| Stub 电商模板在 Broker | `model_broker`：`REPORT_PLAN` / `_build_report` / 中文 intent「省·渠道·类目·退货」整段 | 同上；换 Skill 仍改 Broker |

污染是真的。P0「registry + Stub 迁出 + 去电商 HITL 文案」必须做，不是洁癖。

---

## 2. 架构：哪里站得住

1. **模块化单体 + 刻意不做清单**与宪章一致；§5 禁止项写进稿面，审稿清单可用。
2. **三处接口固化**（Manifest / `decide` / `call`）够用；不引入插件框架、不写 OpenAPI 给内部包——克制对。
3. **依赖 DAG + 禁止 Skill↔Broker/Gateway 互指**正确；Stub 模板迁出正是为堵这条边。
4. **相对现码的 delta 全是「无 migration / 无服务拆分」**——这是本页最重要的反平台信号。
5. 与宪章「是」栏「Skill 热插」的张力：本页写死**不做热加载**。解读为「可换 Skill 的注册表，非运行时热插平台」——接受，但要在稿里写死这句，避免后人拿宪章四个字开 Skill 市场。

---

## 3. 挑刺（必须改 / 建议压）

### 必须改（冻结前）

**A. Registry = 启动期静态表，不是平台**

稿面写 `register/get/default_name` 可以，但验收句要钉死：

- 进程启动时把已知 Manifest 塞进 dict；`default_name` 来自 env。
- **禁止**目录扫描热加载、远程署名、Skill 市场。
- 「换 Skill 不必改 Orchestrator 循环」= 改注册表或传 `skill` 名，不是上插件运行时。

否则 registry 六个字会在 M2 长成平台。

**B. `stub_plan` / stub_hooks 边界写死，禁止 Stub DSL**

迁出污染对；但「按 Skill 注入的 stub_hooks」表述过宽，容易变成第二套编排语言。

约束建议写进 §2.4 / §2.9：

- Stub 默认：**只回放 `tool_results` 拼最小 final**；或读取 **Skill 模块内**的 plan/模板数据。
- Broker **零业务关键词**（不得再出现「省/渠道/类目/退货/电商」）。
- **禁止**通用 hook 协议、条件图、多 Skill 共享 stub 引擎。一个狗粮 Skill 一份 fixture 即可。

**C. M1 实现范围压到「清污染 + 可换 Skill」**

狗粮 #1 仍是：提问 → 结论页 → 追问。当前无外发、无多段确认产品压力。

| 原优先级 | 照言裁定 |
| --- | --- |
| P0 registry / 去硬 import / 去电商 HITL 文案 / Stub 迁出 | **M1 必做** |
| P1 live→stub fallback 打 obs | **并入 M1**（现码静默 fallback，可观测是契约债，改动小） |
| P1 HITL 通用化 segment / user_input / final 全路径 | **砍到「同一暂停路径能吃任意 kind」的最小改法**；不要为四种 kind 各写产品文案与 UI 分支。狗粮未用的 kind 只保留枚举 |
| P1 `requires_final_confirm` 接线 | **推迟**：`policy` 已有 `OUTBOUND_ACTIONS`，狗粮无外发工具；接线无用户剧本 = 加戏 |
| P1 工具名单一 SoT | **可做但不另起包仪式**：Gateway `WHITELIST` 为 SoT，Skill/policy 引用同一常量即可；不必先造空 `tool_names`「平台模块」 |
| P2 cancelled/planning、identity ctx | 维持推后 |

### 建议压（不挡批准）

- **勿为对齐宪章「八层」名词再拆空包。** 现扁平 `agent_os_kernel.*` 已够；稿已写「不为大厂包名再造 runtime」——执行时盯嘉树。
- **TypedDict/Protocol 放 M2**，同意；M1 草图保持语言无关即可。
- 开放问题 2（CI 强制带 insight fixture）：同意「建议是」，但 fixture 挂在 Skill 下，不挂 Broker。

---

## 4. 产品（少做）一眼

用户剧本仍是可意钉死的结论页 + 同线程追问。本页若让嘉树一次做完「四 kind HITL + 外发门禁 + SoT 重构」，是在用架构完整感换狗粮速度。

**少做更好：** M1 验收只问三句——

1. 换一个假 Skill 名，Orchestrator 循环文件是否零业务 diff？
2. Broker 里是否还能搜到电商/类目等词？
3. 无 key 时 stub 是否仍只从 tool_results 出数，且路径与 live 同 `decide`？

三句都「是」即可进嘉树；其余进 backlog。

---

## 5. 给矩白的可执行修改清单

| # | 改什么 | 为什么 | 做到什么算过 |
| --- | --- | --- | --- |
| 1 | §2.9 / §5 加一句：registry = 启动静态注册，明确非热加载 | 防宪章「热插」被读成平台 | 稿面有否决句；delta 无「发现/加载器」项 |
| 2 | §2.4 Stub 净化：删宽泛 stub_hooks，改「Skill 内 fixture/plan 数据；Broker 零业务词」 | 防 Stub DSL | 验收可用 `rg` 扫 broker 无业务词 |
| 3 | §4 delta：P1 外发接线推迟；HITL 通用化写成「单路径 + 枚举保留」；SoT 不强制新文件 | 对齐狗粮少做 | 嘉树开工单只含 P0 + fallback obs |

收完这三条，本页可标「M1 冻结」。

---

## 6. 给庆颃的批准建议

| 问 | 答 |
| --- | --- |
| 是否又堆平台？ | **否。** 单体、无拆服务、不做引擎/市场/中台，成立。 |
| 是否建议批准？ | **建议批准方向**；请矩白按 §5 三条补丁修订后标冻结。 |
| 能否拉嘉树？ | **补丁收口后只拉 P0（+ fallback obs）**；批准前仍不拉。全表 P1 不一次授权。 |

红队加压句：若有人借「SkillManifest 要可扩展」加字段超过狗粮所需（市场元数据、权限声明、远程 URL）——打回。清单只服务「可换 Skill + 清污染」。
