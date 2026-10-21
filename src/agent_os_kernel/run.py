"""Run state machine: created → acting ⇄ awaiting_hitl → completed|failed."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from . import obs

STATES = {"created", "acting", "awaiting_hitl", "completed", "failed"}


def create_run(session_id: str) -> dict[str, Any]:
    obs.init_db()
    rid = "run_" + uuid.uuid4().hex[:12]
    now = time.time()
    with obs._conn() as c:
        c.execute(
            "INSERT INTO runs(id, session_id, state, created_at, updated_at) VALUES (?,?,?,?,?)",
            (rid, session_id, "created", now, now),
        )
    obs.emit(rid, "run_created", {"session_id": session_id})
    return {"id": rid, "session_id": session_id, "state": "created"}


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
    return dict(row)
