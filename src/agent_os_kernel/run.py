"""Run state machine: created → acting ⇄ awaiting_hitl → completed|failed|cancelled."""
from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

from . import obs

STATES = {"created", "acting", "awaiting_hitl", "completed", "failed", "cancelled"}
TERMINAL_STATES = {"completed", "failed", "cancelled"}
CANCELLED_RESULT = "已停止"

_state_lock = threading.Lock()
_flag_lock = threading.Lock()
_cancel_flags: dict[str, threading.Event] = {}


def create_run(
    session_id: str,
    *,
    context_bindings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    obs.init_db()
    rid = "run_" + uuid.uuid4().hex[:12]
    now = time.time()
    cb = json.dumps(context_bindings or {}, ensure_ascii=False)
    with obs._conn() as c:
        c.execute(
            """
            INSERT INTO runs(id, session_id, state, created_at, updated_at, context_bindings)
            VALUES (?,?,?,?,?,?)
            """,
            (rid, session_id, "created", now, now, cb),
        )
    obs.emit(rid, "run_created", {"session_id": session_id, "bindings": context_bindings or {}})
    return {
        "id": rid,
        "session_id": session_id,
        "state": "created",
        "context_bindings": context_bindings or {},
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
    return d


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
