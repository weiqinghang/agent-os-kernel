"""Run state machine: created → acting ⇄ awaiting_hitl → completed|failed."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from . import obs

STATES = {"created", "acting", "awaiting_hitl", "completed", "failed"}


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


def set_state(
    run_id: str,
    state: str,
    *,
    resume_token: str | None = None,
    hitl_kind: str | None = None,
    result: str | None = None,
) -> None:
    if state not in STATES:
        raise ValueError(state)
    with obs._conn() as c:
        c.execute(
            """
            UPDATE runs SET state=?, updated_at=?, resume_token=COALESCE(?, resume_token),
              hitl_kind=COALESCE(?, hitl_kind), result=COALESCE(?, result)
            WHERE id=?
            """,
            (state, time.time(), resume_token, hitl_kind, result, run_id),
        )
    obs.emit(run_id, "state", {"state": state, "hitl_kind": hitl_kind})


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
