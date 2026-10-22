# M1 · 包边界 / 接口草图

- 状态：设计稿（矩白）；待照言审「是否又堆平台」
- 对照：M0 宪章 + 现码 `/workspace/agent-os-kernel`（过夜 POC 已跑通）
- 硬约束：小原型小做；**禁止**无业务驱动的微服务 / 大平台；只设计、本页不落地改码

---

## 0. 结论先行

1. **保持模块化单体**：现扁平包 `agent_os_kernel.*` 即 M1 目标形态；不拆服务。
2. **固化三处接口，其余保持函数级瘦实现**：`SkillManifest`、`ModelBroker.decide`、`ToolGateway.call`；Run 状态机与 HITL token 形状写入契约。
3. **Skill 清单 vs Orchestrator：必须固化**——Skill = 纯清单（无执行）；Orchestrator = 唯一步进循环；默认 Skill 改为注册表查找，去掉硬 `import insight_report`。
4. **stub/live：保持同一 `decide()` 签名**；Stub 内电商报告模板迁出 Broker（属污染），迁到 Skill fixture 或 `skills/_stubs/`。
5. **刻意不做**：工作流引擎、Skill 热加载平台、多 provider 配置中心、独立 Runtime 微服务、租户中台。

---

## 1. 包图与依赖（允许方向）

```text
api (main) ──► session, run, orchestrator, hitl, model_broker, obs, tool_gateway
orchestrator ──► session, run, hitl, model_broker, tool_gateway, policy, context, obs
orchestrator ──► skill_registry.get(name) → SkillManifest   # 禁止硬 import 某业务 Skill
session, run ──► obs
tool_gateway, model_broker, hitl, policy, context, skills/* ──► （叶子或仅 stdlib/httpx）
```

**禁止反向**：Skill → Orchestrator / Gateway / Run；Gateway → Skill；ModelBroker → Skill（Stub 模板也不行）。

现码已接近此 DAG；M1 要改的主要是：**注册表** + **Stub 去业务** + **HITL kinds 通用化**（去掉写死电商文案）。

---

## 2. 各包对外接口（草图）

> 语言无关契约；现码是 Python dict，M2 可逐步换成 TypedDict/Protocol，**不必**上插件框架。

### 2.1 `session` — 聊天线程

```
create_session(meta?) -> {id, created_at}
get_session(id) -> {id, created_at, meta} | null
add_message(session_id, role, content, run_id?)
list_messages(session_id, limit=50) -> [{role, content, created_at, run_id}]
```

- **职责**：Session ≠ Run；只存对话与可选 `active_run_id`（现码可后加，非必做）。
- **不做**：权限模型、多租户表。

### 2.2 `run` — 状态机

```
STATES = created | acting | awaiting_hitl | completed | failed
# M1 仍不强制 planning / cancelled（宪章可选；无产品压力不加）

create_run(session_id) -> {id, session_id, state}
set_state(run_id, state, *, resume_token?, hitl_kind?, result?)
get_run(run_id) -> row | null
```

- **职责**：唯一活跃态；HITL 时必有 `resume_token` + `hitl_kind`。
- **不做**：子 Run 图、补偿事务、工作流 DSL。

### 2.3 `orchestrator` — 唯一步进器

```
handle_user_message(session_id, run_id, user_text, *, skill_name|manifest?, resume=false)
  -> {status: awaiting_hitl|completed|failed, markdown?, hitl?, evidence?}

resume_after_hitl(run_id, user_text?) -> 同上
```

循环契约（固化）：

```text
若需 HITL(kind in manifest.hitl_kinds 且 should_pause) → awaiting_hitl
else 最多 MAX_STEPS:
  decision = broker.decide(system, messages, allowed_tools, tool_results)
  tool_calls → 对每个 call: policy ∧ skill.allowed ∧ gateway → context.summarize → obs
  final → completed
预算耗尽 / 异常 → failed
```

- **职责**：状态迁移与门禁；**不**内嵌业务话术/报告模板。
- **HITL 文案**：来自 Skill（`hitl_prompts[kind]` 可选）或通用占位；**禁止**内核写「近90天电商…」。

### 2.4 `model_broker` — stub/live 同签名（固化）

```
decide(*, system, messages, allowed_tools, tool_results)
  -> {type:"tool_calls", calls:[{name, arguments}]} | {type:"final", content}

model_mode() -> "live" | "stub"
```

| 规则 | 说明 |
| --- | --- |
| 同签名 | live / stub **必须**可互换；Orchestrator 无分支 |
| 切换 | 进程 env（有 key→live）；M1 不要求请求级路由 |
| fallback | live 失败可降 stub，但须 **obs 打标**（现码静默，M1 要求可观测） |
| Stub 净化 | 报告计划/中文 intent/Markdown 模板 **迁出** → `skills/insight_report/stub_plan.py` 或测试 fixture；Broker stub 只许「回放 tool_results 拼最小 final」或「按 Skill 注入的 stub_hooks」 |

- **不做**：模型网格、多厂商配置 UI、prompt 市场。

### 2.5 `tool_gateway` — DAS MCP 客户端

```
WHITELIST = SoT（唯一工具名来源）
call(name, arguments) -> {data, evidence:{fingerprint,...}}
list_tools() -> [name...]
# 拒 sql / 未知方法；默认超时与行数上限
```

- **职责**：传输 + 白名单 + 错误归一；身份透传跟现网契约（M1 接口预留 header/ctx 参数即可，不自造租户）。
- **Policy / Skill**：`allowed_tools ⊆ WHITELIST`；名录 **不以三处拷贝**——Skill 引用 Gateway SoT 或共享常量模块 `tool_names.py`（仍单仓，非新服务）。

### 2.6 `hitl`

```
KINDS = user_input | outline_confirm | segment_confirm | final_confirm
make_token(kind) -> {kind, resume_token}
should_pause(kind) -> bool   # HITL_AUTO
```

- Orchestrator 对 **所有** kind 同一暂停路径（现码只接了 outline）；payload 可带 Skill 提供的 `prompt`/`draft`。
- **不做**：审批流引擎、多人会签。

### 2.7 `policy`

```
allow_tool(name) -> bool          # ⊆ Gateway WHITELIST
requires_final_confirm(action) -> bool  # 外发动作；M1 接线：outbound 前强制 kind=final_confirm
```

- **不做**：RBAC、角色矩阵、ABAC 平台。

### 2.8 `context` / `obs`

```
context.summarize_tool(name, evidence, result) -> {tool, fingerprint, preview}
obs.emit(run_id, kind, payload)
obs.list_events(run_id)
```

- 持久化最小：`sessions` / `messages` / `runs` / `run_events`（现 SQLite 可保留）。
- **不做**：独立可观测平台、全量 sample 入库存档。

### 2.9 `skills` + **注册表（M1 必固化）**

**SkillManifest（清单，无执行函数）：**

```text
name: str
version: str
system_prompt: str
allowed_tools: list[str]     # ⊆ Gateway WHITELIST
hitl_kinds: list[str]        # ⊆ HITL.KINDS
hitl_prompts?: {kind: str}   # 可选；暂停时展示
stub_plan?: ...              # 可选；仅供 StubBackend 读取，禁止放进 model_broker 模块
```

**Registry：**

```
register(manifest)
get(name) -> manifest
default_name() -> "insight_report"   # 配置/env，非硬 import
```

- HTTP `POST .../messages` 可带 `skill` 名；默认走 `default_name()`。
- **验收句（宪章）**：换 Skill 或数据集，**不必改** Orchestrator 循环代码。

### 2.10 `api`（`main`）— 薄壳，保持现路由

| 路由 | 不变要点 |
| --- | --- |
| `POST /sessions` / `GET /sessions/{id}` | Session |
| `POST /sessions/{id}/messages` | 建 Run → orchestrator（传入 skill 名可选） |
| `POST /runs/{id}/resume` | 校验 resume_token → resume_after_hitl |
| `GET /runs/{id}` | run + events |
| `GET /health` `/tools` `/` | 探针与狗粮 UI |

- **不做**：BFF 平台、插件式路由发现。

---

## 3. stub/live 与 Skill 边界 — 是否固化？

| 问题 | M1 决定 |
| --- | --- |
| stub/live 是否同一接口？ | **是，已是；写进契约，禁止再分叉** |
| Skill 清单 vs Orchestrator？ | **固化**：清单三键（+可选 hitl_prompts/stub_plan）；步进只在 Orchestrator |
| 默认 Skill 硬编码？ | **改掉**：registry + default 名 |
| Stub 电商模板在 Broker？ | **迁出**（污染）；否则换 Skill 仍改 Broker，违反宪章 |

---

## 4. 相对现码的 delta（给嘉树的实现序，**未批准不开工**）

| 优先级 | 项 | migration/服务拆分 |
| --- | --- | --- |
| P0 | Skill registry；orchestrator 去硬 import / 去电商 HITL 文案 | 无 |
| P0 | Stub 报告模板迁出 `model_broker` | 无 |
| P1 | HITL 通用 kind 路径（segment/final/user_input） | 无 |
| P1 | 工具名单一 SoT；`requires_final_confirm` 接线 | 无 |
| P1 | live→stub fallback 打 obs 事件 | 无 |
| P2 | `cancelled` / `planning` 仅当产品要 | 无 |
| P2 | Gateway 调用加 identity ctx 参数 | 无 |
| — | 拆微服务 / 上队列 / 工作流引擎 | **禁止** |

---

## 5. 刻意不做（照言审稿清单）

- 微服务拆八层或独立「Skill 服务」
- 工作流引擎 / 多 Agent 聊天室状态机
- Skill 市场、热加载、远程署名
- 模型路由中台、成本中台（v0 最多 obs 粗计数）
- 租户/权限平台化；把 insight 域名表建进内核
- 为对齐「大厂包名」再造 `runtime` 空包（现 `run+orchestrator` 已够）

---

## 6. 开放问题（不挡本页）

1. 聊天壳长期：自带 UI vs Agent App 宿主（宪章 §8）——接口草图已按现 HTTP 薄壳。
2. Stub 迁出后，无 key 的 CI 是否强制带 `skills/insight_report` fixture——建议是。

---

**一句话：** M1 把 POC 收成「可换 Skill 的单体契约」：固化 Manifest + `decide` + `call` + 注册表，清掉 Broker/Orchestrator 里的电商污染，不堆平台。
