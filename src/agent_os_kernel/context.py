"""Context: message + tool summary fingerprints (no full samples)."""
from __future__ import annotations

from typing import Any


def summarize_tool(name: str, evidence: dict[str, Any] | None, result: dict[str, Any]) -> dict[str, Any]:
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
    return {
        "tool": name,
        "fingerprint": (evidence or {}).get("fingerprint"),
        "preview": preview,
    }
