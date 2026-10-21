"""Orchestrator: Run acting loop — Model Broker ↔ Tool Gateway ↔ HITL ↔ Obs."""
from __future__ import annotations

from typing import Any

from . import context, hitl, model_broker, obs, run, session
from .policy import allow_tool
from .skills import insight_report
from .tool_gateway import ToolGateway, ToolGatewayError

MAX_STEPS = 8
DEFAULT_SKILL = insight_report.SKILL


def handle_user_message(
    session_id: str,
    run_id: str,
    user_text: str,
    *,
    skill: dict[str, Any] | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    """Drive one user turn (or HITL resume) to completed / failed / awaiting_hitl."""
    skill = skill or DEFAULT_SKILL
    run.set_state(run_id, "acting")
    obs.emit(
        run_id,
        "state",
        {"state": "acting", "skill": skill.get("name"), "resume": resume},
    )

    # HITL outline_confirm (skip on resume — already approved)
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
                obs.emit(
                    run_id,
                    "hitl",
                    {"kind": kind, "auto": False, "resume_token": token["resume_token"]},
                )
                return {
                    "status": "awaiting_hitl",
                    "hitl": token,
                    "markdown": "请确认报告大纲（近90天电商销量洞察：类目/渠道/省份/退货）后继续。",
                    "message": "请确认报告大纲后继续。",
                }
            if kind == "outline_confirm":
                obs.emit(run_id, "hitl", {"kind": kind, "auto": True})

    messages = session.list_messages(session_id)
    tool_results: list[dict[str, Any]] = []
    gw = ToolGateway()
    allowed = list(skill.get("allowed_tools") or [])

    try:
        for step in range(MAX_STEPS):
            decision = model_broker.decide(
                system=str(skill.get("system_prompt") or ""),
                messages=messages,
                allowed_tools=allowed,
                tool_results=tool_results,
            )
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
                    out = gw.call(name, arguments)
                    summary = context.summarize_tool(name, out.get("evidence"), out.get("data") or {})
                    obs.emit(run_id, "tool_call", summary)
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
    # Ensure there is a user cue on the session for stub intent
    session.add_message(sid, "user", text, run_id=run_id)
    return handle_user_message(sid, run_id, text, resume=True)


def json_key(args: dict[str, Any]) -> str:
    import json

    return json.dumps(args, sort_keys=True, ensure_ascii=False)
