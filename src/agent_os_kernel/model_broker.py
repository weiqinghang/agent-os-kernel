"""Model broker: decide() — live OpenAI-compat or StubBackend (Skill fixtures).

Live chat/completions sends a tools array and replays tool_results as tool-role
messages. Providers that only put a JSON decision in message content still parse.
Stub plans live in Skill modules. Broker stays free of dogfood business copy.

Before either backend runs, context.fit_context applies the length budget.
Under the budget the message list is unchanged, so the tools / tool-role shape
stays as Batch F left it.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from . import context

LIVE_RETRY_BACKOFF_S = 0.2
_LIVE_ATTEMPTS = 2
_ERROR_LIMIT = 160
_TOOL_TEXT_LIMIT = 12000

_DATASET_ID: dict[str, Any] = {"type": "string", "description": "数据集 UUID，或网关能解析的名字"}
_FILTERS: dict[str, Any] = {
    "type": "array",
    "description": "过滤条件。时间窗用 order_date 的 gte 与 lte。",
    "items": {
        "type": "object",
        "properties": {
            "field": {"type": "string"},
            "operator": {"type": "string"},
            "value": {"description": "比较值"},
        },
        "required": ["field", "operator"],
        "additionalProperties": False,
    },
}
_METRICS: dict[str, Any] = {
    "type": "array",
    "description": "聚合项。function 为 sum、avg、count 等。",
    "items": {
        "type": "object",
        "properties": {
            "function": {"type": "string"},
            "field": {"type": "string"},
            "alias": {"type": "string"},
        },
        "required": ["function"],
        "additionalProperties": False,
    },
}
_GROUP_BY: dict[str, Any] = {
    "type": "array",
    "items": {"type": "string"},
    "description": "分组字段名",
}

# Live argument shape already accepted by the gateway. Not a provider catalog.
_TOOL_CATALOG: dict[str, dict[str, Any]] = {
    "list_datasets": {
        "description": "列出可查询的数据集",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "describe_dataset": {
        "description": "查看数据集概览",
        "parameters": {
            "type": "object",
            "properties": {"dataset_id": _DATASET_ID},
            "additionalProperties": False,
        },
    },
    "describe_fields": {
        "description": "查看字段定义。只传 dataset_id，需要时再传 field_names。",
        "parameters": {
            "type": "object",
            "properties": {
                "dataset_id": _DATASET_ID,
                "field_names": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
    },
    "describe_tag_hierarchies": {
        "description": "查看标签层级。参数只给 dataset_id。",
        "parameters": {
            "type": "object",
            "properties": {"dataset_id": _DATASET_ID},
            "additionalProperties": False,
        },
    },
    "query_stats": {
        "description": "按过滤、聚合和分组查询统计。不要传 SQL。",
        "parameters": {
            "type": "object",
            "properties": {
                "dataset_id": _DATASET_ID,
                "filters": _FILTERS,
                "metrics": _METRICS,
                "group_by": _GROUP_BY,
                "limit": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    },
    "search_records": {
        "description": "按条件检索记录。return_fields 不要留空。",
        "parameters": {
            "type": "object",
            "properties": {
                "dataset_id": _DATASET_ID,
                "filters": _FILTERS,
                "return_fields": {"type": "array", "items": {"type": "string"}},
                "evidence_preset": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    },
    "sample_records": {
        "description": "抽取记录样本。return_fields 不要留空。",
        "parameters": {
            "type": "object",
            "properties": {
                "dataset_id": _DATASET_ID,
                "filters": _FILTERS,
                "return_fields": {"type": "array", "items": {"type": "string"}},
                "evidence_preset": {"type": "string"},
                "limit": {"type": "integer"},
                "sample_strategy": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
}


class _LiveRequestError(Exception):
    """One live chat/completions request failed and may be retried."""


def _short_error(exc: BaseException, limit: int = _ERROR_LIMIT) -> str:
    text = " ".join(str(exc).split())
    if not text:
        text = exc.__class__.__name__
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _clip(text: str, limit: int = _TOOL_TEXT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def has_llm() -> bool:
    return bool(
        os.getenv("MODEL_API_KEY")
        or os.getenv("XAI_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )


def model_mode() -> str:
    return "live" if has_llm() else "stub"


def _context_event(fitted: dict[str, Any]) -> dict[str, Any] | None:
    if not fitted.get("truncated"):
        return None
    event: dict[str, Any] = {
        "type": "context_truncated",
        "ok": True,
        "dropped_messages": fitted["dropped_messages"],
        "dropped_tool_results": fitted["dropped_tool_results"],
        "shrunk_tool_results": fitted["shrunk_tool_results"],
        "kept_messages": fitted["kept_messages"],
        "kept_tool_results": fitted["kept_tool_results"],
        "max_chars": fitted["max_chars"],
        "max_messages": fitted["max_messages"],
        "original_chars": fitted["original_chars"],
        "kept_chars": fitted["kept_chars"],
    }
    fingerprint = fitted.get("bindings_fingerprint")
    if fingerprint:
        event["bindings_fingerprint"] = fingerprint
    return event


def _coerce_skill_decision(
    decision: dict[str, Any],
    *,
    messages: list[dict[str, Any]],
    allowed_tools: list[str],
    tool_results: list[dict[str, Any]],
    bindings: dict[str, Any],
    skill_name: str,
) -> dict[str, Any]:
    """Let a skill replace a live final. Empty tool_calls are ignored."""
    from .skills import registry

    coerced = registry.coerce_decision(
        skill_name,
        decision=decision,
        messages=messages,
        allowed_tools=allowed_tools,
        tool_results=tool_results,
        bindings=bindings,
    )
    if not isinstance(coerced, dict):
        return decision
    dtype = coerced.get("type")
    if dtype == "tool_calls" and not coerced.get("calls"):
        return decision
    if dtype not in ("tool_calls", "final"):
        return decision
    return coerced


def _attach_context_events(decision: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    if not events:
        return decision
    prior = decision.get("_broker_events")
    merged = list(events)
    if isinstance(prior, list):
        merged.extend(item for item in prior if isinstance(item, dict))
    decision["_broker_events"] = merged
    return decision


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
    fitted = context.fit_context(
        system=system,
        messages=messages,
        tool_results=tool_results,
        bindings=bindings,
    )
    system = fitted["system"]
    messages = fitted["messages"]
    tool_results = fitted["tool_results"]
    prefix: list[dict[str, Any]] = []
    truncated = _context_event(fitted)
    if truncated is not None:
        prefix.append(truncated)
    if has_llm():
        events: list[dict[str, Any]] = list(prefix)
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
        live = _coerce_skill_decision(
            live,
            messages=messages,
            allowed_tools=allowed_tools,
            tool_results=tool_results,
            bindings=bindings,
            skill_name=skill_name,
        )
        if events:
            live["_broker_events"] = events
        return live
    return _attach_context_events(
        _StubBackend.decide(
            system=system,
            messages=messages,
            allowed_tools=allowed_tools,
            tool_results=tool_results,
            bindings=bindings,
            skill_name=skill_name,
        ),
        prefix,
    )


def _tool_specs(allowed_tools: list[str]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name in allowed_tools:
        if not isinstance(name, str) or not name or name in seen:
            continue
        seen.add(name)
        known = _TOOL_CATALOG.get(name)
        if known is None:
            description = name
            parameters: dict[str, Any] = {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            }
        else:
            description = str(known["description"])
            parameters = known["parameters"]
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                },
            }
        )
    return specs


def _history_note(result: dict[str, Any]) -> str:
    error = result.get("error")
    if error:
        return str(error)
    name = result.get("name")
    if name:
        return str(name)
    return "tool result"


def _tool_content(result: dict[str, Any]) -> str:
    payload: dict[str, Any] = {"ok": bool(result.get("ok"))}
    if result.get("error"):
        payload["error"] = result.get("error")
    if result.get("data") is not None:
        payload["data"] = result["data"]
    if result.get("evidence") is not None:
        payload["evidence"] = result["evidence"]
    return _clip(json.dumps(payload, ensure_ascii=False))


def _chat_messages(
    system: str,
    messages: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    chat: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for message in messages:
        role = message.get("role") or "user"
        if role not in ("user", "assistant", "system"):
            role = "user"
        chat.append({"role": role, "content": str(message.get("content") or "")})
    for index, result in enumerate(tool_results):
        if not isinstance(result, dict):
            continue
        name = result.get("name")
        name_text = name if isinstance(name, str) else ""
        # loop_break and nameless rows are not functions the provider advertised.
        if result.get("loop_break") or not name_text or name_text.startswith("_"):
            chat.append({"role": "user", "content": _clip(_history_note(result))})
            continue
        arguments = result.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        call_id = f"call_{index}"
        chat.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": name_text,
                            "arguments": json.dumps(arguments, ensure_ascii=False),
                        },
                    }
                ],
            }
        )
        chat.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": _tool_content(result),
            }
        )
    return chat


def _coerce_arguments(raw: Any) -> Any:
    if isinstance(raw, dict):
        return raw
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        return parsed
    return raw


def _native_calls(message: dict[str, Any]) -> list[dict[str, Any]] | None:
    raw = message.get("tool_calls")
    if not isinstance(raw, list) or not raw:
        return None
    calls: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        function = item.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        calls.append(
            {
                "name": name if isinstance(name, str) else "",
                "arguments": _coerce_arguments(function.get("arguments")),
            }
        )
    return calls or None


def _message_text(message: dict[str, Any]) -> str:
    text = message.get("content")
    if isinstance(text, str):
        return text
    if text is None:
        return ""
    return str(text)


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
        body: dict[str, Any] = {
            "model": model,
            "messages": _chat_messages(system, messages, tool_results),
            "temperature": 0.2,
        }
        tools = _tool_specs(allowed_tools)
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        try:
            with httpx.Client(timeout=60.0) as client:
                r = client.post(
                    f"{base.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json=body,
                )
        except Exception as exc:
            detail = " ".join(str(exc).split()) or exc.__class__.__name__
            raise _LiveRequestError(f"live request failed: {detail}") from exc
        if r.status_code != 200:
            raise _LiveRequestError(f"live HTTP {r.status_code}")
        try:
            message = r.json()["choices"][0]["message"]
        except Exception as exc:
            raise _LiveRequestError(f"live response unreadable: {exc.__class__.__name__}") from exc
        if not isinstance(message, dict):
            raise _LiveRequestError("live response unreadable: message")
        calls = _native_calls(message)
        if calls:
            return {"type": "tool_calls", "calls": calls}
        text = _message_text(message)
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
