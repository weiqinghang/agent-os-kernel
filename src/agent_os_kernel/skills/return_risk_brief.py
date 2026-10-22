"""return_risk_brief — DEFERRED demo; static registry stub only."""
from __future__ import annotations

from typing import Any

SKILL: dict[str, Any] = {
    "name": "return_risk_brief",
    "version": "0.0.1",
    "status": "deferred",
    "system_prompt": (
        "你是退货风险简报助理（本切片未启用演示）。"
        "只用工具数字；禁止臆造。"
    ),
    "allowed_tools": [
        "list_datasets",
        "describe_dataset",
        "query_stats",
        "search_records",
        "sample_records",
    ],
    "hitl_kinds": [],
    "default_bindings": {
        "dataset": "ecommerce_sales",
        "time_window": {"label": "近90天", "start": "2025-07-25", "end": "2025-10-22"},
    },
}


def get_skill() -> dict[str, Any]:
    return SKILL


def stub_decide(
    *,
    messages: list[dict[str, Any]],
    allowed_tools: list[str],
    tool_results: list[dict[str, Any]],
    bindings: dict[str, Any],
) -> dict[str, Any]:
    del messages, allowed_tools, tool_results, bindings
    return {
        "type": "final",
        "content": "（return_risk_brief 本切片已推迟，未开放演示。请使用 insight_report。）",
    }
