# 失败回合重试

`failed` 的 Run 不在原地改回 `acting`。`POST /runs/{id}/retry` 在同一 Session 里新建一个 Run，深拷贝失败那次的 `context_bindings`，不读当前会话默认。正文可省略，或带 `{text?}`；不写时用户句是「请重试上一轮。」。应答和普通回合一样，多一个 `retry_of`。新 Run 的 `meta.retry_of` 只记这一条，不建 Run 图。

失败原因来自旧 Run 的 `result`，截到 500 字，去掉堆栈、密钥、完整参数、工具名清单、mode 和思维链，再放进新回合的用户消息。观测上新 Run 有 `state` 和 `retry {retry_of}`。旧 Run 仍是 `failed`。

`POST /runs/{id}/resume` 仍只处理确认，不拿 `resume_token` 做失败恢复。`completed`、`cancelled`、`acting`、`awaiting_hitl` 再试返回 409。不回放 `tool_results`，也不自动连试。

聊天里失败气泡有「重试」。进度改跟新的 `run_id`。静态 `plan` 可以再出现，条带文案仍是原来的白名单。

## 冒烟

```bash
python3 -m pytest tests/test_failed_run_retry.py tests/test_ui_p0_prime.py tests/test_cancel_run.py -q
```
