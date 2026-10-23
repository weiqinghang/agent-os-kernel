# Batch H 收口（洞察同壳抛光）

- 状态：**done**
- 基线：P0′ nits 已合；Skill#2 P1 F1–F3 tip `df194b43`
- 本刀只动洞察 stub 的金额可读和尾注/范围人话。D-COST 未改。Batch I、SSE、`phase_*`、HITL 多 mode、Runtime 状态机均未做。

## 交付

| # | 结果 |
| --- | --- |
| H1 | 依据样本 `GMV=` 与退货侧同一规则：至多两位小数，整数不补 `.00`。共享纯函数 `skills/money_fmt.py` 的 `format_gmv`；`return_risk_brief._format_gmv` 改为该函数的别名 |
| H2 | stub 报告的总 GMV、类目/渠道/省份 Top 金额走同一规则。退货率仍按原值加 `%`，不用金额格式化 |
| H3 | 尾注改为「可追问第 2 条依据下钻，或改时间窗后再出一版」。范围行写「本批销量数据」加数据集 id，不再写 `dataset=`。`_evidence:` 指纹仍留在原文 |
| H4 | **跳过**。`insight_report.system_prompt` 没有字面量 `Tool Gateway` 或 `dataset=` |

## 不做

Batch I（events ms/usage）、D-SSE、停止钮产品化、确认代发气泡、幽灵依据、新 Skill / 数据集、HITL 新 kind、`phase_*`、展示中台。

## BTDD

`tests/features/insight_dogfood_polish.feature`
