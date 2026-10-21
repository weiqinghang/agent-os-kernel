"""Session store (Session ≠ Run)."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from . import obs


def create_session(meta: dict[str, Any] | None = None) -> dict[str, Any]:
    obs.init_db()
    sid = "ses_" + uuid.uuid4().hex[:12]
    now = time.time()
    with obs._conn() as c:
        c.execute(
            "INSERT INTO sessions(id, created_at, meta) VALUES (?,?,?)",
            (sid, now, json.dumps(meta or {}, ensure_ascii=False)),
        )
    return {"id": sid, "created_at": now}


def get_session(session_id: str) -> dict[str, Any] | None:
    obs.init_db()
    with obs._conn() as c:
        row = c.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        return None
    return {"id": row["id"], "created_at": row["created_at"], "meta": json.loads(row["meta"] or "{}")}


def add_message(session_id: str, role: str, content: str, run_id: str | None = None) -> None:
    with obs._conn() as c:
        c.execute(
            "INSERT INTO messages(session_id, role, content, created_at, run_id) VALUES (?,?,?,?,?)",
            (session_id, role, content, time.time(), run_id),
        )


def list_messages(session_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with obs._conn() as c:
        rows = c.execute(
            "SELECT role, content, created_at, run_id FROM messages WHERE session_id=? ORDER BY id LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]
