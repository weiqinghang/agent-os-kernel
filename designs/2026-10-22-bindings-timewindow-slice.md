# 切片落地：Context 绑定 + 洞察三拍（时间窗优先）

- 日期：2026-10-22
- 状态：已实现（照言刹车后窄版）
- 对照：`/workspace/designs/2026-10-22-context-bindings.md`、`/workspace/designs/2026-10-22-business-capability-product.md`（照言收窄）

## Done
1. Beat1：锁定范围出结论（Session.meta.bindings + Run.context_bindings 快照；system 注入 bindings JSON；Gateway deep_merge 默认参）
2. Beat2：对发现下钻 sample_records
3. Beat3：改 time_window → 新 Run → 新结论（**无**并排「变了什么」叙事）
4. Chat：**仅**时间窗 chip；改绑后若已有结论则确认再跑
5. 静态 Skill registry；Broker 零业务词（fixture 在 Skill）
6. fake-das 支持 start/end 过滤

## Deferred
- dataset / focus chips
- Skill #2 `return_risk_brief` 演示（registry 占位存在，`status=deferred`）
- 并排 diff / 对照叙事
- 外发 HITL、流式、沙箱

## Demo（约 3 分钟）
打开 `http://127.0.0.1:8787/` → 「写一份洞察报告」→ 「第2条依据/样本」→ 点「近30天」→ 确认再跑。
