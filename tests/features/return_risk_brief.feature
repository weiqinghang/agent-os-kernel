Feature: Return risk assistant Skill#2
  退货风险助手是第二条静态 AgentRecord。换助手要新开 Session。
  结论是风险排序，不是销量全景。Runtime 不另开状态。

  Scenario: 独立入口与风险结论
    Given agent_id 为 return_risk.brief 的会话
    When 用户请求近7天退货风险
    Then 出现 3 到 5 条风险向结论
    And 正文不是洞察全景腔
    And 默认终端没有 dataset= 或 evidence 哈希或工具名

  Scenario: 依据对齐爆点
    When 用户查看第 1 条依据
    Then 样本渠道或类目与该条风险描述一致

  Scenario: 时间窗确认出新版
    Given 同一退货会话里已经有一版近7天结论
    When 用户确认改为近30天
    Then 新 Run 完成且结论时间窗口径为近30天

  Scenario: 同线程还能追问
    When 用户在同一会话追问第 1 条依据
    Then 不需要新开 Session
    And 样本与第 1 条爆点一致

  Scenario: 同集约十万行且无新 MCP
    Given 狗粮数据集 ecommerce_sales_v2
    Then 行数量级约 1e5
    And Gateway 仍只有现有 DAS 连接

  Scenario: Runtime 未为退货改状态机
    When 检视本批内核源码
    Then 无新增 phase_ 事件 kind
    And Run 状态集合无退货专用态
    And 编排没有退货业务分支

  Scenario: 近30天简报不再劝人改成近30天
    When 用户在近30天窗口请求退货风险
    Then 简报不提示把时间窗改成近30天
    And 近7天简报仍提示可改成近30天

  Scenario: 依据样本的 GMV 不带长尾小数
    When 用户查看带长尾 GMV 的第 1 条依据
    Then 样本行 GMV 没有长尾小数
