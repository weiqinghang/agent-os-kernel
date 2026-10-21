# 看板 · agent-os-kernel

更新：2026-10-22（以 `events.jsonl` 为准）

## 当前阶段

**M0 宪章 ✅ → 准备开 M1**（矩白包边界/接口草图，照言审）

## 工件

| 状态 | 工件 |
| --- | --- |
| ✅ | [M0 宪章](../2026-10-21-enterprise-agent-framework-m0-charter.md) |
| ✅ | [狗粮补丁](../2026-10-21-enterprise-agent-dogfood-insight-report.md) |
| ✅ | [v0 架构稿（C 时代参考）](../2026-10-21-enterprise-agent-framework-v0.md) |
| ⏳ | M1 包边界 + Run/HITL/DAS 接口草图 |
| 🚫 | v0 实现（须另令拉嘉树） |

## 开放问题（不挡 M1）

1. 第一版聊天壳：复用 Agent App 宿主 vs 框架自带最小 Web/API
2. Staging DAS 烟测账号谁备
3. 结论页介质：Markdown / HTML 卡片 / JSON 由宿主渲染

## 角色闸门

| 角色 | 本阶段 |
| --- | --- |
| 周矩白 | M1 待启动 |
| 林可意 | 剧本已收口；开放问题待拍 |
| 程照言 | M1 产出后审「是否又堆平台」 |
| 陈嘉树 | **未拉**（宪章硬规则） |

## 协作纪律（借自 Playbook）

- Issue = 单一事实源；聊天与点对点只是过程
- PR / commit 对齐 Issue 编号
- 详见 [PLAYBOOK-BORROW.md](./PLAYBOOK-BORROW.md)

