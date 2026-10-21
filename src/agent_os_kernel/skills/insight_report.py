"""insight_report skill — manifest only. Orchestrator + Model Broker drive the loop.

Skill MUST NOT call DAS directly; all tool use goes through Tool Gateway via orchestrator.
"""
from __future__ import annotations

from typing import Any

SKILL: dict[str, Any] = {
    "name": "insight_report",
    "version": "0.2.0",
    "system_prompt": (
        "你是电商销量洞察报告助理。只用工具返回的聚合数字写中文结论，"
        "每条发现必须带 evidence 指纹；禁止臆造数字或绕过工具网关。"
        "需要数据时先发 tool_calls（list_datasets / describe_dataset / query_stats），"
        "证据齐后再输出 final markdown。"
    ),
    "allowed_tools": [
        "list_datasets",
        "describe_dataset",
        "query_stats",
        "search_records",
        "sample_records",
    ],
    "hitl_kinds": ["outline_confirm"],
}


def get_skill() -> dict[str, Any]:
    return SKILL
