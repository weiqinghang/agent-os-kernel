Feature: 洞察计划事件
  Skill 声明不超过五条中文步骤。进入 acting 时编排只抄一次 plan。
  大纲确认的正文用同一步骤。不发明 phase_，也不在中途改写计划。

  Scenario: 自动确认时仍留下一份计划
    Given 一份带计划步骤的洞察 Skill
    And 自动确认大纲
    When 用户发出洞察请求并跑完
    Then 事件里恰好有一条 plan 且步骤都是未开始
    And 这些步骤与 Skill 声明一致
    And 事件种类里没有 phase_ 前缀

  Scenario: 停下确认时大纲正文列出同一步骤
    Given 一份带计划步骤的洞察 Skill
    And 需要人工确认大纲
    When 用户发出洞察请求并停在大纲
    Then 大纲正文按顺序写出每一步
    And 事件里恰好有一条 plan 且步骤都是未开始
    When 用户确认后跑完
    Then 事件里仍然只有一条 plan
    And 这些步骤与 Skill 声明一致
    And 事件种类里没有 phase_ 前缀

  Scenario: 没有计划步骤的 Skill 不发明计划
    Given 一份没有计划步骤的 Skill
    When 用户发出该 Skill 的请求并跑完
    Then 事件里没有 plan
    And 事件种类里没有 phase_ 前缀

  Scenario: 脏步骤不会整包抄进计划
    Given 一份步骤声明不干净的 Skill
    And 需要人工确认大纲
    When 用户发出洞察请求并停在大纲
    Then 计划里只留下五条干净步骤
    And 大纲正文按顺序写出每一步
    And 事件种类里没有 phase_ 前缀
