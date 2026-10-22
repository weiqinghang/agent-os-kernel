# Batch C · 中途进度

`POST /sessions/{id}/messages` 建好 Run 后立刻返回 `run_id`（`accepted: true`），编排在后台线程里继续。确认后续跑也一样：请求里先离开 `awaiting_hitl`，不等整段循环。拒绝确认仍当场 `failed`，人和轮询看到的都是「已拒绝」。

事件游标是 `GET /runs/{id}/events?after_id=&limit=`。`after_id` 只返回更大的 id；不传则从头拉。应答里带上当前 `state` / `result`，等确认时还有 `hitl`。`GET /runs/{id}` 仍带整段事件，给原来的快照用。

聊天壳拿到 `run_id` 之后轮询这条接口，把新事件投影到同一条进度条。文案仍是 P0′ 白名单：处理中 / 思考中 / 正在查询数据 / 正在整理结论 / 等确认。没有 `tool_call` 就不写「正在查询数据」。工具名和 CoT 不上条带。本批没有 SSE，也没有停止钮。

`scripts/uat.sh` 在 POST 之后轮询到 `completed`，再按原来的结论文本断言，并检查 `after_id`。这步要 DAS 已经起来。单测用闸门挡住编排，不依赖真 DAS。
