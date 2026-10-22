"""Read-only policy; outbound actions require final_confirm."""
from __future__ import annotations

READ_ONLY_TOOLS = {
    "list_datasets",
    "describe_dataset",
    "query_stats",
    "search_records",
    "sample_records",
}

OUTBOUND_ACTIONS = {"email_send", "slack_post", "export_external"}


def allow_tool(name: str) -> bool:
    return name in READ_ONLY_TOOLS


def requires_final_confirm(action: str) -> bool:
    return action in OUTBOUND_ACTIONS
