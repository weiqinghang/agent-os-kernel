"""Orchestrator: Run acting loop — Model Broker ↔ Tool Gateway ↔ HITL ↔ Obs."""
from __future__ import annotations

from typing import Any

from . import context, hitl, model_broker, obs, run, session
from .policy import allow_tool
from .skills import registry
from .tool_gateway import ToolGateway, ToolGatewayError

MAX_STEPS = 8


def handle_user_message(
    session_id: str,
    run_id: str,
    user_text: str,
    *,
    resume: bool = False,
) -> dict[str, Any]:
    """Drive one user turn (or HITL resume) to completed / failed / awaiting_hitl."""
    rr = run.get_run(run_id) or {}
    bindings = rr.get("context_bindings") or {}
    skill_name = bindings.get("skill") or registry.default_name()
    try:
        skill = registry.get(skill_name)
    except KeyError as e:
        run.set_state(run_id, "failed", result=str(e))
        return {"status": "failed", "markdown": str(e)}

    run.set_state(run_id, "acting")
    obs.emit(
        run_id,
        "state",
        {"state": "acting", "skill": skill.get("name"), "resume": resume, "bindings": bindings},
    )

    if not resume:
        for kind in skill.get("hitl_kinds") or []:
            if kind == "outline_confirm" and hitl.should_pause(kind):
                token = hitl.make_token(kind)
                run.set_state(
                    run_id,
                    "awaiting_hitl",
                    resume_token=token["resume_token"],
                    hitl_kind=kind,
                )
                prompts = skill.get("hitl_prompts") or {}
                md = prompts.get(kind) or "请确认后继续。"
                obs.emit(
                    run_id,
                    "hitl",
                    {"kind": kind, "auto": False, "resume_token": token["resume_token"]},
                )
                return {"status": "awaiting_hitl", "hitl": token, "markdown": md, "message": md}
            if kind == "outline_confirm":
                obs.emit(run_id, "hitl", {"kind": kind, "auto": True})

    messages = session.list_messages(session_id)
    tool_results: list[dict[str, Any]] = []
    gw = ToolGateway()
    allowed = list(skill.get("allowed_tools") or [])
    system = context.compose_system(str(skill.get("system_prompt") or ""), bindings)

    try:
        for step in range(MAX_STEPS):
            decision = model_broker.decide(
                system=system,
                messages=messages,
                allowed_tools=allowed,
                tool_results=tool_results,
                bindings=bindings,
                skill_name=skill_name,
            )
            if decision.pop("_live_fallback", None):
                obs.emit(run_id, "model_step", {"step": step, "type": "live_fallback_stub", "mode": "stub"})
            dtype = decision.get("type")
            obs.emit(
                run_id,
                "model_step",
                {
                    "step": step,
                    "type": dtype,
                    "mode": model_broker.model_mode(),
                    "n_calls": len(decision.get("calls") or []) if dtype == "tool_calls" else 0,
                },
            )

            if dtype == "tool_calls":
                calls = decision.get("calls") or []
                if not calls:
                    raise RuntimeError("model returned empty tool_calls")
                for call in calls:
                    name = call.get("name") or ""
                    arguments = call.get("arguments") or {}
                    if name not in allowed or not allow_tool(name):
                        raise ToolGatewayError(f"tool not allowed by skill/policy: {name}")
                    # Gateway merges bindings defaults; arguments win
                    out = gw.call(name, arguments, bindings=bindings)
                    summary = context.summarize_tool(
                        name, out.get("evidence"), out.get("data") or {}, bindings=bindings
                    )
                    obs.emit(run_id, "tool_call", summary)
                    # Keep model/stub-requested args for plan matching; evidence has merged
                    tool_results.append(
                        {
                            "name": name,
                            "arguments": arguments,
                            "data": out.get("data"),
                            "evidence": out.get("evidence"),
                        }
                    )
                continue

            if dtype == "final":
                md = str(decision.get("content") or "")
                fps = {
                    f"{t.get('name')}:{json_key(t.get('arguments') or {})}": (
                        (t.get("evidence") or {}).get("fingerprint")
                    )
                    for t in tool_results
                }
                run.set_state(run_id, "completed", result=md)
                obs.emit(
                    run_id,
                    "state",
                    {"state": "completed", "n_tools": len(tool_results)},
                )
                return {
                    "status": "completed",
                    "markdown": md,
                    "evidence": fps,
                    "context_bindings": bindings,
                }

            raise RuntimeError(f"unknown decision type: {dtype}")

        msg = f"exceeded MAX_STEPS={MAX_STEPS}"
        run.set_state(run_id, "failed", result=msg)
        obs.emit(run_id, "state", {"state": "failed", "error": msg})
        return {"status": "failed", "markdown": msg}
    except Exception as e:  # noqa: BLE001
        msg = f"编排失败：{e}"
        run.set_state(run_id, "failed", result=msg)
        obs.emit(run_id, "state", {"state": "failed", "error": str(e)})
        return {"status": "failed", "markdown": msg}


def resume_after_hitl(run_id: str, user_text: str | None = None) -> dict[str, Any]:
    rr = run.get_run(run_id)
    if not rr:
        raise ValueError("run not found")
    sid = rr["session_id"]
    text = user_text or "请继续生成报告"
    session.add_message(sid, "user", text, run_id=run_id)
    return handle_user_message(sid, run_id, text, resume=True)


def json_key(args: dict[str, Any]) -> str:
    import json

    return json.dumps(args, sort_keys=True, ensure_ascii=False)
