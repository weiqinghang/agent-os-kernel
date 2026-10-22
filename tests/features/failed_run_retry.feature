Feature: 失败回合重试
  只有 failed 可以重试。重试在同一会话里新建一个 Run，深拷贝失败那次的
  context_bindings，旧 Run 保持 failed。不回放 tool_results。
  cancelled 和 completed 不能复活。resume 仍只处理确认。

  Scenario: 失败后可同绑定再跑
    Given 一个带着绑定快照的失败回合
    When 客户端请求重试该回合
    Then 新回合的 retry_of 指向旧回合且绑定等于当时的快照
    And 旧回合仍然是 failed
    And 新回合的事件里有 state 和 retry
    And 模型看到的是失败快照和空的工具结果
    And 失败回合不能靠 resume 恢复
    When 客户端再次重试原来的失败回合
    Then 第二次仍是新的 run_id 且旧回合还是 failed

  Scenario Outline: 非 failed 不可 retry
    Given 一个状态为 "<state>" 的回合
    When 客户端请求重试该回合
    Then 返回 409 且状态仍是 "<state>"

    Examples:
      | state          |
      | completed      |
      | cancelled      |
      | acting         |
      | awaiting_hitl  |
      | created        |

  Scenario: 脱敏
    Given 失败结果里混有工具清单、mode、思维链、密钥、完整参数和堆栈
    When 客户端请求重试该回合
    Then 会话里的失败原因已脱敏且不超过 500 字

  Scenario: 可以改一句重试的话
    Given 一个失败回合
    When 客户端带着「再看渠道」请求重试
    Then 会话里的新用户句是「再看渠道」

  Scenario: 空白正文仍用默认重试句
    Given 一个失败回合
    When 客户端用空白正文请求重试
    Then 会话里的新用户句是「请重试上一轮。」

  Scenario: 没有这个回合
    When 客户端重试一个不存在的回合
    Then 返回 404
