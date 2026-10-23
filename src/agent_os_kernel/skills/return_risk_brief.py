"""return_risk_brief — risk-ranked return brief. Stub fixtures stay inside the skill."""
from __future__ import annotations

import json
import re
from typing import Any

from ..context import SEED_DATASET_ID

# Same shape as the insight plan table: counts and label wildcards, no tool names.
PLAN_PROGRESS: list[dict[str, Any]] = [
    {"when": "outline_confirm", "status": "pending"},
    {"when": "tool_calls", "count": 1, "label": "*窗口*"},
    {"when": "tool_calls", "count": 2, "label": "*渠道*"},
    {"when": "tool_calls", "count": 3, "label": "*类目*"},
    {"when": "tool_calls", "count": 4, "label": "*省*"},
    {"when": "tool_calls", "count": 5, "label": "*排序*"},
    {"when": "finalizing", "label": "*结论*", "status": "active"},
]

_ALLOWED = [
    "list_datasets",
    "describe_dataset",
    "describe_fields",
    "describe_tag_hierarchies",
    "query_stats",
    "search_records",
    "sample_records",
]

SKILL: dict[str, Any] = {
    "name": "return_risk_brief",
    "version": "0.1.0",
    "system_prompt": (
        "你是退货风险简报助理。任务是按退货风险从高到低给出 3 到 5 条发现，"
        "口吻用「偏高」「建议先看」，不要写成销量全景或类目集中的洞察报告。"
        "只用工具返回的数字，禁止臆造；每条风险在内部保留 evidence 指纹。"
        "需要数据时先发 tool_calls（list_datasets / describe_dataset / query_stats / "
        "sample_records / search_records），证据齐后再输出 final markdown。"
        " describe_fields / describe_tag_hierarchies 仅在需要字段或标签层级时调用，参数只给 dataset_id。"
        " query_stats / search_records / sample_records 的 dataset_id 用数据集 UUID（友好名由网关解析）。"
        " filters 是 {field, operator, value} 数组，时间窗用 order_date 的 gte/lte；"
        " metrics 是 {function, field, alias} 数组（function 为 sum/avg/count 等）；"
        " group_by 是字段名数组。不要传 name、dataset、start、end 或 metric 字符串。"
        " 退货率用 metric return_rate，按 channel、category、province 分别聚合后排序。"
        " sample_records / search_records 要带非空 return_fields"
        "（order_id、sku、category、channel、province、order_date、qty、gmv、returned）；"
        "缺省时网关会补上这组字段。用户要看第 N 条依据时，样本必须落在该条的渠道、类目或省上。"
        "标题使用「退货风险简报」并原文引用 time_window.label。"
    ),
    "allowed_tools": list(_ALLOWED),
    "hitl_kinds": ["outline_confirm"],
    "hitl_prompts": {
        "outline_confirm": "请确认这次要看的退货时间窗后继续（默认近7天，可改近30天）。",
    },
    "plan_steps": [
        {"id": "window", "label": "确认退货时间窗口"},
        {"id": "channel", "label": "看清渠道退货高低"},
        {"id": "category", "label": "看清类目退货高低"},
        {"id": "province", "label": "看清各省退货高低"},
        {"id": "rank", "label": "整理风险排序结论"},
    ],
    "plan_progress": PLAN_PROGRESS,
    "default_bindings": {
        "dataset": SEED_DATASET_ID,
        "time_window": {"label": "近7天", "start": "2025-10-15", "end": "2025-10-22"},
    },
}

_CHANNELS = ("天猫", "京东", "抖音电商", "拼多多", "自营商城")
_CATEGORIES = ("电子产品", "服饰鞋包", "家居生活", "美妆个护", "食品饮料")
_PROVINCES = (
    "广东",
    "浙江",
    "江苏",
    "上海",
    "北京",
    "四川",
    "湖北",
    "山东",
    "福建",
    "河南",
    "湖南",
    "安徽",
    "重庆",
    "陕西",
    "辽宁",
)
_DIMS = (
    ("channel", "渠道"),
    ("category", "类目"),
    ("province", "省份"),
)


def get_skill() -> dict[str, Any]:
    return SKILL


def _last_user(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _call_key(name: str, arguments: dict[str, Any] | None) -> str:
    return json.dumps({"name": name, "args": arguments or {}}, sort_keys=True, ensure_ascii=False)


def _have(tool_results: list[dict[str, Any]]) -> set[str]:
    return {_call_key(item.get("name", ""), item.get("arguments") or {}) for item in tool_results}


def _pick(tool_results: list[dict[str, Any]], name: str, **arg_match: Any) -> dict[str, Any] | None:
    for item in tool_results:
        if item.get("name") != name:
            continue
        args = item.get("arguments") or {}
        if all(args.get(key) == value for key, value in arg_match.items()):
            return item
    for item in tool_results:
        if item.get("name") == name:
            return item
    return None


def _fp(item: dict[str, Any] | None) -> str:
    if not item:
        return "n/a"
    return (item.get("evidence") or {}).get("fingerprint") or "n/a"


def _tw_args(bindings: dict[str, Any]) -> dict[str, Any]:
    window = bindings.get("time_window") or {}
    out: dict[str, Any] = {}
    if isinstance(window, dict):
        if window.get("start"):
            out["start"] = window["start"]
        if window.get("end"):
            out["end"] = window["end"]
    out["name"] = bindings.get("dataset") or SEED_DATASET_ID
    return out


def _base_args(bindings: dict[str, Any]) -> dict[str, Any]:
    tw = _tw_args(bindings)
    return {key: tw[key] for key in ("start", "end") if key in tw}


def _window_label(bindings: dict[str, Any]) -> str:
    window = bindings.get("time_window") or {}
    if isinstance(window, dict) and window.get("label"):
        return str(window["label"])
    return "近7天"


def _finding_index(user_text: str) -> int | None:
    matched = re.search(r"第\s*([1-5])\s*条", user_text)
    if matched:
        return int(matched.group(1))
    names = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}
    matched = re.search(r"第\s*([一二三四五])\s*条", user_text)
    if matched:
        return names[matched.group(1)]
    return None


def _intent(user_text: str) -> str:
    if any(token in user_text for token in ("依据", "样本", "下钻", "证据")):
        return "drill"
    if "只看" in user_text:
        return "focus"
    return "report"


def _named_focus(user_text: str) -> tuple[str, str] | None:
    for name in _CHANNELS:
        if name in user_text:
            return "channel", name
    for name in _CATEGORIES:
        if name in user_text:
            return "category", name
    for name in _PROVINCES:
        if name in user_text:
            return "province", name
    return None


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _ranked(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for group_by, label in _DIMS:
        stats = _pick(tool_results, "query_stats", group_by=group_by, metric="return_rate")
        rows = ((stats or {}).get("data") or {}).get("rows") or []
        if not rows or not rows[0].get("key"):
            continue
        top = rows[0]
        found.append(
            {
                "group_by": group_by,
                "label": label,
                "key": str(top.get("key")),
                "value": top.get("value"),
                "fingerprint": _fp(stats),
                "rate": _as_float(top.get("value")),
            }
        )
    found.sort(key=lambda item: item["rate"], reverse=True)
    return found[:5]


def _stats_plan(bindings: dict[str, Any]) -> list[dict[str, Any]]:
    base = _base_args(bindings)
    return [
        {"name": "list_datasets", "arguments": {}},
        {"name": "describe_dataset", "arguments": dict(_tw_args(bindings))},
        {"name": "query_stats", "arguments": {**base, "group_by": "channel", "metric": "return_rate", "limit": 10}},
        {"name": "query_stats", "arguments": {**base, "group_by": "category", "metric": "return_rate", "limit": 10}},
        {"name": "query_stats", "arguments": {**base, "group_by": "province", "metric": "return_rate", "limit": 10}},
    ]


def _drill_plan(
    bindings: dict[str, Any],
    user_text: str,
    tool_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    base = _base_args(bindings)
    plan = [
        {"name": "query_stats", "arguments": {**base, "group_by": group_by, "metric": "return_rate", "limit": 10}}
        for group_by, _label in _DIMS
    ]
    index = _finding_index(user_text)
    ranked = _ranked(tool_results)
    if index and 1 <= index <= len(ranked):
        row = ranked[index - 1]
        plan.append(
            {
                "name": "sample_records",
                "arguments": {**base, "limit": 5, "filters": {row["group_by"]: row["key"]}},
            }
        )
        return plan
    named = _named_focus(user_text)
    if named and index is None:
        group_by, key = named
        plan.append(
            {
                "name": "sample_records",
                "arguments": {**base, "limit": 5, "filters": {group_by: key}},
            }
        )
    return plan


def _focus_plan(bindings: dict[str, Any], user_text: str) -> list[dict[str, Any]]:
    base = _base_args(bindings)
    named = _named_focus(user_text)
    if not named:
        return _stats_plan(bindings)
    group_by, key = named
    return [
        {"name": "query_stats", "arguments": {**base, "group_by": group_by, "metric": "return_rate", "limit": 10}},
        {"name": "sample_records", "arguments": {**base, "limit": 5, "filters": {group_by: key}}},
    ]


def _build_report(tool_results: list[dict[str, Any]], bindings: dict[str, Any]) -> str:
    label = _window_label(bindings)
    ranked = _ranked(tool_results)
    lines = [
        f"# 退货风险简报（{label}）",
        "",
        "按退货率从高到低，先盯这几处。",
        "",
    ]
    if not ranked:
        lines.append("这次窗口里没有足够的退货聚合，建议先看样本后再排风险。")
        return "\n".join(lines)
    for index, row in enumerate(ranked, 1):
        lines.append(
            f"{index}. **{row['label']}退货偏高**：**{row['key']}** 退货率 {row['value']}%，建议先看。"
        )
        lines.append(f"   - evidence: `{row['fingerprint']}`")
        lines.append("")
    lines.append("可追问「第1条依据」，或把时间窗改成近30天后再出一版。")
    return "\n".join(lines)


def _build_drill(tool_results: list[dict[str, Any]], user_text: str) -> str:
    index = _finding_index(user_text)
    ranked = _ranked(tool_results)
    lines = [f"## 第{index}条依据" if index else "## 风险依据", ""]
    row = None
    if index and 1 <= index <= len(ranked):
        row = ranked[index - 1]
    elif index:
        lines.append(f"没有第{index}条风险。当前简报只有 {len(ranked)} 条。")
        return "\n".join(lines)
    else:
        named = _named_focus(user_text)
        if named:
            group_by, key = named
            label = dict(_DIMS).get(group_by, group_by)
            row = {"group_by": group_by, "label": label, "key": key, "value": None}
    if row:
        lines.append(f"这条风险讲的是{row['label']} **{row['key']}**。下面只列出「{row['key']}」的样本。")
        lines.append("")
    sample = _pick(tool_results, "sample_records") or _pick(tool_results, "search_records") or {}
    data = sample.get("data") or {}
    records = data.get("records") or []
    if not records:
        lines.append("没有抽到与这条风险对齐的样本。")
    else:
        lines.append(f"共 {data.get('count', len(records))} 条样本（展示前 {min(5, len(records))}）：")
        lines.append("")
        for offset, record in enumerate(records[:5], 1):
            lines.append(
                f"{offset}. {record.get('order_id')} · {record.get('category')} · {record.get('channel')} · "
                f"{record.get('province')} · {record.get('order_date')} · GMV={record.get('gmv')}"
            )
    lines += ["", f"_evidence: `{_fp(sample)}`_"]
    return "\n".join(lines)


def _rate_for(tool_results: list[dict[str, Any]], group_by: str, key: str) -> Any:
    stats = _pick(tool_results, "query_stats", group_by=group_by, metric="return_rate")
    rows = ((stats or {}).get("data") or {}).get("rows") or []
    for row in rows:
        if str(row.get("key")) == key:
            return row.get("value")
    if rows:
        return rows[0].get("value")
    return None


def _build_focus(tool_results: list[dict[str, Any]], user_text: str) -> str:
    named = _named_focus(user_text)
    if not named:
        return _build_report(tool_results, {"time_window": {"label": "当前窗口"}})
    group_by, key = named
    label = dict(_DIMS).get(group_by, group_by)
    rate = _rate_for(tool_results, group_by, key)
    rate_text = f"退货率 {rate}%，" if rate is not None else ""
    sample = _pick(tool_results, "sample_records") or {}
    records = (sample.get("data") or {}).get("records") or []
    lines = [
        f"**{key}** {rate_text}建议先看这个{label}。",
        "",
    ]
    if not records:
        lines.append("没有抽到这个范围的样本。")
    else:
        lines.append("样本：")
        for record in records[:5]:
            lines.append(
                f"- {record.get('order_id')} · {record.get('category')} · {record.get('channel')} · "
                f"{record.get('province')}"
            )
    lines += ["", f"_evidence: `{_fp(sample)}`_"]
    return "\n".join(lines)


def stub_decide(
    *,
    messages: list[dict[str, Any]],
    allowed_tools: list[str],
    tool_results: list[dict[str, Any]],
    bindings: dict[str, Any],
) -> dict[str, Any]:
    user_text = _last_user(messages)
    intent = _intent(user_text)
    if intent == "drill":
        plan = _drill_plan(bindings, user_text, tool_results)
    elif intent == "focus":
        plan = _focus_plan(bindings, user_text)
    else:
        plan = _stats_plan(bindings)
    allowed = set(allowed_tools or [])
    plan = [call for call in plan if call["name"] in allowed]
    have = _have(tool_results)
    missing = [call for call in plan if _call_key(call["name"], call.get("arguments")) not in have]
    if missing:
        return {"type": "tool_calls", "calls": missing}
    if intent == "drill":
        return {"type": "final", "content": _build_drill(tool_results, user_text)}
    if intent == "focus" and _named_focus(user_text):
        return {"type": "final", "content": _build_focus(tool_results, user_text)}
    return {"type": "final", "content": _build_report(tool_results, bindings)}
