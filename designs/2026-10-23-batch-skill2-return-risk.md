# Skill#2 退货风险助手

状态：**done**（D-SKILL2）。工程范围以冻结页为准：只加静态 AgentRecord、激活 `return_risk_brief`、把同一 `ecommerce_sales_v2` 扩到约 10 万行。没有新的 `phase_*`，没有为退货改 Run 状态机。

重种假 DAS（同一数据集 id，不新建 MCP）：

```bash
uv run python scripts/seed_ecommerce.py
```

真 DAS 仍在原 id 上导入，见 `designs/collab/DAS-LOCAL-ON-BOX.md`。
