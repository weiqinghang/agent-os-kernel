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

# Fake-DAS argument names. Real MCP models use extra=forbid / additionalProperties: false.
_FAKE_ARG_KEYS = frozenset({"name", "dataset", "start", "end", "time_window", "metric"})
_FILTER_TOOLS = frozenset({"query_stats", "search_records", "sample_records"})
_DATE_FIELD = "order_date"
# Dogfood metric ids from the overnight fake DAS, read as a metrics object.
_METRIC_FN: dict[str, tuple[str, str | None]] = {
    "sum_gmv": ("sum", "gmv"),
    "sum_qty": ("sum", "qty"),
    "avg_gmv": ("avg", "gmv"),
    "count": ("count", None),
    "return_rate": ("avg", "returned"),
}

_ACCEPT = "application/json, text/event-stream"


def _window_bounds(src: dict[str, Any]) -> tuple[Any, Any]:
    start, end = src.get("start"), src.get("end")
    window = src.get("time_window")
    if isinstance(window, dict):
        start = start or window.get("start")
        end = end or window.get("end")
    return start, end


def _clauses_from_filters(filters: Any) -> list[dict[str, Any]]:
    """Flat fake filters or an existing DSL object → clause list."""
    if isinstance(filters, list):
        return [clause for clause in filters if isinstance(clause, dict)]
    if not isinstance(filters, dict):
        return []
    nested = filters.get("clauses")
    if isinstance(nested, list):
        return [clause for clause in nested if isinstance(clause, dict)]
    nested = filters.get("conditions")
    if isinstance(nested, list):
        return [clause for clause in nested if isinstance(clause, dict)]
    clauses: list[dict[str, Any]] = []
    for field, value in filters.items():
        if field in {"op", "logic", "and", "or"}:
            continue
        if isinstance(value, dict):
            for op, val in value.items():
                clauses.append({"field": str(field), "op": str(op), "value": val})
        else:
            clauses.append({"field": str(field), "op": "eq", "value": value})
    return clauses


def _with_window(clauses: list[dict[str, Any]], start: Any, end: Any) -> list[dict[str, Any]]:
    seen = {(clause.get("field"), clause.get("op")) for clause in clauses}
    out = list(clauses)
    if start and (_DATE_FIELD, "gte") not in seen and (_DATE_FIELD, "ge") not in seen:
        out.append({"field": _DATE_FIELD, "op": "gte", "value": start})
    if end and (_DATE_FIELD, "lte") not in seen and (_DATE_FIELD, "le") not in seen:
        out.append({"field": _DATE_FIELD, "op": "lte", "value": end})
    return out


def _filters_object(src: dict[str, Any]) -> dict[str, Any] | None:
    start, end = _window_bounds(src)
    clauses = _with_window(_clauses_from_filters(src.get("filters")), start, end)
    if not clauses:
        return None
    return {"op": "and", "clauses": clauses}


def _metrics_object(src: dict[str, Any]) -> Any:
    """String `metric` → metrics object. An existing metrics dict/list wins."""
    existing = src.get("metrics")
    if isinstance(existing, (dict, list)) and existing:
        return existing
    metric = src.get("metric")
    if isinstance(metric, dict) and metric:
        return metric
    if not isinstance(metric, str) or not metric.strip():
        return None
    name = metric.strip()
    fn, field = _METRIC_FN.get(name, (None, None))
    spec: dict[str, Any] = {"name": name}
    if fn:
        spec["fn"] = fn
    if field:
        spec["field"] = field
    return {name: spec}


def adapt_real_arguments(name: str, merged: dict[str, Any]) -> dict[str, Any]:
    """Normalize fake-DAS / mixed args onto the real MCP argument schema.

    Real tools reject unknown keys (``name``, ``dataset``, ``start``, ``end``,
    string ``metric``). Dataset scope is ``dataset_id``. Time window and flat
    equality filters become a ``filters`` object. A metric string becomes a
    ``metrics`` object. ``group_by`` and ``limit`` pass through on ``query_stats``.

    ``describe_*`` only receives ``dataset_id`` — those schemas do not take the
    fake time-window keys. ``USE_FAKE_DAS`` does not call this function.
    """
    dataset_id = merged.get("dataset_id") or merged.get("name") or merged.get("dataset")
    out: dict[str, Any] = {}
    if name in _DATASET_SCOPED and dataset_id:
        out["dataset_id"] = dataset_id

    if name in _FILTER_TOOLS:
        filters = _filters_object(merged)
        if filters:
            out["filters"] = filters
    if name == "query_stats":
        metrics = _metrics_object(merged)
        if metrics:
            out["metrics"] = metrics
        if merged.get("group_by") is not None:
            out["group_by"] = merged["group_by"]
        if merged.get("limit") is not None:
            out["limit"] = merged["limit"]
    elif name == "search_records":
        for key in ("q", "keyword", "field", "query", "limit", "offset"):
            if merged.get(key) not in (None, ""):
                out[key] = merged[key]
    elif name == "sample_records":
        for key in ("limit", "offset"):
            if merged.get(key) is not None:
                out[key] = merged[key]
    elif name not in _DATASET_SCOPED:
        for key, value in merged.items():
            if key not in _FAKE_ARG_KEYS:
                out[key] = value
    return out


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
        """Fake DAS keeps name/start/end/metric. Real MCP gets dataset_id + DSL."""
        if use_fake_das():
            return merged
        return adapt_real_arguments(name, merged)

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
