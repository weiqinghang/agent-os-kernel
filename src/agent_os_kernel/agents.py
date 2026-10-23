"""Static agent catalog. Two rows, loaded at import. Not a hot-plug console."""
from __future__ import annotations

import copy
from typing import Any

from .context import SEED_DATASET_ID

DEFAULT_AGENT_ID = "insight.report"

_INSIGHT_WINDOW = {"label": "近90天", "start": "2025-07-25", "end": "2025-10-22"}
_RETURN_WINDOW = {"label": "近7天", "start": "2025-10-15", "end": "2025-10-22"}

# Order is the startup table. Ids are stable; display names are what the terminal shows.
_RECORDS: tuple[dict[str, Any], ...] = (
    {
        "id": "insight.report",
        "display_name": "洞察报告助理",
        "skills": ["insight_report"],
        "mcps": ["das"],
        "default_bindings": {
            "dataset": SEED_DATASET_ID,
            "time_window": dict(_INSIGHT_WINDOW),
        },
    },
    {
        "id": "return_risk.brief",
        "display_name": "退货风险助手",
        "skills": ["return_risk_brief"],
        "mcps": ["das"],
        "default_bindings": {
            "dataset": SEED_DATASET_ID,
            "time_window": dict(_RETURN_WINDOW),
        },
    },
)

_BY_ID = {row["id"]: row for row in _RECORDS}


def get(agent_id: str | None) -> dict[str, Any]:
    """Return a copy. Blank id is the insight row, kept for older callers."""
    key = str(agent_id).strip() if agent_id else ""
    key = key or DEFAULT_AGENT_ID
    row = _BY_ID.get(key)
    if row is None:
        raise KeyError(f"unknown agent: {key}")
    return copy.deepcopy(row)


def list_agents() -> list[dict[str, Any]]:
    return [get(row["id"]) for row in _RECORDS]
