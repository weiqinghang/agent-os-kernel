"""Model broker: decide() — live OpenAI-compat or StubBackend (Skill fixtures).

Broker stays free of dogfood business copy; stub plans live in Skill modules.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

LIVE_RETRY_BACKOFF_S = 0.2
_LIVE_ATTEMPTS = 2
_ERROR_LIMIT = 160


class _LiveRequestError(Exception):
    """One live chat/completions request failed and may be retried."""


def _short_error(exc: BaseException, limit: int = _ERROR_LIMIT) -> str:
    text = " ".join(str(exc).split())
    if not text:
        text = exc.__class__.__name__
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def has_llm() -> bool:
    return bool(
        os.getenv("MODEL_API_KEY")
        or os.getenv("XAI_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )


def model_mode() -> str:
    return "live" if has_llm() else "stub"


def decide(
    *,
    system: str,
    messages: list[dict[str, Any]],
    allowed_tools: list[str],
    tool_results: list[dict[str, Any]],
    bindings: dict[str, Any] | None = None,
    skill_name: str | None = None,
) -> dict[str, Any]:
    """Return structured decision: tool_calls | final."""
    bindings = bindings or {}
    skill_name = skill_name or bindings.get("skill") or "insight_report"
    if has_llm():
        events: list[dict[str, Any]] = []
        live: dict[str, Any] | None = None
        last_error = "live request failed"
        for attempt in range(1, _LIVE_ATTEMPTS + 1):
            if attempt > 1:
                time.sleep(LIVE_RETRY_BACKOFF_S)
                events.append(
                    {
                        "type": "live_retry",
                        "mode": "live",
                        "attempt": attempt,
                        "ok": False,
                        "error": last_error,
                    }
                )
            try:
                live = _LiveBackend.decide(
                    system=system,
                    messages=messages,
                    allowed_tools=allowed_tools,
                    tool_results=tool_results,
                )
                break
            except _LiveRequestError as exc:
                last_error = _short_error(exc)
                live = None
        if live is None:
            events.append(
                {
                    "type": "live_fallback",
                    "mode": "stub",
                    "ok": False,
                    "error": last_error,
                }
            )
            stub = _StubBackend.decide(
                system=system,
                messages=messages,
                allowed_tools=allowed_tools,
                tool_results=tool_results,
                bindings=bindings,
                skill_name=skill_name,
            )
            stub["_broker_events"] = events
            return stub
        if events:
            live["_broker_events"] = events
        return live
    return _StubBackend.decide(
        system=system,
        messages=messages,
        allowed_tools=allowed_tools,
        tool_results=tool_results,
        bindings=bindings,
        skill_name=skill_name,
    )


class _LiveBackend:
    @staticmethod
    def decide(
        *,
        system: str,
        messages: list[dict[str, Any]],
        allowed_tools: list[str],
        tool_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        import httpx

        key = os.getenv("MODEL_API_KEY") or os.getenv("XAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        base = os.getenv("MODEL_BASE_URL") or (
            "https://api.x.ai/v1" if os.getenv("XAI_API_KEY") else "https://api.openai.com/v1"
        )
        model = os.getenv("MODEL_NAME") or "grok-2-latest"

        chat_msgs: list[dict[str, str]] = [{"role": "system", "content": system}]
        for m in messages:
            role = m.get("role") or "user"
            if role not in ("user", "assistant", "system"):
                role = "user"
            chat_msgs.append({"role": role, "content": str(m.get("content") or "")})
        if tool_results:
            chat_msgs.append(
                {
                    "role": "user",
                    "content": "工具结果（只用这些数字，勿臆造）:\n"
                    + json.dumps(tool_results, ensure_ascii=False)[:12000],
                }
            )
        chat_msgs.append(
            {
                "role": "user",
                "content": (
                    "请以 JSON 回复，二选一：\n"
                    '{"type":"tool_calls","calls":[{"name":"...","arguments":{...}}]}\n'
                    '或 {"type":"final","content":"markdown..."}\n'
                    f"允许工具: {allowed_tools}"
                ),
            }
        )

        try:
            with httpx.Client(timeout=60.0) as client:
                r = client.post(
                    f"{base.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": model, "messages": chat_msgs, "temperature": 0.2},
                )
        except Exception as exc:
            detail = " ".join(str(exc).split()) or exc.__class__.__name__
            raise _LiveRequestError(f"live request failed: {detail}") from exc
        if r.status_code != 200:
            raise _LiveRequestError(f"live HTTP {r.status_code}")
        try:
            text = r.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            raise _LiveRequestError(f"live response unreadable: {exc.__class__.__name__}") from exc
        if not isinstance(text, str):
            text = "" if text is None else str(text)
        parsed = _parse_json_decision(text)
        if parsed:
            return parsed
        return {"type": "final", "content": text}


def _parse_json_decision(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None
    if "```" in text:
        parts = text.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                text = p
                break
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            obj = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(obj, dict):
        return None
    t = obj.get("type")
    if t == "tool_calls" and isinstance(obj.get("calls"), list):
        return {"type": "tool_calls", "calls": obj["calls"]}
    if t == "final" and isinstance(obj.get("content"), str):
        return {"type": "final", "content": obj["content"]}
    return None


class _StubBackend:
    @staticmethod
    def decide(
        *,
        system: str,
        messages: list[dict[str, Any]],
        allowed_tools: list[str],
        tool_results: list[dict[str, Any]],
        bindings: dict[str, Any],
        skill_name: str,
    ) -> dict[str, Any]:
        del system  # fixtures own prompts; system already composed by orchestrator
        from .skills import registry

        return registry.stub_decide(
            skill_name,
            messages=messages,
            allowed_tools=allowed_tools,
            tool_results=tool_results,
            bindings=bindings,
        )
