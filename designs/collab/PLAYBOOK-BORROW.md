# 从 Playbook 借鉴什么（精简）

不全套 `.playbook/` 种子与 Discovery→Delivery 全家桶。只借与「可观测协作 / 单一事实源」直接相关的纪律。

## 借

| 做法 | 在本仓怎么落地 |
| --- | --- |
| **Issue 为源（SoT）** | 每个里程碑/决策开一条 GitHub Issue；结论写回 Issue 评论或关闭说明，不散落在聊天 |
| **MR / 提交对齐 Issue** | 分支与 PR 标题带 ` #<n>`；commit message 首行说清「改了什么 / 对应哪条 Issue」 |
| **小步可审** | 一次 PR 只推一件可验收的事；禁止大泥球「顺手重构」 |
| **事件日志镜像 Issue** | `events.jsonl` 的 `refs` 填 `issue:#n`；看板链到 Issue URL |

## 不借（刻意）

- 下游仓强制 `.seed-lock.json` / 全套 Reviewer·Architect packs（本仓自用够薄即可）
- 为小原型硬套平台式里程碑仪式
- 把洞察业务词写进内核 Issue 模板（仍归 Skill）

## 建议 Issue 模板（最小）

开 Issue 时四行就够：

1. **目标**（一句话）
2. **非目标**（少做什么）
3. **验收**（怎样算完成）
4. **工件**（设计路径 / PR 链接）

角色分工仍走点对点 + `events.jsonl`；Issue 是对外可引用的事实源，不是把四人对话搬进 GitHub 讨论区表演。
