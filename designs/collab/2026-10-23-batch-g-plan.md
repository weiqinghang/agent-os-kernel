# Batch G · 洞察计划可见

`insight_report` 声明最多五条中文 `plan_steps`。Run 进入 `acting` 时编排只抄一次 `plan`（`revision: 1`，步骤都是 `pending`），`HITL_AUTO=1` 也抄。`outline_confirm` 的正文用同一批步骤。

前台在呼吸条旁边放可折叠计划卡，只显示这些中文短句。呼吸白名单不改。本批不推进步骤状态，也不发 `phase_*`。
