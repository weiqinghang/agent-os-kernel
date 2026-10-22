"""Kernel FastAPI entry — :8787."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import context, hitl, model_broker, obs, orchestrator, run, session
from .skills import registry
from .tool_gateway import ToolGateway

ROOT = Path(__file__).resolve().parents[2]
UI_DIR = ROOT / "ui" / "chat"

app = FastAPI(title="agent-os-kernel", version="0.3.0")


@app.on_event("startup")
def _startup() -> None:
    obs.init_db()
    os.environ.setdefault("HITL_AUTO", "1")


@app.get("/health")
def health() -> dict[str, Any]:
    das_ok = ToolGateway().probe_health()
    return {
        "status": "ok",
        "service": "agent-os-kernel",
        "das_reachable": das_ok,
        "dataset_id": context.DEFAULT_DATASET,
        "model_mode": model_broker.model_mode(),
        "chain": "ok" if das_ok else "degraded",
        "hitl_auto": hitl.auto_enabled(),
        "skills": [s["name"] for s in registry.list_skills()],
        "default_skill": registry.default_name(),
    }


class SessionCreate(BaseModel):
    meta: dict[str, Any] = Field(default_factory=dict)
    bindings: dict[str, Any] | None = None


@app.post("/sessions")
def create_session(body: SessionCreate | None = None) -> dict[str, Any]:
    meta = dict((body.meta if body else None) or {})
    if body and body.bindings:
        meta["bindings"] = body.bindings
    # seed default time_window if missing (demo-friendly)
    if "bindings" not in meta:
        meta["bindings"] = {}
    b = context.coerce_partial(meta.get("bindings") or {})
    if not b.get("time_window"):
        skill = registry.get(b.get("skill") or registry.default_name())
        defaults = skill.get("default_bindings") or {}
        b = context.deep_merge(context.coerce_partial(defaults), b)
        b["skill"] = b.get("skill") or registry.default_name()
    meta["bindings"] = b
    return session.create_session(meta)


@app.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    s = session.get_session(session_id)
    if not s:
        raise HTTPException(404, "session not found")
    s["messages"] = session.list_messages(session_id)
    return s


class BindingsPatch(BaseModel):
    bindings: dict[str, Any] = Field(default_factory=dict)
    replace: bool = False


@app.patch("/sessions/{session_id}/context")
@app.put("/sessions/{session_id}/context")
@app.patch("/sessions/{session_id}/bindings")
def patch_bindings(session_id: str, body: BindingsPatch) -> dict[str, Any]:
    try:
        s = session.set_bindings(session_id, body.bindings, replace=body.replace)
    except KeyError:
        raise HTTPException(404, "session not found") from None
    return {"id": s["id"], "bindings": s.get("bindings") or {}, "meta": s.get("meta")}


class MessageIn(BaseModel):
    text: str
    skill: str | None = None
    bindings: dict[str, Any] | None = None
    confirm_rebind: bool | None = None  # UI: user confirmed new Run after chip change


def _resolve_for_message(
    session_id: str,
    body: MessageIn,
) -> dict[str, Any]:
    s = session.get_session(session_id)
    if not s:
        raise HTTPException(404, "session not found")
    sess_b = s.get("bindings") or context.session_bindings_from_meta(s.get("meta"))
    req: dict[str, Any] = dict(body.bindings or {})
    if body.skill:
        req["skill"] = body.skill
    inherit = run.latest_completed_bindings(session_id)
    skill_name = (
        req.get("skill")
        or sess_b.get("skill")
        or (inherit or {}).get("skill")
        or registry.default_name()
    )
    try:
        manifest = registry.get(skill_name)
    except KeyError as e:
        raise HTTPException(400, str(e)) from e
    # Follow-ups inherit prior Run snapshot unless request/session overrides
    resolved = context.resolve_bindings(
        request=req or None,
        session_bindings=sess_b,
        skill_defaults=manifest.get("default_bindings"),
        skill_name=skill_name,
        inherit_run=inherit,
    )
    # Force skill onto resolved
    resolved["skill"] = skill_name
    return resolved


@app.post("/sessions/{session_id}/messages")
def post_message(session_id: str, body: MessageIn) -> dict[str, Any]:
    s = session.get_session(session_id)
    if not s:
        raise HTTPException(404, "session not found")

    resolved = _resolve_for_message(session_id, body)
    session.add_message(session_id, "user", body.text)
    r = run.create_run(session_id, context_bindings=resolved)

    result = orchestrator.handle_user_message(session_id, r["id"], body.text)
    md = result.get("markdown") or result.get("message") or ""
    if result.get("status") in ("completed", "awaiting_hitl"):
        session.add_message(session_id, "assistant", md, run_id=r["id"])

    return {
        "session_id": session_id,
        "run_id": r["id"],
        "status": result.get("status"),
        "markdown": md,
        "hitl": result.get("hitl"),
        "evidence": result.get("evidence"),
        "context_bindings": resolved,
        "text": md,
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
        obs.emit(run_id, "hitl", {"kind": rr.get("hitl_kind"), "approved": False})
        return {"status": "failed", "markdown": "已拒绝"}
    obs.emit(run_id, "hitl", {"kind": rr.get("hitl_kind"), "approved": True})
    result = orchestrator.resume_after_hitl(run_id, body.text or "请继续生成报告")
    md = result.get("markdown") or ""
    if result.get("status") == "completed":
        session.add_message(rr["session_id"], "assistant", md, run_id=run_id)
    return {
        "run_id": run_id,
        "status": result.get("status"),
        "markdown": md,
        "context_bindings": rr.get("context_bindings"),
    }


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    rr = run.get_run(run_id)
    if not rr:
        raise HTTPException(404, "run not found")
    rr["events"] = obs.list_events(run_id)
    return rr


@app.get("/skills")
def skills() -> dict[str, Any]:
    return {"skills": registry.list_skills(), "default": registry.default_name()}


@app.get("/tools")
def tools() -> dict[str, Any]:
    try:
        names = ToolGateway().list_tools()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)) from e
    return {"tools": names}


if UI_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    index_path = UI_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(404, "UI missing")
    return FileResponse(index_path)
