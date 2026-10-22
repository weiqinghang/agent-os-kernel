Feature: 内核经 Bearer 调用真 DAS MCP
  默认不再打无鉴权的 {FAKE_DAS_URL}/mcp。
  狗粮结论页的数据集 id 与种子 ecommerce_sales_v2 对齐。

  Scenario: list_datasets 与 query_stats 打到 /api/mcp 并带 Bearer
    Given 真 DAS MCP 已按契约接好
    When 网关列出工具并调用 list_datasets 与 query_stats
    Then 请求打到 /api/mcp 且带 Bearer
    And 数据集里有 ecommerce_sales_v2
    And query_stats 返回聚合行
    And 工具名单包含 describe_fields 与 describe_tag_hierarchies

  Scenario: 裸 SQL 在出网前被拒绝
    Given 真 DAS MCP 已按契约接好
    When 网关调用 query_stats 并带上 sql
    Then 网关拒绝裸 SQL 且没有发出请求

  Scenario: 假形状参数改写成 dataset_id 与 filters/metrics
    Given 真 DAS MCP 已按契约接好
    When 网关用假 DAS 形状调用 query_stats、describe_dataset 与 sample_records
    Then 真 MCP 参数不含 name、dataset、start、end 与 metric 字符串
    And query_stats 带上 dataset_id、时间窗 filters 数组、metrics 数组与 group_by 数组

  Scenario: 友好数据集名解析为 UUID 且只拉一次目录
    Given 真 DAS MCP 已按契约接好
    And 目录把 ecommerce_sales_v2 映射到 UUID
    When 网关用友好名两次调用 query_stats 并用 UUID 再调用一次
    Then 只请求了一次 list_datasets 且 query_stats 的 dataset_id 都是该 UUID

  Scenario: USE_FAKE_DAS 仍按假形状出网
    Given 假 DAS 开关已打开
    When 网关用假形状调用 query_stats
    Then 参数保留 name、start、end、metric 且没有 dataset_id

  Scenario: 聊天狗粮结论页对齐种子数据集
    Given 真 DAS MCP 已按契约接好
    When 用户让内核写一份电商洞察报告
    Then 结论页包含种子数据集 ecommerce_sales_v2
    And 结论页包含发现

  Scenario: 默认启动脚本拉起真 DAS
    Given 仓库里的启动脚本
    Then 默认路径启动 dataset-analysis-service 且 workers 为 2
    And fake-das 只在 USE_FAKE_DAS 开关后启动
    And 停止脚本会停 das、fake-das 和 kernel
