"""ContextBindings: Session defaults + Run frozen snapshot; tool summaries."""
from __future__ import annotations

import copy
import hashlib
import json
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
        + " Real DAS calls use dataset_id plus filters/metrics objects;"
        + " the gateway rewrites leftover name/dataset/start/end/metric keys."
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
