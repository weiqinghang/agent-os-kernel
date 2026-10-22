Feature: 重复工具调用熔断
  连续相同、或只差空白、键序、整型浮点的工具调用，达到重复上限后不再执行。
  编排器注入说明并再要一次 final。模型给出正文就用正文；没有正文但已有成功证据时，
  完成并给出降级摘要；没有证据时以 loop_break 失败，而不是耗尽 MAX_STEPS。
  ok:false 回灌和同一 decide 内的有界并行写回保持不变。

  Scenario: 连续相同的 sample_records 在步数耗尽前停止并给出降级结论
    Given 一条会跨步重复 sample_records 的洞察 Run
    And 熔断后模型仍继续要工具
    When 编排器跑完重复调用这一轮
    Then 已执行的工具次数停在重复上限之前
    And 这一轮没有耗尽步数预算
    And 降级结论写明已停止重复调用
    And 观测事件带有 loop_break 和 repeated_tool
    And 熔断那次 decide 看见注入说明且没有再执行工具

  Scenario: 注入说明后模型给出 final 就用该结论
    Given 一条会跨步重复 sample_records 的洞察 Run
    And 熔断后模型改回 final
    When 编排器跑完重复调用这一轮
    Then 已执行的工具次数停在重复上限之前
    And 结论文本是模型收束结论
    And 观测事件带有 loop_break 和 repeated_tool

  Scenario: 空白 final 或 decide 异常或非对象结果仍退回降级结论
    Given 一条会跨步重复 sample_records 的洞察 Run
    And 熔断后模型给出空白 final
    When 编排器跑完重复调用这一轮
    Then 降级结论写明已停止重复调用
    And 失败原因不是编排失败
    Given 一条会跨步重复 sample_records 的洞察 Run
    And 熔断后的 decide 抛出异常
    When 编排器跑完重复调用这一轮
    Then 降级结论写明已停止重复调用
    And 失败原因不是编排失败
    Given 一条会跨步重复 sample_records 的洞察 Run
    And 熔断后 decide 返回非对象
    When 编排器跑完重复调用这一轮
    Then 降级结论写明已停止重复调用

  Scenario: 相同失败没有证据时给出 loop_break 失败
    Given 一条会跨步重复失败调用的洞察 Run
    When 编排器跑完重复调用这一轮
    Then 已执行的工具次数停在重复上限之前
    And 这一轮没有耗尽步数预算
    And Run 以 loop_break 失败且不提 MAX_STEPS
    And 熔断前的失败结果已回灌给 decide

  Scenario: 相同失败回灌一次后换工具仍能完成
    Given 一条先重复失败再换工具的洞察 Run
    When 编排器跑完重复调用这一轮
    Then 重复失败被回灌且随后的不同工具已执行
    And 这条 Run 完成且没有 loop_break

  Scenario: 同一批近义参数只跑到重复上限且按顺序写回
    Given 一条把近义调用放在同一批的洞察 Run
    When 编排器跑完重复调用这一轮
    Then 近义前缀按模型顺序写回且熔断后的调用没有执行
    And 结论文本是前缀结论
    And 观测事件带有 loop_break 和 repeated_tool

  Scenario: 正常洞察路径仍然完成
    Given 一条走 stub 计划的洞察 Run
    When 编排器跑完重复调用这一轮
    Then 这条 Run 完成且没有 loop_break
    And 正文里有发现

  Scenario: 不同参数的调用仍受步数预算约束
    Given 步数预算被设为 3
    And 一条每步参数都不同的未授权调用 Run
    When 编排器跑完重复调用这一轮
    Then Run 因 MAX_STEPS 失败且工具次数等于预算
    And 这条 Run 没有 loop_break

  Scenario: 不是对象的连续调用也会熔断
    Given 一条连续给出非对象调用的洞察 Run
    When 编排器跑完重复调用这一轮
    Then 已执行的工具次数停在重复上限之前
    And Run 以 loop_break 失败且不提 MAX_STEPS
