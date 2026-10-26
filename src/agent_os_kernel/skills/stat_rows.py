"""Read query_stats rows from fake `{key, value}` and live dimension + metric columns."""
from __future__ import annotations

from typing import Any

_DIM_FALLBACK = ("channel", "category", "province")
_LIST_KEYS = ("rows", "groups", "items")

EM_DASH = "—"

_METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "sum_gmv": ("sum_gmv", "value"),
    "sum_qty": ("sum_qty", "value"),
    "avg_gmv": ("avg_gmv", "value"),
    "count": ("count", "value"),
    "return_rate": ("return_rate", "avg_returned", "value"),
    "avg_returned": ("avg_returned", "return_rate", "value"),
    "sum_returned": ("sum_returned", "value"),
}


def dash(value: Any) -> str:
    """Missing metrics render as an em dash, never the literal None."""
    if value is None:
        return EM_DASH
    return str(value)


def first_present(*values: Any, fallback: str = EM_DASH) -> Any:
    for value in values:
        if value is None or value == "":
            continue
        return value
    return fallback


def stat_dicts(data: Any) -> list[dict[str, Any]]:
    """Row dicts from rows/groups/items, including a nested result object."""
    if not isinstance(data, dict):
        return []
    for key in _LIST_KEYS:
        rows = data.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    nested = data.get("result")
    if isinstance(nested, dict):
        for key in _LIST_KEYS:
            rows = nested.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def row_dimension(row: dict[str, Any], group_by: str | None = None) -> str | None:
    if not isinstance(row, dict):
        return None
    if row.get("key") not in (None, ""):
        return str(row["key"])
    for bucket in ("dimensions", "group"):
        nested = row.get(bucket)
        if not isinstance(nested, dict):
            continue
        if group_by and nested.get(group_by) not in (None, ""):
            return str(nested[group_by])
        for field in _DIM_FALLBACK:
            if nested.get(field) not in (None, ""):
                return str(nested[field])
    if group_by and row.get(group_by) not in (None, ""):
        return str(row[group_by])
    for field in _DIM_FALLBACK:
        if row.get(field) not in (None, ""):
            return str(row[field])
    return None


def row_measure(row: dict[str, Any], metric: str | None = None) -> Any:
    if not isinstance(row, dict):
        return None
    keys = _METRIC_ALIASES.get(metric or "", (metric, "value") if metric else ("value",))
    sources: list[dict[str, Any]] = []
    for bucket in ("metrics", "values"):
        nested = row.get(bucket)
        if isinstance(nested, dict):
            sources.append(nested)
    sources.append(row)
    for key in keys:
        for source in sources:
            if key and source.get(key) is not None:
                return source[key]
    return None


def normalize_stat_row(
    row: dict[str, Any],
    *,
    group_by: str | None = None,
    metric: str | None = None,
) -> dict[str, Any]:
    return {"key": row_dimension(row, group_by), "value": row_measure(row, metric)}


def group_by_name(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        field = value.get("field") or value.get("name") or value.get("alias")
        return field.strip() if isinstance(field, str) else ""
    if isinstance(value, list):
        for item in value:
            name = group_by_name(item)
            if name:
                return name
    return ""


def _remember_metric(found: set[str], item: Any) -> None:
    if isinstance(item, str) and item.strip():
        found.add(item.strip())
        return
    if not isinstance(item, dict):
        return
    alias = item.get("alias") or item.get("name")
    if isinstance(alias, str) and alias.strip():
        found.add(alias.strip())
    function = item.get("function") or item.get("fn")
    field = item.get("field")
    if not isinstance(function, str) or not isinstance(field, str) or not function or not field:
        return
    found.add(f"{function}_{field}")
    if field == "returned" and function == "avg":
        found.update(("return_rate", "avg_returned"))


def metric_names(arguments: dict[str, Any] | None) -> set[str]:
    args = arguments or {}
    found: set[str] = set()
    _remember_metric(found, args.get("metric"))
    metrics = args.get("metrics")
    if isinstance(metrics, list):
        for item in metrics:
            _remember_metric(found, item)
    else:
        _remember_metric(found, metrics)
    return found


def stat_call_matches(arguments: dict[str, Any] | None, group_by: str, metric: str) -> bool:
    args = arguments or {}
    if group_by_name(args.get("group_by")) != group_by:
        return False
    names = metric_names(args)
    if metric in names:
        return True
    return metric == "return_rate" and "avg_returned" in names
