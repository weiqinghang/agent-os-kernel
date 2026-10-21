# 协作事件 Schema

每行一条 JSON（JSONL），字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ts` | string | ISO-8601，Asia/Shanghai 本地墙钟也可，须带偏移 |
| `from` | string | 发起方：`庆颃` / `Grok` / `周矩白` / `林可意` / `程照言` / `陈嘉树` |
| `to` | string | 接收方；广播用 `*` |
| `action` | enum | 见下表 |
| `artifact` | string\|null | 工件路径或 URL |
| `summary` | string | 一句人话结论（≤120 字） |
| `refs` | string[] | 可选；关联 event id 或 Issue |

## action 枚举

| action | 含义 |
| --- | --- |
| `ask` | 提问 / 征求意见 |
| `propose` | 提出方案或草稿 |
| `challenge` | 挑刺 / 否决局部 |
| `revise` | 按反馈修改 |
| `approve` | 批准 |
| `reject` | 整段否决 |
| `ship` | 交付可运行物（嘉树开工后） |
| `note` | 主控记事（不改变状态） |

v0 不上工作流引擎；本日志只观测，不驱动内核状态机。
