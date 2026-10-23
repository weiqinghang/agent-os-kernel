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
        " 退货不要用名叫 return_rate 的指标。query_stats 的 metrics 用"
        " {\"function\":\"avg\",\"field\":\"returned\",\"alias\":\"avg_returned\"}"
        "（要笔数时再加 {\"function\":\"sum\",\"field\":\"returned\",\"alias\":\"sum_returned\"}）；"
        " group_by 用 [\"channel\"]、[\"category\"] 或 [\"province\"]；"
        " 时间窗写进 filters 的 order_date gte/lte。"
        " 行上的 avg_returned 是 0 到 1 的比例，写成百分数后按从高到低给出 3 到 5 条。"
        " sample_records / search_records 要带非空 return_fields"
        "（order_id、sku、category、channel、province、order_date、qty、gmv、returned）；"
        "缺省时网关会补上这组字段。用户要看第 N 条依据，或只看某个渠道、类目、省时，"
        "必须先调用 sample_records：filters 用该维度 eq，并带上 order_date 时间窗。"
        "没有这次 sample_records 或 search_records 结果时，禁止直接 final。"
        "样本行在工具结果的 rows 或 records 里（有的在 result.rows），"
        "final 至少列出一条，渠道、类目或省与该条风险一致。"
        "只有该时间窗确实没有行时，才说明没有抽到样本。"
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


_RETURN_METRICS: list[dict[str, str]] = [
    {"function": "avg", "field": "returned", "alias": "avg_returned"},
]


def _window_filters(bindings: dict[str, Any]) -> list[dict[str, Any]]:
    window = bindings.get("time_window") or {}
    filters: list[dict[str, Any]] = []
    if not isinstance(window, dict):
        return filters
    if window.get("start"):
        filters.append({"field": "order_date", "operator": "gte", "value": window["start"]})
    if window.get("end"):
        filters.append({"field": "order_date", "operator": "lte", "value": window["end"]})
    return filters


def _stats_arguments(bindings: dict[str, Any], group_by: str) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "group_by": [group_by],
        "metrics": [dict(item) for item in _RETURN_METRICS],
        "limit": 20,
    }
    filters = _window_filters(bindings)
    if filters:
        arguments["filters"] = filters
    return arguments


def _sample_arguments(bindings: dict[str, Any], group_by: str, key: str) -> dict[str, Any]:
    filters = _window_filters(bindings)
    filters.append({"field": group_by, "operator": "eq", "value": key})
    return {"limit": 5, "filters": filters}


def _window_label(bindings: dict[str, Any]) -> str:
    window = bindings.get("time_window") or {}
    if isinstance(window, dict) and window.get("label"):
        return str(window["label"])
    return "近7天"


_LAST_30_LABELS = frozenset({"近30天", "last-30", "last30"})


def _window_token(bindings: dict[str, Any]) -> str:
    window = bindings.get("time_window") if isinstance(bindings, dict) else None
    if isinstance(window, str):
        raw = window
    elif isinstance(window, dict):
        raw = window.get("label") or window.get("id") or window.get("key") or window.get("preset") or ""
    else:
        raw = ""
    return str(raw).strip().lower().replace("_", "-").replace(" ", "")


def _is_last_30(bindings: dict[str, Any]) -> bool:
    """Active window is already the 近30天 / last-30 binding."""
    return _window_token(bindings) in _LAST_30_LABELS


def _closing_line(bindings: dict[str, Any]) -> str:
    ask = "可追问「第1条依据」"
    if _is_last_30(bindings):
        return f"{ask}。"
    return f"{ask}，或把时间窗改成近30天后再出一版。"


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


def _group_by_of(arguments: dict[str, Any]) -> str:
    group_by = arguments.get("group_by")
    if isinstance(group_by, list) and group_by:
        first = group_by[0]
        if isinstance(first, dict):
            return str(first.get("field") or "")
        return str(first)
    if isinstance(group_by, str):
        return group_by
    return ""


def _is_return_query(arguments: dict[str, Any]) -> bool:
    metric = arguments.get("metric")
    if isinstance(metric, str) and metric.strip() in {"return_rate", "avg_returned", "sum_returned"}:
        return True
    metrics = arguments.get("metrics")
    if not isinstance(metrics, list):
        return False
    for item in metrics:
        if isinstance(item, str) and item.strip() in {"return_rate", "avg_returned", "sum_returned"}:
            return True
        if not isinstance(item, dict):
            continue
        field = item.get("field")
        function = item.get("function")
        alias = item.get("alias")
        if field in {"returned", "return_rate"} and function in {"avg", "sum"}:
            return True
        if alias in {"return_rate", "avg_returned", "sum_returned"}:
            return True
        if function == "return_rate":
            return True
    return False


def _return_stats(tool_results: list[dict[str, Any]], group_by: str) -> dict[str, Any] | None:
    for item in tool_results:
        if item.get("name") != "query_stats":
            continue
        arguments = item.get("arguments") or {}
        if _group_by_of(arguments) == group_by and _is_return_query(arguments):
            return item
    return None


def _stat_rows(stats: dict[str, Any] | None) -> list[dict[str, Any]]:
    data = (stats or {}).get("data") or {}
    if not isinstance(data, dict):
        return []
    for key in ("rows", "groups", "items"):
        rows = data.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    nested = data.get("result")
    if isinstance(nested, dict):
        for key in ("rows", "groups", "items"):
            rows = nested.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def _rate_source(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("metrics", "values"):
        nested = row.get(key)
        if isinstance(nested, dict) and any(name in nested for name in ("avg_returned", "return_rate", "value")):
            return nested
    return row


def _row_rate(row: dict[str, Any]) -> Any:
    source = _rate_source(row)
    for key in ("avg_returned", "return_rate", "value"):
        if source.get(key) is not None:
            return source[key]
    return None


def _row_dim(row: dict[str, Any], group_by: str) -> str | None:
    if row.get("key") not in (None, ""):
        return str(row["key"])
    for bucket in ("dimensions", "group"):
        nested = row.get(bucket)
        if isinstance(nested, dict):
            if group_by and nested.get(group_by) not in (None, ""):
                return str(nested[group_by])
            for field in ("channel", "category", "province"):
                if nested.get(field) not in (None, ""):
                    return str(nested[field])
    if group_by and row.get(group_by) not in (None, ""):
        return str(row[group_by])
    for field in ("channel", "category", "province"):
        if row.get(field) not in (None, ""):
            return str(row[field])
    return None


def _format_rate(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if 0 <= number <= 1:
        number *= 100
    rounded = round(number, 2)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.2f}".rstrip("0").rstrip(".")


def _format_gmv(value: Any) -> str:
    """Money-like sample GMV: 2 decimal places, then drop a long float tail."""
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return text
        try:
            number = float(text)
        except ValueError:
            return text
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        return str(value)
    if number != number or number in (float("inf"), float("-inf")):
        return str(value).strip() if isinstance(value, str) else str(value)
    rounded = round(number, 2)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.2f}".rstrip("0").rstrip(".")


def _ranked(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for group_by, label in _DIMS:
        stats = _return_stats(tool_results, group_by)
        best: dict[str, Any] | None = None
        for row in _stat_rows(stats):
            key = _row_dim(row, group_by)
            raw = _row_rate(row)
            if not key or raw is None:
                continue
            rate = _as_float(raw)
            if best is not None and rate <= best["rate"]:
                continue
            best = {
                "group_by": group_by,
                "label": label,
                "key": key,
                "value": raw,
                "fingerprint": _fp(stats),
                "rate": rate,
                "count": row.get("count"),
                "returned_sum": row.get("sum_returned"),
            }
        if best:
            found.append(best)
    found.sort(key=lambda item: item["rate"], reverse=True)
    return found[:5]


def _stats_plan(bindings: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"name": "list_datasets", "arguments": {}},
        {"name": "describe_dataset", "arguments": dict(_tw_args(bindings))},
        {"name": "query_stats", "arguments": _stats_arguments(bindings, "channel")},
        {"name": "query_stats", "arguments": _stats_arguments(bindings, "category")},
        {"name": "query_stats", "arguments": _stats_arguments(bindings, "province")},
    ]


def _drill_plan(
    bindings: dict[str, Any],
    user_text: str,
    tool_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    plan = [
        {"name": "query_stats", "arguments": _stats_arguments(bindings, group_by)}
        for group_by, _label in _DIMS
    ]
    index = _finding_index(user_text)
    ranked = _ranked(tool_results)
    if index and 1 <= index <= len(ranked):
        row = ranked[index - 1]
        plan.append(
            {
                "name": "sample_records",
                "arguments": _sample_arguments(bindings, row["group_by"], row["key"]),
            }
        )
        return plan
    named = _named_focus(user_text)
    if named and index is None:
        group_by, key = named
        plan.append(
            {
                "name": "sample_records",
                "arguments": _sample_arguments(bindings, group_by, key),
            }
        )
    return plan


def _focus_plan(bindings: dict[str, Any], user_text: str) -> list[dict[str, Any]]:
    named = _named_focus(user_text)
    if not named:
        return _stats_plan(bindings)
    group_by, key = named
    return [
        {"name": "query_stats", "arguments": _stats_arguments(bindings, group_by)},
        {"name": "sample_records", "arguments": _sample_arguments(bindings, group_by, key)},
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
        extra = ""
        if row.get("returned_sum") is not None and row.get("count") not in (None, ""):
            extra = f"（{row['count']} 单里退货 {row['returned_sum']}）"
        lines.append(
            f"{index}. **{row['label']}退货偏高**：**{row['key']}** 退货率 {_format_rate(row['value'])}%{extra}，建议先看。"
        )
        lines.append(f"   - evidence: `{row['fingerprint']}`")
        lines.append("")
    lines.append(_closing_line(bindings))
    return "\n".join(lines)


_SAMPLE_FIELDS = (
    "order_id",
    "sku",
    "category",
    "channel",
    "province",
    "order_date",
    "qty",
    "gmv",
    "returned",
)
_RECORD_LIST_KEYS = ("records", "rows", "items")
_RECORD_COUNT_KEYS = ("count", "row_count", "total", "returned_row_count")


def _eq_filters(arguments: dict[str, Any]) -> dict[str, Any]:
    filters = arguments.get("filters")
    found: dict[str, Any] = {}
    if isinstance(filters, dict):
        clauses = filters.get("clauses") or filters.get("conditions")
        if isinstance(clauses, list):
            filters = clauses
        else:
            for key, value in filters.items():
                if key not in {"op", "clauses", "operator", "conditions"}:
                    found[str(key)] = value
            return found
    if isinstance(filters, list):
        for clause in filters:
            if not isinstance(clause, dict) or not clause.get("field"):
                continue
            op = clause.get("operator") or clause.get("op") or "eq"
            if op == "eq":
                found[str(clause["field"])] = clause.get("value")
    return found


def _column_names(columns: list[Any]) -> list[str]:
    names: list[str] = []
    for col in columns:
        if isinstance(col, str) and col.strip():
            names.append(col.strip())
            continue
        if isinstance(col, dict):
            label = col.get("name") or col.get("field")
            if isinstance(label, str) and label.strip():
                names.append(label.strip())
    return names


def _coerce_record(row: Any, columns: list[Any] | None) -> dict[str, Any] | None:
    if isinstance(row, dict):
        if any(row.get(field) not in (None, "") for field in _SAMPLE_FIELDS):
            return row
        for key in ("record", "fields", "values", "data"):
            nested = row.get(key)
            if isinstance(nested, dict):
                merged = dict(nested)
                for field in _SAMPLE_FIELDS:
                    if row.get(field) not in (None, "") and field not in merged:
                        merged[field] = row[field]
                return merged
        return row
    if isinstance(row, (list, tuple)) and columns:
        names = _column_names(columns)
        if not names:
            return None
        return {names[index]: row[index] for index in range(min(len(names), len(row)))}
    return None


def _list_records(data: dict[str, Any]) -> list[dict[str, Any]] | None:
    columns = data.get("columns") or data.get("fields") or data.get("return_fields")
    if not isinstance(columns, list):
        columns = None
    saw_list = False
    for key in _RECORD_LIST_KEYS:
        raw = data.get(key)
        if not isinstance(raw, list):
            continue
        saw_list = True
        parsed = [
            item
            for item in (_coerce_record(row, columns) for row in raw)
            if isinstance(item, dict)
        ]
        if parsed:
            return parsed
    if saw_list:
        return []
    return None


def _record_rows(sample: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Sample payload. Fake DAS uses records; live DAS uses rows (sometimes under result)."""
    data = (sample or {}).get("data") or {}
    if not isinstance(data, dict):
        return []
    direct = _list_records(data)
    if direct:
        return direct
    nested = data.get("result")
    if isinstance(nested, dict):
        inner = _list_records(nested)
        if inner:
            return inner
    return direct or []


def _sample_total(sample: dict[str, Any] | None, records: list[dict[str, Any]]) -> int:
    data = (sample or {}).get("data") or {}
    blobs: list[Any] = [data]
    if isinstance(data, dict) and isinstance(data.get("result"), dict):
        blobs.append(data["result"])
    for blob in blobs:
        if not isinstance(blob, dict):
            continue
        for key in _RECORD_COUNT_KEYS:
            value = blob.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                return value
            if isinstance(value, float) and value.is_integer():
                return int(value)
    return len(records)


def _with_filter_defaults(
    records: list[dict[str, Any]],
    arguments: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    flat = _eq_filters(arguments or {})
    if not flat:
        return records
    filled: list[dict[str, Any]] = []
    for record in records:
        item = dict(record)
        for field, value in flat.items():
            if item.get(field) in (None, ""):
                item[field] = value
        filled.append(item)
    return filled


def _aligned(
    records: list[dict[str, Any]],
    group_by: str | None,
    key: str | None,
) -> list[dict[str, Any]]:
    if not records:
        return []
    if not group_by or key is None:
        return records
    matched = [row for row in records if str(row.get(group_by) or "") == str(key)]
    if matched:
        return matched
    if all(row.get(group_by) in (None, "") for row in records):
        return records
    return []


def _sample_for(
    tool_results: list[dict[str, Any]],
    group_by: str | None = None,
    key: str | None = None,
) -> dict[str, Any]:
    candidates = [
        item
        for item in tool_results
        if item.get("name") in ("sample_records", "search_records")
    ]
    if not candidates:
        return {}

    def fits(item: dict[str, Any]) -> bool:
        if not group_by or key is None:
            return False
        return _eq_filters(item.get("arguments") or {}).get(group_by) == key

    def usable(item: dict[str, Any]) -> list[dict[str, Any]]:
        rows = _with_filter_defaults(_record_rows(item), item.get("arguments") or {})
        return _aligned(rows, group_by, key)

    filtered = [item for item in candidates if fits(item) and usable(item)]
    if filtered:
        return filtered[-1]
    any_rows = [item for item in candidates if usable(item)]
    if any_rows:
        return any_rows[-1]
    matched = [item for item in candidates if fits(item)]
    if matched:
        return matched[-1]
    return candidates[-1]


def _visible_records(
    sample: dict[str, Any],
    group_by: str | None,
    key: str | None,
) -> list[dict[str, Any]]:
    rows = _with_filter_defaults(_record_rows(sample), sample.get("arguments") or {})
    return _aligned(rows, group_by, key)


def _hotspot(
    intent: str,
    user_text: str,
    tool_results: list[dict[str, Any]],
) -> tuple[str, str] | None:
    if intent == "drill":
        index = _finding_index(user_text)
        ranked = _ranked(tool_results)
        if index and 1 <= index <= len(ranked):
            row = ranked[index - 1]
            return str(row["group_by"]), str(row["key"])
        if index is None:
            return _named_focus(user_text)
        return None
    return _named_focus(user_text)


def _has_aligned_rows(
    tool_results: list[dict[str, Any]],
    hotspot: tuple[str, str],
) -> bool:
    group_by, key = hotspot
    sample = _sample_for(tool_results, group_by, key)
    return bool(_visible_records(sample, group_by, key))


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
    group_by = row["group_by"] if row else None
    key = row["key"] if row else None
    if row:
        lines.append(f"这条风险讲的是{row['label']} **{row['key']}**。下面只列出「{row['key']}」的样本。")
        lines.append("")
    sample = _sample_for(tool_results, group_by, key)
    records = _visible_records(sample, group_by, key)
    if not records:
        lines.append("没有抽到与这条风险对齐的样本。这个时间窗里没有这类行。")
    else:
        total = _sample_total(sample, records)
        lines.append(f"共 {total} 条样本（展示前 {min(5, len(records))}）：")
        lines.append("")
        for offset, record in enumerate(records[:5], 1):
            lines.append(
                f"{offset}. {record.get('order_id')} · {record.get('category')} · {record.get('channel')} · "
                f"{record.get('province')} · {record.get('order_date')} · GMV={_format_gmv(record.get('gmv'))}"
            )
    lines += ["", f"_evidence: `{_fp(sample)}`_"]
    return "\n".join(lines)


def _rate_for(tool_results: list[dict[str, Any]], group_by: str, key: str) -> Any:
    rows = _stat_rows(_return_stats(tool_results, group_by))
    for row in rows:
        if _row_dim(row, group_by) == key:
            return _row_rate(row)
    if rows:
        return _row_rate(rows[0])
    return None


def _build_focus(tool_results: list[dict[str, Any]], user_text: str) -> str:
    named = _named_focus(user_text)
    if not named:
        return _build_report(tool_results, {"time_window": {"label": "当前窗口"}})
    group_by, key = named
    label = dict(_DIMS).get(group_by, group_by)
    rate = _rate_for(tool_results, group_by, key)
    rate_text = f"退货率 {_format_rate(rate)}%，" if rate is not None else ""
    sample = _sample_for(tool_results, group_by, key)
    records = _visible_records(sample, group_by, key)
    lines = [
        f"**{key}** {rate_text}建议先看这个{label}。",
        "",
    ]
    if not records:
        lines.append("没有抽到这个范围的样本。这个时间窗里没有这类行。")
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


def coerce_decision(
    *,
    decision: dict[str, Any],
    messages: list[dict[str, Any]],
    allowed_tools: list[str],
    tool_results: list[dict[str, Any]],
    bindings: dict[str, Any],
) -> dict[str, Any]:
    """Live final for drill/focus still has to sample, then show rows or records."""
    if decision.get("type") != "final":
        return decision
    user_text = _last_user(messages)
    intent = _intent(user_text)
    if intent not in ("drill", "focus"):
        return decision
    if intent == "focus" and _named_focus(user_text) is None:
        return decision
    hotspot = _hotspot(intent, user_text, tool_results)
    if hotspot and _has_aligned_rows(tool_results, hotspot):
        if intent == "drill":
            return {"type": "final", "content": _build_drill(tool_results, user_text)}
        return {"type": "final", "content": _build_focus(tool_results, user_text)}
    stub = stub_decide(
        messages=messages,
        allowed_tools=allowed_tools,
        tool_results=tool_results,
        bindings=bindings,
    )
    if stub.get("type") == "tool_calls" and stub.get("calls"):
        return {"type": "tool_calls", "calls": list(stub["calls"])}
    if stub.get("type") == "final" and isinstance(stub.get("content"), str):
        return {"type": "final", "content": stub["content"]}
    return decision
