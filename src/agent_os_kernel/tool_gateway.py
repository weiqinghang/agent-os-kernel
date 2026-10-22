"""MCP client gateway — whitelist only; merge ContextBindings defaults before call.

Real DAS (default): POST JSON-RPC to ``DAS_MCP_URL`` (``…/api/mcp``) with
``Authorization: Bearer <DAS_MCP_AUTH_TOKEN>``.
Overnight fake DAS only when ``USE_FAKE_DAS=1`` (``{FAKE_DAS_URL}/mcp``, no auth).
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from . import context

# SoT for tool names. describe_* added because on-box real MCP tools/list
# exposes describe_fields and describe_tag_hierarchies (see designs/collab/DAS-LOCAL-ON-BOX.md).
WHITELIST = {
    "list_datasets",
    "describe_dataset",
    "describe_fields",
    "describe_tag_hierarchies",
    "query_stats",
    "search_records",
    "sample_records",
}

_DATASET_SCOPED = frozenset(
    {
        "describe_dataset",
        "describe_fields",
        "describe_tag_hierarchies",
        "query_stats",
        "search_records",
        "sample_records",
    }
)

_ACCEPT = "application/json, text/event-stream"


class ToolGatewayError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def use_fake_das() -> bool:
    return os.getenv("USE_FAKE_DAS", "").strip() == "1"


def resolve_mcp_url() -> str:
    """Endpoint for tools/list and tools/call."""
    explicit = (os.getenv("DAS_MCP_URL") or "").strip().rstrip("/")
    if use_fake_das():
        base = (os.getenv("FAKE_DAS_URL") or "http://127.0.0.1:8788").rstrip("/")
        return f"{base}/mcp"
    if explicit:
        return explicit
    base = (os.getenv("FAKE_DAS_URL") or "http://127.0.0.1:8788").rstrip("/")
    return f"{base}/api/mcp"


def resolve_health_url() -> str:
    explicit = (os.getenv("DAS_HEALTH_URL") or "").strip()
    if explicit:
        return explicit
    parts = urlsplit(resolve_mcp_url())
    return urlunsplit((parts.scheme, parts.netloc, "/health", "", ""))


def _fingerprint(name: str, args: dict[str, Any]) -> str:
    raw = json.dumps({"name": name, "args": args}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _parse_sse(text: str) -> dict[str, Any]:
    """Last JSON-RPC response in an SSE body (ignore notifications)."""
    found: dict[str, Any] | None = None
    for block in text.split("\n\n"):
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if not data_lines:
            continue
        raw = "\n".join(data_lines).strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ToolGatewayError(f"DAS SSE is not JSON: {raw[:120]}") from e
        if not isinstance(payload, dict):
            continue
        method = str(payload.get("method") or "")
        if method.startswith("notifications/"):
            continue
        if "result" in payload or "error" in payload:
            found = payload
    if found is None:
        raise ToolGatewayError("empty SSE from DAS")
    return found


def parse_mcp_response(response: httpx.Response) -> dict[str, Any]:
    if response.status_code != 200:
        raise ToolGatewayError(
            f"DAS {response.status_code}: {response.text[:200]}",
            response.status_code,
        )
    ctype = (response.headers.get("content-type") or "").lower()
    if "text/event-stream" in ctype:
        body = _parse_sse(response.text)
    else:
        try:
            body = response.json()
        except json.JSONDecodeError as e:
            raise ToolGatewayError(f"DAS body is not JSON: {response.text[:120]}") from e
    if not isinstance(body, dict):
        raise ToolGatewayError("DAS JSON-RPC body is not an object")
    if body.get("error"):
        err = body["error"]
        message = err.get("message") if isinstance(err, dict) else str(err)
        raise ToolGatewayError(f"DAS JSON-RPC error: {message}")
    return body


def structured_from_result(result: dict[str, Any]) -> dict[str, Any]:
    """MCP result → dict. Prefer structuredContent, else JSON text/json content."""
    if result.get("isError"):
        bits: list[str] = []
        for item in result.get("content") or []:
            if isinstance(item, dict) and item.get("text"):
                bits.append(str(item["text"]))
        raise ToolGatewayError("DAS tool error: " + (" ".join(bits) or "isError"))
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    if isinstance(structured, list):
        return {"items": structured}
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "json" and isinstance(item.get("json"), dict):
            return item["json"]
        if item.get("type") == "text" and isinstance(item.get("text"), str):
            text = item["text"].strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
            if isinstance(parsed, dict):
                return parsed
            return {"items": parsed}
    return {}


class ToolGateway:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        token: str | None = None,
        client: httpx.Client | None = None,
    ):
        # base_url, when passed, is the full MCP endpoint (tests / explicit override).
        self._mcp_url = base_url.rstrip("/") if base_url else None
        self._token = token
        self._client = client

    def mcp_url(self) -> str:
        return self._mcp_url or resolve_mcp_url()

    def auth_token(self) -> str:
        if self._token is not None:
            return self._token.strip()
        return (os.getenv("DAS_MCP_AUTH_TOKEN") or "").strip()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": _ACCEPT,
            "Content-Type": "application/json",
        }
        token = self.auth_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        elif not use_fake_das():
            raise ToolGatewayError(
                "DAS_MCP_AUTH_TOKEN required for real DAS MCP",
                status_code=401,
            )
        return headers

    @contextmanager
    def _open(self) -> Iterator[httpx.Client]:
        if self._client is not None:
            yield self._client
            return
        with httpx.Client(timeout=5.0) as client:
            yield client

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._open() as client:
            response = client.post(self.mcp_url(), json=payload, headers=self._headers())
        return parse_mcp_response(response)

    def _adapt(self, name: str, merged: dict[str, Any]) -> dict[str, Any]:
        """Real MCP tools take dataset_id; fake DAS uses name. Fill only missing keys."""
        if use_fake_das() or name not in _DATASET_SCOPED:
            return merged
        ds = merged.get("dataset_id") or merged.get("name") or merged.get("dataset")
        if ds and "dataset_id" not in merged:
            merged = {**merged, "dataset_id": ds}
        return merged

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
        if "sql" in merged or any(str(k).lower() == "sql" for k in merged):
            raise ToolGatewayError("raw SQL rejected by tool gateway")
        merged = self._adapt(name, merged)
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
        body = self._post(payload)
        result = body.get("result") or {}
        if not isinstance(result, dict):
            raise ToolGatewayError("DAS result is not an object")
        structured = structured_from_result(result)
        evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
        evidence = {
            **evidence,
            "tool": evidence.get("tool") or name,
            "arguments": evidence.get("arguments") or merged,
        }
        if not evidence.get("fingerprint"):
            evidence["fingerprint"] = _fingerprint(name, merged)
        return {"data": structured, "evidence": evidence}

    def list_tools(self) -> list[str]:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        body = self._post(payload)
        result = body.get("result") or {}
        tools = result.get("tools") if isinstance(result, dict) else None
        names: list[str] = []
        for tool in tools or []:
            if isinstance(tool, dict) and tool.get("name"):
                names.append(str(tool["name"]))
            elif isinstance(tool, str):
                names.append(tool)
        return names

    def probe_health(self) -> bool:
        try:
            with self._open() as client:
                response = client.get(resolve_health_url(), timeout=2.0)
            return response.status_code == 200
        except Exception:  # noqa: BLE001
            return False
