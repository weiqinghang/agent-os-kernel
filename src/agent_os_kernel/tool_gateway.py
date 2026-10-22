"""MCP client gateway — whitelist only; merge ContextBindings defaults before call."""
from __future__ import annotations

import os
from typing import Any

import httpx

from . import context

WHITELIST = {
    "list_datasets",
    "describe_dataset",
    "query_stats",
    "search_records",
    "sample_records",
}


class ToolGatewayError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class ToolGateway:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or os.getenv("FAKE_DAS_URL") or "http://127.0.0.1:8788").rstrip("/")

    def call(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        bindings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if name not in WHITELIST:
            raise ToolGatewayError(f"tool not whitelisted: {name}")
        merged = context.merge_tool_arguments(name, arguments, bindings or {})
        if "sql" in merged:
            raise ToolGatewayError("raw SQL rejected by tool gateway")
        # tenant/project passthrough only — attach if DAS ever reads them
        if bindings:
            if bindings.get("tenant_id") and "tenant_id" not in merged:
                merged["tenant_id"] = bindings["tenant_id"]
            if bindings.get("project_id") and "project_id" not in merged:
                merged["project_id"] = bindings["project_id"]
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": merged},
        }
        with httpx.Client(timeout=5.0) as client:
            r = client.post(f"{self.base_url}/mcp", json=payload)
        if r.status_code != 200:
            raise ToolGatewayError(f"DAS {r.status_code}: {r.text[:200]}", r.status_code)
        body = r.json()
        result = body.get("result") or {}
        structured = result.get("structuredContent") or {}
        evidence = result.get("evidence") or {}
        # ensure merged args visible on evidence for stub key matching
        if "arguments" not in evidence:
            evidence = {**evidence, "arguments": merged}
        return {"data": structured, "evidence": evidence}

    def list_tools(self) -> list[str]:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        with httpx.Client(timeout=5.0) as client:
            r = client.post(f"{self.base_url}/mcp", json=payload)
        r.raise_for_status()
        tools = (r.json().get("result") or {}).get("tools") or []
        return [t["name"] for t in tools]
