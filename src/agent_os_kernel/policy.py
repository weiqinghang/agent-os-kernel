"""Read-only policy; outbound actions require final_confirm."""
from __future__ import annotations

from .tool_gateway import WHITELIST

# Same set as the gateway SoT (includes describe_fields / describe_tag_hierarchies).
READ_ONLY_TOOLS = set(WHITELIST)

OUTBOUND_ACTIONS = {"email_send", "slack_post", "export_external"}


def allow_tool(name: str) -> bool:
    return name in READ_ONLY_TOOLS


def requires_final_confirm(action: str) -> bool:
    return action in OUTBOUND_ACTIONS
