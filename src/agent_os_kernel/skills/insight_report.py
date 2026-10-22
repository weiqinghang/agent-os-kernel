"""insight_report skill — manifest + stub fixtures (Broker stays business-free)."""
from __future__ import annotations

import json
import re
from typing import Any

from ..context import SEED_DATASET_ID

# 计划卡进度只看计数和步骤文案通配，不看工具名。编排不按步骤编号推进。
PLAN_PROGRESS: list[dict[str, Any]] = [
    {"when": "outline_confirm", "status": "pending"},
    {"when": "tool_calls", "count": 1, "label": "*范围*"},
    {"when": "tool_calls", "count": 2, "label": "*类目*"},
    {"when": "tool_calls", "count": 3, "label": "*渠道*"},
    {"when": "tool_calls", "count": 4, "label": "*省*"},
    {"when": "tool_calls", "count": 5, "label": "*退货*"},
    {"when": "finalizing", "label": "*结论*", "status": "active"},
]

SKILL: dict[str, Any] = {
    "name": "insight_report",
    "version": "0.5.0",
    "system_prompt": (
        "你是电商销量洞察报告助理。只用工具返回的聚合数字写中文结论，"
        "每条发现必须带 evidence 指纹；禁止臆造数字或绕过工具网关。"
        "需要数据时先发 tool_calls（list_datasets / describe_dataset / query_stats / "
        "sample_records / search_records），证据齐后再输出 final markdown。"
        " describe_fields / describe_tag_hierarchies 仅在需要字段或标签层级时调用，参数只给 dataset_id。"
        " query_stats / search_records / sample_records 的 dataset_id 用数据集 UUID（友好名由网关解析）。"
        " filters 是 {field, operator, value} 数组，时间窗用 order_date 的 gte/lte；"
        " metrics 是 {function, field, alias} 数组（function 为 sum/avg/count 等）；"
        " group_by 是字段名数组。不要传 name、dataset、start、end 或 metric 字符串。"
        " sample_records / search_records 要带非空 return_fields"
        "（order_id、sku、category、channel、province、order_date、qty、gmv、returned）；"
        "缺省时网关会补上这组字段。数据集已发布默认证据字段时也可传 evidence_preset 为 default。"
        "用户要下钻某条发现的依据时，调用 sample_records 或 search_records。"
        "最终 markdown 在 Context bindings 已给出 dataset 与 time_window 时，"
        "必须原文引用 dataset（dataset_id）和 time_window.label，不要改写这两个字面值。"
    ),
    "allowed_tools": [
        "list_datasets",
        "describe_dataset",
        "describe_fields",
        "describe_tag_hierarchies",
        "query_stats",
        "search_records",
        "sample_records",
    ],
    "hitl_kinds": ["outline_confirm"],
    "hitl_prompts": {
        "outline_confirm": "请确认报告大纲后继续（范围以会话 Context bindings 为准）。",
    },
    # 业务计划只写在这里。编排抄进 plan 事件和大纲正文，不另起阶段机。
    "plan_steps": [
        {"id": "scope", "label": "确认这次要看的范围"},
        {"id": "category", "label": "看清类目卖得怎么样"},
        {"id": "channel", "label": "看清渠道结构"},
        {"id": "province", "label": "看清各省贡献"},
        {"id": "returns", "label": "看清退货风险"},
    ],
    "plan_progress": PLAN_PROGRESS,
    "default_bindings": {
        "dataset": SEED_DATASET_ID,
        "time_window": {"label": "近90天", "start": "2025-07-25", "end": "2025-10-22"},
    },
}


def get_skill() -> dict[str, Any]:
    return SKILL


def _last_user(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return str(m.get("content") or "")
    return ""


def _call_key(name: str, arguments: dict[str, Any] | None) -> str:
    return json.dumps({"name": name, "args": arguments or {}}, sort_keys=True, ensure_ascii=False)


def _have(tool_results: list[dict[str, Any]]) -> set[str]:
    return {_call_key(t.get("name", ""), t.get("arguments") or {}) for t in tool_results}


def _pick(tool_results: list[dict[str, Any]], name: str, **arg_match: Any) -> dict[str, Any] | None:
    for t in tool_results:
        if t.get("name") != name:
            continue
        args = t.get("arguments") or {}
        if all(args.get(k) == v for k, v in arg_match.items()):
            return t
    for t in tool_results:
        if t.get("name") == name:
            return t
    return None


def _fmt_rows(rows: list[dict[str, Any]], n: int = 5) -> str:
    return "；".join(f"{r.get('key')}={r.get('value')}" for r in rows[:n])


def _fp(t: dict[str, Any] | None) -> str:
    if not t:
        return "n/a"
    return (t.get("evidence") or {}).get("fingerprint") or "n/a"


def _intent(user_text: str, messages: list[dict[str, Any]]) -> str:
    t = user_text.lower()
    # drill / evidence
    if any(k in user_text for k in ("依据", "样本", "下钻", "证据", "sample", "第2", "第二条", "第 2")):
        return "drill"
    if ("报告" in user_text or "洞察" in user_text or "结论" in user_text) and not any(
        k in user_text for k in ("哪个省", "哪省", "哪个渠道", "哪个类目", "依据", "样本")
    ):
        return "report"
    if "省" in user_text or "province" in t or "区域" in user_text:
        return "province"
    if "渠道" in user_text or "channel" in t:
        return "channel"
    if "类目" in user_text or "category" in t:
        return "category"
    if "退货" in user_text or "return" in t:
        return "return"
    assistant_count = sum(1 for m in messages if m.get("role") == "assistant")
    if assistant_count > 0:
        if "省" in user_text:
            return "province"
        if "渠道" in user_text:
            return "channel"
        if "类目" in user_text:
            return "category"
    return "report"


def _tw_args(bindings: dict[str, Any]) -> dict[str, Any]:
    tw = bindings.get("time_window") or {}
    out: dict[str, Any] = {}
    if isinstance(tw, dict):
        if tw.get("start"):
            out["start"] = tw["start"]
        if tw.get("end"):
            out["end"] = tw["end"]
    ds = bindings.get("dataset") or SEED_DATASET_ID
    out["name"] = ds
    return out


def _report_plan(bindings: dict[str, Any]) -> list[dict[str, Any]]:
    tw = _tw_args(bindings)
    base = {k: tw[k] for k in ("start", "end") if k in tw}
    ds = {"name": tw.get("name", SEED_DATASET_ID), **base}
    return [
        {"name": "list_datasets", "arguments": {}},
        {"name": "describe_dataset", "arguments": dict(ds)},
        {"name": "query_stats", "arguments": {**base, "group_by": "category", "metric": "sum_gmv", "limit": 10}},
        {"name": "query_stats", "arguments": {**base, "group_by": "channel", "metric": "sum_gmv", "limit": 10}},
        {"name": "query_stats", "arguments": {**base, "group_by": "province", "metric": "sum_gmv", "limit": 10}},
        {"name": "query_stats", "arguments": {**base, "group_by": "channel", "metric": "return_rate", "limit": 10}},
    ]


# 报告四条发现的顺序是固定的：第 N 条要对齐该条的维度，不能整窗随便抽。
_FINDINGS = {
    1: ("category", "sum_gmv", "类目"),
    2: ("channel", "sum_gmv", "渠道"),
    3: ("province", "sum_gmv", "省份"),
    4: ("channel", "return_rate", "退货渠道"),
}


def _finding_index(user_text: str) -> int | None:
    m = re.search(r"第\s*([1-4])\s*条", user_text)
    if m:
        return int(m.group(1))
    cn = {"一": 1, "二": 2, "三": 3, "四": 4}
    m = re.search(r"第\s*([一二三四])\s*条", user_text)
    if m:
        return cn[m.group(1)]
    return None


def _top_key(tool_results: list[dict[str, Any]], group_by: str, metric: str) -> str | None:
    stats = _pick(tool_results, "query_stats", group_by=group_by, metric=metric)
    rows = ((stats or {}).get("data") or {}).get("rows") or []
    if rows and rows[0].get("key"):
        return str(rows[0]["key"])
    return None


def _drill_plan(
    bindings: dict[str, Any],
    user_text: str,
    tool_results: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    tool_results = tool_results or []
    tw = _tw_args(bindings)
    base = {k: tw[k] for k in ("start", "end") if k in tw}
    idx = _finding_index(user_text)
    if idx in _FINDINGS:
        group_by, metric, _label = _FINDINGS[idx]
        stats_args = {**base, "group_by": group_by, "metric": metric, "limit": 10}
        plan: list[dict[str, Any]] = [{"name": "query_stats", "arguments": stats_args}]
        top = _top_key(tool_results, group_by, metric)
        if top:
            plan.append(
                {
                    "name": "sample_records",
                    "arguments": {**base, "limit": 5, "filters": {group_by: top}},
                }
            )
        return plan
    filters: dict[str, Any] = {}
    for cat in ("电子产品", "服装", "美妆", "家居", "食品"):
        if cat in user_text:
            filters["category"] = cat
            break
    args: dict[str, Any] = {**base, "limit": 5}
    if filters:
        args["filters"] = filters
    return [{"name": "sample_records", "arguments": args}]


def _followup_plan(intent: str, bindings: dict[str, Any]) -> list[dict[str, Any]]:
    tw = _tw_args(bindings)
    base = {k: tw[k] for k in ("start", "end") if k in tw}
    plans = {
        "province": [{"name": "query_stats", "arguments": {**base, "group_by": "province", "metric": "sum_gmv", "limit": 10}}],
        "channel": [{"name": "query_stats", "arguments": {**base, "group_by": "channel", "metric": "sum_gmv", "limit": 10}}],
        "category": [{"name": "query_stats", "arguments": {**base, "group_by": "category", "metric": "sum_gmv", "limit": 10}}],
        "return": [{"name": "query_stats", "arguments": {**base, "group_by": "channel", "metric": "return_rate", "limit": 10}}],
    }
    return plans.get(intent, plans["province"])


def _build_report(tool_results: list[dict[str, Any]], bindings: dict[str, Any]) -> str:
    tw = bindings.get("time_window") or {}
    label = (tw.get("label") if isinstance(tw, dict) else None) or "当前时间窗"
    desc_t = _pick(tool_results, "describe_dataset") or {}
    desc = desc_t.get("data") or {}
    summary = desc.get("summary") or {}
    by_cat = (_pick(tool_results, "query_stats", group_by="category", metric="sum_gmv") or {}).get("data") or {}
    by_ch = (_pick(tool_results, "query_stats", group_by="channel", metric="sum_gmv") or {}).get("data") or {}
    by_prov = (_pick(tool_results, "query_stats", group_by="province", metric="sum_gmv") or {}).get("data") or {}
    ret_ch = (_pick(tool_results, "query_stats", group_by="channel", metric="return_rate") or {}).get("data") or {}
    top_cat = (by_cat.get("rows") or [{}])[0]
    top_ch = (by_ch.get("rows") or [{}])[0]
    top_prov = (by_prov.get("rows") or [{}])[0]
    worst_ret = (ret_ch.get("rows") or [{}])[0]
    lines = [
        f"# 电商销量洞察报告（{label}）",
        "",
        f"**范围**：dataset=`{bindings.get('dataset')}`；"
        f"时间窗 {tw.get('start') if isinstance(tw, dict) else '?'} ~ "
        f"{tw.get('end') if isinstance(tw, dict) else '?'}（{label}）。",
        f"**数据概览**：共 {desc.get('row_count', summary.get('filtered_rows', '—'))} 行；"
        f"日期 {summary.get('min_date')} ~ {summary.get('max_date')}；"
        f"总 GMV ≈ {summary.get('total_gmv')}；整体退货率 {summary.get('return_pct')}%。",
        f"_evidence: {_fp(desc_t)}_",
        "",
        "## 发现",
        "",
        f"1. **类目集中**：GMV 最高类目为 **{top_cat.get('key')}**（{top_cat.get('value')}）。"
        f" Top：{_fmt_rows(by_cat.get('rows') or [])}。",
        f"   - evidence: `{_fp(_pick(tool_results, 'query_stats', group_by='category', metric='sum_gmv'))}`",
        "",
        f"2. **渠道结构**：GMV 最高渠道为 **{top_ch.get('key')}**（{top_ch.get('value')}）。"
        f" Top：{_fmt_rows(by_ch.get('rows') or [])}。",
        f"   - evidence: `{_fp(_pick(tool_results, 'query_stats', group_by='channel', metric='sum_gmv'))}`",
        "",
        f"3. **区域贡献**：GMV 最高省份为 **{top_prov.get('key')}**（{top_prov.get('value')}）。"
        f" Top：{_fmt_rows(by_prov.get('rows') or [])}。",
        f"   - evidence: `{_fp(_pick(tool_results, 'query_stats', group_by='province', metric='sum_gmv'))}`",
        "",
        f"4. **退货风险**：按渠道退货率最高为 **{worst_ret.get('key')}**（{worst_ret.get('value')}%）。",
        f"   - evidence: `{_fp(_pick(tool_results, 'query_stats', group_by='channel', metric='return_rate'))}`",
        "",
        "---",
        "_数字均来自 Tool Gateway；可追问「第2条依据/样本」下钻，或改时间窗后重跑。_",
    ]
    return "\n".join(lines)


def _build_drill(tool_results: list[dict[str, Any]], user_text: str = "") -> str:
    idx = _finding_index(user_text)
    spec = _FINDINGS.get(idx) if idx else None
    top = None
    dim = "样本"
    if spec:
        group_by, metric, dim = spec
        top = _top_key(tool_results, group_by, metric)
    t = _pick(tool_results, "sample_records") or _pick(tool_results, "search_records") or {}
    data = t.get("data") or {}
    recs = data.get("records") or []
    fp = _fp(t)
    title = f"第{idx}条依据" if idx else "发现依据"
    lines = [f"## {title}", ""]
    if top:
        lines.append(f"这条发现讲的是{dim} **{top}**。下面只列出「{top}」的样本。")
        lines.append("")
    if not recs:
        lines.append("没有抽到与这条发现对齐的样本。")
    else:
        lines.append(f"共 {data.get('count', len(recs))} 条样本（展示前 {min(5, len(recs))}）：")
        lines.append("")
        for i, r in enumerate(recs[:5], 1):
            lines.append(
                f"{i}. {r.get('order_id')} · {r.get('category')} · {r.get('channel')} · "
                f"{r.get('province')} · {r.get('order_date')} · GMV={r.get('gmv')}"
            )
    lines += ["", f"_evidence: `{fp}`_"]
    return "\n".join(lines)


def _build_followup(intent: str, tool_results: list[dict[str, Any]]) -> str:
    t = tool_results[-1] if tool_results else {}
    data = t.get("data") or {}
    rows = data.get("rows") or []
    fp = _fp(t)
    if not rows:
        return f"未能从 DAS 取到聚合（intent={intent}）。\n\n_evidence: `{fp}`_"
    top = rows[0]
    labels = {
        "province": ("省份 GMV 排名", "GMV"),
        "channel": ("渠道 GMV", "GMV"),
        "category": ("类目 GMV", "GMV"),
        "return": ("渠道退货率", "%"),
    }
    title, unit = labels.get(intent, ("汇总", ""))
    suffix = "%" if intent == "return" else ""
    return (
        f"**{title}**（最高：**{top.get('key')}**，{unit}={top.get('value')}{suffix}）\n\n"
        f"{_fmt_rows(rows, 8)}\n\n"
        f"_evidence: `{fp}`_"
    )


def stub_decide(
    *,
    messages: list[dict[str, Any]],
    allowed_tools: list[str],
    tool_results: list[dict[str, Any]],
    bindings: dict[str, Any],
) -> dict[str, Any]:
    user_text = _last_user(messages)
    intent = _intent(user_text, messages)
    if intent == "report":
        plan = [c for c in _report_plan(bindings) if c["name"] in allowed_tools]
    elif intent == "drill":
        plan = [c for c in _drill_plan(bindings, user_text, tool_results) if c["name"] in allowed_tools]
    else:
        plan = [c for c in _followup_plan(intent, bindings) if c["name"] in allowed_tools]

    have = _have(tool_results)
    missing = [c for c in plan if _call_key(c["name"], c.get("arguments")) not in have]
    if missing:
        return {"type": "tool_calls", "calls": missing}

    if intent == "report":
        return {"type": "final", "content": _build_report(tool_results, bindings)}
    if intent == "drill":
        return {"type": "final", "content": _build_drill(tool_results, user_text)}
    return {"type": "final", "content": _build_followup(intent, tool_results)}


_SEEKING_TYPES = {"tool_calls", "live_retry", "live_fallback", "live_fallback_stub"}
_WRITING_TYPES = {"final", "finalizing", "loop_break"}


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("payload")
    return raw if isinstance(raw, dict) else {}


def _label_matches(label: str, pattern: str) -> bool:
    """通配只按字面片段顺序匹配步骤文案。`*` 以外的字是必须出现的片段。"""
    parts = [part for part in pattern.split("*") if part]
    if not parts:
        return False
    pos = 0
    for part in parts:
        found = label.find(part, pos)
        if found < 0:
            return False
        pos = found + len(part)
    return True


def _writing_model_step(payload: dict[str, Any]) -> bool:
    """与呼吸条同一套收尾判定。不读工具名。"""
    kind = payload.get("type")
    kind = kind if isinstance(kind, str) else ""
    if kind in _SEEKING_TYPES:
        return False
    if kind in _WRITING_TYPES:
        return True
    if payload.get("finalizing") is True:
        return True
    if payload.get("loop_break") is True:
        return True
    if payload.get("n_calls") == 0 and kind:
        return True
    return False


def _progress_signals(events: list[Any] | None) -> dict[str, Any]:
    outline = False
    seen_outline = False
    successes = 0
    finalizing = False
    cancelled = False
    for event in events or []:
        if not isinstance(event, dict):
            continue
        kind = event.get("kind")
        payload = _event_payload(event)
        if kind == "tool_call":
            if payload.get("ok") is True:
                successes += 1
            continue
        if kind == "hitl" and payload.get("kind") == "outline_confirm":
            if payload.get("auto") is True or payload.get("approved") is True:
                outline = False
                seen_outline = False
            else:
                outline = True
                seen_outline = True
            continue
        if kind == "state":
            state = payload.get("state")
            state = state if isinstance(state, str) else ""
            held = payload.get("hitl_kind") == "outline_confirm" or seen_outline
            if state == "awaiting_hitl" and held:
                outline = True
                seen_outline = True
            elif seen_outline and state and state != "awaiting_hitl":
                outline = False
            if state == "failed":
                finalizing = False
            elif state == "cancelled":
                cancelled = True
            elif state in {"completed", "finalizing"}:
                finalizing = True
            continue
        if kind == "model_step" and _writing_model_step(payload):
            finalizing = True
            continue
        if kind == "assistant":
            finalizing = True
    return {
        "outline_confirm": outline,
        "successful_tool_calls": successes,
        "finalizing": finalizing,
        "cancelled": cancelled,
    }


def _clean_progress_steps(steps: list[Any] | None) -> list[dict[str, str]]:
    clean: list[dict[str, str]] = []
    if not isinstance(steps, list):
        return clean
    for item in steps:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            continue
        step_id = item.get("id")
        clean.append(
            {
                "id": step_id.strip() if isinstance(step_id, str) else "",
                "label": label.strip(),
            }
        )
    return clean


def _rule_when(rules: list[Any], when: str) -> bool:
    return any(isinstance(rule, dict) and rule.get("when") == when for rule in rules)


def _statuses_from_counts(steps: list[dict[str, str]], rules: list[Any], successes: int) -> list[str]:
    work: list[tuple[int, str]] = []
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("when") != "tool_calls":
            continue
        count = rule.get("count")
        pattern = rule.get("label")
        if type(count) is not int or count < 1 or not isinstance(pattern, str) or not pattern:
            continue
        work.append((count, pattern))
    work.sort(key=lambda item: item[0])
    assigned: dict[int, int] = {}
    for count, pattern in work:
        for index, step in enumerate(steps):
            if index in assigned:
                continue
            if _label_matches(step["label"], pattern):
                assigned[index] = count
                break
    statuses = ["pending"] * len(steps)
    active_at: int | None = None
    active_count: int | None = None
    for index, count in assigned.items():
        if successes >= count:
            statuses[index] = "done"
        elif active_count is None or count < active_count:
            active_at = index
            active_count = count
    if active_at is not None:
        statuses[active_at] = "active"
    return statuses


def _mark_conclusion(steps: list[dict[str, str]], statuses: list[str], rules: list[Any]) -> None:
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("when") != "finalizing":
            continue
        pattern = rule.get("label")
        if not isinstance(pattern, str) or not pattern:
            continue
        for index, step in enumerate(steps):
            if _label_matches(step["label"], pattern) and statuses[index] != "done":
                statuses[index] = "active"
                break


def apply_plan_progress(
    steps: list[Any] | None,
    events: list[Any] | None,
    rules: list[Any] | None = None,
) -> list[dict[str, str]]:
    """用规则表和已有事件算出每步 pending|active|done|skipped。

    不读工具名，也不改事件。取消时没做完的步骤改为 skipped。
    """
    table = PLAN_PROGRESS if rules is None else rules
    if not isinstance(table, list):
        table = []
    clean = _clean_progress_steps(steps)
    signals = _progress_signals(events)
    if signals["outline_confirm"] and not signals["cancelled"] and _rule_when(table, "outline_confirm"):
        statuses = ["pending"] * len(clean)
    else:
        statuses = _statuses_from_counts(clean, table, signals["successful_tool_calls"])
        if signals["finalizing"]:
            _mark_conclusion(clean, statuses, table)
    if signals["cancelled"]:
        statuses = ["done" if status == "done" else "skipped" for status in statuses]
    return [
        {"id": step["id"], "label": step["label"], "status": statuses[index]}
        for index, step in enumerate(clean)
    ]
