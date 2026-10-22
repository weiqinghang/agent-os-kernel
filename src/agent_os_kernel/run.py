"""Run state machine: created → acting ⇄ awaiting_hitl → completed|failed|cancelled.

A failed Run is not restarted in place. Retry creates another Run.
"""
from __future__ import annotations

import copy
import json
import re
import threading
import time
import uuid
from typing import Any

from . import obs

STATES = {"created", "acting", "awaiting_hitl", "completed", "failed", "cancelled"}
TERMINAL_STATES = {"completed", "failed", "cancelled"}
CANCELLED_RESULT = "已停止"
RETRY_TEXT = "请重试上一轮。"
_REASON_LIMIT = 500
_FALLBACK_REASON = "上一轮没有完成。"
# Names that must not ride along in a Session message. Lists of other snake_case
# names are removed even when they are not in this tuple.
_TOOL_NAMES = (
    "describe_tag_hierarchies",
    "describe_dataset",
    "describe_fields",
    "sample_records",
    "search_records",
    "query_stats",
    "list_datasets",
)
_TRACEBACK = re.compile(r"(?i)\btraceback\b")
_THINK = re.compile(r"(?is)<\s*think\s*>.*?<\s*/\s*think\s*>")
_COT_LINE = re.compile(
    r"(?i)(?:思维链|chain[- ]of[- ]thought|hidden-cot|\bCoT\b)\s*[:：]?[^\n]*"
)
_SECRET = re.compile(
    r"(?i)\b(?:api[_-]?key|secret|password|passwd|token|bearer|authorization)\b\s*[:=]\s*\S+"
)
_SK = re.compile(r"(?i)\bsk-[A-Za-z0-9_\-]{4,}\b")
_BEARER = re.compile(r"(?i)\bBearer\s+\S+")
_MODE = re.compile(r"(?i)\bmode\s*[:=]\s*\S+")
_MODE_WORD = re.compile(r"(?i)\b(?:stub|live)\b")
_TOOL_LIST = re.compile(
    r"(?i)\b[a-z][a-z0-9_]{2,}(?:\s*[,、]\s*[a-z][a-z0-9_]{2,})+\b"
)
_FRAME = re.compile(r'(?i)\bFile\s+".*?",\s*line\s+\d+')
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([，,。])")

_state_lock = threading.Lock()
_flag_lock = threading.Lock()
_cancel_flags: dict[str, threading.Event] = {}


def _strip_wrapped(text: str, open_ch: str, close_ch: str) -> str:
    out: list[str] = []
    depth = 0
    for ch in text:
        if ch == open_ch:
            depth += 1
        elif ch == close_ch and depth:
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def redact_failure_reason(result: str | None) -> str:
    """Short human reason safe to store in a Session message.

    The source is the failed Run's result. Stacks, secrets, tool argument
    payloads, tool-name lists, mode, and chain-of-thought are left out.
    """
    text = str(result or "")
    text = _TRACEBACK.split(text, maxsplit=1)[0]
    text = _THINK.sub(" ", text)
    text = _COT_LINE.sub(" ", text)
    text = _strip_wrapped(text, "{", "}")
    text = _strip_wrapped(text, "[", "]")
    text = _SECRET.sub(" ", text)
    text = _SK.sub(" ", text)
    text = _BEARER.sub(" ", text)
    text = _MODE.sub(" ", text)
    text = _MODE_WORD.sub(" ", text)
    for name in _TOOL_NAMES:
        text = re.sub(rf"(?i)\b{re.escape(name)}\b", " ", text)
    text = _TOOL_LIST.sub(" ", text)
    text = _FRAME.sub(" ", text)
    text = " ".join(text.split())
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text).strip(" ，,")
    if not text:
        text = _FALLBACK_REASON
    if len(text) > _REASON_LIMIT:
        text = text[: _REASON_LIMIT - 1] + "…"
    return text


def retry_message(result: str | None, text: str | None) -> str:
    """User turn for a retry: redacted reason, then the spoken line."""
    spoken = (text or "").strip() or RETRY_TEXT
    return f"{redact_failure_reason(result)}\n\n{spoken}"


def create_run(
    session_id: str,
    *,
    context_bindings: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    obs.init_db()
    rid = "run_" + uuid.uuid4().hex[:12]
    now = time.time()
    stored_bindings = copy.deepcopy(context_bindings or {})
    stored_meta = copy.deepcopy(meta or {})
    cb = json.dumps(stored_bindings, ensure_ascii=False)
    meta_json = json.dumps(stored_meta, ensure_ascii=False)
    with obs._conn() as c:
        c.execute(
            """
            INSERT INTO runs(
                id, session_id, state, created_at, updated_at, context_bindings, meta
            )
            VALUES (?,?,?,?,?,?,?)
            """,
            (rid, session_id, "created", now, now, cb, meta_json),
        )
    obs.emit(rid, "run_created", {"session_id": session_id, "bindings": stored_bindings})
    return {
        "id": rid,
        "session_id": session_id,
        "state": "created",
        "context_bindings": stored_bindings,
        "meta": stored_meta,
    }


def _cancel_flag(run_id: str) -> threading.Event:
    with _flag_lock:
        flag = _cancel_flags.get(run_id)
        if flag is None:
            flag = threading.Event()
            _cancel_flags[run_id] = flag
        return flag


def cancel_requested(run_id: str) -> bool:
    """True after request_cancel has marked this Run. Safe to call from the turn thread."""
    with _flag_lock:
        flag = _cancel_flags.get(run_id)
    return bool(flag is not None and flag.is_set())


def request_cancel(run_id: str) -> dict[str, Any]:
    """Move a non-terminal Run to cancelled.

    Already completed, failed, or cancelled runs are left unchanged
    (`applied` is false). The turn thread sees the flag and stops between steps.
    """
    with _state_lock:
        with obs._conn() as c:
            row = c.execute("SELECT state, result FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError(run_id)
            previous = row["state"]
            if previous in TERMINAL_STATES:
                return {
                    "applied": False,
                    "state": previous,
                    "result": row["result"],
                    "previous": previous,
                }
            _cancel_flag(run_id).set()
            c.execute(
                """
                UPDATE runs SET state=?, updated_at=?, result=?
                WHERE id=? AND state=?
                """,
                ("cancelled", time.time(), CANCELLED_RESULT, run_id, previous),
            )
        obs.emit(run_id, "state", {"state": "cancelled"})
    return {
        "applied": True,
        "state": "cancelled",
        "result": CANCELLED_RESULT,
        "previous": previous,
    }


def set_state(
    run_id: str,
    state: str,
    *,
    resume_token: str | None = None,
    hitl_kind: str | None = None,
    result: str | None = None,
) -> bool:
    """Persist a state. A cancelled Run stays cancelled; the write is skipped."""
    if state not in STATES:
        raise ValueError(state)
    with _state_lock:
        with obs._conn() as c:
            row = c.execute("SELECT state FROM runs WHERE id=?", (run_id,)).fetchone()
            if row and row["state"] == "cancelled" and state != "cancelled":
                return False
            c.execute(
                """
                UPDATE runs SET state=?, updated_at=?, resume_token=COALESCE(?, resume_token),
                  hitl_kind=COALESCE(?, hitl_kind), result=COALESCE(?, result)
                WHERE id=?
                """,
                (state, time.time(), resume_token, hitl_kind, result, run_id),
            )
        obs.emit(run_id, "state", {"state": state, "hitl_kind": hitl_kind})
    return True


def get_run(run_id: str) -> dict[str, Any] | None:
    with obs._conn() as c:
        row = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    raw = d.get("context_bindings")
    if isinstance(raw, str):
        try:
            d["context_bindings"] = json.loads(raw or "{}")
        except json.JSONDecodeError:
            d["context_bindings"] = {}
    elif raw is None:
        d["context_bindings"] = {}
    d["meta"] = _load_meta(d.get("meta"))
    return d


def _load_meta(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def latest_completed_bindings(session_id: str) -> dict[str, Any] | None:
    with obs._conn() as c:
        row = c.execute(
            """
            SELECT context_bindings FROM runs
            WHERE session_id=? AND state='completed'
            ORDER BY created_at DESC LIMIT 1
            """,
            (session_id,),
        ).fetchone()
    if not row:
        return None
    raw = row["context_bindings"]
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
