Feature: 原生工具调用
  live 的 chat/completions 使用 OpenAI 兼容 tools，并把已有工具结果写成 tool 角色消息。
  decide() 对外仍是 tool_calls 或 final。
  没有原生 tool_calls 时，正文里的 JSON 决策仍可解析。
  stub 不发 HTTP。

  Scenario: live 请求带 tools 并按顺序解析 tool_calls
    Given 可拦截的 live chat completions
    When decide 收到两个原生 tool_calls
    Then 请求 tools 含 query_stats 且没有 JSON 指令
    And decide 返回的 calls 顺序与参数保持不变

  Scenario: 工具失败以 tool 角色回灌后回合完成
    Given 可拦截的 live chat completions
    And live 网关对 describe_dataset 抛出可恢复错误
    When 模型先原生调用两个工具再给出正文结论
    Then 第一次请求包含洞察工具且没有 JSON 指令
    And 第二次请求把失败写在 tool 角色消息里
    And 原生工具这一轮 Run 完成
    And 结论文本是「live 洞察」
    And 两次工具调用按模型顺序留下事件

  Scenario: 没有原生 tool_calls 时回退解析 JSON 正文
    Given 可拦截的 live chat completions
    When 响应没有 tool_calls 只有围栏 JSON
    Then decide 类型是 tool_calls
    And 这次请求仍然带 tools

  Scenario: stub 洞察不访问 chat completions
    Given stub 模式且 chat completions 被禁止
    When 用户在 stub 下要求生成洞察报告
    Then 原生工具这一轮 Run 完成
    And 没有发出 chat completions
    And 洞察结论文本来自 stub
