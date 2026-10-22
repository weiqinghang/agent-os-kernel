# 计划卡步骤进度（Batch G.1 冻结）

`pending|active|done` 在 **App** 里算。材料是 Skill 的纯 `plan_progress` 规则表，加上已经存在的 `run_events`。编排不按步骤 id 推进，也不发 `phase_*`。

Batch G 的那条 `plan` 仍是 `revision: 1`、步骤全是 `pending`。页面拿到它只采用 id 和 label，status 当场重算。

## 1. 谁算

- Skill 声明规则。纯函数 `apply_plan_progress(steps, events, rules)` 放在 `insight_report`，不读工具名，不写事件。
- App 用同一张表在轮询之后刷新计划卡。呼吸条继续只看白名单，不读计划状态。
- v0 **不发**第二条 `plan`。内核可以以后 import 这个纯函数；本批不让编排再抄一版带状态的计划。

## 2. 信号

只从已有事件读，字段里的 `tool` / `name` 当没看见。

| 信号 | 判定 |
| --- | --- |
| `outline_confirm` | `hitl.kind == outline_confirm` 且不是 `auto` / `approved`，或 `state == awaiting_hitl` 且 `hitl_kind == outline_confirm`（或之前已经进入这次确认）。离开 `awaiting_hitl` 之后信号落下。 |
| `successful_tool_calls` | `kind == tool_call` 且 `payload.ok === true` 的条数。失败、缺 `ok`、载荷不是对象，都不计。 |
| `finalizing` | 与呼吸条同一套收尾 `model_step`（`final` / `finalizing` / `loop_break`，或这两个标记，或已不再要工具且 `n_calls` 为 0），或 `assistant`，或 `state` 为 `completed` / `finalizing`。`state == failed` 撤下。 |
| `cancelled` | `state == cancelled`。回合以 cancelled 收尾时，页面把这个信号补进同一次计算。 |

## 3. 默认规则

`insight_report.plan_progress`：

1. `outline_confirm` → 全部 `pending`，到此为止（取消除外）。
2. `tool_calls` + `count` + label 通配。成功次数 ≥ count 的步骤是 `done`；还没到、且 count 最小的那一步是 `active`；其余 `pending`。还没有成功查数时，count 最小的那一步就是 `active`。通配只匹配步骤 label。
3. `finalizing` + `*结论*` → 该步 `active`。已经 `done` 的不退回。
4. 取消不写进表：不是 `done` 的步骤改为 `skipped`。`done` 保持 `done`。

默认计数：范围 1、类目 2、渠道 3、省 4、退货 5。现有五行里没有「结论」字样，所以收尾规则只在 label 对得上时点亮，不另造一步。

计划卡上屏只有 label 和状态（未开始 / 进行中 / 完成 / 跳过）。工具名、mode、id、JSON、思维链、指纹不上卡。

## 4. 不做

Skill#2、模型中途改大纲、可编辑计划、DAG、内核步骤状态机、改 P0′ 白名单、SSE、`phase_*`、失败重试的行为改动。失败重试仍是新 Run、新计划卡。呼吸条不读计划状态。
