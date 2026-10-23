"""Session store (Session ≠ Run). meta.bindings = thread defaults."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from . import agents, context, obs


def create_session(meta: dict[str, Any] | None = None) -> dict[str, Any]:
    obs.init_db()
    sid = "ses_" + uuid.uuid4().hex[:12]
    now = time.time()
    meta = dict(meta or {})
    # Blank id stays on the insight row. A known id is stored once and not rewritten later.
    agent = agents.get(meta.get("agent_id"))
    meta["agent_id"] = agent["id"]
    # Normalize bindings under meta.bindings
    if "bindings" in meta:
        meta["bindings"] = context.coerce_partial(meta.get("bindings") or {})
    elif any(k in meta for k in ("skill", "dataset", "time_window", "project_id", "tenant_id")):
        meta["bindings"] = context.coerce_partial(meta)
    with obs._conn() as c:
        c.execute(
            "INSERT INTO sessions(id, created_at, meta) VALUES (?,?,?)",
            (sid, now, json.dumps(meta, ensure_ascii=False)),
        )
    return {
        "id": sid,
        "created_at": now,
        "meta": meta,
        "agent_id": meta["agent_id"],
        "bindings": meta.get("bindings") or {},
    }


def get_session(session_id: str) -> dict[str, Any] | None:
    obs.init_db()
    with obs._conn() as c:
        row = c.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        return None
    meta = json.loads(row["meta"] or "{}")
    bindings = context.session_bindings_from_meta(meta)
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "meta": meta,
        "agent_id": meta.get("agent_id") or agents.DEFAULT_AGENT_ID,
        "bindings": bindings,
    }


def set_bindings(session_id: str, partial: dict[str, Any], *, replace: bool = False) -> dict[str, Any]:
    """Update Session.meta.bindings (thread defaults). agent_id stays as stored."""
    s = get_session(session_id)
    if not s:
        raise KeyError("session not found")
    if isinstance(partial, dict) and "agent_id" in partial:
        raise ValueError("agent_id is immutable")
    meta = dict(s.get("meta") or {})
    current = context.session_bindings_from_meta(meta)
    if replace:
        new_b = context.coerce_partial(partial)
    else:
        new_b = context.deep_merge(current, context.coerce_partial(partial))
    meta["bindings"] = new_b
    with obs._conn() as c:
        c.execute(
            "UPDATE sessions SET meta=? WHERE id=?",
            (json.dumps(meta, ensure_ascii=False), session_id),
        )
    return get_session(session_id)  # type: ignore[return-value]


def add_message(session_id: str, role: str, content: str, run_id: str | None = None) -> None:
    with obs._conn() as c:
        c.execute(
            "INSERT INTO messages(session_id, role, content, created_at, run_id) VALUES (?,?,?,?,?)",
            (session_id, role, content, time.time(), run_id),
        )


def list_messages(session_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Newest `limit` messages, still in chronological order.

    The broker length policy trims further. This window is what keeps the
    latest user turn available once a thread is longer than `limit`.
    """
    cap = limit if isinstance(limit, int) and limit > 0 else 50
    with obs._conn() as c:
        rows = c.execute(
            """
            SELECT role, content, created_at, run_id FROM messages
            WHERE session_id=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, cap),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def latest_run_id(session_id: str) -> str | None:
    with obs._conn() as c:
        row = c.execute(
            """
            SELECT id FROM runs WHERE session_id=? AND state='completed'
            ORDER BY created_at DESC LIMIT 1
            """,
            (session_id,),
        ).fetchone()
    return row["id"] if row else None
