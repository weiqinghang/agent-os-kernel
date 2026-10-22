Feature: 上下文长度策略
  会话消息和工具结果变长时，broker 调用前按规则截断。
  保留系统提示、最近一条用户话、较新的成功工具证据和 bindings。
  先丢掉最旧的中间轮次，再丢掉较旧的工具大块。
  没有向量记忆，也没有摘要 Agent。

  Scenario: 超长历史会截断且 decide 仍给出结论
    Given 一条远超预算的会话历史和工具结果
    When 用这条历史上限调用 decide
    Then 截断事件带有丢掉的消息数和工具结果数
    And 送入模型的上下文留下系统提示、最近用户话和成功证据
    And decide 返回截断后的 final

  Scenario: live 请求在截断后仍带 tools 和最近的 tool 结果
    Given 一条远超预算的会话历史和工具结果
    And 可拦截的长度策略 live 补全
    When 用这条历史上限调用 decide
    Then 截断事件带有丢掉的消息数和工具结果数
    And 发出的 messages 不含被丢掉的旧块
    And 请求仍带 tools 且保留最近的成功 tool 消息

  Scenario: 超长会话历史的洞察回合仍能完成
    Given 会话里堆了远超预算的旧消息
    When 用户在长度预算下要求生成洞察报告
    Then 这一轮截断后的 Run 完成
    And 观测里有上下文截断事件
