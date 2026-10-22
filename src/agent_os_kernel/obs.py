"""Observability: append-only run_events in SQLite."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "kernel.db"


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                meta TEXT
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at REAL NOT NULL,
                run_id TEXT
            );
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                resume_token TEXT,
                hitl_kind TEXT,
                result TEXT,
                context_bindings TEXT
            );
            CREATE TABLE IF NOT EXISTS run_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                ts REAL NOT NULL,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            """
        )
        # migrate older DBs missing context_bindings
        cols = {r[1] for r in c.execute("PRAGMA table_info(runs)").fetchall()}
        if "context_bindings" not in cols:
            c.execute("ALTER TABLE runs ADD COLUMN context_bindings TEXT")


def emit(run_id: str, kind: str, payload: dict[str, Any] | None = None) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO run_events(run_id, ts, kind, payload) VALUES (?,?,?,?)",
            (run_id, time.time(), kind, json.dumps(payload or {}, ensure_ascii=False)),
        )


def list_events(run_id: str, limit: int = 100) -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute(
            "SELECT ts, kind, payload FROM run_events WHERE run_id=? ORDER BY id LIMIT ?",
            (run_id, limit),
        ).fetchall()
    out = []
    for r in rows:
        out.append({"ts": r["ts"], "kind": r["kind"], "payload": json.loads(r["payload"])})
    return out
