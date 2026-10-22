# Context 绑定设计（多 Skill 复用 · 业务能力优先）

- 状态：设计稿（矩白）；承接庆颃「底座＝多 Skill 复用 + 上下文绑定优先」；M1 冻结候选**吸收进本更大切片**，不再单等「仅 P0」签字
- 对照：M0 宪章、M1 包边界（照言补丁后）、现码 `session.meta` / `ToolGateway` / Skill 清单
- 硬约束：反过度工程；**禁止**租户中台、工作流引擎、为绑定再拆微服务

---

## 0. 一句话

一次聊天（Session）钉住「用哪个 Skill、看哪份数据、哪个时间窗」；每次 Run 冻结一份绑定快照，注入 **Model system** 与 **ToolGateway 默认参数**。Skill 仍是静态清单；绑定是数据，不是编排语言。

---

## 1. 字段形状

### 1.1 规范对象 `ContextBindings`

```text
ContextBindings {
  skill: string              # 必填（解析后）；静态 registry 中的 name
  dataset?: string           # 数据集 id/名（DAS list_datasets 返回的那种，不发明新 ID 体系）
  time_window?: {            # 可选；未填则 Skill/用户话里自带
    start?: string           # ISO-8601 date 或 datetime
    end?: string
    label?: string           # 展示用，如「近90天」；模型可读，工具以 start/end 为准
  }
  project_id?: string        # 可选；不透明字符串，透传/记日志，框架不做项目 CRUD
  tenant_id?: string         # 可选；不透明字符串，透传 DAS/宿主身份用，框架不做租户模型
  extras?: {string: string|number|bool}  # 逃逸阀；禁止塞嵌套工作流；体积建议 ≤2KB
}
```

**不做的字段：** roles、审批链、多 Agent 拓扑、远程 Skill URL、权限声明书。

### 1.2 放哪

| 位置 | 含义 | 可变性 |
| --- | --- | --- |
| `Session.meta.bindings` | 线程默认绑定（创建或 PATCH 会话时设） | 可随用户改口径更新 |
| `Run.context_bindings` | **本 Run 冻结快照**（create_run / 首条 message 时 resolve） | 创建后只读；审计与追问复用同一数据集/窗 |
| 请求体 `bindings` 覆盖 | `POST /sessions/{id}/messages` 可选 patch | 只影响**新** Run 的 resolve，不默默改历史 Run |

现码：`sessions.meta` 已是 JSON；`runs` 表 **加一列** `context_bindings TEXT`（JSON）即可——单列，不做绑定服务。

### 1.3 Resolve 顺序（写死）

```text
request.bindings  ⊇  Session.meta.bindings  ⊇  SkillManifest.default_bindings（可选）  ⊇  {}
再强制：skill = request.skill || bindings.skill || registry.default_name()
校验：skill ∈ 静态 registry；dataset/time_window 形态合法；未知键进 extras 或 400
→ 写入 Run.context_bindings（深拷贝）
```

同线程追问：默认**继承**上一 Run 的 `context_bindings`，除非请求显式覆盖（换数据集/换窗/换 Skill）。

---

## 2. 注入点

### 2.1 → Model system

Orchestrator 在调 `decide` 前组装：

```text
system = SkillManifest.system_prompt
       + "\n\n# Context bindings\n"
       + 稳定 JSON（仅 skill/dataset/time_window/project_id/tenant_id；extras 按白名单键）
```

规则：

- 绑定块由**内核**格式化，Skill 不拼字符串（避免各 Skill 各写一套）。
- 模型被告知：工具参数未指明时，采用绑定中的 dataset / time_window。
- **禁止**把 sample 全量行塞进 system；仍走 Gateway + Context 指纹摘要。

### 2.2 → ToolGateway 默认参数

```text
gw.call(name, arguments) 之前：
  merged = deep_merge(tool_defaults_from(bindings), arguments)
  # arguments 显式键优先；bindings 只填缺失键
  拒 sql；name ∈ WHITELIST ∩ Skill.allowed_tools
```

`tool_defaults_from(bindings)`（内核小函数，非插件）：

| 工具 | 默认填入（键名跟现网 DAS 契约对齐，下表为示意） |
| --- | --- |
| `describe_dataset` / `query_stats` / `search_records` / `sample_records` | `dataset` ← `bindings.dataset`（若参数名是 `dataset_id` 则映射一处，SoT 在 Gateway） |
| 带时间过滤的 stats/search | `start`/`end` ← `time_window`（DAS 若用别的键，只在 Gateway 适配层映射） |
| `list_datasets` | 通常不强制 dataset；可选 filter 放 extras |

身份：`tenant_id` / `project_id` **不**当业务 DSL；若 DAS 要 header/字段，Gateway `call(..., ctx=bindings)` 透传——**透传不是租户中台**。

### 2.3 与 `context.summarize_tool`

不变；摘要里可多带 `bindings_fingerprint`（dataset+window 的短 hash）便于 Obs 对齐，可选。

---

## 3. 与静态 Skill registry 怎么接

```text
启动：registry = { insight_report: Manifest, ... }   # 写死/显式 register；非热加载
Run 开始：
  name = resolve_skill_name(request, session.meta, registry.default_name())
  manifest = registry.get(name)   # 缺失 → 400
  bindings = resolve_bindings(...)  # 含 skill=name
  system = compose_system(manifest, bindings)
  allowed = manifest.allowed_tools
  loop: decide / gateway.merge(bindings) / hitl…
```

| Skill 提供 | 绑定提供 | 内核提供 |
| --- | --- | --- |
| system_prompt、allowed_tools、hitl_kinds、可选 default_bindings、fixture | 本次用哪份数据/窗/skill | resolve、注入、校验、Run 快照 |

换 Skill＝换 registry 条目 + 可选换 bindings.skill；**不必改** Orchestrator 循环。多 Skill 复用同一套绑定与 Gateway。

Manifest **仍禁止**：远程 URL、市场元数据、stub_hooks。`default_bindings` 只许 `ContextBindings` 子集（如默认 time_window label），不许嵌套步骤图。

---

## 4. API 草图（薄改，非新服务）

```text
POST /sessions  { meta?: { bindings?: ContextBindingsPartial } }
PATCH /sessions/{id}  { bindings?: partial }          # 可选；更新线程默认
POST /sessions/{id}/messages
  { text, skill?, bindings?: partial }               # 覆盖 → 新 Run 快照
GET /runs/{id}  → 含 context_bindings
```

HITL resume：**不**重算绑定；沿用该 Run 快照（改绑定＝新 Run）。

---

## 5. 相对 M1 / 实现切片（给主控排期，本页不下令开工）

吸收原 M1 P0，并加上绑定：

| 项 | 说明 |
| --- | --- |
| 静态 registry + 去硬 import / 去内核电商文案 | 保持 |
| Stub 模板只在 Skill fixture；Broker 零业务词 | 保持 |
| live→stub fallback obs | 保持 |
| **新增** `Run.context_bindings` 列 + resolve/注入 | 单列 JSON；Gateway merge defaults |
| Session.meta.bindings | 复用现 meta，约定键名 |

仍推迟：外发接线、四 kind 产品化 UI、租户模型、工作流。

---

## 6. 刻意不做

- 租户中台 / 项目服务 / 绑定图数据库
- 工作流引擎（绑定≠步骤编排）
- 按租户热加载 Skill
- 把 DAS 查询计划编进 bindings
- 为 Context 单独立进程

---

## 7. 验收句

1. 同一内核、两个 Skill 名，只改 bindings.skill + registry 条目即可跑。
2. 固定 dataset+time_window 后，模型未传参时 Gateway 调用仍带上默认参数。
3. 追问 Run 默认继承绑定快照；显式覆盖才换数。
4. 代码检索：无租户服务、无工作流 DSL、Broker 仍零狗粮业务词。

---

**一句话：** 绑定是 Session 默认 + Run 冻结快照；注入 system 与工具默认参数；Skill 静态可换——业务跑远靠复用，不靠堆平台。
