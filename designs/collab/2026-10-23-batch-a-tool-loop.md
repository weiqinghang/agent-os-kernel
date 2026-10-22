# Batch A · 工具循环韧性

空 `tool_calls` 没有可回灌的调用，记 `failed` 后结束，不逼再 decide。坏参数、未授权工具、`ToolGatewayError` 写入 `tool_results`（`ok:false` + 短 error）并继续。缺少 Skill、未知决策、超过 `MAX_STEPS` 仍然失败。参数各不相同的连续调用仍走到步数上限；相同参数的连续重复由 [loop break](./2026-10-23-loop-break.md) 提前停住。

DAS HTTP 只对超时和 5xx 短退避重试一次；401/4xx、JSON-RPC 错误和连接错误不重试。live 请求失败后再试一次，仍失败则 stub 降级。事件为 `model_step.type = live_retry` 与 `live_fallback`（`ok:false`）。

## 箱上 UAT

1. 不配模型 key，`HITL_AUTO=1`，按 `./scripts/start.sh` 拉起。发一条洞察报告，`status` 仍是 `completed`，`/runs/{id}` 里成功的 `tool_call` 带 `ok:true`。
2. 把 `DAS_MCP_URL` 暂时指到会 503 的地址（或停掉 DAS）再问一次。Run 应继续走完 stub 路径；events 里能看到 `tool_call.ok=false` 和短 `error`，而不是第一次工具失败就整段 `编排失败`。
3. 配一个会失败的 `MODEL_BASE_URL`（或错误 key）。events 里应先有 `type=live_retry`，再有 `type=live_fallback`，随后仍能出 stub 结论。
