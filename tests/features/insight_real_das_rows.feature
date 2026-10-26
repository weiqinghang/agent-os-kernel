Feature: 洞察终稿读真 DAS 行形
  真 query_stats 行是维度列加指标列，describe 可以没有 summary。
  缺的日期和总 GMV 用破折号，终稿不出现字面 None。
  退货简报已能读同一类行，不能回退。

  Scenario: stub 参数配真 DAS 行时写出维度和数字
    Given 洞察工具结果用 stub 参数但行是真 DAS 的类目渠道省退货聚合且没有 summary
    When 用这些结果组装洞察终稿
    Then 终稿写出家居生活、京东、上海、拼多多和对应数字
    And 终稿的证据指纹分别对上四次聚合
    And 终稿不出现字面 None
    And 缺失的日期、总 GMV 和退货率显示为破折号

  Scenario: live 参数配真 DAS 行时仍对上各自维度
    Given 洞察工具结果用 live 的 group_by 与 metrics 且行是真 DAS 聚合且没有 summary
    When 用这些结果组装洞察终稿
    Then 终稿写出家居生活、京东、上海、拼多多和对应数字
    And 终稿的证据指纹分别对上四次聚合
    And 终稿不出现字面 None

  Scenario: 渠道追问读真 DAS 行
    Given 渠道追问的最后一条是真 DAS 行京东与 sum_gmv
    When 组装这条渠道追问
    Then 追问正文写出京东和 880.5 且不出现字面 None

  Scenario: 第2条依据对得上真 DAS 渠道名
    Given 洞察工具结果用 stub 参数但行是真 DAS 的类目渠道省退货聚合且没有 summary
    When 用这些结果组装第2条依据
    Then 依据正文写出渠道京东且不出现字面 None

  Scenario: 退货简报读真 DAS 行不回退
    Given 退货聚合行是渠道拼多多的 avg_returned 而不是 key value
    When 组装退货风险简报
    Then 简报写出拼多多和退货率 13% 且不出现字面 None
