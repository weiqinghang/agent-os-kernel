"""Orchestrator: Run acting loop — Model Broker ↔ Tool Gateway ↔ HITL ↔ Obs.

One decide() may list several tool calls. They run in this process, at most
MAX_PARALLEL_TOOLS at a time, and tool_results are appended in that same order.

The same normalized tool name and arguments may run REPEAT_LIMIT - 1 times in
a row. The next identical call is not executed: one decide is asked for a
final answer, and if that is empty the run completes from tool evidence or
fails with a loop_break signal instead of burning MAX_STEPS.

Cancel is cooperative: the loop looks between decides and tool batches, lets
the current batch finish, then leaves. It does not write model text into the
user-facing result.
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import context, hitl, model_broker, obs, run, session
from .policy import allow_tool
from .skills import registry
from .tool_gateway import ToolGateway, ToolGatewayError

# One tool per step plus a few distinct corrections. Identical spins stop earlier.
MAX_STEPS = 12
MAX_STEPS_CAP = 32
# Bound for calls returned by a single decide(). Not a cross-step scheduler.
MAX_PARALLEL_TOOLS = 3
# The Nth consecutive normalized call is not run.
REPEAT_LIMIT = 3
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


def resolve_max_steps() -> int:
    """Positive AGENT_MAX_STEPS up to MAX_STEPS_CAP; anything else keeps MAX_STEPS."""
    raw = os.getenv("AGENT_MAX_STEPS", "").strip()
    if not raw:
        return MAX_STEPS
    try:
        value = int(raw)
    except ValueError:
        return MAX_STEPS
    if value < 1 or value > MAX_STEPS_CAP:
        return MAX_STEPS
    return value


def _normalize(value: Any) -> Any:
    """Key order, surrounding whitespace, and 5.0 vs 5 do not make a new call."""
    if isinstance(value, dict):
        return {
            str(key): _normalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _fingerprint(name: str, arguments: Any) -> str:
    if isinstance(arguments, dict):
        payload: Any = _normalize(arguments)
    else:
        payload = {"_raw": _normalize(arguments)}
    return f"{name}:{json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)}"


def _call_name(call: Any) -> str:
    if isinstance(call, dict):
        return str(call.get("name") or "")
    return ""


def _call_fingerprint(call: Any) -> str:
    if isinstance(call, dict):
        return _fingerprint(_call_name(call), call.get("arguments"))
    if isinstance(call, (str, int, float, bool)) or call is None:
        raw: Any = call
    else:
        raw = str(call)
    return _fingerprint("", {"_call": raw})


def _trailing(fingerprints: list[str]) -> tuple[str | None, int]:
    if not fingerprints:
        return None, 0
    last = fingerprints[-1]
    count = 0
    for fp in reversed(fingerprints):
        if fp != last:
            break
        count += 1
    return last, count


def _split_repeat(
    calls: list[Any], fingerprints: list[str]
) -> tuple[list[Any], dict[str, str] | None]:
    """Keep the prefix that stays under REPEAT_LIMIT. The tripping call is not run."""
    last, streak = _trailing(fingerprints)
    accepted: list[Any] = []
    for call in calls:
        fp = _call_fingerprint(call)
        if last is not None and fp == last:
            streak += 1
        else:
            last = fp
            streak = 1
        if streak >= REPEAT_LIMIT:
            return accepted, {"name": _call_name(call), "fingerprint": fp}
        accepted.append(call)
    return accepted, None


def _visible_results(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in tool_results if not item.get("loop_break")]


def _has_usable_evidence(tool_results: list[dict[str, Any]]) -> bool:
    for record in _visible_results(tool_results):
        if record.get("ok") is True and (record.get("data") or record.get("evidence")):
            return True
    return False


def _evidence_map(tool_results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        f"{item.get('name')}:{json_key(item.get('arguments') or {})}": (
            (item.get("evidence") or {}).get("fingerprint")
        )
        for item in _visible_results(tool_results)
    }


def _should_stop(run_id: str) -> bool:
    if run.cancel_requested(run_id):
        return True
    current = run.get_run(run_id)
    return bool(current and current.get("state") == "cancelled")


def _cancelled_result(run_id: str, bindings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Short human close. Partial tool events already stored stay where they are."""
    current = run.get_run(run_id) or {}
    if current.get("state") != "cancelled":
        run.set_state(run_id, "cancelled", result=run.CANCELLED_RESULT)
    resolved = bindings if bindings is not None else (current.get("context_bindings") or {})
    return {
        "status": "cancelled",
        "markdown": run.CANCELLED_RESULT,
        "message": run.CANCELLED_RESULT,
        "context_bindings": resolved,
    }


def _complete(
    run_id: str,
    markdown: str,
    tool_results: list[dict[str, Any]],
    bindings: dict[str, Any],
    *,
    loop_break: bool = False,
    repeated_tool: str = "",
) -> dict[str, Any]:
    if _should_stop(run_id):
        return _cancelled_result(run_id, bindings)
    visible = _visible_results(tool_results)
    payload: dict[str, Any] = {"state": "completed", "n_tools": len(visible)}
    if loop_break:
        payload["loop_break"] = True
        payload["repeated_tool"] = repeated_tool
    if not run.set_state(run_id, "completed", result=markdown):
        return _cancelled_result(run_id, bindings)
    obs.emit(run_id, "state", payload)
    out: dict[str, Any] = {
        "status": "completed",
        "markdown": markdown,
        "evidence": _evidence_map(tool_results),
        "context_bindings": bindings,
    }
    if loop_break:
        out["loop_break"] = True
        out["repeated_tool"] = repeated_tool
    return out


def _degraded_summary(name: str, tool_results: list[dict[str, Any]]) -> str:
    tool = name or "tool"
    lines = [
        f"已停止重复调用 `{tool}`：相同参数连续出现，未再消耗剩余步数。",
        "以下为已取得的工具证据，结论可能不完整。",
        "",
    ]
    for record in _visible_results(tool_results):
        if record.get("ok") is True:
            evidence = record.get("evidence") if isinstance(record.get("evidence"), dict) else {}
            fp = evidence.get("fingerprint") or "n/a"
            lines.append(f"- {record.get('name')} · evidence `{fp}`")
    return "\n".join(lines)


def _fail_loop(run_id: str, name: str) -> dict[str, Any]:
    if _should_stop(run_id):
        return _cancelled_result(run_id)
    tool = name or "tool"
    msg = f"loop_break: 连续重复调用 {tool}，没有可用工具证据"
    if not run.set_state(run_id, "failed", result=msg):
        return _cancelled_result(run_id)
    obs.emit(
        run_id,
        "state",
        {"state": "failed", "error": msg, "loop_break": True, "repeated_tool": tool},
    )
    return {"status": "failed", "markdown": msg, "loop_break": True, "repeated_tool": tool}


def _stop_for_repeat(
    run_id: str,
    step: int,
    system: str,
    messages: list[dict[str, Any]],
    allowed: list[str],
    tool_results: list[dict[str, Any]],
    bindings: dict[str, Any],
    skill_name: str,
    repeated: dict[str, str],
) -> dict[str, Any]:
    """Ask once for a final answer. Do not execute the repeated call."""
    if _should_stop(run_id):
        return _cancelled_result(run_id, bindings)
    name = repeated.get("name") or ""
    shown = name or "tool"
    obs.emit(
        run_id,
        "model_step",
        {
            "step": step,
            "type": "loop_break",
            "loop_break": True,
            "repeated_tool": shown,
            "fingerprint": repeated.get("fingerprint") or "",
            "repeat_limit": REPEAT_LIMIT,
            "mode": model_broker.model_mode(),
        },
    )
    tool_results.append(
        {
            "name": "_loop_break",
            "arguments": {
                "repeated_tool": shown,
                "fingerprint": repeated.get("fingerprint") or "",
            },
            "ok": False,
            "loop_break": True,
            "repeated_tool": shown,
            "error": f"loop_break: 相同参数已连续出现，请直接给出 final，不要再调用 `{shown}`。",
            "data": None,
            "evidence": None,
        }
    )
    forced_system = (
        system.rstrip()
        + "\n\n# Loop break\n"
        + f"loop_break: 工具 `{shown}` 的相同参数已连续出现。请只回复 final，"
        + "基于已有工具结果写结论；不要再 tool_calls。\n"
    )
    decision: dict[str, Any] | None
    try:
        raw_decision = model_broker.decide(
            system=forced_system,
            messages=messages,
            allowed_tools=allowed,
            tool_results=tool_results,
            bindings=bindings,
            skill_name=skill_name,
        )
    except Exception:
        decision = None
    else:
        decision = raw_decision if isinstance(raw_decision, dict) else None
    if decision is not None:
        _emit_broker_events(run_id, step, decision)
        dtype = decision.get("type")
        obs.emit(
            run_id,
            "model_step",
            {
                "step": step,
                "type": dtype,
                "mode": model_broker.model_mode(),
                "loop_break": True,
                "repeated_tool": shown,
                "n_calls": len(decision.get("calls") or []) if dtype == "tool_calls" else 0,
            },
        )
    if _should_stop(run_id):
        return _cancelled_result(run_id, bindings)
    content = ""
    if decision is not None and decision.get("type") == "final":
        content = str(decision.get("content") or "").strip()
    if content:
        return _complete(
            run_id,
            content,
            tool_results,
            bindings,
            loop_break=True,
            repeated_tool=shown,
        )
    if _has_usable_evidence(tool_results):
        return _complete(
            run_id,
            _degraded_summary(shown, tool_results),
            tool_results,
            bindings,
            loop_break=True,
            repeated_tool=shown,
        )
    return _fail_loop(run_id, shown)


def handle_user_message(
    session_id: str,
    run_id: str,
    user_text: str,
    *,
    resume: bool = False,
) -> dict[str, Any]:
    """Drive one user turn (or HITL resume) to completed / failed / awaiting_hitl / cancelled."""
    if _should_stop(run_id):
        return _cancelled_result(run_id)
    rr = run.get_run(run_id) or {}
    bindings = rr.get("context_bindings") or {}
    skill_name = bindings.get("skill") or registry.default_name()
    try:
        skill = registry.get(skill_name)
    except KeyError as e:
        if not run.set_state(run_id, "failed", result=str(e)):
            return _cancelled_result(run_id)
        return {"status": "failed", "markdown": str(e)}

    if not run.set_state(run_id, "acting"):
        return _cancelled_result(run_id, bindings)
    obs.emit(
        run_id,
        "state",
        {"state": "acting", "skill": skill.get("name"), "resume": resume, "bindings": bindings},
    )

    if not resume:
        for kind in skill.get("hitl_kinds") or []:
            if kind == "outline_confirm" and hitl.should_pause(kind):
                token = hitl.make_token(kind)
                prompts = skill.get("hitl_prompts") or {}
                md = prompts.get(kind) or "请确认后继续。"
                if not run.set_state(
                    run_id,
                    "awaiting_hitl",
                    resume_token=token["resume_token"],
                    hitl_kind=kind,
                    result=md,
                ):
                    return _cancelled_result(run_id, bindings)
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
        fingerprints: list[str] = []
        step_budget = resolve_max_steps()
        for step in range(step_budget):
            if _should_stop(run_id):
                return _cancelled_result(run_id, bindings)
            decision = model_broker.decide(
                system=system,
                messages=messages,
                allowed_tools=allowed,
                tool_results=tool_results,
                bindings=bindings,
                skill_name=skill_name,
            )
            _emit_broker_events(run_id, step, decision)
            if _should_stop(run_id):
                return _cancelled_result(run_id, bindings)
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
                if _should_stop(run_id):
                    return _cancelled_result(run_id, bindings)
                # Same normalized call stops before this batch spends the step budget.
                accepted, repeated = _split_repeat(calls, fingerprints)
                if accepted:
                    # Dogfood tools are reads, so the prefix shares the parallel bound.
                    _run_tool_batch(run_id, gw, accepted, allowed, bindings, tool_results)
                    fingerprints.extend(_call_fingerprint(call) for call in accepted)
                if _should_stop(run_id):
                    return _cancelled_result(run_id, bindings)
                if repeated:
                    return _stop_for_repeat(
                        run_id,
                        step,
                        system,
                        messages,
                        allowed,
                        tool_results,
                        bindings,
                        skill_name,
                        repeated,
                    )
                continue

            if dtype == "final":
                if _should_stop(run_id):
                    return _cancelled_result(run_id, bindings)
                return _complete(run_id, str(decision.get("content") or ""), tool_results, bindings)

            raise RuntimeError(f"unknown decision type: {dtype}")

        msg = f"exceeded MAX_STEPS={step_budget}"
        if _should_stop(run_id) or not run.set_state(run_id, "failed", result=msg):
            return _cancelled_result(run_id, bindings)
        obs.emit(run_id, "state", {"state": "failed", "error": msg})
        return {"status": "failed", "markdown": msg}
    except Exception as e:  # noqa: BLE001
        if _should_stop(run_id):
            return _cancelled_result(run_id, bindings)
        msg = f"编排失败：{e}"
        if not run.set_state(run_id, "failed", result=msg):
            return _cancelled_result(run_id, bindings)
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
    return json.dumps(args, sort_keys=True, ensure_ascii=False)
