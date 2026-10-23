Feature: Insight dogfood polish Batch H closeout
  洞察 stub 的金额与退货侧同一套两位小数规则。
  尾注和范围行不再写出 Tool Gateway 或 dataset=。

  Scenario: 依据样本 GMV 无长尾
    Given insight_report 下钻或 stub 依据含 gmv=934.0200000000 源值
    When 组装默认可见样本行
    Then 行内 GMV 展示为至多两位小数（如 934.02）

  Scenario: 结论金额可读
    Given stub 洞察报告含总 GMV 与类目/渠道 Top 金额
    When 组装报告正文
    Then 金额类数字无长尾浮点串

  Scenario: stub 无技术串教坏演示
    When 组装 stub 洞察结论
    Then 正文不含 "Tool Gateway"
    And 正文不含 "dataset="

  Scenario: 底座未膨胀
    When 检视本批 diff
    Then 无 phase_*、无 run_events usage/ms 产品字段、无新 MCP
