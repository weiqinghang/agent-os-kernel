# 重复工具调用熔断

狗粮里模型会拿同一组 `sample_records` 参数空转，直到 `MAX_STEPS` 把 Run 判失败。这一批只加循环守卫，不加重试调度。

`MAX_STEPS` 默认从 8 调到 12，让「一步一个工具」的洞察计划留出几次不同参数的修正。`AGENT_MAX_STEPS` 可设为 1～32 的整数，非法值仍用默认。真正停住空转的是重复上限，不是把预算再放大。

连续 3 次工具名 + 规范化参数相同，第 3 次不执行。键序、首尾空白、以及 `5` 与 `5.0` 算同一次；字符串 `"5"` 和数字 `5` 不算。同一 `decide` 里未触达上限的前缀仍按 Batch B 并行、按模型顺序写回，上限之后的调用（含不同工具）不再启动。Batch A 的 `ok:false` 回灌保留：相同失败可以再跑一次，第 3 次相同提案才停。参数每次都不同的调用仍会走到步数上限。

停住后往 `tool_results` 写入 `_loop_break` 说明，并在 system 末尾加一段 `# Loop break`，再 `decide` 一次且不再执行工具。模型返回非空 `final` 就用该正文完成。没有正文但已有 `ok:true` 证据时，用短降级摘要完成（工具名和 evidence 指纹，不写业务报告模板）。没有可用证据时 Run `failed`，原因以 `loop_break:` 开头，而不是 `exceeded MAX_STEPS`。

观测：`model_step.type = loop_break`，并带 `loop_break: true` 与 `repeated_tool`。接着那一次 decide 的 `model_step` 也带这两个字段。
