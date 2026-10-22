# Batch G.1 · 计划卡步骤进度

步骤的 `pending|active|done` 在 App 里用 `insight_report.plan_progress` 和已有 `run_events` 计算。规则只数成功的 `tool_call`，并用 label 通配；不看工具名。`outline_confirm` 时全部未开始，呼吸条仍是「等确认」。收尾时 label 像结论的那一步为进行中。取消后没做完的改为跳过。

编排仍只发 G 的那一条 `plan`（`revision: 1`，全 `pending`），不按步骤 id 推进，不发 `phase_*`。P3（再发 `revision ≥ 2`）本批不做：App 本地计算就是 v0。
