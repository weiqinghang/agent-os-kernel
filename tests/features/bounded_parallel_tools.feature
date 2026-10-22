Feature: 同步有界并行工具调用
  同一次 decide 列出的多个工具调用在进程内并行执行，同时不超过 3 个。
  tool_results 与 tool_call 事件按模型给出的顺序写回。
  可恢复失败仍以 ok:false 回灌；其它异常仍让 Run 失败。

  Scenario: 四个独立读调用的墙钟短于串行
    Given 一条会睡眠的四读网关
    When 编排器执行这四个读调用
    Then 墙钟低于四次串行睡眠且峰值并发为 3
    And 四条 tool_call 都带 ms 且 model_step 的 n_calls 为 4

  Scenario: 乱序完成的成功与失败仍按模型顺序写回
    Given 一条会故意乱序完成的读网关
    When 编排器执行这组混排调用
    Then 混排这一轮 Run 完成
    And tool_results 与 tool_call 事件都保持模型顺序且中间失败

  Scenario: 非网关异常不会被回灌成 ok false
    Given 第二个读调用会抛出非网关异常
    When 编排器执行这对调用
    Then 这一轮 Run 失败且原因包含 boom-not-gateway
    And 抛出的异常没有变成 ok 为 false 的工具结果
