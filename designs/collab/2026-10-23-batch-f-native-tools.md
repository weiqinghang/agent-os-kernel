# Batch F · 原生 tool calling

`decide()` 签名不变，仍返回 `tool_calls` 或 `final`。Orchestrator 不分支，并行、失败回灌、熔断和取消都还走原来的 `calls`。

live 的 `chat/completions` 带 `tools`（本次 `allowed_tools`，已知狗粮工具用网关已经接受的参数形状）和 `tool_choice: auto`。已有 `tool_results` 按顺序写成 assistant 的 `tool_calls`，后面紧跟 `role: tool`。`ok: false` 的短错误在这条 tool 消息里。`loop_break`、下划线名字和没有名字的结果不伪装成函数，错误原文另起一条 user 消息。不再把「请以 JSON 回复」或整包工具结果塞进 user。

响应先读 `message.tool_calls`。`function.arguments` 可以是 JSON 字符串或对象。没有可用的原生调用时，正文里的 JSON 决策仍按围栏或截取解析；解析不了就当作 final 正文。stub 不发 HTTP。

没有多厂商配置，也没有 LangChain。换地址仍是 `MODEL_BASE_URL`。
