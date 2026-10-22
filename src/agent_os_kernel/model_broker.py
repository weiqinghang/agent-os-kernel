"""Model broker: decide() — live OpenAI-compat or StubBackend (Skill fixtures).

Broker stays free of dogfood business copy; stub plans live in Skill modules.
"""
from __future__ import annotations

import json
import os
from typing import Any


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
        out = _LiveBackend.decide(
            system=system,
            messages=messages,
            allowed_tools=allowed_tools,
            tool_results=tool_results,
        )
        if out.get("_fallback_stub"):
            from . import obs  # local; fallback may lack run_id — caller emits

            del out["_fallback_stub"]
            stub = _StubBackend.decide(
                system=system,
                messages=messages,
                allowed_tools=allowed_tools,
                tool_results=tool_results,
                bindings=bindings,
                skill_name=skill_name,
            )
            stub["_live_fallback"] = True
            return stub
        return out
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
            if r.status_code == 200:
                text = r.json()["choices"][0]["message"]["content"]
                parsed = _parse_json_decision(text)
                if parsed:
                    return parsed
                return {"type": "final", "content": text}
        except Exception:  # noqa: BLE001
            pass
        return {"type": "final", "content": "", "_fallback_stub": True}


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
