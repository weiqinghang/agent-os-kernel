# 洞察计划可见（Batch G 冻结）

业务步骤的主人是 Skill。内核只抄字段，不发明阶段机。

## 主人

`insight_report.plan_steps`：最多 5 条 `{id, label}`。label 是中文业务短句，不含工具名、JSON、指纹。

同一次进入 `acting`（含停在 `outline_confirm`，也含 `HITL_AUTO=1`）只发一条 `plan`：

```json
{"revision": 1, "steps": [{"id": "…", "label": "…", "status": "pending"}]}
```

`outline_confirm` 的 HITL 正文用这同一批步骤。编排不另写业务句子，不发 `phase_*`。

## 前台

计划卡只读 `run_events.kind == "plan"`，可折叠。本批步骤状态保持未开始，不靠工具事件猜进度。

呼吸条白名单不变：`处理中` / `思考中` / `正在查询数据` / `正在整理结论` / `等确认`。不把业务短句写进白名单。

默认终端和计划卡不出现工具名、mode、run id、JSON、思维链、证据指纹。

## 不做

内核 `phase_*`、工作流引擎、计划卡上的工具名、中途多次 `update_plan`、SSE / 取消范围扩张、Skill#2。
