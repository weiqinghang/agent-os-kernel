Feature: 工具循环韧性
  可恢复的工具失败写入 tool_results 后继续 decide。
  DAS 超时或 5xx 只重试一次；认证、4xx 和连接错误不重试。
  live 请求失败后再试一次，仍失败则降级到 stub，并记下 live_retry 与 live_fallback。

  Scenario: 注入的工具失败回灌后 Run 继续完成
    Given 一条 stub 洞察 Run
    And 网关对 describe_dataset 抛出可恢复错误
    When 用户要求生成洞察报告
    Then Run 状态是 completed
    And 事件里有 describe_dataset 的失败 tool_call
    And 同一步里后续工具仍然成功
    And 下一次 decide 看见 ok 为 false 的短错误

  Scenario: 坏调用形状回灌后继续
    Given 一条 stub 洞察 Run
    And 模型先返回坏调用再给出结论
    When 编排器执行这一轮
    Then Run 状态是 completed
    And 事件里有 ok 为 false 的 tool_call
    And 过长的工具错误被截短
    And 下一次 decide 看见这些失败结果

  Scenario: 不可恢复的情况仍然失败 Run
    Given 一条 stub 洞察 Run
    When 编排器处理未知 Skill
    Then Run 失败且原因包含「unknown skill」
    When 模型返回空的 tool_calls
    Then Run 失败且原因包含「empty tool_calls」
    When 模型返回未知决策
    Then Run 失败且原因包含「unknown decision」
    When 模型连续要求调用工具直到超过 MAX_STEPS
    Then Run 失败且原因包含「MAX_STEPS」
    And 失败前每一步工具错误都已回灌

  Scenario: DAS 超时或 5xx 只重试一次，认证和 4xx 不重试
    Given 可脚本化的 DAS HTTP 传输
    When DAS 第一次超时且第二次成功
    Then 调用成功并且只退避了一次
    When DAS 第一次返回 503 且第二次成功
    Then 调用成功并且只退避了一次
    When DAS 连续两次返回 503
    Then 调用失败且状态为 503 并且只请求了 2 次
    When DAS 连续两次超时
    Then 调用失败且状态为 504 并且只请求了 2 次
    When DAS 返回 401
    Then 调用失败且状态为 401 并且只请求了 1 次
    When DAS 返回 400
    Then 调用失败且状态为 400 并且只请求了 1 次
    When DAS 连接失败
    Then 调用失败且状态为 502 并且只请求了 1 次
    When DAS 返回 JSON-RPC 错误
    Then 调用失败且没有重试

  Scenario: live 失败后再试一次然后降级到 stub
    Given 一条 live 模型的洞察 Run
    And live 请求每次都失败
    When 用户要求生成洞察报告
    Then 事件包含 live_retry 和 live_fallback
    And Run 状态是 completed
    And 结论仍来自 stub 路径

  Scenario: live 第二次成功则不降级
    Given 一条 live 模型的洞察 Run
    And live 第一次失败第二次返回 final
    When 用户要求生成洞察报告
    Then 事件包含 live_retry 且没有 live_fallback
    And Run 状态是 completed
    And 结论文本是「live ok」
