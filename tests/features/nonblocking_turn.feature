Feature: 非阻塞回合与事件游标
  聊天 POST 不等整段编排结束。客户端用 after_id 只取新事件。

  Scenario: 发送后立即拿到 run_id，结论随后才出现
    Given 一个可编排的新会话
    When 用户发消息且编排停在闸门上
    Then 接口已经返回 run_id 且回合还没完成
    When 后台在闸门内追加两条事件
    Then 用 after_id 只看到新事件
    When 放开编排闸门
    Then 轮询到完成的结论
    And 会话里留下助手正文

  Scenario: 编排线程抛错时回合失败
    Given 一个可编排的新会话
    When 用户发消息且编排直接抛错
    Then 轮询到失败且原因含抛错短句

  Scenario: 事件游标拒绝不存在的 Run 和坏参数
    Given 一个可编排的新会话
    When 查询不存在的事件流
    Then 事件接口返回 404
    When 用户发消息且编排停在闸门上
    Then 负数 after_id 和非法 limit 被拒绝
    When 放开编排闸门
    Then 轮询到完成的结论

  Scenario: 确认继续也不堵住请求
    Given 一个停在确认的回合
    When 用户确认且续跑停在闸门上
    Then 续跑接口已经返回且状态不再是等待确认
    And 立刻再确认一次会被拒绝
    When 放开编排闸门
    Then 轮询到完成的结论
    And 会话里留下助手正文

  Scenario: 拒绝确认仍然立刻返回人话
    Given 一个停在确认的回合
    When 用户拒绝确认
    Then 拒绝接口当场返回已拒绝
