"""Static Skill registry — startup dict only; NOT hot-load / no directory scan."""
from __future__ import annotations

from typing import Any, Callable

from . import insight_report, return_risk_brief

# Explicit register — write-once at import
_REGISTRY: dict[str, dict[str, Any]] = {
    insight_report.SKILL["name"]: insight_report.SKILL,
    return_risk_brief.SKILL["name"]: return_risk_brief.SKILL,  # deferred stub
}

_STUB: dict[str, Callable[..., dict[str, Any]]] = {
    "insight_report": insight_report.stub_decide,
    "return_risk_brief": return_risk_brief.stub_decide,
}

DEFAULT_NAME = "insight_report"


def default_name() -> str:
    return DEFAULT_NAME


def get(name: str | None) -> dict[str, Any]:
    key = name or DEFAULT_NAME
    if key not in _REGISTRY:
        raise KeyError(f"unknown skill: {key}")
    return _REGISTRY[key]


def list_skills() -> list[dict[str, Any]]:
    out = []
    for s in _REGISTRY.values():
        out.append(
            {
                "name": s["name"],
                "version": s.get("version"),
                "status": s.get("status", "active"),
                "allowed_tools": list(s.get("allowed_tools") or []),
            }
        )
    return out


def stub_decide(skill_name: str, **kwargs: Any) -> dict[str, Any]:
    fn = _STUB.get(skill_name) or _STUB[DEFAULT_NAME]
    return fn(**kwargs)
