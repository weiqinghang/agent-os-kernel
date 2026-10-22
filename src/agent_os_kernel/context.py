"""ContextBindings: Session defaults + Run frozen snapshot; tool summaries.

Long broker inputs are trimmed here by rule. There is no vector store and no
summarizer model: the system prompt (including the bindings block) stays, the
latest user turn stays, and older turns or tool blobs are dropped or shortened.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from typing import Any

# Known top-level keys; everything else → extras (or 400 if nested workflow-like)
_KNOWN = frozenset(
    {"skill", "dataset", "time_window", "project_id", "tenant_id", "extras", "project"}
)
_DATASET_TOOLS = frozenset(
    {
        "describe_dataset",
        "describe_fields",
        "describe_tag_hierarchies",
        "query_stats",
        "search_records",
        "sample_records",
    }
)

# Dogfood seed published by the on-box dataset-analysis-service.
SEED_DATASET_ID = "ecommerce_sales_v2"
DEFAULT_DATASET = SEED_DATASET_ID
DEFAULT_SKILL = "insight_report"


def empty_bindings() -> dict[str, Any]:
    return {}


def _normalize_time_window(tw: Any) -> dict[str, Any] | None:
    if tw is None:
        return None
    if not isinstance(tw, dict):
        return None
    out: dict[str, Any] = {}
    if tw.get("start"):
        out["start"] = str(tw["start"])
    if tw.get("end"):
        out["end"] = str(tw["end"])
    if tw.get("label"):
        out["label"] = str(tw["label"])
    return out or None


def coerce_partial(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize a partial ContextBindings; unknown flat keys → extras."""
    if not raw:
        return {}
    out: dict[str, Any] = {}
    extras: dict[str, Any] = {}
    if isinstance(raw.get("extras"), dict):
        for k, v in raw["extras"].items():
            if isinstance(v, (str, int, float, bool)):
                extras[str(k)] = v

    for k, v in raw.items():
        if k == "extras":
            continue
        if k == "skill" and v:
            out["skill"] = str(v).strip()
        elif k == "dataset" and v:
            out["dataset"] = str(v).strip()
        elif k == "time_window":
            tw = _normalize_time_window(v)
            if tw is not None:
                out["time_window"] = tw
            elif v is None:
                out["time_window"] = None
        elif k in ("project_id", "project"):
            # accept legacy "project" as project_id
            if v:
                out["project_id"] = str(v).strip()
            elif k == "project_id":
                out["project_id"] = None
        elif k == "tenant_id":
            out["tenant_id"] = str(v).strip() if v else None
        elif k not in _KNOWN and isinstance(v, (str, int, float, bool)):
            extras[str(k)] = v
        # nested unknowns ignored (no workflow DSL)
    if extras:
        out["extras"] = extras
    return out


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge overlay onto base; overlay wins on scalar keys; dicts recurse."""
    result = copy.deepcopy(base) if base else {}
    for k, v in (overlay or {}).items():
        if v is None and k not in ("time_window", "project_id", "tenant_id", "dataset"):
            continue
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = copy.deepcopy(v)
    return result


def resolve_bindings(
    *,
    request: dict[str, Any] | None = None,
    session_bindings: dict[str, Any] | None = None,
    skill_defaults: dict[str, Any] | None = None,
    skill_name: str | None = None,
    inherit_run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """request ⊇ session ⊇ skill.default ⊇ {} ; optional inherit_run as session-like base.

    Follow-ups: pass previous Run.context_bindings as inherit_run so snapshot is inherited
    unless request/session explicitly overrides.
    """
    layers: list[dict[str, Any]] = [{}]
    if skill_defaults:
        layers.append(coerce_partial(skill_defaults))
    if inherit_run:
        layers.append(coerce_partial(inherit_run))
    if session_bindings:
        layers.append(coerce_partial(session_bindings))
    if request:
        layers.append(coerce_partial(request))

    merged: dict[str, Any] = {}
    for layer in layers:
        merged = deep_merge(merged, layer)

    # Force skill resolution
    skill = (
        (request or {}).get("skill")
        or merged.get("skill")
        or skill_name
        or DEFAULT_SKILL
    )
    merged["skill"] = str(skill).strip()

    # Sensible POC default dataset if still missing
    if not merged.get("dataset"):
        merged["dataset"] = DEFAULT_DATASET

    return merged


def session_bindings_from_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    meta = meta or {}
    if isinstance(meta.get("bindings"), dict):
        return coerce_partial(meta["bindings"])
    # legacy: meta.context or flat keys
    if isinstance(meta.get("context"), dict):
        return coerce_partial(meta["context"])
    flat = {k: meta[k] for k in ("skill", "dataset", "time_window", "project_id", "tenant_id", "project") if k in meta}
    return coerce_partial(flat) if flat else {}


def compose_system(manifest_prompt: str, bindings: dict[str, Any]) -> str:
    """system = Manifest.system_prompt + stable bindings JSON (kernel-formatted)."""
    public = {
        k: bindings[k]
        for k in ("skill", "dataset", "time_window", "project_id", "tenant_id")
        if k in bindings and bindings[k] is not None
    }
    extras = bindings.get("extras")
    if isinstance(extras, dict) and extras:
        # only flat scalars already coerced
        public["extras"] = extras
    block = json.dumps(public, ensure_ascii=False, sort_keys=True)
    return (
        (manifest_prompt or "").rstrip()
        + "\n\n# Context bindings\n"
        + block
        + "\n\nWhen tool arguments omit dataset or time_window, use the bindings above."
        + " Real DAS calls use a UUID dataset_id, a filters array"
        + " ({field, operator, value}), a metrics array ({function, field, alias}),"
        + " and a group_by array. sample_records and search_records need a"
        + " non-empty return_fields list, or evidence_preset \"default\"."
        + " The gateway rewrites leftover name/dataset/start/end/metric keys,"
        + " fills ecommerce return_fields when that projection is omitted,"
        + " and resolves a dataset name through list_datasets."
    )


def tool_defaults_from(bindings: dict[str, Any], tool_name: str) -> dict[str, Any]:
    """Defaults filled before Gateway call; arguments win via deep_merge."""
    defaults: dict[str, Any] = {}
    if tool_name in _DATASET_TOOLS:
        ds = bindings.get("dataset")
        if ds:
            # DAS SoT: `name` (also accepts `dataset`); only fill missing keys via merge
            defaults["name"] = ds
        tw = bindings.get("time_window")
        if isinstance(tw, dict):
            if tw.get("start"):
                defaults["start"] = tw["start"]
            if tw.get("end"):
                defaults["end"] = tw["end"]
    return defaults


def merge_tool_arguments(
    tool_name: str,
    arguments: dict[str, Any] | None,
    bindings: dict[str, Any],
) -> dict[str, Any]:
    """deep_merge(tool_defaults_from(bindings), arguments) — arguments win."""
    return deep_merge(tool_defaults_from(bindings, tool_name), arguments or {})


def bindings_fingerprint(bindings: dict[str, Any]) -> str:
    raw = json.dumps(
        {
            "dataset": bindings.get("dataset"),
            "time_window": bindings.get("time_window"),
            "skill": bindings.get("skill"),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


# Broker input budget. The tools schema is not counted; it is fixed per skill.
CONTEXT_MAX_CHARS = 48_000
CONTEXT_MAX_MESSAGES = 40
_CHARS_MIN = 256
_CHARS_MAX = 500_000
_MESSAGES_MIN = 1
_MESSAGES_MAX = 200
_ARG_SHRINK_AT = 400
_ERROR_SHRINK_AT = 160


def resolve_context_limits() -> tuple[int, int]:
    """AGENT_CONTEXT_MAX_CHARS and AGENT_CONTEXT_MAX_MESSAGES.

    Blank, non-integers, and values outside the allowed range keep the defaults.
    """
    return (
        _env_int("AGENT_CONTEXT_MAX_CHARS", CONTEXT_MAX_CHARS, _CHARS_MIN, _CHARS_MAX),
        _env_int("AGENT_CONTEXT_MAX_MESSAGES", CONTEXT_MAX_MESSAGES, _MESSAGES_MIN, _MESSAGES_MAX),
    )


def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value < low or value > high:
        return default
    return value


def _json_len(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _message_chars(message: Any) -> int:
    if isinstance(message, dict):
        role = str(message.get("role") or "")
        content = message.get("content")
        text = "" if content is None else str(content)
        return len(role) + len(text)
    return len(str(message))


def _tool_chars(tool: Any) -> int:
    if isinstance(tool, dict):
        return _json_len(tool)
    return len(str(tool))


def _payload_chars(system: str, messages: list[Any], tools: list[Any]) -> int:
    return len(system) + sum(_message_chars(item) for item in messages) + sum(_tool_chars(item) for item in tools)


def _copy_message(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        role = item.get("role") or "user"
        if role not in ("user", "assistant", "system"):
            role = "user"
        content = item.get("content")
        return {"role": role, "content": "" if content is None else str(content)}
    return {"role": "user", "content": str(item)}


def _copy_tool(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return copy.deepcopy(item)
    return {"name": "", "ok": False, "error": str(item), "data": None, "evidence": None}


def _latest_user_index(messages: list[dict[str, Any]]) -> int | None:
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.get("role") == "user" and not message.get("_context_note"):
            return index
    return None


def _drop_oldest_message(messages: list[dict[str, Any]]) -> bool:
    protected = _latest_user_index(messages)
    for index in range(len(messages)):
        if index == protected:
            continue
        messages.pop(index)
        return True
    return False


def _newest_loop_break(tools: list[dict[str, Any]]) -> int | None:
    found: int | None = None
    for index, tool in enumerate(tools):
        if tool.get("loop_break"):
            found = index
    return found


def _oldest_non_ok(tools: list[dict[str, Any]]) -> int | None:
    protected = _newest_loop_break(tools)
    for index, tool in enumerate(tools):
        if index == protected:
            continue
        if tool.get("ok") is not True:
            return index
    return None


def _oldest_extra_ok(tools: list[dict[str, Any]]) -> int | None:
    oks = [index for index, tool in enumerate(tools) if tool.get("ok") is True]
    if len(oks) < 2:
        return None
    return oks[0]


def _drop_before_last_ok(tools: list[dict[str, Any]]) -> int | None:
    """Drop the oldest row that is not the newest successful evidence."""
    if len(tools) <= 1:
        return None
    oks = [index for index, tool in enumerate(tools) if tool.get("ok") is True]
    keep = oks[-1] if oks else None
    for index in range(len(tools)):
        if index != keep:
            return index
    return None


def _clip_text(text: str, keep: int) -> str:
    if keep >= len(text):
        return text
    if keep <= 1:
        return "…"
    return text[: keep - 1] + "…"


def _shrink_tool(result: dict[str, Any]) -> bool:
    """Replace a large blob with a short rule-made preview. Keep name, ok, evidence."""
    before = _json_len(result)
    saved = {
        "data": copy.deepcopy(result.get("data")),
        "arguments": copy.deepcopy(result.get("arguments")),
        "error": result.get("error"),
    }
    evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
    fingerprint = evidence.get("fingerprint")
    data = result.get("data")
    preview: dict[str, Any] = {"truncated": True}
    if fingerprint:
        preview["fingerprint"] = fingerprint
    replaced = False
    if isinstance(data, dict):
        rows = data.get("rows")
        if isinstance(rows, list):
            preview["n"] = len(rows)
            if rows and isinstance(rows[0], dict):
                top: dict[str, Any] = {}
                for key in list(rows[0])[:4]:
                    value = rows[0][key]
                    if isinstance(value, str) and len(value) > 40:
                        value = value[:39] + "…"
                    if isinstance(value, (str, int, float, bool)) or value is None:
                        top[str(key)] = value
                if top:
                    preview["top"] = top
        else:
            for key, value in list(data.items())[:3]:
                if isinstance(value, (int, float, bool)) or value is None:
                    preview[str(key)] = value
                elif isinstance(value, str):
                    preview[str(key)] = value[:40]
        result["data"] = preview
        replaced = True
    elif isinstance(data, list):
        preview["n"] = len(data)
        result["data"] = preview
        replaced = True
    elif isinstance(data, str) and data:
        preview["text"] = data[:40]
        result["data"] = preview
        replaced = True
    elif data not in (None, "", {}, []):
        result["data"] = preview
        replaced = True

    arguments = result.get("arguments")
    if isinstance(arguments, dict) and _json_len(arguments) > _ARG_SHRINK_AT:
        result["arguments"] = {"_truncated": True}
        replaced = True
    error = result.get("error")
    if isinstance(error, str) and len(error) > _ERROR_SHRINK_AT:
        result["error"] = error[: _ERROR_SHRINK_AT - 1] + "…"
        replaced = True
    if not replaced or _json_len(result) >= before:
        result["data"] = saved["data"]
        result["arguments"] = saved["arguments"]
        result["error"] = saved["error"]
        result.pop("data_truncated", None)
        return False
    result["data_truncated"] = True
    return True


def _clip_latest_user(
    messages: list[dict[str, Any]],
    system: str,
    tools: list[dict[str, Any]],
    max_chars: int,
) -> bool:
    changed = False
    for _ in range(4):
        size = _payload_chars(system, messages, tools)
        if size <= max_chars:
            return changed
        index = _latest_user_index(messages)
        if index is None:
            return changed
        content = str(messages[index].get("content") or "")
        if len(content) <= 1:
            return changed
        overflow = size - max_chars
        updated = _clip_text(content, max(1, len(content) - overflow))
        if updated == content:
            return changed
        messages[index]["content"] = updated
        changed = True
    return changed


def _context_note(dropped_messages: int, dropped_tools: int, shrunk_tools: int) -> dict[str, Any]:
    return {
        "role": "user",
        "_context_note": True,
        "content": (
            "[context] "
            f"dropped_messages={dropped_messages} "
            f"dropped_tool_results={dropped_tools} "
            f"shrunk_tool_results={shrunk_tools}"
        ),
    }


def fit_context(
    *,
    system: str,
    messages: list[Any] | None,
    tool_results: list[Any] | None,
    bindings: dict[str, Any] | None = None,
    max_chars: int | None = None,
    max_messages: int | None = None,
) -> dict[str, Any]:
    """Trim one broker call. Inputs are not mutated.

    Drop oldest messages first (never the latest user turn), then oldest
    unsuccessful tool rows, then older successful rows. A still-too-large kept
    row is replaced by a short preview. The newest loop_break row is kept until
    that preview pass is not enough.
    """
    env_chars, env_messages = resolve_context_limits()
    char_budget = env_chars if max_chars is None else max(1, int(max_chars))
    message_budget = env_messages if max_messages is None else max(1, int(max_messages))
    system_text = "" if system is None else str(system)
    raw_messages = list(messages or [])
    raw_tools = list(tool_results or [])
    original_chars = _payload_chars(system_text, raw_messages, raw_tools)
    fingerprint = bindings_fingerprint(bindings) if bindings else None
    base = {
        "system": system_text,
        "max_chars": char_budget,
        "max_messages": message_budget,
        "original_chars": original_chars,
        "dropped_messages": 0,
        "dropped_tool_results": 0,
        "shrunk_tool_results": 0,
        "clipped_user": False,
        "bindings_fingerprint": fingerprint,
    }
    if original_chars <= char_budget and len(raw_messages) <= message_budget:
        return {
            **base,
            "messages": raw_messages,
            "tool_results": raw_tools,
            "truncated": False,
            "kept_messages": len(raw_messages),
            "kept_tool_results": len(raw_tools),
            "kept_chars": original_chars,
        }

    kept_messages = [_copy_message(item) for item in raw_messages]
    kept_tools = [_copy_tool(item) for item in raw_tools]
    dropped_messages = 0
    dropped_tools = 0
    shrunk_tools = 0

    while len(kept_messages) > message_budget:
        if not _drop_oldest_message(kept_messages):
            break
        dropped_messages += 1

    while _payload_chars(system_text, kept_messages, kept_tools) > char_budget:
        if not _drop_oldest_message(kept_messages):
            break
        dropped_messages += 1

    while _payload_chars(system_text, kept_messages, kept_tools) > char_budget:
        index = _oldest_non_ok(kept_tools)
        if index is None:
            break
        kept_tools.pop(index)
        dropped_tools += 1

    while _payload_chars(system_text, kept_messages, kept_tools) > char_budget:
        index = _oldest_extra_ok(kept_tools)
        if index is None:
            break
        kept_tools.pop(index)
        dropped_tools += 1

    shrunk_at: set[int] = set()
    while _payload_chars(system_text, kept_messages, kept_tools) > char_budget:
        index = next((i for i in range(len(kept_tools)) if i not in shrunk_at), None)
        if index is None:
            break
        shrunk_at.add(index)
        if _shrink_tool(kept_tools[index]):
            shrunk_tools += 1

    while _payload_chars(system_text, kept_messages, kept_tools) > char_budget:
        index = _drop_before_last_ok(kept_tools)
        if index is None:
            break
        kept_tools.pop(index)
        dropped_tools += 1

    clipped_user = _clip_latest_user(kept_messages, system_text, kept_tools, char_budget)
    if dropped_messages or dropped_tools or shrunk_tools or clipped_user:
        kept_messages.insert(0, _context_note(dropped_messages, dropped_tools, shrunk_tools))
        if _payload_chars(system_text, kept_messages, kept_tools) > char_budget:
            # The marker is optional. The latest user turn is not.
            kept_messages.pop(0)

    kept_chars = _payload_chars(system_text, kept_messages, kept_tools)
    truncated = bool(
        dropped_messages
        or dropped_tools
        or shrunk_tools
        or clipped_user
        or original_chars > char_budget
        or len(raw_messages) > message_budget
    )
    return {
        **base,
        "messages": kept_messages,
        "tool_results": kept_tools,
        "truncated": truncated,
        "dropped_messages": dropped_messages,
        "dropped_tool_results": dropped_tools,
        "shrunk_tool_results": shrunk_tools,
        "clipped_user": clipped_user,
        "kept_messages": len(kept_messages),
        "kept_tool_results": len(kept_tools),
        "kept_chars": kept_chars,
    }


def summarize_tool(
    name: str,
    evidence: dict[str, Any] | None,
    result: dict[str, Any],
    *,
    bindings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    preview: Any
    if "rows" in result:
        preview = {"n": len(result["rows"]), "top": result["rows"][:5]}
    elif "datasets" in result:
        preview = result["datasets"]
    elif "summary" in result:
        preview = result["summary"]
    elif "count" in result:
        preview = {"count": result["count"]}
    else:
        preview = {k: result[k] for k in list(result)[:5]}
    out: dict[str, Any] = {
        "tool": name,
        "fingerprint": (evidence or {}).get("fingerprint"),
        "preview": preview,
    }
    if bindings:
        out["bindings_fingerprint"] = bindings_fingerprint(bindings)
    return out
