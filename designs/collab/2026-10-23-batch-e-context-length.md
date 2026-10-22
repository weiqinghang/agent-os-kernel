# Batch E · 上下文长度策略

`decide()` 在 stub 和 live 之前调用 `context.fit_context`。没超预算时，消息和 `tool_results` 原样送入，原生 `tools` / `tool` 角色、取消、熔断、并行和中途轮询都不改。

预算来自 `AGENT_CONTEXT_MAX_CHARS`（默认 48000）和 `AGENT_CONTEXT_MAX_MESSAGES`（默认 40）。空值、非整数、小于下限或大于上限时用默认。字符数只算系统提示、消息正文和工具结果 JSON，不算 `tools` 参数表。单条 tool 正文仍有原来的 12000 字截断。

保留系统提示（里面的 bindings 快照不动）。保留最近一条 user。较新的 `ok: true` 工具结果优先留下，证据指纹留在预览里。最新一条 `loop_break` 比更旧的失败行更晚才丢。

超出时按这个顺序收：最旧的中间消息、最旧的非成功工具结果、较旧的成功结果。还超就把留下的大块收成短预览（行数、首行几个字段或指纹），不另起摘要模型。最近用户话只在它自己超长时截断。能放下时，在消息最前面加一行 `[context] dropped_messages=…`；放不下就只记事件，不拿这行去挤掉用户话。

截断发生时，`model_step.type` 为 `context_truncated`，带 `dropped_messages`、`dropped_tool_results`、`shrunk_tool_results` 和保留后的字符数。有 bindings 时带 `bindings_fingerprint`。会话 `list_messages` 改为取最近一页再按时间排，避免长线程把最新用户话留在库里却送不进 broker。

没有向量记忆，也没有摘要 Agent。
