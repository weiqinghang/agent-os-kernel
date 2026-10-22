"""Kernel FastAPI entry — :8787."""
from __future__ import annotations

import copy
import os
import threading
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


def _persist_assistant(session_id: str, run_id: str, result: dict[str, Any]) -> None:
    md = result.get("markdown") or result.get("message") or ""
    if result.get("status") in ("completed", "awaiting_hitl", "cancelled") and md:
        session.add_message(session_id, "assistant", md, run_id=run_id)


def _drive_turn(session_id: str, run_id: str, target: Any, args: tuple[Any, ...]) -> None:
    try:
        result = target(*args)
    except Exception as exc:  # noqa: BLE001
        if run.cancel_requested(run_id):
            result = {"status": "cancelled", "markdown": run.CANCELLED_RESULT}
        else:
            run.set_state(run_id, "failed", result=f"编排失败：{exc}")
            return
    try:
        _persist_assistant(session_id, run_id, result or {})
    except Exception:
        return


def _start_turn(session_id: str, run_id: str, target: Any, args: tuple[Any, ...]) -> None:
    threading.Thread(
        target=_drive_turn,
        args=(session_id, run_id, target, args),
        name=f"turn-{run_id}",
        daemon=True,
    ).start()


def _accept(session_id: str, run_id: str, bindings: dict[str, Any]) -> dict[str, Any]:
    snap = run.get_run(run_id) or {}
    return {
        "session_id": session_id,
        "run_id": run_id,
        "status": snap.get("state") or "created",
        "accepted": True,
        "context_bindings": bindings,
    }


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
    # The agent loop keeps running after this response. Clients poll events.
    _start_turn(
        session_id,
        r["id"],
        orchestrator.handle_user_message,
        (session_id, r["id"], body.text),
    )
    return _accept(session_id, r["id"], resolved)


class HitlResume(BaseModel):
    resume_token: str
    approve: bool = True
    text: str | None = None


class RetryIn(BaseModel):
    text: str | None = None


@app.post("/runs/{run_id}/retry")
def retry_failed_run(run_id: str, body: RetryIn | None = None) -> dict[str, Any]:
    """New Run in the same Session. Copies the failed Run's binding snapshot.

    HITL stays on POST /runs/{id}/resume. This route does not take a resume_token
    and does not move the failed Run back to acting.
    """
    rr = run.get_run(run_id)
    if not rr:
        raise HTTPException(404, "run not found")
    if rr["state"] != "failed":
        raise HTTPException(409, f"run not failed: {rr['state']}")
    bindings = copy.deepcopy(rr.get("context_bindings") or {})
    spoken = body.text if body else None
    created = run.create_run(
        rr["session_id"],
        context_bindings=bindings,
        meta={"retry_of": run_id},
    )
    content = run.retry_message(rr.get("result"), spoken)
    session.add_message(rr["session_id"], "user", content, run_id=created["id"])
    obs.emit(created["id"], "state", {"state": "created"})
    obs.emit(created["id"], "retry", {"retry_of": run_id})
    _start_turn(
        rr["session_id"],
        created["id"],
        orchestrator.handle_user_message,
        (rr["session_id"], created["id"], content),
    )
    accepted = _accept(rr["session_id"], created["id"], bindings)
    accepted["retry_of"] = run_id
    return accepted


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
        run.set_state(run_id, "failed", result="已拒绝")
        obs.emit(run_id, "hitl", {"kind": rr.get("hitl_kind"), "approved": False})
        return {
            "run_id": run_id,
            "status": "failed",
            "markdown": "已拒绝",
            "accepted": True,
            "context_bindings": rr.get("context_bindings") or {},
        }
    obs.emit(run_id, "hitl", {"kind": rr.get("hitl_kind"), "approved": True})
    # Leave awaiting_hitl before returning so a second resume cannot start another loop.
    run.set_state(run_id, "acting")
    text = body.text or "请继续生成报告"
    _start_turn(rr["session_id"], run_id, orchestrator.resume_after_hitl, (run_id, text))
    accepted = _accept(rr["session_id"], run_id, rr.get("context_bindings") or {})
    accepted.pop("session_id", None)
    return accepted


@app.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict[str, Any]:
    """Stop a Run that has not finished. Terminal runs, including a rejected confirm, get 409."""
    try:
        outcome = run.request_cancel(run_id)
    except KeyError:
        raise HTTPException(404, "run not found") from None
    if not outcome["applied"]:
        raise HTTPException(409, f"run already {outcome['state']}")
    # The acting turn thread records the line when it leaves the loop.
    # A Run parked on confirm has no thread left, so record it here.
    if outcome["previous"] == "awaiting_hitl":
        current = run.get_run(run_id) or {}
        sid = current.get("session_id")
        if sid:
            session.add_message(sid, "assistant", run.CANCELLED_RESULT, run_id=run_id)
    return {
        "run_id": run_id,
        "status": "cancelled",
        "markdown": run.CANCELLED_RESULT,
        "result": run.CANCELLED_RESULT,
        "accepted": True,
    }


def _run_or_404(run_id: str) -> dict[str, Any]:
    rr = run.get_run(run_id)
    if not rr:
        raise HTTPException(404, "run not found")
    return rr


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    rr = _run_or_404(run_id)
    rr["events"] = obs.list_events(run_id)
    return rr


@app.get("/runs/{run_id}/events")
def get_run_events(
    run_id: str,
    after_id: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Poll new events. after_id is the last id the client already has."""
    if after_id is not None and after_id < 0:
        raise HTTPException(400, "bad after_id")
    if limit < 1 or limit > 500:
        raise HTTPException(400, "bad limit")
    rr = _run_or_404(run_id)
    body: dict[str, Any] = {
        "run_id": run_id,
        "state": rr["state"],
        "result": rr.get("result"),
        "context_bindings": rr.get("context_bindings") or {},
        "events": obs.list_events(run_id, limit=limit, after_id=after_id),
    }
    if rr["state"] == "awaiting_hitl":
        body["hitl"] = {"kind": rr.get("hitl_kind"), "resume_token": rr.get("resume_token")}
    return body


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
