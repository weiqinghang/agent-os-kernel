# 失败回合重试（冻结）

只允许 `state==failed` 再跑。做法是在同一 Session 里新建一个 Run，深拷贝该失败 Run 的 `context_bindings` 快照，并注入一句不超过 500 字的脱敏失败原因。旧 Run 保持 `failed`。

不把 `failed` 改回 `acting`。不回放 `tool_results`。不复活 `cancelled` / `completed`。不自动无限重试。`POST /runs/{id}/resume` 仍只服务 HITL，失败恢复不使用 `resume_token`。

## 接口

`POST /runs/{run_id}/retry`，正文可省略，或 `{text?}`。

- 新 Run：同一个 `session_id`；`context_bindings` 等于失败 Run 的快照，不是当前 Session 默认。
- 逃逸口只有新 Run 的 `meta.retry_of=失败 run_id`。不建父子图。
- 应答与现有回合相同，并带 `retry_of`。
- 用户消息：`text`，缺省「请重试上一轮。」。前面加上脱敏后的失败原因。
- 新 Run 的观测：`state`，以及 `retry` 载荷 `{retry_of}`。

非 `failed`（含 `completed`、`cancelled`、`acting`、`awaiting_hitl`）返回 409。没有这个 Run 返回 404。

## 脱敏

原因取失败 Run 的 `result`，截断到 500 字。进入 Session 的文本里不保留堆栈、密钥、完整工具参数、工具名清单、mode、思维链。

## 界面

失败气泡上的「重试」调用上面的接口。进度跟新的 `run_id`。静态 plan 可以在新回合再次出现，不因此改白名单文案。

## 本批不做

原地重启、工具结果回放、取消恢复、SSE、新的 HITL 种类、P0′ 白名单改写、plan 状态推进、Skill#2、工作流 / phase_* / saga / checkpoint。
