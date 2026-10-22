# Batch B · 同步有界并行 tool_calls

同一次 `decide` 的 `calls` 在进程内线程池执行，并发上限 `MAX_PARALLEL_TOOLS = 3`。`tool_results` 和 `tool_call` 事件按模型给出的顺序写回。可恢复失败仍是 `ok:false` 回灌，不中断 Run。每条 `tool_call` 带本次调用耗时 `ms`（毫秒）。`model_step.n_calls` 仍是这一步的调用个数。

狗粮工具都是读，这一批整批共享这个上限，没有按写工具再拆一条串行路径。数据集名字缓存加了一把锁，避免并行解析时一次失败把已经填好的目录清掉。

没有任务图、队列、跨步调度或乱序写回。
