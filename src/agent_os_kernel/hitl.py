"""HITL kinds with resume_token; HITL_AUTO=1 auto-approves."""
from __future__ import annotations

import os
import uuid
from typing import Any

KINDS = {"user_input", "outline_confirm", "segment_confirm", "final_confirm"}


def auto_enabled() -> bool:
    return os.getenv("HITL_AUTO", "1") != "0"


def make_token(kind: str) -> dict[str, Any]:
    if kind not in KINDS:
        raise ValueError(kind)
    return {"kind": kind, "resume_token": "hitl_" + uuid.uuid4().hex[:16]}


def should_pause(kind: str) -> bool:
    if auto_enabled():
        return False
    return kind in KINDS
