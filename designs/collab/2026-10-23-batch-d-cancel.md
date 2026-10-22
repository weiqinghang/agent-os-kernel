# Batch D · 停止

`POST /runs/{id}/cancel` 把还没结束的 Run 收成 `cancelled`，`result` 写成「已停止」。`created`、`acting`、`awaiting_hitl` 可以停。`completed`、`failed`、`cancelled` 返回 409，状态和结果不动。拒绝确认之后的 Run 已经是 `failed`，再取消也是 409。

编排在决定之前、工具批次之前和之后看取消标记。正在跑的那一批工具会跑完，`tool_call` 事件留下；下一次决定不再发生。完成或失败如果写不回去，不会把 `cancelled` 盖掉，也不会把模型正文写进结果或会话。思维链不进结果、不进事件。

回合线程名是 `turn-{run_id}`。当前这一步或这一批结束后线程退出。没有 SSE，也没有工作流。P0′ 白名单文案不改。

聊天壳只在轮询时显示「停止」。取消被接受后进度条收起，轮询停住，终端留下「已停止」。

## 冒烟

不依赖 DAS。闸门挡住工具或决定再取消：

```bash
python3 -m pytest tests/test_cancel_run.py tests/test_ui_p0_prime.py -q
```

`scripts/uat.sh` 的结算把 `cancelled` 当成未完成并立刻失败，避免停住的回合空等三分钟。stub 回合通常来不及用手取消，以闸门测试为准。
