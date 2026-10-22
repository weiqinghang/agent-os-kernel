"""Orchestrator: Run acting loop — Model Broker ↔ Tool Gateway ↔ HITL ↔ Obs.

One decide() may list several tool calls. They run in this process, at most
MAX_PARALLEL_TOOLS at a time, and tool_results are appended in that same order.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import context, hitl, model_broker, obs, run, session
from .policy import allow_tool
from .skills import registry
from .tool_gateway import ToolGateway, ToolGatewayError

MAX_STEPS = 8
# Bound for calls returned by a single decide(). Not a cross-step scheduler.
MAX_PARALLEL_TOOLS = 3
_ERROR_LIMIT = 180


def _short_error(exc: BaseException, limit: int = _ERROR_LIMIT) -> str:
    text = " ".join(str(exc).split())
    if not text:
        text = exc.__class__.__name__
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _tool_failure(name: str, arguments: Any, exc: BaseException) -> dict[str, Any]:
    args = arguments if isinstance(arguments, dict) else {}
    return {
        "name": name,
        "arguments": args,
        "ok": False,
        "error": _short_error(exc),
        "data": None,
        "evidence": None,
    }


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _failure_event(
    name: str,
    exc: BaseException,
    bindings: dict[str, Any],
    ms: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"tool": name, "ok": False, "error": _short_error(exc), "ms": ms}
    if bindings:
        payload["bindings_fingerprint"] = context.bindings_fingerprint(bindings)
    return payload


def _perform_tool_call(
    gw: ToolGateway,
    call: Any,
    allowed: list[str],
    bindings: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one call. ToolGatewayError is recoverable; anything else propagates."""
    started = time.perf_counter()
    if not isinstance(call, dict):
        err = ToolGatewayError("tool call must be an object")
        return _tool_failure("", {}, err), _failure_event("", err, bindings, _elapsed_ms(started))
    name = str(call.get("name") or "")
    raw_args = call.get("arguments")
    if raw_args is None:
        raw_args = {}
    try:
        if not isinstance(raw_args, dict):
            raise ToolGatewayError("tool arguments must be an object")
        if name not in allowed or not allow_tool(name):
            raise ToolGatewayError(f"tool not allowed by skill/policy: {name}")
        out = gw.call(name, raw_args, bindings=bindings)
    except ToolGatewayError as exc:
        return (
            _tool_failure(name, raw_args, exc),
            _failure_event(name, exc, bindings, _elapsed_ms(started)),
        )
    summary = context.summarize_tool(
        name, out.get("evidence"), out.get("data") or {}, bindings=bindings
    )
    summary["ok"] = True
    summary["ms"] = _elapsed_ms(started)
    record = {
        "name": name,
        "arguments": raw_args,
        "ok": True,
        "data": out.get("data"),
        "evidence": out.get("evidence"),
    }
    return record, summary


def _run_tool_batch(
    run_id: str,
    gw: ToolGateway,
    calls: list[Any],
    allowed: list[str],
    bindings: dict[str, Any],
    tool_results: list[dict[str, Any]],
) -> None:
    """Run this decide's calls with a bounded pool, then write them back in order."""
    workers = min(MAX_PARALLEL_TOOLS, len(calls))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_perform_tool_call, gw, call, allowed, bindings) for call in calls]
    for fut in futures:
        record, event = fut.result()
        obs.emit(run_id, "tool_call", event)
        tool_results.append(record)


def _emit_broker_events(run_id: str, step: int, decision: dict[str, Any]) -> None:
    events = decision.pop("_broker_events", None) or []
    for event in events:
        if not isinstance(event, dict):
            continue
        payload = {"step": step}
        payload.update(event)
        obs.emit(run_id, "model_step", payload)


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
            _emit_broker_events(run_id, step, decision)
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
                # Empty tool_calls has nothing to feed back, so the Run fails.
                # A bad call or ToolGatewayError is recoverable: record ok:false and continue.
                if not calls:
                    raise RuntimeError("model returned empty tool_calls")
                # Dogfood tools are reads, so the whole batch shares the bound.
                _run_tool_batch(run_id, gw, calls, allowed, bindings, tool_results)
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
