"""Kernel FastAPI entry — :8787."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import hitl, obs, run, session
from .skills import insight_report
from .tool_gateway import ToolGateway

ROOT = Path(__file__).resolve().parents[2]
UI_DIR = ROOT / "ui" / "chat"

app = FastAPI(title="agent-os-kernel", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    obs.init_db()
    os.environ.setdefault("HITL_AUTO", "1")


@app.get("/health")
def health() -> dict[str, Any]:
    das_ok = False
    try:
        import httpx

        r = httpx.get(os.getenv("FAKE_DAS_URL", "http://127.0.0.1:8788") + "/health", timeout=2.0)
        das_ok = r.status_code == 200
    except Exception:  # noqa: BLE001
        das_ok = False
    return {
        "status": "ok",
        "service": "agent-os-kernel",
        "das_reachable": das_ok,
        "hitl_auto": hitl.auto_enabled(),
        "llm": bool(
            os.getenv("MODEL_API_KEY") or os.getenv("XAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        ),
    }


class SessionCreate(BaseModel):
    meta: dict[str, Any] = Field(default_factory=dict)


@app.post("/sessions")
def create_session(body: SessionCreate | None = None) -> dict[str, Any]:
    return session.create_session((body.meta if body else None) or {})


@app.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    s = session.get_session(session_id)
    if not s:
        raise HTTPException(404, "session not found")
    s["messages"] = session.list_messages(session_id)
    return s


class MessageIn(BaseModel):
    text: str


@app.post("/sessions/{session_id}/messages")
def post_message(session_id: str, body: MessageIn) -> dict[str, Any]:
    s = session.get_session(session_id)
    if not s:
        raise HTTPException(404, "session not found")

    session.add_message(session_id, "user", body.text)
    r = run.create_run(session_id)

    prior = session.list_messages(session_id)
    # follow-up if session already has an assistant message before this user turn
    assistant_before = sum(1 for m in prior[:-1] if m["role"] == "assistant")
    is_followup = assistant_before > 0

    result = insight_report.run_insight(r["id"], body.text, is_followup=is_followup)
    md = result.get("markdown") or result.get("message") or ""
    session.add_message(session_id, "assistant", md, run_id=r["id"])

    return {
        "session_id": session_id,
        "run_id": r["id"],
        "status": result.get("status"),
        "markdown": md,
        "hitl": result.get("hitl"),
        "evidence": result.get("evidence"),
        "text": md,  # alias for UAT convenience
    }


class HitlResume(BaseModel):
    resume_token: str
    approve: bool = True
    text: str | None = None


@app.post("/runs/{run_id}/resume")
def resume_run(run_id: str, body: HitlResume) -> dict[str, Any]:
    rr = run.get_run(run_id)
    if not rr:
        raise HTTPException(404, "run not found")
    if rr["state"] != "awaiting_hitl":
        raise HTTPException(400, f"run not awaiting_hitl: {rr['state']}")
    if rr.get("resume_token") and rr["resume_token"] != body.resume_token:
        raise HTTPException(403, "bad resume_token")
    if not body.approve:
        run.set_state(run_id, "failed", result="HITL rejected")
        return {"status": "failed", "markdown": "已拒绝"}
    result = insight_report.resume_after_hitl(run_id, body.text or "请继续生成报告")
    md = result.get("markdown") or ""
    session.add_message(rr["session_id"], "assistant", md, run_id=run_id)
    return {"run_id": run_id, "status": result.get("status"), "markdown": md}


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    rr = run.get_run(run_id)
    if not rr:
        raise HTTPException(404, "run not found")
    rr["events"] = obs.list_events(run_id)
    return rr


@app.get("/tools")
def tools() -> dict[str, Any]:
    try:
        names = ToolGateway().list_tools()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)) from e
    return {"tools": names}


# Static chat UI
if UI_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    index_path = UI_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(404, "UI missing")
    return FileResponse(index_path)
